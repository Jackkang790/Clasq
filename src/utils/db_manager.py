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
import os
import sqlite3
import hashlib
import shutil
import time
from contextlib import contextmanager
from typing import Dict, Any, Optional, List, Tuple
import re


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
    #    (기존에 이미 만들어진 files.db가 있어도 안전하게 컬럼만 추가)
    # ---------------------------------------------------------
    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name TEXT,
                    file_path TEXT UNIQUE,
                    ai_comment TEXT,
                    category TEXT
                )
                """
            )
            self._migrate_add_columns(
                conn,
                {
                    "display_name": "TEXT",
                    "file_hash": "TEXT",
                    "file_size": "INTEGER",
                    "created_at": "TEXT",
                    "updated_at": "TEXT",
                    "file_created_at": "TEXT",
                    "file_modified_at": "TEXT",
                    "tags": "TEXT",
                    "source_path": "TEXT",
                },
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_hash ON files(file_hash)")
            
            # 경로 관리 테이블 생성
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS managed_paths (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _migrate_add_columns(self, conn: sqlite3.Connection, columns: Dict[str, str]) -> None:
        """기존 DB에 신규 컬럼이 없으면 ALTER TABLE로 안전하게 추가 (기존 데이터는 그대로 보존)"""
        existing_cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(files)").fetchall()}
        for col_name, col_type in columns.items():
            if col_name not in existing_cols:
                conn.execute(
                    f"ALTER TABLE files ADD COLUMN {col_name} {col_type}")

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

    def get_managed_path_presets(self) -> List[Tuple[int, str]]:
        """프리셋 목록용 (id, path) 조회 - 기존 managed_paths 테이블을 그대로 사용합니다."""
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            rows = conn.execute("SELECT id, path FROM managed_paths ORDER BY id").fetchall()
            return [(row[0], row[1]) for row in rows]
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
            file_size = os.path.getsize(final_path)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            file_created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getctime(final_path)))
            file_modified_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(final_path)))

            # 🌟 [개선] INSERT OR REPLACE -> UPSERT(ON CONFLICT)로 변경
            #    기존 INSERT OR REPLACE는 내부적으로 DELETE 후 INSERT를 수행해
            #    기존 레코드의 id가 매번 바뀌는 부작용이 있었다. ON CONFLICT
            #    DO UPDATE는 같은 id를 유지한 채 값만 갱신하므로 향후 다른
            #    테이블에서 file_id를 참조(FK)하게 되어도 안전하다.
            conn.execute(
                """
                INSERT INTO files (file_name, display_name, file_path, ai_comment, category,
                                    file_hash, file_size, created_at, updated_at, file_created_at, file_modified_at, tags, source_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    source_path = excluded.source_path
                """,
                (file_name, display_name, final_path, ai_comment,
                 category, file_hash, file_size, now, now, file_created_at, file_modified_at, tags_str, source_path),
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
        conn = self._get_conn()
        owns_conn = self._bulk_conn is None
        try:
            conn.execute("DELETE FROM files;")
            conn.execute("DELETE FROM sqlite_sequence WHERE name='files';")
            conn.commit()
        finally:
            if owns_conn:
                conn.close()

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

    def delete_file(self, file_id: int) -> bool:
        """
        [물리적 파일 삭제 및 DB 레코드 제거]
        지정된 file_id의 실제 파일을 디스크 및 DB에서 삭제.
        """
        conn = self._get_conn()
        try:
            # 1. DB에서 파일 경로 찾기
            row = conn.execute(
                "SELECT file_path FROM files WHERE id = ?", (file_id,)).fetchone()
            if not row:
                return False

            target_path = row[0]

            # 2. 물리적 디스크에서 파일 삭제 (파일이 존재할 경우만)
            if os.path.exists(target_path):
                os.remove(target_path)

            # 3. DB 레코드 삭제
            conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
            conn.commit()
            return True

        except Exception as e:
            print(f"[파일 삭제 실패]: {e}")
            return False
        finally:
            if self._bulk_conn is None:
                conn.close()
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
