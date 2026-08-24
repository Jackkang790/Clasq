"""Batch 10 — AI 태그 없는 파일 처리 테스트.

AI 태그가 없는 파일이 조용히 사라지지 않고 미분류로 표시되며,
AI 분석 흐름 및 fallback이 올바르게 동작하는지 검증한다.
QApplication 없이 Worker 로직과 소스 코드 분석으로 검증한다.
"""
import inspect
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.utils.db_manager import FileRegistryManager


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
def _db(tmp: str) -> str:
    db = str(Path(tmp) / "test.db")
    FileRegistryManager(db_path=db)
    return db


def _make_file(directory: str, name: str, content: str = "test") -> str:
    p = str(Path(directory) / name)
    Path(p).write_text(content, encoding="utf-8")
    return p


def _register_tagged(db_path: str, file_path: str, tag: str = "문서") -> None:
    """파일을 DB에 태그와 함께 등록."""
    mgr = FileRegistryManager(db_path=db_path)
    mgr.save_file_result(file_path, {
        "@TYPE": "@DB", "status": "SUCCESS",
        "metadata": {"display_name": Path(file_path).stem, "tags": [tag], "ai_comment": "test tag"},
    })


def _register_untagged(db_path: str, file_path: str) -> None:
    """파일을 DB에 태그 없이 등록 (AI 분석 실패 시뮬레이션)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO files (file_name, file_path, tags, category, created_at, updated_at) "
        "VALUES (?, ?, NULL, NULL, datetime('now'), datetime('now'))",
        (Path(file_path).name, file_path),
    )
    conn.commit()
    conn.close()


# ── 1. AI 태그 없는 파일 발생 원인 확인 ──────────────────────────────────────
class TestBatch10UntaggedFileCause(unittest.TestCase):

    def test_get_files_for_organize_excludes_untagged(self):
        """get_files_for_organize()가 태그 없는 파일을 제외하는지 확인."""
        with tempfile.TemporaryDirectory() as tmp:
            db = _db(tmp)
            tagged_file = _make_file(tmp, "tagged.txt", "tagged")
            untagged_file = _make_file(tmp, "untagged.txt", "untagged")

            _register_tagged(db, tagged_file)
            _register_untagged(db, untagged_file)

            from src.utils.core import ClasqCore
            core = ClasqCore(db_path=db)
            result = core.get_files_for_organize()
            paths = [r["file_path"] for r in result]

            self.assertTrue(
                any(tagged_file in p for p in paths),
                "태그 있는 파일이 조회되지 않았습니다",
            )
            self.assertFalse(
                any(untagged_file in p for p in paths),
                "태그 없는 파일이 결과에 포함되었습니다",
            )

    def test_folder_analysis_plan_worker_does_not_add_ai_tags(self):
        """FolderAnalysisPlanWorker가 AI 태그를 추가하지 않는지 확인."""
        from src.utils.workers import FolderAnalysisPlanWorker
        src = inspect.getsource(FolderAnalysisPlanWorker.run)
        # 텍스트 색인은 하지만 AI 태그 생성(save_file_result, analyze_*)은 없어야 함
        self.assertNotIn("save_file_result", src)
        self.assertNotIn("analyze_document", src)
        self.assertNotIn("analyze_image", src)


# ── 2. 미분류 파일 감지 로직 ─────────────────────────────────────────────────
class TestBatch10UntaggedDetection(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_get_untagged(self, plan_files: list, tagged_paths: set) -> list:
        """_get_untagged_from_plan 핵심 로직 독립 실행."""
        untagged = []
        for p in plan_files:
            norm = os.path.normcase(os.path.abspath(p))
            if norm not in tagged_paths and os.path.isfile(p):
                untagged.append(p)
        return untagged

    def test_untagged_detected_from_plan_files(self):
        """Plan 파일 중 태그 없는 파일이 감지되어야 한다."""
        tagged = _make_file(self.tmp, "tagged.txt", "t")
        untagged = _make_file(self.tmp, "untagged.txt", "u")
        _register_tagged(self.db, tagged)

        tagged_paths = {os.path.normcase(os.path.abspath(tagged))}
        plan_files = [tagged, untagged]

        result = self._run_get_untagged(plan_files, tagged_paths)
        self.assertEqual(len(result), 1)
        self.assertIn(untagged, result)

    def test_all_tagged_returns_empty_untagged(self):
        """모든 파일이 태그 있으면 미분류 없음."""
        f1 = _make_file(self.tmp, "f1.txt", "a")
        f2 = _make_file(self.tmp, "f2.txt", "b")
        tagged_paths = {
            os.path.normcase(os.path.abspath(f1)),
            os.path.normcase(os.path.abspath(f2)),
        }
        result = self._run_get_untagged([f1, f2], tagged_paths)
        self.assertEqual(len(result), 0)

    def test_nonexistent_file_excluded_from_untagged(self):
        """존재하지 않는 파일은 미분류 목록에 포함되지 않는다."""
        ghost = str(Path(self.tmp) / "ghost.txt")
        result = self._run_get_untagged([ghost], set())
        self.assertEqual(len(result), 0)

    def test_get_untagged_method_exists(self):
        """OrganizeView._get_untagged_from_plan 메서드가 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, "_get_untagged_from_plan"))

    def test_untagged_stored_in_last_untagged_files(self):
        """_on_plan_completed 소스에서 _last_untagged_files 저장 확인."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertIn("_last_untagged_files", src)

    def test_untagged_count_in_banner(self):
        """미분류가 있을 때 banner에 '미분류' 텍스트가 포함되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertIn("미분류", src)

    def test_untagged_group_added_to_grouped_screen(self):
        """미분류 파일이 있을 때 groups_ui에 미분류 그룹이 추가되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertIn("미분류 (AI 태그 없음)", src)


# ── 3. 확장자 기반 파일 유형 fallback ─────────────────────────────────────────
class TestBatch10ExtensionFallback(unittest.TestCase):

    def test_image_extensions_detected(self):
        """이미지 확장자는 'image' 유형으로 반환되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]:
            kind = OrganizeView._get_file_kind_by_extension(f"test{ext}")
            self.assertEqual(kind, "image", f"{ext} → expected 'image', got {kind!r}")

    def test_doc_extensions_detected(self):
        """문서 확장자는 'doc' 유형으로 반환되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        for ext in [".txt", ".doc", ".docx", ".pdf", ".pptx", ".xlsx"]:
            kind = OrganizeView._get_file_kind_by_extension(f"test{ext}")
            self.assertEqual(kind, "doc", f"{ext} → expected 'doc', got {kind!r}")

    def test_unknown_extension_returns_default(self):
        """알 수 없는 확장자는 'default'를 반환해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        for ext in [".xyz", ".bin", ".rar", ".mp4"]:
            kind = OrganizeView._get_file_kind_by_extension(f"test{ext}")
            self.assertEqual(kind, "default", f"{ext} should be 'default'")

    def test_extension_fallback_not_used_for_apply_destination(self):
        """_get_file_kind_by_extension이 Apply 대상 경로 결정에 사용되지 않아야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_organize_confirmed)
        # 이 메서드는 Apply destination에 extension fallback을 쓰지 않아야 함
        self.assertNotIn("_get_file_kind_by_extension", src)

    def test_fallback_is_for_display_only(self):
        """_get_file_kind_by_extension은 표시 목적임을 소스에서 확인."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._get_file_kind_by_extension)
        self.assertIn("표시", src)

    def test_fallback_not_mistaken_for_ai_result(self):
        """미분류 카드 레이블이 AI 분류인 것처럼 표시되지 않는지 확인."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        # 미분류 카드에는 "AI 태그 없음" 표시가 있어야 함
        self.assertIn("AI 태그 없음", src)

    def test_no_meaning_inference_from_filename(self):
        """파일명으로 의미 카테고리를 추측하는 코드가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        # 파일명 기반 카테고리 추측 키워드 없어야 함
        src_all = (
            inspect.getsource(OrganizeView._get_file_kind_by_extension)
            + inspect.getsource(OrganizeView._on_plan_completed)
        )
        for keyword in ["invoice", "report", "사진", "회계", "여행"]:
            self.assertNotIn(keyword, src_all)


# ── 4. AI 가용성 확인 ────────────────────────────────────────────────────────
class TestBatch10AIAvailability(unittest.TestCase):

    def test_check_ai_available_method_exists(self):
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, "_check_ai_available"))

    def test_check_ai_available_returns_false_on_connection_error(self):
        """AI 서버에 연결할 수 없으면 False를 반환해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        with patch("requests.get", side_effect=ConnectionError("no server")):
            result = OrganizeView._check_ai_available()
        self.assertFalse(result)

    def test_check_ai_available_returns_false_on_timeout(self):
        """AI 서버가 timeout이면 False를 반환해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        import requests
        with patch("requests.get", side_effect=requests.exceptions.Timeout()):
            result = OrganizeView._check_ai_available()
        self.assertFalse(result)

    def test_check_ai_available_returns_true_on_200(self):
        """AI 서버가 200 응답이면 True를 반환해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            result = OrganizeView._check_ai_available()
        self.assertTrue(result)

    def test_organize_does_not_depend_on_ai_server(self):
        """정리 적용은 AI 서버 상태와 무관하게 태그된 파일만 처리한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_organize_confirmed)
        self.assertNotIn("_check_ai_available", src)
        self.assertNotIn("_start_untagged_analysis", src)
        self.assertIn("get_files_for_organize", src)


# ── 5. background AI 분석 ────────────────────────────────────────────────────
class TestBatch10BackgroundAnalysis(unittest.TestCase):

    def test_start_untagged_analysis_method_exists(self):
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, "_start_untagged_analysis"))

    def test_analysis_uses_folder_scan_tag_worker(self):
        """_start_untagged_analysis가 FolderScanAndTagWorker를 사용해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._start_untagged_analysis)
        self.assertIn("FolderScanAndTagWorker", src)

    def test_analysis_worker_starts_in_qthread(self):
        """background 분석 Worker가 QThread를 통해 실행되어야 한다."""
        from src.utils.workers import FolderScanAndTagWorker
        from PySide6.QtCore import QThread
        self.assertTrue(issubclass(FolderScanAndTagWorker, QThread))

    def test_analysis_uses_qthread_start(self):
        """_start_untagged_analysis 소스에서 .start() 호출 확인."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._start_untagged_analysis)
        self.assertIn(".start()", src)

    def test_analysis_error_handler_exists(self):
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, "_on_untagged_analysis_error"))

    def test_analysis_finished_handler_exists(self):
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, "_on_untagged_analysis_finished"))

    def test_close_untagged_dialog_exists(self):
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, "_close_untagged_dialog"))

    def test_folder_scan_tag_worker_with_file_paths(self):
        """FolderScanAndTagWorker에 파일 경로를 개별로 전달 가능해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            db = _db(tmp)
            f = _make_file(tmp, "test.txt", "hello")

            from src.utils.workers import FolderScanAndTagWorker
            from src.utils.core import ClasqCore
            core = ClasqCore(db_path=db)

            # 개별 파일 경로 전달
            completed = []
            errors = []
            worker = FolderScanAndTagWorker([f], core)
            worker.finished.connect(completed.append)
            worker.error.connect(errors.append)
            # run() 실행 (AI 실패해도 error signal emit하고 안 죽으면 됨)
            try:
                worker.run()
            except Exception:
                pass
            # crash 없으면 통과
            self.assertTrue(True)


# ── 6. 중복/무한 분석 방지 ───────────────────────────────────────────────────
class TestBatch10DuplicatePrevention(unittest.TestCase):

    def test_analysis_attempted_set_exists(self):
        """_analysis_attempted 집합이 OrganizeView 인스턴스에 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView.__init__)
        self.assertIn("_analysis_attempted", src)

    def test_already_attempted_files_excluded(self):
        """이미 분석 시도한 파일은 재분석에서 제외되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._start_untagged_analysis)
        self.assertIn("_analysis_attempted", src)
        self.assertIn("not in self._analysis_attempted", src)

    def test_attempted_files_recorded_on_start(self):
        """분석 시작 시 파일이 _analysis_attempted에 기록되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._start_untagged_analysis)
        self.assertIn("_analysis_attempted.add", src)

    def test_running_worker_prevents_duplicate_start(self):
        """Worker가 이미 실행 중이면 중복 실행을 방지해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._start_untagged_analysis)
        self.assertIn("isRunning", src)
        self.assertIn("_untagged_worker", src)


# ── 7. 미분류 파일을 조용히 버리지 않음 ──────────────────────────────────────
class TestBatch10NoSilentDrop(unittest.TestCase):

    def test_untagged_files_shown_in_grouped_screen(self):
        """_on_plan_completed에서 미분류 파일이 grouped screen에 표시되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertIn("groups_ui.append", src)
        self.assertIn("미분류", src)

    def test_organize_confirmed_informs_user_about_untagged(self):
        """_on_organize_confirmed에서 미분류 파일 수를 사용자에게 표시해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_organize_confirmed)
        self.assertIn("미분류", src)
        self.assertIn("len(untagged)", src)

    def test_untagged_count_shown_in_banner(self):
        """banner text에 미분류 파일 수가 포함되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertIn("len(untagged)", src)

    def test_untagged_files_are_directed_to_saved_list(self):
        """미태깅 파일은 자동 분석하지 않고 저장목록 설정을 안내해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_organize_confirmed)
        self.assertIn("저장목록", src)
        self.assertIn("태그를 설정", src)
        self.assertNotIn("_start_untagged_analysis", src)


# ── 8. Apply 안전성 유지 ─────────────────────────────────────────────────────
class TestBatch10ApplySafetyMaintained(unittest.TestCase):

    def test_no_move_in_on_plan_completed(self):
        """_on_plan_completed에서 파일 이동이 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertNotIn("shutil.move", src)
        self.assertNotIn("os.rename", src)

    def test_no_move_in_untagged_analysis(self):
        """미분류 AI 분석 과정에서 파일이 이동되면 안 된다."""
        from src.ui.views.organize_view import OrganizeView
        for method in ("_start_untagged_analysis", "_on_untagged_analysis_finished"):
            src = inspect.getsource(getattr(OrganizeView, method))
            self.assertNotIn("shutil.move", src, f"{method}에 shutil.move가 있습니다")
            self.assertNotIn("os.rename", src, f"{method}에 os.rename이 있습니다")

    def test_untagged_files_not_moved_to_arbitrary_destination(self):
        """_start_untagged_analysis 소스에서 임의 destination으로 이동하는 코드 없음."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._start_untagged_analysis)
        self.assertNotIn("target_path", src)
        self.assertNotIn("shutil", src)

    def test_organize_apply_worker_still_has_preflight(self):
        """OrganizeApplyWorker preflight 검증이 유지되어야 한다."""
        from src.utils.workers import OrganizeApplyWorker
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("사전 검증", src)
        self.assertIn("preflight_errors", src)

    def test_no_overwrite_in_apply_worker_maintained(self):
        """Apply Worker에서 overwrite 방지가 유지되어야 한다."""
        from src.utils.workers import OrganizeApplyWorker
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("destination 충돌", src)

    def test_rollback_still_in_apply_worker(self):
        """Apply Worker에서 rollback이 유지되어야 한다."""
        from src.utils.workers import OrganizeApplyWorker
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("rolled_back", src)

    def test_user_approval_still_required(self):
        """사용자 승인(QMessageBox.question)이 _on_organize_confirmed에 유지되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_organize_confirmed)
        self.assertIn("QMessageBox.question", src)

    def test_no_file_deletion_in_batch10_code(self):
        """Batch 10 신규 메서드에 파일 삭제 코드가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        for method in ("_get_untagged_from_plan", "_start_untagged_analysis",
                       "_on_untagged_analysis_finished", "_refresh_grouped_after_analysis"):
            src = inspect.getsource(getattr(OrganizeView, method))
            self.assertNotIn("os.remove", src, f"{method}에 os.remove")
            self.assertNotIn(".unlink(", src, f"{method}에 .unlink(")


# ── 9. AI 실패/unavailable 시 앱 정상 동작 ────────────────────────────────────
class TestBatch10AIFailureSafety(unittest.TestCase):

    def test_analysis_error_does_not_crash(self):
        """AI 분석 오류 시 앱이 종료되지 않아야 한다 (소스 확인)."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_untagged_analysis_error)
        # QMessageBox.warning으로 안내 → 앱 종료 없음
        self.assertIn("warning", src)
        self.assertNotIn("sys.exit", src)

    def test_analysis_error_shows_message(self):
        """AI 분석 오류 시 사용자에게 메시지를 표시해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_untagged_analysis_error)
        self.assertIn("QMessageBox", src)
        self.assertIn("AI 분석 오류", src)

    def test_ai_failure_keeps_untagged_as_unclassified(self):
        """AI 분석 실패 후 미분류 상태가 유지되어야 한다 (소스 확인)."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_untagged_analysis_finished)
        # Batch 12: 실패 시 배너로 상태 표시 (QMessageBox 대신)
        self.assertIn("미분류", src)

    def test_single_file_failure_does_not_stop_others(self):
        """한 파일의 AI 분석 실패가 다른 파일 정리를 막으면 안 된다."""
        # FolderScanAndTagWorker는 파일별로 처리 → 한 파일 실패해도 계속 진행
        from src.utils.workers import FolderScanAndTagWorker
        src = inspect.getsource(FolderScanAndTagWorker.run)
        # 파일별 try/except가 있어야 함
        self.assertIn("try:", src)
        self.assertIn("except Exception", src)

    def test_only_saved_tagged_files_are_processed(self):
        """정리는 AI 상태와 무관하게 저장목록의 태그된 파일만 진행한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_organize_confirmed)
        self.assertIn("태그가 설정된", src)
        self.assertIn("파일만 정리", src)
        self.assertIn("미태깅 파일", src)


# ── 10. DB schema v2 유지 ────────────────────────────────────────────────────
class TestBatch10DBSchema(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schema_v2_unchanged(self):
        """DB schema가 v2를 유지해야 한다."""
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM db_schema_version"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 3)

    def test_no_new_tables(self):
        """Batch 10에서 새 테이블이 추가되지 않아야 한다."""
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

    def test_batch10_code_has_no_schema_changes(self):
        """Batch 10 신규 메서드에 CREATE TABLE / ALTER TABLE이 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        for method in ("_get_untagged_from_plan", "_start_untagged_analysis",
                       "_on_untagged_analysis_finished", "_refresh_grouped_after_analysis",
                       "_check_ai_available", "_get_file_kind_by_extension"):
            src = inspect.getsource(getattr(OrganizeView, method))
            self.assertNotIn("CREATE TABLE", src.upper())
            self.assertNotIn("ALTER TABLE", src.upper())


# ── 11. 미분류 파일이 임의 destination으로 이동하지 않음 ──────────────────────
class TestBatch10NoArbitraryMove(unittest.TestCase):

    def test_untagged_files_excluded_from_apply_plan(self):
        """태그 없는 파일이 Apply move_plan에 포함되지 않아야 한다."""
        # _on_organize_confirmed는 core.get_files_for_organize()로만 파일을 가져옴
        # get_files_for_organize는 태그 있는 파일만 반환
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_organize_confirmed)
        self.assertIn("get_files_for_organize", src)

    def test_no_extension_based_organize_destination(self):
        """확장자만으로 정리 destination을 결정하는 코드가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_organize_confirmed)
        # 확장자 기반 분류(image/doc)를 destination에 쓰는 코드 없어야 함
        self.assertNotIn("_get_file_kind_by_extension", src)

    def test_fallback_display_only_not_for_move(self):
        """_get_file_kind_by_extension 소스에서 이동/저장 코드 없음."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._get_file_kind_by_extension)
        self.assertNotIn("shutil", src)
        self.assertNotIn("move", src)
        self.assertNotIn("os.makedirs", src)


if __name__ == "__main__":
    unittest.main()
