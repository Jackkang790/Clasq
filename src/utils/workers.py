import os
import logging
from PySide6.QtCore import QThread, Signal
from ollama_manager import OllamaManager
from .core import ClasqCore
from .query_parser import SearchQueryParser
from .config import SUPPORTED_EXTENSIONS

log = logging.getLogger(__name__)


def estimate_analysis_eta(file_count: int, seconds_per_file: float | None = None) -> str:
    """Return a deliberately coarse Korean ETA rather than false precision."""
    if file_count <= 0:
        return "추가 분석 없음"
    if seconds_per_file and seconds_per_file > 0:
        low = file_count * seconds_per_file * 0.8
        high = file_count * seconds_per_file * 1.2
    else:
        return "예상시간 계산 중 (첫 분석 완료 후 갱신)"

    def readable(seconds: float) -> str:
        if seconds < 60:
            return f"{max(10, int(round(seconds / 10) * 10))}초"
        minutes = max(1, int(round(seconds / 60)))
        return f"{minutes}분"

    return f"약 {readable(low)}~{readable(high)}"


class IncrementalInventoryWorker(QThread):
    """Fast stat/fingerprint inventory without AI or text-index synchronization."""

    progress = Signal(str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, folder_paths=None, db_path="file_manager.db", file_paths=None, parent=None):
        super().__init__(parent)
        self.folder_paths = list(folder_paths or [])
        self.db_path = db_path
        self.file_paths = list(file_paths) if file_paths is not None else None

    def run(self):
        try:
            from .core import build_incremental_analysis_plan, scan_directory_files_flat
            if self.file_paths is None:
                files = []
                self.progress.emit("지원 파일 목록을 확인하고 있습니다...")
                for folder in self.folder_paths:
                    files.extend(scan_directory_files_flat(folder))
                files = sorted(set(files), key=str.casefold)
            else:
                files = list(dict.fromkeys(self.file_paths))
            self.progress.emit(f"{len(files):,}개 파일의 변경 여부를 확인하고 있습니다...")
            self.completed.emit(build_incremental_analysis_plan(files, self.db_path))
        except Exception as exc:
            log.exception("incremental inventory failed")
            self.error.emit(f"파일 변경 확인 중 오류가 발생했습니다: {exc}")


class FolderScanAndTagWorker(QThread):
    progress = Signal(str)
    fileProgress = Signal(int, int, str)  # 처리 순번, 전체 개수, 현재 파일명
    fileCompleted = Signal(int, int, str)  # 완료 순번, 전체 개수, 완료 파일명
    taggingFinished = Signal()
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, folder_paths: list, core: ClasqCore):
        super().__init__()
        self.folder_paths = folder_paths
        self.core = core

    def run(self):
        log.info("file scan and AI tagging started roots=%d", len(self.folder_paths))
        try:
            files_to_process = []

            for target_path in self.folder_paths:
                clean_target = os.path.abspath(os.path.normpath(target_path))
                if os.path.isfile(clean_target):
                    if clean_target.lower().endswith(SUPPORTED_EXTENSIONS):
                        files_to_process.append(clean_target)
                    continue
                if not os.path.isdir(clean_target):
                    continue
                for root, dirs, files in os.walk(clean_target):
                    dirs[:] = [name for name in dirs if name != self.core.registry.duplicates_dir_name]
                    for file in files:
                        if file.lower().endswith(SUPPORTED_EXTENSIONS):
                            full_path = os.path.join(root, file)
                            files_to_process.append(os.path.abspath(os.path.normpath(full_path)))

            files_to_process = list(dict.fromkeys(files_to_process))
            if not files_to_process:
                self.error.emit("스캔할 지원 파일이 지정된 경로에 없습니다.")
                return

            total_count = len(files_to_process)

            succeeded, failures, results = 0, [], []
            for idx, file_path in enumerate(files_to_process, start=1):
                file_name = os.path.basename(file_path)
                self.progress.emit(f"AI 분석 중 ({idx}/{total_count}): {file_name}")
                self.fileProgress.emit(idx, total_count, file_name)
                try:
                    result = self.core.process_file_upload(file_path)
                    if result.get("status") == "SUCCESS":
                        succeeded += 1
                        results.append({"file_path": file_path, "result": result})
                    else:
                        failures.append({"file_path": file_path, "reason": result.get("error") or result.get("message", "분석 실패")})
                except Exception as exc:
                    failures.append({"file_path": file_path, "reason": str(exc)})
                finally:
                    self.fileCompleted.emit(idx, total_count, file_name)

            # Keep Search/index state current, but only for this incremental
            # batch.  Unchanged files are intentionally not re-extracted.
            index_stats = {}
            try:
                from .local_text_index import LocalTextIndexer
                from .search_snapshot import refresh_search_snapshot
                successful_paths = [item["file_path"] for item in results]
                if successful_paths:
                    self.progress.emit("분석한 파일의 검색 인덱스를 갱신하고 있습니다...")
                    index_stats = LocalTextIndexer(self.core.db_path).synchronize(successful_paths)
                    refresh_search_snapshot(self.core.db_path)
            except Exception as exc:
                log.warning("incremental post-tag index refresh failed: %s", exc)

            summary = {"total": total_count, "success": succeeded, "failed": failures,
                       "results": results, "text_index": index_stats}
            self.taggingFinished.emit()  # 기존 UI 연결 호환성
            self.finished.emit(summary)
            log.info(
                "file scan and AI tagging completed total=%d success=%d failed=%d",
                summary.get("total", total_count), summary.get("success", 0),
                len(summary.get("failed", [])),
            )

        except Exception as e:
            log.exception("file scan and AI tagging unexpected failure")
            self.error.emit(f"스캔 및 태깅 작업 중 오류 발생: {str(e)}")


class OllamaInitWorker(QThread):
    """Ollama 설치·서버·모델 준비 과정을 GUI 스레드 밖에서 단계별로 수행합니다."""

    TOTAL_STEPS = 4

    progress = Signal(int, int, str)  # 완료 단계, 전체 단계, 현재 상태 문구
    completed = Signal(bool, str)

    def run(self):
        try:
            self.progress.emit(0, self.TOTAL_STEPS, "Ollama 설치 상태를 확인하고 있습니다...")
            if not OllamaManager.is_installed() and not OllamaManager.install():
                self.completed.emit(False, "Ollama를 설치하지 못했습니다.")
                return

            self.progress.emit(1, self.TOTAL_STEPS, "Ollama 서버를 시작하고 있습니다...")
            if not OllamaManager.start_server():
                self.completed.emit(False, "Ollama 서버를 시작하지 못했습니다.")
                return

            model_name = OllamaManager.MODEL_NAME
            self.progress.emit(2, self.TOTAL_STEPS, f"{model_name} 모델을 확인하고 있습니다...")
            if not OllamaManager.model_exists() and not OllamaManager.download_model():
                self.completed.emit(False, f"{model_name} 모델을 내려받지 못했습니다.")
                return

            self.progress.emit(3, self.TOTAL_STEPS, f"{model_name} 모델을 불러오고 있습니다...")
            if not OllamaManager.test_model():
                self.completed.emit(False, "AI 모델 응답 확인에 실패했습니다.")
                return

            self.progress.emit(self.TOTAL_STEPS, self.TOTAL_STEPS, "AI 모델 준비를 마쳤습니다.")
            self.completed.emit(True, "")
        except Exception as exc:
            self.completed.emit(False, f"Ollama 초기화 중 오류가 발생했습니다: {exc}")


class QueryParseWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, user_text: str, query_parser: SearchQueryParser):
        super().__init__()
        self.user_text = user_text
        self.query_parser = query_parser

    def run(self):
        try:
            result = self.query_parser.parse_user_query(self.user_text)
            self.finished.emit(result)   # ← self.taggingFinished.emit() 에서 수정 (result도 복구)
        except Exception as e:
            self.error.emit(f"자연어 파싱 처리 중 오류: {str(e)}")


class FolderAnalysisPlanWorker(QThread):
    """폴더 스캔 → 증분 분석 계획 수립 → 텍스트 색인 → 검색 snapshot 갱신.

    AI 서버 없이 동작한다. UI 스레드를 차단하지 않는다.
    completed signal payload keys:
      plan       - build_incremental_analysis_plan() 결과
      text_index - LocalTextIndexer.synchronize() 결과
      search_snapshot - {"rows": int, "build_time_ms": float, "approximate_bytes": int}
    """

    progress = Signal(str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        folder_paths: list,
        db_path: str = "file_manager.db",
        file_paths: list | None = None,
        excluded_directories=None,
        parent=None,
    ):
        super().__init__(parent)
        self.folder_paths = list(folder_paths)
        self.db_path = db_path
        self.file_paths = list(file_paths) if file_paths is not None else None
        self.excluded_directories = excluded_directories

    def run(self):
        log.info(
            "analysis plan started explicit_files=%s root_count=%d",
            self.file_paths is not None, len(self.folder_paths),
        )
        try:
            from .core import build_incremental_analysis_plan, scan_directory_files_flat
            from .local_text_index import LocalTextIndexer
            from .search_snapshot import refresh_search_snapshot
            from .db_manager import FileRegistryManager

            self.progress.emit("지원 파일을 검색하고 있습니다...")
            if self.file_paths is None:
                files: list[str] = []
                for folder_path in self.folder_paths:
                    files.extend(
                        scan_directory_files_flat(folder_path, self.excluded_directories)
                    )
                files = sorted(set(files), key=str.casefold)
            else:
                files = self.file_paths

            self.progress.emit(f"{len(files):,}개 파일의 변경 여부를 확인하고 있습니다...")
            plan = build_incremental_analysis_plan(files, self.db_path)

            # 동일 내용 파일은 AI 없이 기존 분석 결과 재사용 등록
            registry = FileRegistryManager(db_path=self.db_path)
            reused, failed_reuse = [], []
            for item in list(plan.get("same_content", [])):
                result = registry.register_reused_analysis(
                    item["file_path"], item["source_file_path"], item["file_hash"]
                )
                if result.get("success"):
                    reused.append(item)
                else:
                    failed_item = dict(item)
                    failed_item["reason"] = result.get("message", "reuse_failed")
                    failed_reuse.append(failed_item)
                    plan.setdefault("pending", []).append(failed_item)
            plan["same_content"] = reused

            self.progress.emit("문서 본문 텍스트 색인을 갱신하고 있습니다...")
            text_indexer = LocalTextIndexer(self.db_path)
            legacy_ppt = text_indexer.discover_legacy_ppt(self.folder_paths)
            text_index_stats = text_indexer.synchronize([*files, *legacy_ppt])
            plan["text_index"] = text_index_stats

            # 검색 snapshot을 미리 빌드해 다음 검색이 warm-path를 타도록 함
            self.progress.emit("검색 인덱스를 갱신하고 있습니다...")
            search_snap = refresh_search_snapshot(self.db_path)
            plan["search_snapshot"] = {
                "rows": len(search_snap.records),
                "build_time_ms": search_snap.build_time_ms,
                "approximate_bytes": search_snap.approximate_bytes,
            }
            log.info(
                "index and search synchronization completed candidates=%d indexed=%d unchanged=%d "
                "failed=%d deleted=%d search_rows=%d",
                text_index_stats.get("candidates", 0), text_index_stats.get("indexed", 0),
                text_index_stats.get("unchanged", 0), text_index_stats.get("failed", 0),
                text_index_stats.get("deleted", 0), len(search_snap.records),
            )
            self.completed.emit(plan)
            log.info(
                "analysis plan completed scanned=%d new=%d changed=%d unchanged=%d removed=%d failed=%d",
                len(plan.get("scanned", [])), len(plan.get("new", [])), len(plan.get("changed", [])),
                len(plan.get("already_analyzed", [])), 0, len(plan.get("errors", [])),
            )

        except Exception as exc:
            log.exception("analysis plan unexpected failure")
            self.error.emit(f"파일 분석 계획 생성 중 오류가 발생했습니다: {exc}")


class OrganizeApplyWorker(QThread):
    """승인된 정리 Plan을 실제 파일 이동으로 적용하는 Worker.

    파일 삭제·overwrite·임의 rename 없이 source → destination 이동만 수행한다.
    Preflight validation 후 이동하며, 부분 실패 시 역순 rollback을 시도한다.

    move_plan item 필수 키:
        file_path   - source 절대 경로
        target_path - destination 절대 경로
        file_id     - files 테이블 id (DB 갱신용, None 허용)

    completed signal result_dict 키:
        moved                   - 성공 [{old_path, new_path, file_id}]
        failed                  - 실패 [{file_path, reason}]
        rolled_back             - rollback 결과 [{new_path, old_path, success, reason?}]
        partial_rollback_failures - rollback 실패 사유 목록 (str)
    """

    progress = Signal(int, int, str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        move_plan: list,
        db_path: str,
        plan_fingerprints: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.move_plan = list(move_plan)
        self.db_path = db_path
        self.plan_fingerprints = plan_fingerprints or {}

    def run(self):
        import sqlite3 as _sqlite3
        import time as _time

        now_str = lambda: _time.strftime("%Y-%m-%d %H:%M:%S")

        log.info("organize apply started count=%d", len(self.move_plan))
        try:
            total = len(self.move_plan)

            # ── Preflight Validation ─────────────────────────────────────────
            self.progress.emit(0, total, "사전 검증 중...")
            preflight_errors: list[str] = []
            validated: list[dict] = []
            seen_sources: set[str] = set()
            seen_dests: set[str] = set()

            for item in self.move_plan:
                src = (item.get("file_path") or "").strip()
                dst = (item.get("target_path") or "").strip()

                if not src or not dst:
                    preflight_errors.append(f"비어있는 경로: {src!r} → {dst!r}")
                    continue

                norm_src = os.path.normcase(os.path.abspath(src))
                norm_dst = os.path.normcase(os.path.abspath(dst))

                if norm_src == norm_dst:
                    preflight_errors.append(f"source == destination: {src}")
                    continue
                if norm_src in seen_sources:
                    preflight_errors.append(f"중복 source: {src}")
                    continue
                seen_sources.add(norm_src)

                if norm_dst in seen_dests:
                    preflight_errors.append(f"중복 destination: {dst}")
                    continue
                seen_dests.add(norm_dst)

                if not os.path.isfile(src):
                    preflight_errors.append(f"source 파일 없음: {src}")
                    continue
                if not os.access(src, os.R_OK):
                    preflight_errors.append(f"source 접근 불가: {src}")
                    continue
                if os.path.exists(dst):
                    preflight_errors.append(f"destination 충돌 (파일 이미 존재): {dst}")
                    continue

                # Plan 생성 이후 파일 변경 감지 (stat 기반)
                fp = self.plan_fingerprints.get(norm_src)
                if fp and fp.get("size") is not None and fp.get("mtime_ns") is not None:
                    try:
                        st = os.stat(src)
                        if st.st_size != fp["size"] or st.st_mtime_ns != fp["mtime_ns"]:
                            preflight_errors.append(f"Plan 생성 후 파일이 변경됨: {src}")
                            continue
                    except OSError:
                        pass  # stat 실패 → 허용

                validated.append(item)

            if preflight_errors:
                log.warning("organize apply preflight failed count=%d", len(preflight_errors))
                self.error.emit(
                    f"사전 검증 실패 ({len(preflight_errors)}건):\n"
                    + "\n".join(preflight_errors[:10])
                )
                return

            if not validated:
                self.completed.emit({
                    "moved": [], "failed": [],
                    "rolled_back": [], "partial_rollback_failures": [],
                })
                return

            # ── Apply ────────────────────────────────────────────────────────
            moved: list[dict] = []
            failed: list[dict] = []

            for idx, item in enumerate(validated, 1):
                src = item["file_path"]
                dst = item["target_path"]
                file_id = item.get("file_id")
                target_dir = os.path.dirname(dst)

                self.progress.emit(idx, len(validated), os.path.basename(src))

                try:
                    os.makedirs(target_dir, exist_ok=True)

                    # destination 재확인 (preflight 이후 생겼을 수 있음)
                    if os.path.exists(dst):
                        failed.append({
                            "file_path": src,
                            "reason": f"destination이 이미 존재함: {dst}",
                        })
                        break  # abort → rollback

                    import shutil as _shutil
                    _shutil.move(src, dst)

                    if not os.path.isfile(dst):
                        raise OSError(f"이동 후 파일을 확인할 수 없음: {dst}")

                    # DB 업데이트
                    if file_id is not None:
                        try:
                            conn = _sqlite3.connect(self.db_path, timeout=10)
                            conn.execute("BEGIN IMMEDIATE")
                            conn.execute(
                                "UPDATE files SET file_name = ?, file_path = ?, "
                                "source_path = ?, updated_at = ? WHERE id = ?",
                                (os.path.basename(dst), dst, target_dir, now_str(), file_id),
                            )
                            conn.commit()
                            conn.close()
                        except Exception as db_exc:
                            # DB 갱신 실패 → 물리 이동 되돌리기
                            try:
                                _shutil.move(dst, src)
                            except Exception:
                                pass
                            failed.append({
                                "file_path": src,
                                "reason": f"DB 업데이트 실패: {db_exc}",
                            })
                            break

                    moved.append({"old_path": src, "new_path": dst, "file_id": file_id})

                except Exception as exc:
                    failed.append({"file_path": src, "reason": str(exc)})
                    break  # abort → rollback

            # ── Rollback (부분 실패 시) ──────────────────────────────────────
            rolled_back: list[dict] = []
            partial_rollback_failures: list[str] = []

            if failed and moved:
                import shutil as _shutil
                for move_record in reversed(moved.copy()):
                    old_path = move_record["old_path"]
                    new_path = move_record["new_path"]
                    rb_file_id = move_record.get("file_id")
                    try:
                        if os.path.isfile(new_path) and not os.path.exists(old_path):
                            _shutil.move(new_path, old_path)
                            if rb_file_id is not None:
                                try:
                                    conn = _sqlite3.connect(self.db_path, timeout=10)
                                    conn.execute("BEGIN IMMEDIATE")
                                    conn.execute(
                                        "UPDATE files SET file_name = ?, file_path = ?, updated_at = ? WHERE id = ?",
                                        (os.path.basename(old_path), old_path, now_str(), rb_file_id),
                                    )
                                    conn.commit()
                                    conn.close()
                                except Exception:
                                    pass
                            rolled_back.append({"new_path": new_path, "old_path": old_path, "success": True})
                            moved.remove(move_record)
                        else:
                            reason = (
                                "원위치에 이미 파일이 있음" if os.path.exists(old_path)
                                else "새 위치에서 파일을 찾을 수 없음"
                            )
                            rolled_back.append({"new_path": new_path, "old_path": old_path, "success": False, "reason": reason})
                            partial_rollback_failures.append(f"{os.path.basename(new_path)}: {reason}")
                    except Exception as rb_exc:
                        rolled_back.append({"new_path": new_path, "old_path": old_path, "success": False, "reason": str(rb_exc)})
                        partial_rollback_failures.append(f"{os.path.basename(new_path)}: {rb_exc}")

            # ── Post-apply: index path 동기화 + snapshot invalidate ──────────
            # 이동 성공 파일에 대해 file_text_index·file_fingerprint_cache의
            # file_path를 old_path → new_path로 즉시 갱신한다 (내용 보존).
            # rollback된 파일은 moved 목록에서 제거되므로 여기서 처리하지 않는다.
            index_sync_errors: list[str] = []

            if moved:
                try:
                    conn = _sqlite3.connect(self.db_path, timeout=10)
                    conn.execute("BEGIN")

                    for m in moved:
                        old_path = m["old_path"]
                        new_path = m["new_path"]

                        # 이동 완료 파일의 현재 stat (mtime_ns, size 갱신용)
                        try:
                            st = os.stat(new_path)
                            cur_size: int | None = st.st_size
                            cur_mtime_ns: int | None = st.st_mtime_ns
                        except OSError:
                            cur_size = cur_mtime_ns = None

                        # ── file_text_index: old_path → new_path (추출 텍스트 보존) ──
                        try:
                            old_row = conn.execute(
                                "SELECT file_hash, file_size, file_mtime_ns, "
                                "extracted_text, extractor_type, extract_status, updated_at "
                                "FROM file_text_index WHERE file_path = ?",
                                (old_path,),
                            ).fetchone()
                            conn.execute(
                                "DELETE FROM file_text_index WHERE file_path = ?", (old_path,)
                            )
                            conn.execute(
                                "DELETE FROM file_text_index WHERE file_path = ?", (new_path,)
                            )
                            if old_row:
                                conn.execute(
                                    "INSERT INTO file_text_index "
                                    "(file_path, file_hash, file_size, file_mtime_ns, "
                                    "extracted_text, extractor_type, extract_status, updated_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (
                                        new_path,
                                        old_row[0],
                                        cur_size if cur_size is not None else old_row[1],
                                        cur_mtime_ns if cur_mtime_ns is not None else old_row[2],
                                        old_row[3],
                                        old_row[4],
                                        old_row[5],
                                        old_row[6],
                                    ),
                                )
                        except Exception as e:
                            index_sync_errors.append(
                                f"text_index {os.path.basename(old_path)}: {e}"
                            )

                        # ── file_fingerprint_cache: old_path → new_path ──────────
                        try:
                            cache_row = conn.execute(
                                "SELECT file_hash, file_size, file_mtime_ns "
                                "FROM file_fingerprint_cache WHERE file_path = ?",
                                (old_path,),
                            ).fetchone()
                            conn.execute(
                                "DELETE FROM file_fingerprint_cache WHERE file_path = ?",
                                (old_path,),
                            )
                            conn.execute(
                                "DELETE FROM file_fingerprint_cache WHERE file_path = ?",
                                (new_path,),
                            )
                            if cache_row:
                                conn.execute(
                                    "INSERT INTO file_fingerprint_cache "
                                    "(file_path, file_hash, file_size, file_mtime_ns) "
                                    "VALUES (?, ?, ?, ?)",
                                    (
                                        new_path,
                                        cache_row[0],
                                        cur_size if cur_size is not None else cache_row[1],
                                        cur_mtime_ns if cur_mtime_ns is not None else cache_row[2],
                                    ),
                                )
                        except Exception as e:
                            index_sync_errors.append(
                                f"fingerprint_cache {os.path.basename(old_path)}: {e}"
                            )

                    conn.execute("COMMIT")
                    conn.close()
                except Exception as outer_exc:
                    try:
                        conn.rollback()
                        conn.close()
                    except Exception:
                        pass
                    index_sync_errors.append(f"index sync transaction 실패: {outer_exc}")

                try:
                    from .search_snapshot import invalidate_search_snapshot
                    invalidate_search_snapshot(self.db_path)
                except Exception:
                    pass

            # ── History 기록 (Apply 성공 파일만) ─────────────────────────────
            import uuid as _uuid
            operation_id: str | None = str(_uuid.uuid4()) if moved else None
            history_errors: list[str] = []

            if moved and operation_id:
                try:
                    from .db_manager import FileRegistryManager
                    FileRegistryManager(db_path=self.db_path)  # migration v3 보장
                except Exception:
                    pass

                try:
                    conn = _sqlite3.connect(self.db_path, timeout=10)
                    conn.execute("BEGIN")
                    now_ts = now_str()
                    for m in moved:
                        old_p = m["old_path"]
                        new_p = m["new_path"]
                        file_id = m.get("file_id")

                        # hash: fingerprint_cache(new_path) 우선, fallback files 테이블
                        h_row = conn.execute(
                            "SELECT file_hash, file_size FROM file_fingerprint_cache WHERE file_path = ?",
                            (new_p,),
                        ).fetchone()
                        if not h_row and file_id:
                            h_row = conn.execute(
                                "SELECT file_hash, file_size FROM files WHERE id = ?",
                                (file_id,),
                            ).fetchone()

                        # Undo 무결성 검증은 stale cache가 아니라 Apply 완료 파일의
                        # 실제 bytes를 기준으로 해야 한다.
                        from .db_manager import FileRegistryManager
                        applied_hash = FileRegistryManager.compute_file_hash(new_p)
                        applied_size = os.path.getsize(new_p)

                        conn.execute(
                            "INSERT INTO organize_history "
                            "(operation_id, original_path, moved_path, file_hash, file_size, "
                            "status, applied_at) VALUES (?, ?, ?, ?, ?, 'applied', ?)",
                            (
                                operation_id, old_p, new_p,
                                applied_hash,
                                applied_size,
                                now_ts,
                            ),
                        )
                    conn.execute("COMMIT")
                    conn.close()
                except Exception as hist_exc:
                    try:
                        conn.rollback()
                        conn.close()
                    except Exception:
                        pass
                    history_errors.append(f"history 기록 실패: {hist_exc}")
                    operation_id = None  # Undo 불가

                    # History가 없으면 재시작 후 안전한 Undo를 제공할 수 없다.
                    # Apply 전체를 실패로 취급하고, overwrite 없이 원위치로 복구한다.
                    for move_record in reversed(moved.copy()):
                        old_path = move_record["old_path"]
                        new_path = move_record["new_path"]
                        file_id = move_record.get("file_id")
                        try:
                            if not os.path.isfile(new_path) or os.path.exists(old_path):
                                raise OSError("history 실패 rollback 경로 충돌 또는 파일 없음")
                            _shutil.move(new_path, old_path)

                            rollback_conn = _sqlite3.connect(self.db_path, timeout=10)
                            try:
                                rollback_conn.execute("BEGIN IMMEDIATE")
                                if file_id is not None:
                                    rollback_conn.execute(
                                        "UPDATE files SET file_name = ?, file_path = ?, "
                                        "source_path = ?, updated_at = ? WHERE id = ?",
                                        (
                                            os.path.basename(old_path), old_path,
                                            os.path.dirname(old_path), now_str(), file_id,
                                        ),
                                    )
                                for table in ("file_text_index", "file_fingerprint_cache"):
                                    rollback_conn.execute(
                                        f"UPDATE {table} SET file_path = ? WHERE file_path = ?",
                                        (old_path, new_path),
                                    )
                                rollback_conn.commit()
                            finally:
                                rollback_conn.close()
                            rolled_back.append({
                                "new_path": new_path, "old_path": old_path, "success": True,
                            })
                            moved.remove(move_record)
                        except Exception as rollback_exc:
                            partial_rollback_failures.append(
                                f"{os.path.basename(new_path)}: {rollback_exc}"
                            )

            self.completed.emit({
                "moved": moved,
                "failed": failed,
                "rolled_back": rolled_back,
                "partial_rollback_failures": partial_rollback_failures,
                "index_sync_errors": index_sync_errors,
                "operation_id": operation_id,
                "history_errors": history_errors,
            })
            log.info(
                "organize apply completed success=%d failed=%d rollback=%d rollback_failed=%d",
                len(moved), len(failed), len(rolled_back), len(partial_rollback_failures),
            )

        except Exception as exc:
            log.exception("organize apply unexpected failure")
            self.error.emit(f"파일 정리 적용 중 오류가 발생했습니다: {exc}")


class OrganizeUndoWorker(QThread):
    """organize_history 기반으로 파일 정리를 안전하게 되돌리는 Worker.

    Preflight → Undo 이동 → 부분 실패 시 rollback → DB/index 동기화(Batch 11 패턴 재사용)
    → History 상태 갱신 → snapshot invalidate.

    파일 삭제·overwrite·임의 rename 없음.

    history_records: list[dict] (id, operation_id, original_path, moved_path, file_hash, status)

    completed payload 키:
        undone                  - 성공 [{moved_path, original_path, id}]
        failed                  - 실패 [{moved_path, original_path, id, reason}]
        rolled_back             - rollback 결과 [{…, success}]
        partial_rollback_failures - rollback 실패 사유
        index_sync_errors       - index 동기화 오류
    """

    progress = Signal(int, int, str)
    completed = Signal(object)
    error = Signal(str)

    def __init__(self, history_records: list, db_path: str, parent=None):
        super().__init__(parent)
        self.history_records = list(history_records)
        self.db_path = db_path

    def run(self):
        import sqlite3 as _sqlite3
        import time as _time
        import shutil as _shutil

        now_str = lambda: _time.strftime("%Y-%m-%d %H:%M:%S")

        log.info("organize undo started count=%d", len(self.history_records))
        try:
            total = len(self.history_records)

            # ── Preflight ────────────────────────────────────────────────────
            self.progress.emit(0, total, "되돌리기 검증 중...")
            preflight_errors: list[str] = []
            validated: list[dict] = []
            seen_moved: set[str] = set()
            seen_original: set[str] = set()

            for rec in self.history_records:
                rec_id = rec["id"]
                original = (rec.get("original_path") or "").strip()
                moved = (rec.get("moved_path") or "").strip()
                stored_hash = rec.get("file_hash")
                status = rec.get("status", "applied")

                if status != "applied":
                    preflight_errors.append(f"이미 되돌려진 항목 (id={rec_id})")
                    continue

                if not moved or not original:
                    preflight_errors.append(f"경로 비어있음 (id={rec_id})")
                    continue

                norm_moved = os.path.normcase(os.path.abspath(moved))
                norm_original = os.path.normcase(os.path.abspath(original))

                if norm_moved in seen_moved:
                    preflight_errors.append(f"중복 이동 대상: {moved}")
                    continue
                seen_moved.add(norm_moved)

                if norm_original in seen_original:
                    preflight_errors.append(f"중복 원위치: {original}")
                    continue
                seen_original.add(norm_original)

                if not os.path.isfile(moved):
                    preflight_errors.append(f"이동된 파일 없음: {moved}")
                    continue

                if not os.access(moved, os.R_OK):
                    preflight_errors.append(f"파일 접근 불가: {moved}")
                    continue

                if os.path.exists(original):
                    preflight_errors.append(
                        f"원위치에 이미 파일 존재 (덮어쓰기 불가): {original}"
                    )
                    continue

                # Apply 이후 파일 수정 감지 (hash 기반)
                if stored_hash:
                    try:
                        from .db_manager import FileRegistryManager
                        current_hash = FileRegistryManager.compute_file_hash(moved)
                        if current_hash != stored_hash:
                            preflight_errors.append(
                                f"Apply 이후 파일 수정됨 (hash 불일치): {os.path.basename(moved)}"
                            )
                            continue
                    except Exception as hash_exc:
                        preflight_errors.append(
                            f"파일 hash 확인 실패: {os.path.basename(moved)} ({hash_exc})"
                        )
                        continue

                validated.append(rec)

            if preflight_errors:
                log.warning("organize undo preflight failed count=%d", len(preflight_errors))
                self.error.emit(
                    f"되돌리기 검증 실패 ({len(preflight_errors)}건):\n"
                    + "\n".join(preflight_errors[:10])
                )
                return

            if not validated:
                self.completed.emit({
                    "undone": [], "failed": [],
                    "rolled_back": [], "partial_rollback_failures": [],
                    "index_sync_errors": [],
                })
                return

            # ── Undo (moved → original) ──────────────────────────────────────
            undone: list[dict] = []
            failed: list[dict] = []

            for idx, rec in enumerate(validated, 1):
                original = rec["original_path"]
                moved = rec["moved_path"]
                rec_id = rec["id"]

                self.progress.emit(idx, len(validated), os.path.basename(moved))

                try:
                    os.makedirs(os.path.dirname(original), exist_ok=True)

                    if os.path.exists(original):
                        failed.append({
                            "moved_path": moved, "original_path": original, "id": rec_id,
                            "reason": f"원위치에 이미 파일 존재: {original}",
                        })
                        break

                    _shutil.move(moved, original)

                    if not os.path.isfile(original):
                        raise OSError(f"되돌리기 후 파일 확인 불가: {original}")

                    undone.append({"moved_path": moved, "original_path": original, "id": rec_id})

                except Exception as exc:
                    failed.append({
                        "moved_path": moved, "original_path": original, "id": rec_id,
                        "reason": str(exc),
                    })
                    break

            # ── Rollback (부분 실패 시) ──────────────────────────────────────
            rolled_back: list[dict] = []
            partial_rollback_failures: list[str] = []

            if failed and undone:
                for u in reversed(undone.copy()):
                    original_p = u["original_path"]
                    moved_p = u["moved_path"]
                    try:
                        if os.path.isfile(original_p) and not os.path.exists(moved_p):
                            _shutil.move(original_p, moved_p)
                            rolled_back.append({"original_path": original_p, "moved_path": moved_p, "success": True})
                            undone.remove(u)
                        else:
                            reason = (
                                "원위치 파일 없음" if not os.path.exists(original_p)
                                else "이동 대상에 이미 파일 있음"
                            )
                            rolled_back.append({"original_path": original_p, "moved_path": moved_p, "success": False, "reason": reason})
                            partial_rollback_failures.append(f"{os.path.basename(original_p)}: {reason}")
                    except Exception as rb_exc:
                        rolled_back.append({"original_path": original_p, "moved_path": moved_p, "success": False, "reason": str(rb_exc)})
                        partial_rollback_failures.append(f"{os.path.basename(original_p)}: {rb_exc}")

            # ── Post-undo: DB/index → original path 동기화 ───────────────────
            index_sync_errors: list[str] = []

            if undone:
                try:
                    conn = _sqlite3.connect(self.db_path, timeout=10)
                    conn.execute("BEGIN")

                    for u in undone:
                        moved_p = u["moved_path"]
                        original_p = u["original_path"]
                        rec_id = u["id"]

                        try:
                            st = os.stat(original_p)
                            cur_size: int | None = st.st_size
                            cur_mtime_ns: int | None = st.st_mtime_ns
                        except OSError:
                            cur_size = cur_mtime_ns = None

                        # files: moved_path → original_path
                        try:
                            conn.execute(
                                "UPDATE files SET file_name = ?, file_path = ?, updated_at = ? "
                                "WHERE file_path = ?",
                                (os.path.basename(original_p), original_p, now_str(), moved_p),
                            )
                        except Exception as e:
                            index_sync_errors.append(f"files {os.path.basename(moved_p)}: {e}")
                            raise

                        # file_text_index: moved_path → original_path (내용 보존)
                        try:
                            text_row = conn.execute(
                                "SELECT file_hash, file_size, file_mtime_ns, extracted_text, "
                                "extractor_type, extract_status, updated_at "
                                "FROM file_text_index WHERE file_path = ?",
                                (moved_p,),
                            ).fetchone()
                            conn.execute("DELETE FROM file_text_index WHERE file_path = ?", (moved_p,))
                            conn.execute("DELETE FROM file_text_index WHERE file_path = ?", (original_p,))
                            if text_row:
                                conn.execute(
                                    "INSERT INTO file_text_index "
                                    "(file_path, file_hash, file_size, file_mtime_ns, "
                                    "extracted_text, extractor_type, extract_status, updated_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (
                                        original_p, text_row[0],
                                        cur_size if cur_size is not None else text_row[1],
                                        cur_mtime_ns if cur_mtime_ns is not None else text_row[2],
                                        text_row[3], text_row[4], text_row[5], text_row[6],
                                    ),
                                )
                        except Exception as e:
                            index_sync_errors.append(f"text_index {os.path.basename(moved_p)}: {e}")
                            raise

                        # file_fingerprint_cache: moved_path → original_path
                        try:
                            cache_row = conn.execute(
                                "SELECT file_hash, file_size, file_mtime_ns "
                                "FROM file_fingerprint_cache WHERE file_path = ?",
                                (moved_p,),
                            ).fetchone()
                            conn.execute("DELETE FROM file_fingerprint_cache WHERE file_path = ?", (moved_p,))
                            conn.execute("DELETE FROM file_fingerprint_cache WHERE file_path = ?", (original_p,))
                            if cache_row:
                                conn.execute(
                                    "INSERT INTO file_fingerprint_cache "
                                    "(file_path, file_hash, file_size, file_mtime_ns) VALUES (?, ?, ?, ?)",
                                    (
                                        original_p, cache_row[0],
                                        cur_size if cur_size is not None else cache_row[1],
                                        cur_mtime_ns if cur_mtime_ns is not None else cache_row[2],
                                    ),
                                )
                        except Exception as e:
                            index_sync_errors.append(f"fingerprint_cache {os.path.basename(moved_p)}: {e}")
                            raise

                        # History status → 'undone'
                        try:
                            conn.execute(
                                "UPDATE organize_history SET status = 'undone', undone_at = ? WHERE id = ?",
                                (now_str(), rec_id),
                            )
                        except Exception as e:
                            index_sync_errors.append(f"history status id={rec_id}: {e}")
                            raise

                    conn.execute("COMMIT")
                    conn.close()
                except Exception as outer_exc:
                    try:
                        conn.rollback()
                        conn.close()
                    except Exception:
                        pass
                    index_sync_errors.append(f"undo index sync transaction 실패: {outer_exc}")
                    # DB transaction이 실패하면 filesystem도 moved path로 복원한다.
                    for u in reversed(undone.copy()):
                        original_p = u["original_path"]
                        moved_p = u["moved_path"]
                        try:
                            if not os.path.isfile(original_p) or os.path.exists(moved_p):
                                raise OSError("DB 동기화 실패 rollback 경로 충돌 또는 파일 없음")
                            _shutil.move(original_p, moved_p)
                            rolled_back.append({
                                "original_path": original_p, "moved_path": moved_p,
                                "success": True,
                            })
                            undone.remove(u)
                        except Exception as rollback_exc:
                            reason = str(rollback_exc)
                            rolled_back.append({
                                "original_path": original_p, "moved_path": moved_p,
                                "success": False, "reason": reason,
                            })
                            partial_rollback_failures.append(
                                f"{os.path.basename(original_p)}: {reason}"
                            )

                try:
                    from .search_snapshot import invalidate_search_snapshot
                    invalidate_search_snapshot(self.db_path)
                except Exception:
                    pass

            self.completed.emit({
                "undone": undone,
                "failed": failed,
                "rolled_back": rolled_back,
                "partial_rollback_failures": partial_rollback_failures,
                "index_sync_errors": index_sync_errors,
            })
            log.info(
                "organize undo completed success=%d failed=%d rollback=%d rollback_failed=%d",
                len(undone), len(failed), len(rolled_back), len(partial_rollback_failures),
            )

        except Exception as exc:
            log.exception("organize undo unexpected failure")
            self.error.emit(f"되돌리기 중 오류가 발생했습니다: {exc}")
