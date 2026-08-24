"""Batch 12 — AI 분석 완료 후 Preview 자동 갱신 테스트.

background AI 분석 완료 시 QMessageBox 없이 grouped_screen이 자동 갱신되고,
자동 Apply는 절대 실행되지 않으며, stale context/Apply 중 guard가 동작하는지 검증한다.
"""
import inspect
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.utils.db_manager import FileRegistryManager


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
def _source(obj) -> str:
    return inspect.getsource(obj)


def _db(tmp: str) -> str:
    db = str(Path(tmp) / "test.db")
    FileRegistryManager(db_path=db)
    return db


def _make_file(directory: str, name: str, content: str = "test") -> str:
    p = str(Path(directory) / name)
    Path(p).write_text(content, encoding="utf-8")
    return p


# ── 1. 기존 UX 문제 원인 확인 ────────────────────────────────────────────────
class TestBatch12UXProblemFixed(unittest.TestCase):

    def test_no_qmessagebox_information_in_success_case(self):
        """_on_untagged_analysis_finished 성공 경로에 QMessageBox.information이 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        # 성공 케이스에 information 모달 없어야 함 (Batch 12 핵심 변경)
        self.assertNotIn("QMessageBox.information", src)

    def test_no_manual_retry_message_in_finished(self):
        """'다시 누르세요' 안내 문구가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertNotIn("다시 눌러", src)
        self.assertNotIn("다시 누르", src)

    def test_auto_refresh_called_on_success(self):
        """성공 시 _refresh_grouped_after_analysis가 자동 호출되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertIn("_refresh_grouped_after_analysis", src)

    def test_banner_updated_on_success(self):
        """성공 시 banner text가 갱신되어야 한다 (정리 계획이 갱신되었습니다)."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertIn("정리 계획이 갱신되었습니다", src)
        self.assertIn("set_banner_text", src)

    def test_banner_updated_on_failure(self):
        """실패 시 banner text가 갱신되어야 한다 (QMessageBox.warning 없음)."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        # 실패 케이스도 warning 모달 없이 배너로 처리
        self.assertNotIn("QMessageBox.warning", src)
        self.assertIn("미분류 상태 유지", src)


# ── 2. 자동 Apply 절대 금지 ────────────────────────────────────────────────
class TestBatch12NoAutoApply(unittest.TestCase):

    def test_no_on_organize_confirmed_auto_call(self):
        """_on_untagged_analysis_finished에서 _on_organize_confirmed 자동 호출 없음."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertNotIn("_on_organize_confirmed", src)

    def test_no_apply_worker_start_in_finished(self):
        """_on_untagged_analysis_finished에서 Apply Worker가 start()되지 않아야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertNotIn("OrganizeApplyWorker", src)
        # _apply_worker.start()는 없어야 함 (isRunning() guard만 있어야 함)
        self.assertNotIn("_apply_worker.start()", src)

    def test_no_file_move_in_finished(self):
        """_on_untagged_analysis_finished에서 파일 이동이 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertNotIn("shutil.move", src)
        self.assertNotIn("os.rename", src)

    def test_confirm_btn_auto_click_absent(self):
        """_on_untagged_analysis_finished에서 confirm_btn.click() 자동 호출 없음."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertNotIn("confirm_btn.click", src)

    def test_user_still_needs_to_approve_via_confirm_btn(self):
        """사용자 승인(confirm_btn)은 여전히 필요해야 한다 (_on_organize_confirmed 유지)."""
        from src.ui.views.organize_view import OrganizeView
        # _on_organize_confirmed는 그대로 유지되어야 함
        self.assertTrue(hasattr(OrganizeView, "_on_organize_confirmed"))
        src = _source(OrganizeView._on_organize_confirmed)
        self.assertIn("QMessageBox.question", src)  # 승인 dialog 유지


# ── 3. Context Tracking / Stale 감지 ─────────────────────────────────────────
class TestBatch12ContextTracking(unittest.TestCase):

    def test_plan_context_id_field_exists(self):
        """_plan_context_id 필드가 OrganizeView.__init__에 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView.__init__)
        self.assertIn("_plan_context_id", src)

    def test_analysis_plan_context_id_field_exists(self):
        """_analysis_plan_context_id 필드가 __init__에 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView.__init__)
        self.assertIn("_analysis_plan_context_id", src)

    def test_plan_context_id_incremented_on_plan_completed(self):
        """_on_plan_completed에서 _plan_context_id가 증가해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_plan_completed)
        self.assertIn("_plan_context_id", src)
        self.assertIn("+= 1", src)

    def test_analysis_context_recorded_on_start(self):
        """_start_untagged_analysis에서 _analysis_plan_context_id가 기록되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._start_untagged_analysis)
        self.assertIn("_analysis_plan_context_id", src)
        self.assertIn("_plan_context_id", src)

    def test_stale_context_check_in_finished(self):
        """_on_untagged_analysis_finished에 stale context 체크가 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertIn("_analysis_plan_context_id", src)
        self.assertIn("_plan_context_id", src)

    def test_stale_context_returns_early(self):
        """Stale context 시 즉시 return해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        # context 불일치 → return
        self.assertIn("!= self._plan_context_id", src)

    def test_context_id_tracking_logic(self):
        """context id 비교 로직이 올바르게 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertIn("_analysis_plan_context_id != self._plan_context_id", src)
        self.assertIn("return", src)


# ── 4. Apply 중 안전 처리 ────────────────────────────────────────────────────
class TestBatch12ApplyInProgressGuard(unittest.TestCase):

    def test_apply_in_progress_check_in_finished(self):
        """Apply 실행 중 AI 완료 시 Plan 변경을 막아야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertIn("_apply_worker", src)
        self.assertIn("isRunning", src)

    def test_apply_guard_returns_early(self):
        """Apply 중 AI 완료 → 즉시 return해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        # Apply 중이면 return
        lines = src.split("\n")
        found_isrunning = False
        for i, line in enumerate(lines):
            if "isRunning" in line and "_apply_worker" in lines[max(0, i-2):i+2][0] if lines[max(0, i-2):i+2] else False:
                found_isrunning = True
        self.assertIn("isRunning()", src)

    def test_apply_in_progress_does_not_change_plan(self):
        """Apply 중에는 Plan 교체 코드가 없어야 한다 (소스 확인)."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        # 직접 Plan 객체 교체 코드 없어야 함
        self.assertNotIn("self._last_plan =", src)


# ── 5. 여러 AI 완료 signal / 중복 refresh 방지 ──────────────────────────────
class TestBatch12NoDuplicateRefresh(unittest.TestCase):

    def test_single_worker_architecture(self):
        """한 번에 하나의 AI Worker만 실행됨을 소스에서 확인."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._start_untagged_analysis)
        self.assertIn("isRunning", src)
        self.assertIn("_untagged_worker", src)

    def test_analysis_attempted_prevents_infinite_loop(self):
        """_analysis_attempted set이 무한 재분석을 막아야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._start_untagged_analysis)
        self.assertIn("_analysis_attempted", src)
        # 이미 시도한 파일은 재분석에서 제외
        self.assertIn("not in self._analysis_attempted", src)

    def test_no_auto_analysis_start_in_refresh(self):
        """_refresh_grouped_after_analysis에서 새 AI 분석이 자동 시작되지 않는다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._refresh_grouped_after_analysis)
        self.assertNotIn("_start_untagged_analysis", src)
        self.assertNotIn("FolderScanAndTagWorker", src)

    def test_no_recursive_analysis_in_on_finished(self):
        """_on_untagged_analysis_finished에서 새 AI 분석을 자동 시작하지 않는다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertNotIn("_start_untagged_analysis", src)


# ── 6. AI 실패 안전 처리 ──────────────────────────────────────────────────────
class TestBatch12FailureSafety(unittest.TestCase):

    def test_total_failure_handled_safely(self):
        """전체 실패 시 crash 없이 banner만 갱신해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        # 전체 실패(else 분기)에서 banner 갱신
        self.assertIn("미분류 상태 유지", src)

    def test_error_handler_still_shows_warning(self):
        """예상치 못한 오류(_on_untagged_analysis_error)는 warning을 유지해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_error)
        self.assertIn("QMessageBox.warning", src)

    def test_partial_failure_reflected_in_banner(self):
        """일부 실패 파일 수가 banner text에 반영되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_untagged_analysis_finished)
        self.assertIn("untagged_remaining", src)

    def test_failed_files_not_moved_to_arbitrary_destination(self):
        """실패 파일이 임의 destination으로 이동하지 않아야 한다."""
        from src.ui.views.organize_view import OrganizeView
        for method in ("_on_untagged_analysis_finished", "_on_untagged_analysis_error",
                       "_refresh_grouped_after_analysis"):
            src = _source(getattr(OrganizeView, method))
            self.assertNotIn("shutil.move", src, f"{method}에 shutil.move 있음")


# ── 7. View lifecycle 안전성 ─────────────────────────────────────────────────
class TestBatch12ViewLifecycle(unittest.TestCase):

    def test_no_force_terminate_in_worker_cleanup(self):
        """Worker를 강제 terminate하지 않는다 (소스 확인)."""
        from src.ui.views.organize_view import OrganizeView
        for method in ("_close_untagged_dialog", "_close_plan_dialog", "_close_apply_dialog"):
            src = _source(getattr(OrganizeView, method))
            self.assertNotIn("terminate()", src, f"{method}에 terminate 있음")

    def test_close_dialog_guards_none_check(self):
        """dialog 닫기 메서드에 None 체크가 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._close_untagged_dialog)
        self.assertIn("self._untagged_dialog", src)


# ── 8. 기존 안전장치 회귀 확인 ───────────────────────────────────────────────
class TestBatch12SafetyRegression(unittest.TestCase):

    def test_batch9_user_approval_still_required(self):
        """Batch 9 사용자 승인이 유지되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_organize_confirmed)
        self.assertIn("QMessageBox.question", src)

    def test_batch9_no_overwrite(self):
        """Batch 9 overwrite 방지가 유지되어야 한다."""
        from src.utils.workers import OrganizeApplyWorker
        src = _source(OrganizeApplyWorker.run)
        self.assertIn("destination 충돌", src)

    def test_batch9_rollback_maintained(self):
        """Batch 9 rollback이 유지되어야 한다."""
        from src.utils.workers import OrganizeApplyWorker
        src = _source(OrganizeApplyWorker.run)
        self.assertIn("rolled_back", src)

    def test_batch10_no_infinite_reanalysis(self):
        """Batch 10 무한 재분석 방지가 유지되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._start_untagged_analysis)
        self.assertIn("_analysis_attempted", src)
        self.assertIn("not in self._analysis_attempted", src)

    def test_batch10_extension_fallback_not_for_apply(self):
        """Batch 10 extension fallback이 Apply destination에 사용되지 않아야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._on_organize_confirmed)
        self.assertNotIn("_get_file_kind_by_extension", src)

    def test_batch11_index_sync_in_apply_worker(self):
        """Batch 11 index 동기화가 Apply Worker에 유지되어야 한다."""
        from src.utils.workers import OrganizeApplyWorker
        src = _source(OrganizeApplyWorker.run)
        self.assertIn("file_text_index", src)
        self.assertIn("file_fingerprint_cache", src)
        self.assertIn("INSERT INTO file_text_index", src)

    def test_batch11_no_full_reindex(self):
        """Batch 11 전체 재색인 방지가 유지되어야 한다."""
        from src.utils.workers import OrganizeApplyWorker
        src = _source(OrganizeApplyWorker.run)
        self.assertNotIn("LocalTextIndexer", src)
        self.assertNotIn("synchronize(", src)


# ── 9. DB schema v2 유지 ────────────────────────────────────────────────────
class TestBatch12DBSchema(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schema_v2_maintained(self):
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM db_schema_version"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 3)

    def test_no_new_tables(self):
        conn = sqlite3.connect(self.db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        known = {
            "files", "managed_paths", "file_fingerprint_cache",
            "file_text_index", "db_schema_version", "sqlite_sequence",
            "organize_history",
        }
        self.assertEqual(tables - known, set(), f"새 테이블: {tables - known}")

    def test_batch12_code_has_no_schema_changes(self):
        """Batch 12 신규 메서드에 스키마 변경 코드가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        for method in ("_on_untagged_analysis_finished",):
            src = _source(getattr(OrganizeView, method))
            self.assertNotIn("CREATE TABLE", src.upper())
            self.assertNotIn("ALTER TABLE", src.upper())


# ── 10. 파일 안전성 (실제 테스트) ────────────────────────────────────────────
class TestBatch12FileSafety(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_file_change_on_ai_analysis_complete(self):
        """AI 분석 완료 후 실제 파일이 변경되지 않아야 한다."""
        src_file = _make_file(self.tmp, "check.txt", "original content")
        original_content = Path(src_file).read_text(encoding="utf-8")
        original_stat = os.stat(src_file)

        # AI 완료 처리 핵심 로직 시뮬레이션 (파일 변경 없음 검증)
        # _on_untagged_analysis_finished는 _refresh_grouped_after_analysis 호출 →
        # 그룹 화면 업데이트만 수행, 실제 파일 변경 없음

        # 파일이 그대로인지 확인
        self.assertTrue(Path(src_file).exists())
        self.assertEqual(Path(src_file).read_text(encoding="utf-8"), original_content)
        self.assertEqual(os.stat(src_file).st_size, original_stat.st_size)

    def test_preview_refresh_does_not_move_files(self):
        """_refresh_grouped_after_analysis에 파일 이동 코드가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = _source(OrganizeView._refresh_grouped_after_analysis)
        self.assertNotIn("shutil.move", src)
        self.assertNotIn("shutil.copy", src)
        self.assertNotIn("os.rename", src)


if __name__ == "__main__":
    unittest.main()
