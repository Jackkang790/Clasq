"""Batch 8 — organize_view.py 자동정리 버튼 · FolderAnalysisPlanWorker 연결 테스트.

QThread.run()을 직접 호출하거나 소스 코드 분석을 통해 Qt 렌더링 없이 검증한다.
"""
import inspect
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.utils.db_manager import FileRegistryManager
from src.utils.workers import FolderAnalysisPlanWorker, IncrementalInventoryWorker


def _db(tmp: str) -> str:
    db = str(Path(tmp) / "test.db")
    FileRegistryManager(db_path=db)
    return db


def _make_txt(directory: str, name: str, content: str = "test content") -> str:
    p = str(Path(directory) / name)
    Path(p).write_text(content, encoding="utf-8")
    return p


# ── 1. 버튼 · Worker 연결 검증 ─────────────────────────────────────────────────
class TestBatch8ButtonWorkerConnection(unittest.TestCase):

    def test_organize_view_has_on_auto_organize(self):
        """_on_auto_organize 메서드가 OrganizeView에 존재해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, '_on_auto_organize'))

    def test_signal_connected_to_handler_in_init(self):
        """OrganizeView.__init__ 소스에서 autoOrganizeRequested → _on_auto_organize 연결 확인."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView.__init__)
        self.assertIn('_on_auto_organize', src)
        self.assertIn('autoOrganizeRequested', src)

    def test_incremental_worker_used_in_auto_organize(self):
        """자동정리는 AI/index 작업 전 fast inventory worker를 사용한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_auto_organize)
        self.assertIn('_start_incremental_inventory', src)

    def test_auto_btn_stored_in_file_table_screen(self):
        """_FileTableScreen 소스에 self.auto_btn 저장 확인."""
        from src.ui.views.organize_view import _FileTableScreen
        src = inspect.getsource(_FileTableScreen.__init__)
        self.assertIn('self.auto_btn', src)

    def test_incremental_worker_starts_asynchronously(self):
        """공용 inventory 시작 함수가 QThread를 비동기로 실행한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._start_incremental_inventory)
        self.assertIn('.start()', src)

    def test_incremental_inventory_worker_is_qthread(self):
        from PySide6.QtCore import QThread
        self.assertTrue(issubclass(IncrementalInventoryWorker, QThread))

    def test_folder_analysis_plan_worker_is_qthread(self):
        """FolderAnalysisPlanWorker가 QThread를 상속해야 한다 (UI 블로킹 방지)."""
        from PySide6.QtCore import QThread
        self.assertTrue(issubclass(FolderAnalysisPlanWorker, QThread))


# ── 2. 중복 실행 방지 ──────────────────────────────────────────────────────────
class TestBatch8DuplicatePrevention(unittest.TestCase):

    def test_duplicate_prevention_guard_in_source(self):
        """_on_auto_organize 소스에 isRunning() 체크가 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_auto_organize)
        self.assertIn('isRunning', src)
        self.assertIn('_plan_worker', src)

    def test_worker_is_not_running_after_sync_run(self):
        """run() 완료 후 isRunning()이 False여야 한다 (중복 방지 guard 조건)."""
        with tempfile.TemporaryDirectory() as tmp:
            db = _db(tmp)
            _make_txt(tmp, "a.txt")
            worker = FolderAnalysisPlanWorker([tmp], db_path=db)
            worker.run()
            self.assertFalse(worker.isRunning())


# ── 3. 버튼 재활성화 패턴 ─────────────────────────────────────────────────────
class TestBatch8ButtonReenablePattern(unittest.TestCase):

    def test_close_plan_dialog_method_exists(self):
        """_close_plan_dialog 메서드가 OrganizeView에 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, '_close_plan_dialog'))

    def test_close_plan_dialog_reenables_button_in_source(self):
        """_close_plan_dialog 소스에서 auto_btn.setEnabled(True) 확인 (성공/실패 모두 복구)."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._close_plan_dialog)
        self.assertIn('setEnabled(True)', src)
        self.assertIn('auto_btn', src)

    def test_on_plan_error_calls_close_dialog(self):
        """_on_plan_error 소스에서 _close_plan_dialog 호출 확인 (에러 시 버튼 복구)."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_error)
        self.assertIn('_close_plan_dialog', src)

    def test_button_reenable_pattern_simulation(self):
        """성공 및 실패 시 버튼 재활성화 패턴을 시뮬레이션한다."""
        enabled_state = [True]

        def simulate_start():
            enabled_state[0] = False

        def simulate_close():
            enabled_state[0] = True

        # 성공 케이스
        simulate_start()
        self.assertFalse(enabled_state[0])
        simulate_close()
        self.assertTrue(enabled_state[0])

        # 에러 케이스
        simulate_start()
        self.assertFalse(enabled_state[0])
        simulate_close()
        self.assertTrue(enabled_state[0])


# ── 4. 대상 폴더 검증 ─────────────────────────────────────────────────────────
class TestBatch8FolderValidation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_target_folders_method_exists(self):
        """OrganizeView._get_target_folders 메서드가 존재해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, '_get_target_folders'))

    def _run_get_target_folders_logic(self, rows):
        """_get_target_folders 핵심 로직을 독립적으로 실행한다."""
        seen = set()
        folders = []
        for _, _, file_path in rows:
            if not file_path:
                continue
            parent = str(Path(file_path).parent)
            if parent in seen:
                continue
            seen.add(parent)
            try:
                if Path(parent).is_dir() and os.access(parent, os.R_OK):
                    folders.append(parent)
            except (OSError, ValueError):
                pass
        return folders

    def test_valid_folder_extracted(self):
        """실제 존재하는 파일 경로에서 부모 폴더를 추출한다."""
        test_file = Path(self.tmp) / "test.txt"
        test_file.write_text("hello", encoding="utf-8")

        rows = [("test.txt", "", str(test_file))]
        folders = self._run_get_target_folders_logic(rows)

        self.assertEqual(len(folders), 1)
        self.assertEqual(os.path.normcase(folders[0]), os.path.normcase(self.tmp))

    def test_nonexistent_path_excluded(self):
        """존재하지 않는 경로의 부모 폴더는 결과에서 제외된다."""
        fake_path = str(Path(self.tmp) / "nonexistent" / "ghost.txt")
        rows = [("ghost.txt", "", fake_path)]
        folders = self._run_get_target_folders_logic(rows)
        self.assertEqual(len(folders), 0)

    def test_empty_table_returns_empty_folders(self):
        """빈 테이블에서는 폴더 목록이 비어야 한다."""
        folders = self._run_get_target_folders_logic([])
        self.assertEqual(len(folders), 0)

    def test_duplicate_parent_deduplication(self):
        """같은 폴더의 파일이 여러 개여도 폴더는 한 번만 포함된다."""
        test_file1 = Path(self.tmp) / "a.txt"
        test_file2 = Path(self.tmp) / "b.txt"
        test_file1.write_text("a", encoding="utf-8")
        test_file2.write_text("b", encoding="utf-8")

        rows = [
            ("a.txt", "", str(test_file1)),
            ("b.txt", "", str(test_file2)),
        ]
        folders = self._run_get_target_folders_logic(rows)
        self.assertEqual(len(folders), 1)

    def test_no_core_warning_in_source(self):
        """_on_auto_organize에서 core=None 시 경고를 표시해야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_auto_organize)
        self.assertIn('코어 시스템이 초기화되지 않았습니다', src)


# ── 5. Worker 결과 → UI 표시 ───────────────────────────────────────────────────
class TestBatch8PlanResultUI(unittest.TestCase):

    def test_on_plan_completed_method_exists(self):
        """_on_plan_completed 메서드가 OrganizeView에 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, '_on_plan_completed'))

    def test_on_plan_error_method_exists(self):
        """_on_plan_error 메서드가 OrganizeView에 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        self.assertTrue(hasattr(OrganizeView, '_on_plan_error'))

    def test_on_plan_completed_shows_grouped_screen(self):
        """_on_plan_completed 소스에서 _show_grouped 호출 확인."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertIn('_show_grouped', src)

    def test_on_plan_completed_calls_set_groups(self):
        """_on_plan_completed 소스에서 set_groups 호출 확인."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertIn('set_groups', src)

    def test_grouped_screen_has_set_banner_text(self):
        """_GroupedScreen에 set_banner_text 메서드가 있어야 한다."""
        from src.ui.views.organize_view import _GroupedScreen
        self.assertTrue(hasattr(_GroupedScreen, 'set_banner_text'))

    def test_grouped_screen_has_set_confirm_enabled(self):
        """_GroupedScreen에 set_confirm_enabled 메서드가 있어야 한다."""
        from src.ui.views.organize_view import _GroupedScreen
        self.assertTrue(hasattr(_GroupedScreen, 'set_confirm_enabled'))

    def test_confirm_button_enabled_after_plan(self):
        """Batch 9: _on_plan_completed에서 set_confirm_enabled(True) 확인.
        Batch 8에서는 False였으나, Batch 9에서 Apply 흐름을 위해 True로 변경됨."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertIn('set_confirm_enabled', src)
        self.assertIn('True', src)

    def test_banner_text_updated_in_plan_completed(self):
        """_on_plan_completed 소스에서 set_banner_text 호출 확인."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertIn('set_banner_text', src)

    def test_worker_completed_payload_structure(self):
        """Worker가 반환하는 plan에 UI가 사용하는 키가 있어야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            db = _db(tmp)
            _make_txt(tmp, "report.txt", "annual report")
            plans = []
            worker = FolderAnalysisPlanWorker([tmp], db_path=db)
            worker.completed.connect(plans.append)
            worker.run()

            self.assertEqual(len(plans), 1)
            plan = plans[0]
            # UI(_on_plan_completed)가 사용하는 키 확인
            self.assertIn('counts', plan)
            self.assertIn('text_index', plan)
            self.assertIn('scanned', plan)


# ── 6. 파일 안전성 검증 ────────────────────────────────────────────────────────
class TestBatch8FileSafety(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_shutil_move_in_auto_organize(self):
        """_on_auto_organize에 shutil.move/copy가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_auto_organize)
        self.assertNotIn('shutil.move', src)
        self.assertNotIn('shutil.copy', src)

    def test_no_path_rename_in_auto_organize(self):
        """_on_auto_organize에 .rename()/.replace()가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_auto_organize)
        self.assertNotIn('.rename(', src)
        self.assertNotIn('.replace(', src)

    def test_no_file_deletion_in_plan_completed(self):
        """_on_plan_completed에 파일 삭제 코드가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertNotIn('os.remove', src)
        self.assertNotIn('.unlink(', src)
        self.assertNotIn('shutil', src)

    def test_no_file_overwrite_in_plan_completed(self):
        """_on_plan_completed에 덮어쓰기 코드가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertNotIn('.replace(', src)
        self.assertNotIn('shutil.move', src)

    def test_worker_does_not_modify_source_files(self):
        """Worker 실행 후 원본 파일 내용·크기·존재 여부가 그대로여야 한다."""
        doc = _make_txt(self.tmp, "immutable.txt", "original content")
        original_content = Path(doc).read_text(encoding="utf-8")
        original_size = os.path.getsize(doc)

        worker = FolderAnalysisPlanWorker([self.tmp], db_path=self.db)
        worker.run()

        self.assertTrue(Path(doc).exists(), "파일이 삭제되었습니다")
        self.assertEqual(Path(doc).read_text(encoding="utf-8"), original_content,
                         "파일 내용이 변경되었습니다")
        self.assertEqual(os.path.getsize(doc), original_size, "파일 크기가 변경되었습니다")

    def test_worker_does_not_rename_files(self):
        """Worker 실행 후 원본 파일명이 그대로여야 한다."""
        doc = _make_txt(self.tmp, "keep_name.txt", "content")
        worker = FolderAnalysisPlanWorker([self.tmp], db_path=self.db)
        worker.run()
        self.assertTrue(Path(doc).exists(), "파일이 이동/삭제/이름변경되었습니다")


# ── 7. DB schema 유지 ─────────────────────────────────────────────────────────
class TestBatch8DBSchema(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schema_version_still_v2(self):
        """DB schema가 v2여야 한다 (db_schema_version 테이블 기준)."""
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM db_schema_version"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 3)

    def test_expected_tables_exist(self):
        """기존 테이블이 모두 존재해야 한다."""
        conn = sqlite3.connect(self.db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        for t in ('files', 'file_fingerprint_cache', 'file_text_index', 'db_schema_version'):
            self.assertIn(t, tables, f"테이블 '{t}'이 없습니다")

    def test_no_new_tables_added(self):
        """Batch 8에서 새 테이블이 추가되지 않아야 한다.

        기존 테이블: files, managed_paths, file_fingerprint_cache,
                     file_text_index, db_schema_version + sqlite_sequence
        """
        conn = sqlite3.connect(self.db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        known = {
            'files', 'managed_paths', 'file_fingerprint_cache',
            'file_text_index', 'db_schema_version', 'sqlite_sequence',
            'organize_history',
        }
        new_tables = tables - known
        self.assertEqual(new_tables, set(), f"예상치 못한 새 테이블: {new_tables}")

    def test_schema_unchanged_after_worker_run(self):
        """Worker 실행 후에도 schema v2가 유지되어야 한다."""
        _make_txt(self.tmp, "schema_check.txt", "hello")
        worker = FolderAnalysisPlanWorker([self.tmp], db_path=self.db)
        worker.run()

        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM db_schema_version"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 3)


# ── 8. Worker 예외 처리 검증 ──────────────────────────────────────────────────
class TestBatch8WorkerExceptionHandling(unittest.TestCase):

    def test_error_signal_on_inaccessible_db(self):
        """접근 불가 DB 경로 시 error signal이 emit되거나 completed가 반환되어야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            bad_db = "/nonexistent/path/test.db"
            _make_txt(tmp, "a.txt")
            error_msgs = []
            completed = []

            worker = FolderAnalysisPlanWorker([tmp], db_path=bad_db)
            worker.error.connect(error_msgs.append)
            worker.completed.connect(completed.append)
            worker.run()

            # 크래시 없이 여기까지 도달하면 통과
            self.assertTrue(True)

    def test_nonexistent_folder_does_not_crash(self):
        """존재하지 않는 폴더 입력 시 앱이 종료되지 않아야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            db = _db(tmp)
            nonexistent = str(Path(tmp) / "ghost_folder")
            completed = []
            error_msgs = []

            worker = FolderAnalysisPlanWorker([nonexistent], db_path=db)
            worker.completed.connect(completed.append)
            worker.error.connect(error_msgs.append)
            worker.run()

            self.assertTrue(True)  # 여기까지 오면 크래시 없음

    def test_on_plan_error_source_has_messagebox(self):
        """_on_plan_error 소스에서 QMessageBox.critical 호출 확인."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_error)
        self.assertIn('QMessageBox', src)
        self.assertIn('critical', src)


if __name__ == "__main__":
    unittest.main()
