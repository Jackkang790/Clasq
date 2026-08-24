# =========================================================
# [db_manager.py]
# DB 자동 저장 무결성 보장, 파일 해시 기반 중복 감지,
# 물리적 파일 동기화 정책을 전담하는 모듈 (담당: 팀원 C)
#
# 기존 main_processor.py에 있던 _init_db / _save_to_db 로직을
# 이 클래스로 완전히 이전하고, 아래 3가지를 보강했다.
#   1) 파일 해시(SHA-256) 기반 중복 감지 -> 내용이 같은 파일은
#      file_path가 달라도 걸러내고 정책에 따라 물리적으로 격리
#   2) 대량 스캔(폴더 전체) 시 커넥션 재사용 + WAL 모드로
#      "database is locked" 및 성능 저하 방지
#   3) DB 레코드와 실제 디스크 파일 상태를 맞추는 동기화 기능
#      (외부에서 파일이 지워지거나 이동된 경우 정리)
# =========================================================
import logging
import os
import sqlite3
import hashlib
import shutil
import time
from contextlib import contextmanager
from typing import Dict, Any, Optional, List
import re

log = logging.getLogger(__name__)


class FileRegistryManager:
    """
    [DB / 물리 파일 담당 클래스]

    main_processor.py, gui_app.py 양쪽에서 공통으로 사용하는
    '파일 레코드 저장소'. files 테이블 스키마와 저장/중복감지/동기화
    로직을 이 클래스 하나로 단일화하여, main_processor.py와
    gui_app.py에 스키마가 중복 정의되던 문제를 제거한다.
    """

    def __init__(
        self,
        db_path: str = "file_manager.db",
        duplicate_policy: str = "quarantine",
        duplicates_dir_name: str = "_duplicates",
    ):
        """
        duplicate_policy (내용이 동일한 파일을 새로 발견했을 때의 처리 정책)
          - "quarantine" : 스캔된 파일을 같은 폴더의 _duplicates 하위 폴더로
                           물리적으로 옮기고, DB에는 원본 파일 경로만 유지 (기본값)
          - "skip"       : 물리 파일은 그대로 두고, DB에도 새로 기록하지 않음
          - "keep"       : 중복이어도 그냥 완전히 별개 파일처럼 DB에 저장
        """
        self.db_path = db_path
        self.duplicate_policy = duplicate_policy
        self.duplicates_dir_name = duplicates_dir_name
        self._bulk_conn: Optional[sqlite3.Connection] = None  # 대량 스캔용 재사용 커넥션
        self._init_db()

    # ---------------------------------------------------------
    # 0. 커넥션 helper
    #    WAL 모드: GUI 스레드가 읽는 동안 워커 스레드가 써도 서로 안 막힘
    #    busy_timeout: 스캔 중 순간적인 락 충돌 시 즉시 에러 대신 잠깐 대기
    # ---------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.text_factory = str  # 한글을 변환 없이 UTF-8 문자열 그대로 읽고 씁니다.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA encoding='UTF-8'")
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        """bulk_session() 안이면 재사용 커넥션을, 아니면 새 커넥션을 반환"""
        return self._bulk_conn if self._bulk_conn is not None else self._connect()

    # ---------------------------------------------------------
    # 1. DB 초기화 및 스키마 마이그레이션
    #    _init_db(): 최소 기반 구조(files + db_schema_version)만 보장
    #    _run_migrations(): 버전별 schema 확장을 순서대로 적용
    # ---------------------------------------------------------

    # 현재 최신 schema version 번호
    _CURRENT_VERSION = 3

    def _init_db(self) -> None:
        """최소 기반 구조만 생성. 실제 schema 확장은 _run_migrations()에서 처리."""
        conn = self._connect()
        try:
            # 절대 최소: AI 분석 결과를 저장할 기본 테이블
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name TEXT,
                    file_path TEXT UNIQUE,
                    ai_comment TEXT,
                    category  TEXT
                )
                """
            )
            # migration 인프라: schema 버전 기록 테이블
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS db_schema_version (
                    version    INTEGER PRIMARY KEY,
                    applied_at TEXT    DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        self._run_migrations()

    def _run_migrations(self) -> None:
        """db_schema_version 기준으로 미적용 migration을 순서대로 실행.

        - 각 migration은 독립 트랜잭션으로 실행된다.
        - 성공한 경우에만 version을 기록한다.
        - 실패하면 해당 migration만 rollback하고 이후 migration도 중단한다
          (의존 관계 보호: 이전 단계 없이 다음 단계를 적용하지 않는다).
        """
        conn = self._connect()
        try:
            current = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM db_schema_version"
            ).fetchone()[0]
        finally:
            conn.close()

        migrations = [
            (1, "core tables / columns / indexes", self._migration_v1),
            (2, "file_modified_at backfill from file_mtime_ns", self._migration_v2),
            (3, "organize_history table for Undo/History", self._migration_v3),
        ]

        for version, description, fn in migrations:
            if current >= version:
                continue
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                fn(conn)
                conn.execute(
                    "INSERT INTO db_schema_version (version, description) VALUES (?, ?)",
                    (version, description),
                )
                conn.commit()
                current = version
                log.info("DB migration v%d applied: %s", version, description)
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                log.error(
                    "DB migration v%d failed (DB unchanged): %s", version, exc
                )
                break  # 이후 버전도 실행하지 않음
            finally:
                conn.close()

    # ---------------------------------------------------------
    # 1-a. Migration 구현 (각 버전은 idempotent: IF NOT EXISTS + 컬럼 존재 체크)
    # ---------------------------------------------------------

    def _migration_v1(self, conn: sqlite3.Connection) -> None:
        """모든 테이블·컬럼·인덱스를 확보한다.

        이미 존재하는 항목은 건너뛰므로 기존 DB에서도 안전하게 실행된다.
        """
        # ── files 추가 컬럼 ─────────────────────────────────────────────
        self._add_columns(conn, "files", {
            "display_name":    "TEXT",
            "file_hash":       "TEXT",
            "file_size":       "INTEGER",
            "created_at":      "TEXT",
            "updated_at":      "TEXT",
            "file_created_at": "TEXT",
            "file_modified_at":"TEXT",
            "tags":            "TEXT",
            "source_path":     "TEXT",
            # 증분 분석 변경 감지용 nanosecond mtime
            # file_modified_at(TEXT 초단위)과 별개로 유지한다.
            "file_mtime_ns":   "INTEGER",
        })
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_hash ON files(file_hash)"
        )

        # ── 경로 관리 테이블 ────────────────────────────────────────────
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS managed_paths (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                path       TEXT UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # ── 증분 분석용 파일 지문 캐시 ──────────────────────────────────
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_fingerprint_cache (
                file_path     TEXT PRIMARY KEY,
                file_hash     TEXT NOT NULL,
                file_size     INTEGER NOT NULL,
                file_mtime_ns INTEGER NOT NULL,
                updated_at    TEXT
            )
            """
        )

        # ── 문서 텍스트 검색 인덱스 ─────────────────────────────────────
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_text_index (
                file_path      TEXT PRIMARY KEY,
                file_hash      TEXT,
                file_size      INTEGER,
                file_mtime_ns  INTEGER,
                extracted_text TEXT,
                extractor_type TEXT,
                extract_status TEXT,
                updated_at     TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_text_status "
            "ON file_text_index(extract_status)"
        )

    def _migration_v3(self, conn: sqlite3.Connection) -> None:
        """organize_history: 파일 정리 이력 + Undo 지원 (schema v3).

        한 번의 Apply 승인을 operation_id(UUID)로 묶어 저장한다.
        migration은 idempotent (IF NOT EXISTS).
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS organize_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id  TEXT    NOT NULL,
                original_path TEXT    NOT NULL,
                moved_path    TEXT    NOT NULL,
                file_hash     TEXT,
                file_size     INTEGER,
                status        TEXT    NOT NULL DEFAULT 'applied',
                applied_at    TEXT    NOT NULL,
                undone_at     TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_org_hist_op "
            "ON organize_history(operation_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_org_hist_status "
            "ON organize_history(status, applied_at)"
        )

    def _migration_v2(self, conn: sqlite3.Connection) -> None:
        """file_modified_at backfill: NULL 레코드에만 적용, 기존 값 절대 덮어쓰지 않음.

        ON CONFLICT 없는 단순 UPDATE — 조건 자체가 덮어쓰기를 방지한다.
        """
        conn.execute(
            """
            UPDATE files
            SET    file_modified_at = datetime(
                       file_mtime_ns / 1000000000, 'unixepoch', 'localtime'
                   )
            WHERE  file_modified_at IS NULL
              AND  file_mtime_ns    IS NOT NULL
            """
        )

    # ---------------------------------------------------------
    # 1-b. 내부 schema 헬퍼
    # ---------------------------------------------------------

    def _add_columns(
        self, conn: sqlite3.Connection, table: str, columns: Dict[str, str]
    ) -> None:
        """지정 테이블에 없는 컬럼만 추가한다 (idempotent)."""
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for col_name, col_type in columns.items():
            if col_name not in existing:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                )

    def _migrate_add_columns(
        self, conn: sqlite3.Connection, columns: Dict[str, str]
    ) -> None:
        """하위 호환 래퍼 — _add_columns(conn, 'files', columns) 에 위임."""
        self._add_columns(conn, "files", columns)

    # ---------------------------------------------------------
    # 2. 대량 스캔용 커넥션 재사용 세션
    #    - 파일마다 connect/close를 반복하지 않아 폴더 스캔 성능이 개선됨
    #    - 각 파일 저장은 여전히 개별 트랜잭션(BEGIN~COMMIT)으로 처리되므로
    #      중간에 한 파일이 실패해도 그 전까지 저장된 파일들은 유지된다
    #      (전체를 한 트랜잭션으로 묶지 않는 이유: 수백 개 중 1개 실패로
    #       이미 분석 끝난 나머지 파일들의 DB 기록까지 날아가면 안 되기 때문)
    # ---------------------------------------------------------
    @contextmanager
    def bulk_session(self):
        conn = self._connect()
        self._bulk_conn = conn
        try:
            yield self
        finally:
            conn.close()
            self._bulk_conn = None

    # ---------------------------------------------------------
    # 3. 파일 해시 계산 (중복 감지의 기준)
    # ---------------------------------------------------------
    @staticmethod
    def compute_file_hash(file_path: str, chunk_size: int = 65536) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _find_by_hash(
        self, conn: sqlite3.Connection, file_hash: str, exclude_path: Optional[str] = None
    ) -> Optional[tuple]:
        query = "SELECT file_path FROM files WHERE file_hash = ?"
        params: List[Any] = [file_hash]
        if exclude_path:
            query += " AND file_path != ?"
            params.append(exclude_path)
        return conn.execute(query, params).fetchone()

    # ---------------------------------------------------------
    # 4. 물리적 파일 동기화 정책
    # ---------------------------------------------------------
    def _quarantine_file(self, file_path: str) -> str:
        """내용이 같은 파일을 같은 폴더의 _duplicates 하위 폴더로 이동시키고 새 경로 반환"""
        base_dir = os.path.dirname(file_path) or "."
        dest_dir = os.path.join(base_dir, self.duplicates_dir_name)
        os.makedirs(dest_dir, exist_ok=True)

        filename = os.path.basename(file_path)
        dest_path = os.path.join(dest_dir, filename)

        stem, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(dest_path):
            dest_path = os.path.join(dest_dir, f"{stem}({counter}){ext}")
            counter += 1

        shutil.move(file_path, dest_path)
        return dest_path

    def sync_missing_files(self) -> List[str]:
        """
        DB에는 있지만 실제 디스크에는 없는(사용자가 탐색기에서 지우거나 옮긴)
        레코드를 찾아 정리한다. '물리적 파일 동기화'의 핵심 기능.
        반환값: 정리(삭제)된 file_path 리스트
        """
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        removed: List[str] = []
        try:
            rows = conn.execute("SELECT file_path FROM files").fetchall()
            for (file_path,) in rows:
                if not os.path.exists(file_path):
                    conn.execute(
                        "DELETE FROM files WHERE file_path = ?", (file_path,))
                    removed.append(file_path)
            conn.commit()
        finally:
            if owns_conn:
                conn.close()
        return removed

    # ---------------------------------------------------------
    # 5. 경로 관리 함수
    # ---------------------------------------------------------
    def add_managed_path(self, path: str) -> Dict[str, Any]:
        """관리할 경로를 DB에 추가"""
        path = os.path.abspath(path)
        if not os.path.exists(path):
            return {"success": False, "message": f"경로가 존재하지 않습니다: {path}"}
        
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            conn.execute(
                "INSERT OR IGNORE INTO managed_paths (path) VALUES (?)",
                (path,)
            )
            conn.commit()
            return {"success": True, "message": f"경로가 추가되었습니다: {path}"}
        except Exception as e:
            return {"success": False, "message": f"경로 추가 실패: {str(e)}"}
        finally:
            if owns_conn:
                conn.close()

    def get_managed_paths(self) -> List[str]:
        """관리 중인 모든 경로 조회"""
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            rows = conn.execute("SELECT path FROM managed_paths ORDER BY created_at").fetchall()
            return [row[0] for row in rows]
        finally:
            if owns_conn:
                conn.close()

    def remove_managed_path(self, path: str) -> Dict[str, Any]:
        """관리 경로 제거"""
        path = os.path.abspath(path)
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            cursor = conn.execute(
                "DELETE FROM managed_paths WHERE path = ?",
                (path,)
            )
            conn.commit()
            if cursor.rowcount > 0:
                return {"success": True, "message": f"경로가 제거되었습니다: {path}"}
            else:
                return {"success": False, "message": f"경로를 찾을 수 없습니다: {path}"}
        except Exception as e:
            return {"success": False, "message": f"경로 제거 실패: {str(e)}"}
        finally:
            if owns_conn:
                conn.close()

    # ---------------------------------------------------------
    # 6. 메인 저장 함수 (main_processor.py의 _save_to_db 대체)
    # ---------------------------------------------------------
    def save_file_result(self, file_path: str, metadata_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        AI 분석 완료 후 호출되는 저장 함수.
          1) 파일 해시 계산
          2) 동일 해시(내용 동일) 레코드가 이미 있는지 확인 -> 중복 정책 적용
          3) UPSERT(신규 INSERT / 기존 UPDATE)로 DB 반영
        한 번의 호출은 항상 하나의 트랜잭션으로 처리되어, 저장 도중 오류가 나도
        DB에 절반만 반영된 깨진 레코드가 남지 않는다.
        """
        result: Dict[str, Any] = {"success": False,
                                  "file_path": file_path, "is_duplicate": False}

        if not os.path.exists(file_path):
            result["message"] = f"파일을 찾을 수 없습니다: {file_path}"
            return result

        conn = self._get_conn()
        owns_conn = self._bulk_conn is None

        try:
            conn.execute("BEGIN IMMEDIATE")

            file_hash = self.compute_file_hash(file_path)
            dup_row = self._find_by_hash(
                conn, file_hash, exclude_path=file_path)

            final_path = file_path
            if dup_row is not None:
                result["is_duplicate"] = True
                result["duplicate_of"] = dup_row[0]

                if self.duplicate_policy == "quarantine":
                    final_path = self._quarantine_file(file_path)
                    result["quarantined_to"] = final_path
                    # 격리된 중복본은 원본과 별개 검색 레코드를 만들지 않는다.
                    conn.commit()
                    result["success"] = True
                    result["message"] = "중복 파일을 _duplicates 폴더로 격리했습니다."
                    return result
                elif self.duplicate_policy == "skip":
                    conn.commit()
                    result["success"] = True
                    result["message"] = "중복 파일 - DB 반영 없이 스킵됨"
                    return result
                # "keep" 정책이면 별개 파일처럼 그대로 진행

            meta = metadata_result.get("metadata", {})
            file_name = os.path.basename(final_path)
            display_name = meta.get("display_name", os.path.splitext(file_name)[0])
            ai_comment = meta.get("ai_comment", "")
            tags = meta.get("tags", [])
            category = f"#{tags[0]}" if tags else "#일반"
            tags_str = ",".join(tags) if tags else ""
            source_path = os.path.dirname(final_path)
            stat = os.stat(final_path)
            file_size = stat.st_size
            file_mtime_ns = stat.st_mtime_ns
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            file_created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_ctime))
            file_modified_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

            conn.execute(
                """
                INSERT INTO files (file_name, display_name, file_path, ai_comment, category,
                                    file_hash, file_size, created_at, updated_at, file_created_at,
                                    file_modified_at, tags, source_path, file_mtime_ns)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_name = excluded.file_name,
                    display_name = excluded.display_name,
                    ai_comment = excluded.ai_comment,
                    category = excluded.category,
                    file_hash = excluded.file_hash,
                    file_size = excluded.file_size,
                    updated_at = excluded.updated_at,
                    file_created_at = excluded.file_created_at,
                    file_modified_at = excluded.file_modified_at,
                    tags = excluded.tags,
                    source_path = excluded.source_path,
                    file_mtime_ns = excluded.file_mtime_ns
                """,
                (file_name, display_name, final_path, ai_comment,
                 category, file_hash, file_size, now, now, file_created_at,
                 file_modified_at, tags_str, source_path, file_mtime_ns),
            )

            # 실제 AI 분석 직후에도 증분 fingerprint cache를 준비한다.
            # Apply/Undo는 이 행의 path만 갱신하며 hash를 다시 계산하지 않는다.
            conn.execute(
                """
                INSERT INTO file_fingerprint_cache
                    (file_path, file_hash, file_size, file_mtime_ns, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash     = excluded.file_hash,
                    file_size     = excluded.file_size,
                    file_mtime_ns = excluded.file_mtime_ns,
                    updated_at    = excluded.updated_at
                """,
                (final_path, file_hash, file_size, file_mtime_ns, now),
            )

            conn.commit()
            result["success"] = True
            result["file_path"] = final_path
            return result

        except Exception as e:
            conn.rollback()
            result["message"] = f"DB 저장 오류: {e}"
            return result
        finally:
            if owns_conn:
                conn.close()

    # ---------------------------------------------------------
    # 6. DB 초기화 (gui_app.py의 reset_db_and_path 대체용)
    #    - os.remove() 대신 SQL DELETE + sqlite_sequence 리셋 방식을
    #      그대로 유지 (WinError 32 프로세스 점유 에러 방지 정책 계승)
    #    - 스키마 정의를 이 클래스 한 곳에서만 관리하도록 일원화
    # ---------------------------------------------------------
    def reset_all(self) -> None:
        """모든 분석 데이터와 캐시를 일관되게 초기화한다.

        삭제 대상: files, file_fingerprint_cache, file_text_index
        보존 대상: managed_paths (사용자 경로 설정), db_schema_version (migration 이력)
        """
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            conn.execute("DELETE FROM files;")
            conn.execute("DELETE FROM file_fingerprint_cache;")
            conn.execute("DELETE FROM file_text_index;")
            conn.execute("DELETE FROM sqlite_sequence WHERE name='files';")
            conn.commit()
        finally:
            if owns_conn:
                conn.close()
        # search snapshot 무효화 (search_engine이 없을 수 있으므로 직접 호출)
        try:
            from .search_snapshot import invalidate_search_snapshot
            invalidate_search_snapshot(self.db_path)
        except Exception:
            pass

    def list_files(self) -> List[Dict[str, Any]]:
        """저장 목록에서 사용할 분석 결과를 실제 디스크 상태와 함께 반환합니다."""
        self.sync_missing_files()
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            rows = conn.execute(
                """SELECT id, file_name, display_name, file_path, category, tags,
                          ai_comment, file_size, COALESCE(file_modified_at, updated_at)
                   FROM files ORDER BY updated_at DESC, file_name"""
            ).fetchall()
            return [
                {
                    "id": row[0], "file_name": row[1],
                    "display_name": row[2] or os.path.splitext(row[1])[0],
                    "file_path": row[3], "category": row[4] or "#일반",
                    "tags": row[5] or "",
                    "description": (row[6] or "").split(" / 코멘트: ", 1)[-1],
                    "file_size": row[7] or 0, "updated_at": row[8] or "",
                }
                for row in rows
            ]
        finally:
            if owns_conn:
                conn.close()

    def update_file_metadata(self, file_id: int, display_name: str, tags: str, description: str) -> Dict[str, Any]:
        """표시명·태그·설명을 갱신하며 실제 파일의 위치와 이름은 건드리지 않습니다."""
        clean_tags = [tag.strip().lstrip("#") for tag in tags.split(",") if tag.strip()]
        category = f"#{clean_tags[0]}" if clean_tags else "#일반"
        comment = f"태그: {', '.join('#' + tag for tag in clean_tags) or '#일반'} / 코멘트: {description.strip()}"
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            cursor = conn.execute(
                """UPDATE files SET display_name = ?, tags = ?, category = ?, ai_comment = ?,
                           updated_at = ? WHERE id = ?""",
                (display_name.strip(), ",".join(clean_tags), category, comment,
                 time.strftime("%Y-%m-%d %H:%M:%S"), file_id),
            )
            conn.commit()
            return {"success": cursor.rowcount == 1,
                    "message": "메타데이터를 저장했습니다." if cursor.rowcount == 1 else "저장할 파일 레코드를 찾을 수 없습니다."}
        except sqlite3.Error as exc:
            conn.rollback()
            return {"success": False, "message": f"DB 저장 실패: {exc}"}
        finally:
            if owns_conn:
                conn.close()

    @staticmethod
    def _available_destination(directory: str, file_name: str) -> str:
        candidate = os.path.join(directory, file_name)
        stem, extension = os.path.splitext(file_name)
        index = 1
        while os.path.exists(candidate):
            candidate = os.path.join(directory, f"{stem} ({index}){extension}")
            index += 1
        return candidate

    def move_file_safely(self, file_id: int, target_dir: str) -> Dict[str, Any]:
        """파일 이동을 검증한 뒤 DB를 갱신하고, DB 실패 시 물리적 이동을 되돌립니다."""
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        old_path = new_path = ""
        try:
            row = conn.execute("SELECT file_path FROM files WHERE id = ?", (file_id,)).fetchone()
            if not row:
                return {"success": False, "message": f"DB 레코드 없음 (id={file_id})"}
            old_path = row[0]
            if not os.path.isfile(old_path):
                return {"success": False, "message": f"파일 없음: {old_path}"}
            os.makedirs(target_dir, exist_ok=True)
            new_path = self._available_destination(target_dir, os.path.basename(old_path))
            shutil.move(old_path, new_path)
            if not os.path.isfile(new_path):
                raise OSError("파일 이동 결과를 확인하지 못했습니다.")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE files SET file_name = ?, file_path = ?, source_path = ?, updated_at = ? WHERE id = ?",
                (os.path.basename(new_path), new_path, target_dir, time.strftime("%Y-%m-%d %H:%M:%S"), file_id),
            )
            conn.commit()
            return {"success": True, "old_path": old_path, "new_path": new_path}
        except Exception as exc:
            conn.rollback()
            if new_path and os.path.isfile(new_path) and old_path and not os.path.exists(old_path):
                try:
                    shutil.move(new_path, old_path)
                except OSError as rollback_error:
                    return {"success": False, "message": f"이동 후 DB 반영 실패: {exc}; 되돌리기 실패: {rollback_error}"}
            return {"success": False, "message": f"이동 실패 ({old_path}): {exc}"}
        finally:
            if owns_conn:
                conn.close()

    def organize_file(self, file_id: int, target_category: str, base_dir: str) -> bool:
        """물리적 파일 이동 및 DB 업데이트"""
        # 0. 폴더명으로 쓸 수 없는 문자 제거
        safe_category = re.sub(r'[\\/*?:"<>|]', "", target_category).strip()

        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT file_path FROM files WHERE id = ?", (file_id,)
            ).fetchone()

            if not row:
                return False

            old_path = row[0]

            if not os.path.exists(old_path):
                print(f"[에러] 원본 파일을 찾을 수 없음: {old_path}")
                return False

            new_dir = os.path.join(base_dir, safe_category)
            os.makedirs(new_dir, exist_ok=True)
            new_path = os.path.join(new_dir, os.path.basename(old_path))

            # 파일 이동
            shutil.move(old_path, new_path)

            # DB 업데이트
            conn.execute(
                "UPDATE files SET file_path = ? WHERE id = ?", (
                    new_path, file_id)
            )
            conn.commit()
            return True

        except Exception as e:
            print(f"[파일 이동 실패]: {e}")
            return False

        finally:
            if self._bulk_conn is None:  # bulk_session이 아닐 때만 닫음
                conn.close()

    def delete_record(self, file_id: int) -> bool:
        """DB 레코드 삭제 + 연관 캐시 레코드 cascade 정리.

        - files 레코드를 삭제한다.
        - file_fingerprint_cache, file_text_index 의 동일 file_path 레코드를 함께 정리한다.
        - 실제 파일/폴더는 절대 건드리지 않는다 (os.remove / unlink / shutil.rmtree 금지).
        - 세 DELETE는 하나의 트랜잭션으로 묶인다.
        """
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            row = conn.execute(
                "SELECT file_path FROM files WHERE id = ?", (file_id,)
            ).fetchone()
            if not row:
                return False
            file_path = row[0]

            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
            conn.execute(
                "DELETE FROM file_fingerprint_cache WHERE file_path = ?", (file_path,)
            )
            conn.execute(
                "DELETE FROM file_text_index WHERE file_path = ?", (file_path,)
            )
            conn.commit()
            return True

        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[DB 레코드 삭제 실패]: {exc}")
            return False
        finally:
            if owns_conn:
                conn.close()

    def delete_file(self, file_id: int) -> bool:
        """delete_record()의 이전 이름. DB 레코드만 삭제하며 실제 파일은 유지된다."""
        return self.delete_record(file_id)

    def get_all_files(self) -> List[Dict[str, Any]]:
        """저장 목록 화면용 - DB에 저장된 모든 파일을 조회"""
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            rows = conn.execute(
                "SELECT id, file_name, tags, file_path, category FROM files ORDER BY file_name"
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "file_name": row[1],
                    "tags": row[2] or "",
                    "file_path": row[3],
                    "category": row[4],
                }
                for row in rows
            ]
        finally:
            if owns_conn:
                conn.close()

    def get_file_by_id(self, file_id: int) -> Optional[Dict[str, Any]]:
        """단일 파일 레코드를 id로 조회한다. 존재하지 않으면 None 반환."""
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            row = conn.execute(
                """SELECT id, file_name, display_name, file_path, category, tags,
                          ai_comment, file_size, file_modified_at, file_mtime_ns
                   FROM files WHERE id = ?""",
                (file_id,),
            ).fetchone()
            if row is None:
                return None
            file_name = row[1] or ""
            return {
                "id":               row[0],
                "file_name":        file_name,
                "display_name":     row[2] or (file_name.rsplit(".", 1)[0] if file_name else ""),
                "file_path":        row[3] or "",
                "category":         row[4] or "#일반",
                "tags":             row[5] or "",
                "ai_comment":       row[6] or "",
                "file_size":        row[7] or 0,
                "file_modified_at": row[8] or "",
                "file_mtime_ns":    row[9],
            }
        finally:
            if owns_conn:
                conn.close()

    def update_tags(self, file_id: int, tags_str: str) -> bool:
        """태그 저장 목록 화면에서 인라인 수정한 태그를 DB에 반영"""
        tags_list = [t.strip() for t in tags_str.split(",") if t.strip()]
        category = f"#{tags_list[0]}" if tags_list else "#일반"

        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            conn.execute(
                "UPDATE files SET tags = ?, category = ? WHERE id = ?",
                (",".join(tags_list), category, file_id),
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"[태그 업데이트 실패]: {e}")
            return False
        finally:
            if owns_conn:
                conn.close()  
                  
    def rename_file(self, file_id: int, new_filename: str) -> bool:
        """
        [물리적 파일 이름 변경 및 DB 갱신]
        지정된 file_id의 실제 파일 이름을 바꾸고, DB의 file_name과 file_path를 업데이트.
        (new_filename에는 확장자도 포함되어야 함. 예: "휴가계획.txt")
        """
        # 윈도우에서 사용할 수 없는 특수문자 방어
        safe_name = re.sub(r'[\\/*?:"<>|]', "", new_filename).strip()

        conn = self._get_conn()
        try:
            # 1. DB에서 현재 파일 경로 찾기
            row = conn.execute(
                "SELECT file_path FROM files WHERE id = ?", (file_id,)).fetchone()
            if not row:
                return False

            old_path = row[0]
            if not os.path.exists(old_path):
                print(f"[에러] 원본 파일을 찾을 수 없음: {old_path}")
                return False

            # 2. 새로운 경로(이름) 생성
            base_dir = os.path.dirname(old_path)
            new_path = os.path.join(base_dir, safe_name)

            # 3. 물리적 파일 이름 변경
            os.rename(old_path, new_path)

            # 4. DB 정보 업데이트 (파일 이름, 파일 경로 둘 다 변경)
            conn.execute(
                "UPDATE files SET file_name = ?, file_path = ? WHERE id = ?",
                (safe_name, new_path, file_id)
            )
            conn.commit()
            return True

        except Exception as e:
            print(f"[파일 이름 변경 실패]: {e}")
            return False
        finally:
            if self._bulk_conn is None:
                conn.close()

    def register_reused_analysis(
        self, file_path: str, source_file_path: str, expected_hash: str
    ) -> Dict[str, Any]:
        """동일 내용 파일의 기존 분석 결과를 재사용 등록 (AI 호출 없음).

        증분 스캔에서 hash가 일치하는 기존 분석 레코드를 찾아
        중복 격리 정책 없이 별도 파일로 등록한다.
        """
        result: Dict[str, Any] = {
            "success": False,
            "file_path": file_path,
            "reused_from": source_file_path,
        }
        if not os.path.isfile(file_path):
            result["message"] = f"파일을 찾을 수 없습니다: {file_path}"
            return result

        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            actual_hash = self.compute_file_hash(file_path)
            if actual_hash != expected_hash:
                result["message"] = "증분 스캔 이후 파일이 변경되었습니다."
                return result

            source = conn.execute(
                "SELECT ai_comment, category, tags, display_name "
                "FROM files WHERE file_path = ? AND file_hash = ?",
                (source_file_path, expected_hash),
            ).fetchone()
            if source is None:
                result["message"] = "재사용 가능한 분석 레코드가 없습니다."
                return result

            ai_comment, category, tags, display_name = source
            if not ((ai_comment or "").strip() or (category or "").strip()):
                result["message"] = "원본 레코드에 저장된 분석 결과가 없습니다."
                return result

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            file_stat = os.stat(file_path)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO files (
                    file_name, file_path, ai_comment, category, tags,
                    display_name, file_hash, file_size, file_mtime_ns,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    ai_comment    = excluded.ai_comment,
                    category      = excluded.category,
                    tags          = excluded.tags,
                    display_name  = excluded.display_name,
                    file_hash     = excluded.file_hash,
                    file_size     = excluded.file_size,
                    file_mtime_ns = excluded.file_mtime_ns,
                    updated_at    = excluded.updated_at
                """,
                (
                    os.path.basename(file_path), file_path,
                    ai_comment or "", category or "", tags or "",
                    display_name or os.path.splitext(os.path.basename(file_path))[0],
                    actual_hash, file_stat.st_size, file_stat.st_mtime_ns,
                    now, now,
                ),
            )
            conn.execute(
                "DELETE FROM file_fingerprint_cache WHERE file_path = ?", (file_path,)
            )
            conn.commit()
            try:
                from .search_snapshot import invalidate_search_snapshot
                invalidate_search_snapshot(self.db_path)
            except Exception:
                pass
            result["success"] = True
            return result
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            result["message"] = f"분석 결과 재사용 등록 실패: {exc}"
            return result
        finally:
            if owns_conn:
                conn.close()
