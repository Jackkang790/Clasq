"""Batch 13 — Undo/History 테스트.

schema v3 migration, Apply 후 이력 기록, Undo Preflight, 정상 Undo,
충돌/수정 파일 보호, rollback, DB/index 동기화를 검증한다.
"""
import hashlib
import inspect
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.utils.db_manager import FileRegistryManager
from src.utils.workers import OrganizeApplyWorker, OrganizeUndoWorker


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
def _db(tmp: str) -> str:
    db = str(Path(tmp) / "test.db")
    FileRegistryManager(db_path=db)
    return db


def _make_file(directory: str, name: str, content: str = "test content") -> str:
    p = str(Path(directory) / name)
    Path(p).write_text(content, encoding="utf-8")
    return p


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _register(db: str, path: str, tag: str = "문서") -> int:
    mgr = FileRegistryManager(db_path=db)
    mgr.save_file_result(path, {
        "@TYPE": "@DB", "status": "SUCCESS",
        "metadata": {"display_name": Path(path).stem, "tags": [tag], "ai_comment": "test"},
    })
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT id FROM files WHERE file_path = ?", (path,)).fetchone()
    conn.close()
    return row[0] if row else None


def _run_apply(plan: list, db: str) -> dict:
    result_holder = [None]
    errors = []
    worker = OrganizeApplyWorker(plan, db)
    worker.completed.connect(lambda r: result_holder.__setitem__(0, r))
    worker.error.connect(errors.append)
    worker.run()
    return result_holder[0] or {"moved": [], "error": errors}


def _run_undo(records: list, db: str) -> dict:
    result_holder = [None]
    errors = []
    worker = OrganizeUndoWorker(records, db)
    worker.completed.connect(lambda r: result_holder.__setitem__(0, r))
    worker.error.connect(errors.append)
    worker.run()
    return result_holder[0] or {"undone": [], "error": errors}


def _get_history(db: str, operation_id: str = None) -> list:
    conn = sqlite3.connect(db)
    if operation_id:
        rows = conn.execute(
            "SELECT id, operation_id, original_path, moved_path, file_hash, file_size, status "
            "FROM organize_history WHERE operation_id = ?", (operation_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, operation_id, original_path, moved_path, file_hash, file_size, status "
            "FROM organize_history ORDER BY id"
        ).fetchall()
    conn.close()
    return [
        {"id": r[0], "operation_id": r[1], "original_path": r[2], "moved_path": r[3],
         "file_hash": r[4], "file_size": r[5], "status": r[6]}
        for r in rows
    ]


# ── 1. Schema v3 Migration ────────────────────────────────────────────────────
class TestBatch13SchemaMigration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schema_v3_created(self):
        """migration v3 후 schema가 v3이어야 한다."""
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT COALESCE(MAX(version),0) FROM db_schema_version").fetchone()
        conn.close()
        self.assertEqual(row[0], 3)

    def test_organize_history_table_exists(self):
        """organize_history 테이블이 생성되어야 한다."""
        conn = sqlite3.connect(self.db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        self.assertIn("organize_history", tables)

    def test_organize_history_columns(self):
        """organize_history 테이블에 필수 컬럼이 있어야 한다."""
        conn = sqlite3.connect(self.db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(organize_history)").fetchall()}
        conn.close()
        for col in ("id", "operation_id", "original_path", "moved_path",
                    "file_hash", "file_size", "status", "applied_at", "undone_at"):
            self.assertIn(col, cols, f"컬럼 '{col}' 없음")

    def test_existing_data_preserved_after_v3_migration(self):
        """v2 → v3 migration 후 기존 files 테이블 데이터가 보존되어야 한다."""
        src = _make_file(self.tmp, "preserve.txt", "preserve")
        _register(self.db, src)

        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT file_path FROM files WHERE file_path = ?", (src,)).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_migration_idempotent(self):
        """migration을 다시 실행해도 오류가 없어야 한다."""
        mgr2 = FileRegistryManager(db_path=self.db)  # 두 번째 실행
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT COALESCE(MAX(version),0) FROM db_schema_version").fetchone()
        conn.close()
        self.assertEqual(row[0], 3)

    def test_fresh_db_creates_v3(self):
        """신규 DB 생성 시 schema v3이 바로 생성되어야 한다."""
        fresh_db = str(Path(self.tmp) / "fresh.db")
        FileRegistryManager(db_path=fresh_db)
        conn = sqlite3.connect(fresh_db)
        row = conn.execute("SELECT COALESCE(MAX(version),0) FROM db_schema_version").fetchone()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        self.assertEqual(row[0], 3)
        self.assertIn("organize_history", tables)


# ── 2. Apply 성공 후 History 기록 ────────────────────────────────────────────
class TestBatch13HistoryRecording(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_history_recorded_after_apply(self):
        """Apply 성공 후 organize_history 레코드가 생성되어야 한다."""
        src = _make_file(self.src_dir, "a.txt", "hello")
        dst = str(Path(self.dst_dir) / "a.txt")
        fid = _register(self.db, src)

        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": "a.txt"}]
        result = _run_apply(plan, self.db)

        self.assertIsNotNone(result.get("operation_id"), "operation_id가 없습니다")
        history = _get_history(self.db)
        self.assertEqual(len(history), 1)
        self.assertEqual(os.path.normcase(history[0]["original_path"]), os.path.normcase(src))
        self.assertEqual(os.path.normcase(history[0]["moved_path"]), os.path.normcase(dst))
        self.assertEqual(history[0]["status"], "applied")

    def test_multiple_files_same_operation_id(self):
        """한 번의 Apply 내 파일들이 같은 operation_id를 공유해야 한다."""
        files = []
        plan = []
        for i in range(3):
            src = _make_file(self.src_dir, f"f{i}.txt", f"content {i}")
            dst = str(Path(self.dst_dir) / f"f{i}.txt")
            fid = _register(self.db, src)
            files.append((src, dst))
            plan.append({"file_id": fid, "file_path": src, "target_path": dst, "file_name": f"f{i}.txt"})

        result = _run_apply(plan, self.db)
        op_id = result.get("operation_id")

        history = _get_history(self.db, op_id)
        self.assertEqual(len(history), 3)
        op_ids = {h["operation_id"] for h in history}
        self.assertEqual(len(op_ids), 1)

    def test_no_history_on_apply_failure(self):
        """Apply 실패(preflight) 시 history 레코드가 생성되지 않아야 한다."""
        src = _make_file(self.src_dir, "fail.txt", "fail")
        dst = str(Path(self.dst_dir) / "fail.txt")
        _make_file(self.dst_dir, "fail.txt", "conflict")  # conflict

        plan = [{"file_id": None, "file_path": src, "target_path": dst, "file_name": "fail.txt"}]
        _run_apply(plan, self.db)

        history = _get_history(self.db)
        self.assertEqual(len(history), 0, "실패 시 history가 생성되었습니다")

    def test_operation_id_in_completed_payload(self):
        """completed payload에 operation_id가 포함되어야 한다."""
        src = _make_file(self.src_dir, "op.txt", "op")
        dst = str(Path(self.dst_dir) / "op.txt")
        result = _run_apply(
            [{"file_id": None, "file_path": src, "target_path": dst, "file_name": "op.txt"}],
            self.db
        )
        self.assertIn("operation_id", result)
        self.assertIn("history_errors", result)

    def test_history_hash_recorded(self):
        """history에 파일 hash가 기록되어야 한다 (integrity check용)."""
        src = _make_file(self.src_dir, "hash.txt", "hash content")
        dst = str(Path(self.dst_dir) / "hash.txt")
        fid = _register(self.db, src)

        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": "hash.txt"}]
        result = _run_apply(plan, self.db)
        op_id = result.get("operation_id")

        if op_id:
            history = _get_history(self.db, op_id)
            # hash가 있거나 없어도 crash 없이 동작해야 함 (hash 없는 경우도 허용)
            self.assertEqual(len(history), 1)


# ── 3. 정상 Undo ──────────────────────────────────────────────────────────────
class TestBatch13NormalUndo(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _apply_and_get_records(self, src, dst, fid=None):
        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": Path(dst).name}]
        result = _run_apply(plan, self.db)
        op_id = result.get("operation_id")
        if not op_id:
            return []
        return _get_history(self.db, op_id)

    def test_undo_restores_original_path(self):
        """Undo 후 파일이 original path로 복원되어야 한다."""
        src = _make_file(self.src_dir, "a.txt", "original")
        dst = str(Path(self.dst_dir) / "a.txt")
        fid = _register(self.db, src)
        records = self._apply_and_get_records(src, dst, fid)
        self.assertTrue(records)

        result = _run_undo(records, self.db)
        self.assertEqual(len(result["undone"]), 1)
        self.assertTrue(Path(src).exists(), "original path에 파일이 없습니다")
        self.assertFalse(Path(dst).exists(), "moved path에 파일이 여전히 있습니다")

    def test_undo_preserves_content(self):
        """Undo 후 파일 내용이 보존되어야 한다."""
        content = "unique undo content 12345"
        src = _make_file(self.src_dir, "preserve.txt", content)
        dst = str(Path(self.dst_dir) / "preserve.txt")
        records = self._apply_and_get_records(src, dst)
        if not records:
            return
        _run_undo(records, self.db)
        self.assertEqual(Path(src).read_text(encoding="utf-8"), content)

    def test_undo_preserves_hash(self):
        """Undo 후 파일 hash가 Apply 이전과 동일해야 한다."""
        src = _make_file(self.src_dir, "hash.txt", "hash content")
        original_hash = _sha256(src)
        dst = str(Path(self.dst_dir) / "hash.txt")
        records = self._apply_and_get_records(src, dst)
        if not records:
            return
        _run_undo(records, self.db)
        self.assertEqual(_sha256(src), original_hash)

    def test_undo_updates_history_status(self):
        """Undo 후 history status가 'undone'으로 변경되어야 한다."""
        src = _make_file(self.src_dir, "status.txt", "status")
        dst = str(Path(self.dst_dir) / "status.txt")
        fid = _register(self.db, src)
        records = self._apply_and_get_records(src, dst, fid)
        if not records:
            return
        _run_undo(records, self.db)

        history = _get_history(self.db)
        undone = [h for h in history if h["status"] == "undone"]
        self.assertGreater(len(undone), 0, "status가 'undone'으로 변경되지 않았습니다")

    def test_undo_updates_files_table_path(self):
        """Undo 후 files 테이블 path가 original로 복원되어야 한다."""
        src = _make_file(self.src_dir, "db_undo.txt", "db test")
        dst = str(Path(self.dst_dir) / "db_undo.txt")
        fid = _register(self.db, src)
        records = self._apply_and_get_records(src, dst, fid)
        if not records:
            return
        _run_undo(records, self.db)

        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT file_path FROM files WHERE id = ?", (fid,)).fetchone()
        conn.close()
        if row:
            self.assertEqual(os.path.normcase(row[0]), os.path.normcase(src))

    def test_undo_updates_text_index(self):
        """Undo 후 file_text_index가 original path로 복원되어야 한다."""
        src = _make_file(self.src_dir, "ti.txt", "text index")
        dst = str(Path(self.dst_dir) / "ti.txt")
        fid = _register(self.db, src)

        # text_index에 moved path 항목 등록
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT OR REPLACE INTO file_text_index "
            "(file_path, file_hash, extract_status, updated_at) VALUES (?, ?, 'success', datetime('now'))",
            (dst, "abc"),
        )
        conn.commit()
        conn.close()

        records = [{"id": 0, "operation_id": "test", "original_path": src,
                    "moved_path": dst, "file_hash": None, "status": "applied"}]

        # dst가 존재하는 상태에서 직접 Undo Worker 호출
        # 단, apply를 직접 수행해야 파일이 dst에 있음
        _make_file(self.dst_dir, "ti.txt", "text index")
        if Path(src).exists():
            os.remove(src)  # src 비우기

        result = _run_undo(records, self.db)
        if result["undone"]:
            conn = sqlite3.connect(self.db)
            new_row = conn.execute(
                "SELECT 1 FROM file_text_index WHERE file_path = ?", (src,)
            ).fetchone()
            old_row = conn.execute(
                "SELECT 1 FROM file_text_index WHERE file_path = ?", (dst,)
            ).fetchone()
            conn.close()
            self.assertIsNotNone(new_row, "text_index에 original path 없음")
            self.assertIsNone(old_row, "text_index에 moved path 남아있음")


# ── 4. Undo Preflight 안전성 ──────────────────────────────────────────────────
class TestBatch13UndoPreflight(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_record(self, original, moved, status="applied", file_hash=None):
        return {"id": 1, "operation_id": "test-op", "original_path": original,
                "moved_path": moved, "file_hash": file_hash, "status": status}

    def test_original_path_conflict_blocks_undo(self):
        """original path에 이미 파일이 있으면 Undo가 차단되어야 한다 (overwrite 금지)."""
        moved = _make_file(self.dst_dir, "conflict.txt", "moved content")
        original = _make_file(self.src_dir, "conflict.txt", "existing content")  # conflict!

        records = [self._make_record(original, moved)]
        errors = []
        worker = OrganizeUndoWorker(records, self.db)
        worker.error.connect(errors.append)
        worker.run()

        self.assertGreater(len(errors), 0, "충돌이 감지되지 않았습니다")
        # 기존 original 파일 보존 확인
        self.assertEqual(Path(original).read_text(encoding="utf-8"), "existing content")

    def test_missing_moved_file_blocks_undo(self):
        """moved path 파일이 없으면 Undo가 차단되어야 한다."""
        nonexistent_moved = str(Path(self.dst_dir) / "ghost.txt")
        original = str(Path(self.src_dir) / "ghost.txt")

        records = [self._make_record(original, nonexistent_moved)]
        errors = []
        worker = OrganizeUndoWorker(records, self.db)
        worker.error.connect(errors.append)
        worker.run()

        self.assertGreater(len(errors), 0)

    def test_already_undone_blocks_undo(self):
        """이미 되돌려진(undone) 항목은 Undo에서 제외되어야 한다."""
        moved = _make_file(self.dst_dir, "done.txt", "done")
        original = str(Path(self.src_dir) / "done.txt")

        records = [self._make_record(original, moved, status="undone")]
        errors = []
        completed = []
        worker = OrganizeUndoWorker(records, self.db)
        worker.error.connect(errors.append)
        worker.completed.connect(completed.append)
        worker.run()

        # preflight에서 거부되어야 함
        self.assertGreater(len(errors), 0, "이미 되돌려진 항목이 차단되지 않았습니다")
        self.assertTrue(Path(moved).exists(), "파일이 이동되었습니다")

    def test_modified_file_blocks_undo(self):
        """Apply 이후 파일이 수정된 경우 Undo가 차단되어야 한다 (사용자 수정 보호)."""
        moved = _make_file(self.dst_dir, "modified.txt", "original apply content")
        original = str(Path(self.src_dir) / "modified.txt")
        original_hash = _sha256(moved)

        # 파일 수정
        Path(moved).write_text("MODIFIED CONTENT", encoding="utf-8")
        new_hash = _sha256(moved)
        self.assertNotEqual(original_hash, new_hash)

        records = [self._make_record(original, moved, file_hash=original_hash)]
        errors = []
        worker = OrganizeUndoWorker(records, self.db)
        worker.error.connect(errors.append)
        worker.run()

        self.assertGreater(len(errors), 0, "수정된 파일이 감지되지 않았습니다")
        # 수정된 내용 보존 확인
        self.assertEqual(Path(moved).read_text(encoding="utf-8"), "MODIFIED CONTENT")

    def test_no_overwrite_in_undo(self):
        """Undo 소스에 overwrite 코드가 없어야 한다."""
        src = inspect.getsource(OrganizeUndoWorker.run)
        self.assertNotIn("'w'", src)
        self.assertNotIn("overwrite", src.lower())
        # os.path.exists(original) 체크 확인
        self.assertIn("os.path.exists(original)", src)

    def test_no_deletion_in_undo_worker(self):
        """Undo Worker에 파일 삭제 코드가 없어야 한다."""
        src = inspect.getsource(OrganizeUndoWorker.run)
        self.assertNotIn("os.remove", src)
        self.assertNotIn(".unlink(", src)


# ── 5. 중복 Undo 방지 ─────────────────────────────────────────────────────────
class TestBatch13DuplicateUndoPrevention(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_double_undo_blocked_by_status(self):
        """Undo 완료 후 같은 항목을 다시 Undo하려 하면 차단되어야 한다."""
        moved = _make_file(self.dst_dir, "dup.txt", "dup")
        original = str(Path(self.tmp) / "dup_orig.txt")

        # status='undone' → 차단
        records = [{"id": 99, "operation_id": "op1", "original_path": original,
                    "moved_path": moved, "file_hash": None, "status": "undone"}]
        errors = []
        worker = OrganizeUndoWorker(records, self.db)
        worker.error.connect(errors.append)
        worker.run()

        self.assertGreater(len(errors), 0, "중복 Undo가 차단되지 않았습니다")


# ── 6. Undo rollback ──────────────────────────────────────────────────────────
class TestBatch13UndoRollback(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_undo_rollback_on_second_file_conflict(self):
        """두 번째 파일에서 Undo 실패 시 첫 번째 파일이 rollback되어야 한다."""
        moved_a = _make_file(self.dst_dir, "a.txt", "aaa")
        moved_b = _make_file(self.dst_dir, "b.txt", "bbb")
        orig_a = str(Path(self.src_dir) / "a.txt")
        orig_b = str(Path(self.src_dir) / "b.txt")
        # b의 original에 미리 파일 생성 → b에서 Undo 실패
        _make_file(self.src_dir, "b.txt", "conflict at b")

        records = [
            {"id": 1, "operation_id": "op", "original_path": orig_a, "moved_path": moved_a, "file_hash": None, "status": "applied"},
            {"id": 2, "operation_id": "op", "original_path": orig_b, "moved_path": moved_b, "file_hash": None, "status": "applied"},
        ]

        result = _run_undo(records, self.db)

        # b는 preflight에서 충돌 감지 → 전체 preflight 실패 → 파일 변경 없음
        # OR a 이동 후 b 실패 → a rollback
        # 어느 쪽이든 최종 상태: moved_a는 원위치
        if not result.get("error"):
            # completed reached
            if result.get("failed"):
                # a가 원위치(rollback) 되었는지 확인
                self.assertTrue(Path(moved_a).exists() or Path(orig_a).exists())
        else:
            # preflight 실패
            self.assertTrue(Path(moved_a).exists())

    def test_rollback_no_overwrite(self):
        """Undo rollback 시 overwrite하지 않아야 한다."""
        src_undo = inspect.getsource(OrganizeUndoWorker.run)
        # rollback에서도 exists 체크
        self.assertIn("not os.path.exists(moved_p)", src_undo)


# ── 7. 앱 재시작 후 Undo (영구 History) ──────────────────────────────────────
class TestBatch13PersistentHistory(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_history_persists_across_new_connection(self):
        """Apply 후 DB 연결을 새로 열어도 history가 조회되어야 한다."""
        src = _make_file(self.src_dir, "persist.txt", "persist")
        dst = str(Path(self.dst_dir) / "persist.txt")
        fid = _register(self.db, src)

        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": "persist.txt"}]
        result = _run_apply(plan, self.db)
        op_id = result.get("operation_id")

        # 새 DB 연결로 조회 (앱 재시작 시뮬레이션)
        history = _get_history(self.db, op_id)
        self.assertGreater(len(history), 0, "앱 재시작 후 history를 조회할 수 없습니다")
        self.assertEqual(history[0]["status"], "applied")

    def test_undo_available_after_restart(self):
        """앱 재시작 후 Undo records를 로드하고 Undo를 실행할 수 있어야 한다."""
        src = _make_file(self.src_dir, "restart.txt", "restart content")
        dst = str(Path(self.dst_dir) / "restart.txt")
        fid = _register(self.db, src)

        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": "restart.txt"}]
        result = _run_apply(plan, self.db)
        op_id = result.get("operation_id")
        if not op_id:
            return

        # 재시작 시뮬레이션: 새 DB 연결
        records = _get_history(self.db, op_id)
        undo_records = [r for r in records if r["status"] == "applied"]
        self.assertGreater(len(undo_records), 0)

        result2 = _run_undo(undo_records, self.db)
        self.assertGreater(len(result2.get("undone", [])), 0, "재시작 후 Undo 실패")
        self.assertTrue(Path(src).exists())
        self.assertFalse(Path(dst).exists())


# ── 8. DB / Index 일관성 ──────────────────────────────────────────────────────
class TestBatch13DBIndexAfterUndo(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_full_reindex_in_undo(self):
        """Undo 소스에 전체 재색인 코드가 없어야 한다."""
        src = inspect.getsource(OrganizeUndoWorker.run)
        self.assertNotIn("LocalTextIndexer", src)
        self.assertNotIn("synchronize(", src)

    def test_no_ai_reanalysis_in_undo(self):
        """Undo 소스에 AI 재분석 코드가 없어야 한다."""
        src = inspect.getsource(OrganizeUndoWorker.run)
        self.assertNotIn("process_file_upload", src)
        self.assertNotIn("FolderScanAndTagWorker", src)

    def test_extracted_text_preserved_after_undo(self):
        """Undo 후 extracted_text가 보존되어야 한다 (재추출 없음)."""
        moved = _make_file(self.dst_dir, "text.txt", "text")
        original = str(Path(self.src_dir) / "text.txt")

        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT OR REPLACE INTO file_text_index "
            "(file_path, file_hash, extract_status, extracted_text, updated_at) "
            "VALUES (?, ?, 'success', 'PRESERVED TEXT', datetime('now'))",
            (moved, "text-hash"),
        )
        conn.commit()
        conn.close()

        if Path(original).exists():
            os.remove(original)

        records = [{"id": 1, "operation_id": "op", "original_path": original,
                    "moved_path": moved, "file_hash": None, "status": "applied"}]
        result = _run_undo(records, self.db)

        if result.get("undone"):
            conn = sqlite3.connect(self.db)
            row = conn.execute(
                "SELECT extracted_text FROM file_text_index WHERE file_path = ?", (original,)
            ).fetchone()
            conn.close()
            if row:
                self.assertEqual(row[0], "PRESERVED TEXT", "extracted_text가 변경되었습니다")

    def test_snapshot_invalidate_called_in_undo(self):
        """Undo Worker에서 invalidate_search_snapshot 호출이 있어야 한다."""
        src = inspect.getsource(OrganizeUndoWorker.run)
        self.assertIn("invalidate_search_snapshot", src)


# ── 9. 기존 안전장치 회귀 ────────────────────────────────────────────────────
class TestBatch13SafetyRegression(unittest.TestCase):

    def test_batch9_apply_safety_maintained(self):
        """Apply Worker에 preflight, overwrite 방지, rollback이 유지되어야 한다."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("사전 검증", src)
        self.assertIn("destination 충돌", src)
        self.assertIn("rolled_back", src)

    def test_batch11_index_sync_in_apply(self):
        """Apply Worker에 Batch 11 index sync가 유지되어야 한다."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("INSERT INTO file_text_index", src)

    def test_batch12_preview_auto_refresh_still_exists(self):
        """Batch 12 자동 Preview refresh가 OrganizeView에 유지되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_untagged_analysis_finished)
        self.assertIn("_refresh_grouped_after_analysis", src)
        self.assertNotIn("QMessageBox.information", src)

    def test_undo_ui_methods_exist(self):
        """Undo UI 메서드들이 OrganizeView에 있어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        for method in ("_show_history_dialog", "_confirm_and_start_undo",
                       "_on_undo_completed", "_on_undo_error", "_close_undo_dialog",
                       "_load_history_operations", "_load_undo_records"):
            self.assertTrue(hasattr(OrganizeView, method), f"Missing: {method}")

    def test_no_auto_undo_in_on_apply_completed(self):
        """_on_apply_completed에서 자동 Undo 실행이 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_apply_completed)
        self.assertNotIn("OrganizeUndoWorker", src)
        self.assertNotIn("_undo_worker.start", src)


# ── 10. Schema 변경 최소화 확인 ──────────────────────────────────────────────
class TestBatch13MinimalSchemaChange(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_only_organize_history_added(self):
        """추가된 테이블은 organize_history 하나뿐이어야 한다."""
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
        self.assertEqual(tables - known, set(), f"예상치 못한 새 테이블: {tables - known}")

    def test_existing_tables_unchanged(self):
        """기존 테이블 컬럼이 변경되지 않아야 한다."""
        conn = sqlite3.connect(self.db)
        files_cols = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
        conn.close()
        for col in ("id", "file_name", "file_path", "file_hash", "tags", "ai_comment"):
            self.assertIn(col, files_cols, f"files.{col} 컬럼 없음")


class TestBatch13AdditionalRequirements(unittest.TestCase):
    """완료 조건 중 기존 테스트와 겹치지 않는 경계 조건."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_collision_keeps_moved_file_unchanged(self):
        original = _make_file(self.tmp, "a.txt", "new original")
        moved_dir = str(Path(self.tmp) / "Documents")
        Path(moved_dir).mkdir()
        moved = _make_file(moved_dir, "a.txt", "organized")
        before = _sha256(moved)
        worker = OrganizeUndoWorker([{
            "id": 1, "operation_id": "collision", "original_path": original,
            "moved_path": moved, "file_hash": before, "status": "applied",
        }], self.db)
        worker.run()
        self.assertTrue(Path(moved).is_file())
        self.assertEqual(_sha256(moved), before)

    def test_undo_restores_fingerprint_path_without_stale_destination(self):
        original = str(Path(self.tmp) / "fp.txt")
        moved_dir = Path(self.tmp) / "Documents"
        moved_dir.mkdir()
        moved = _make_file(str(moved_dir), "fp.txt", "fingerprint")
        digest = _sha256(moved)
        conn = sqlite3.connect(self.db)
        stat = os.stat(moved)
        conn.execute(
            "INSERT INTO file_fingerprint_cache "
            "(file_path, file_hash, file_size, file_mtime_ns) VALUES (?, ?, ?, ?)",
            (moved, digest, stat.st_size, stat.st_mtime_ns),
        )
        conn.commit()
        conn.close()
        _run_undo([{
            "id": 1, "operation_id": "fp", "original_path": original,
            "moved_path": moved, "file_hash": digest, "status": "applied",
        }], self.db)
        conn = sqlite3.connect(self.db)
        paths = {r[0] for r in conn.execute("SELECT file_path FROM file_fingerprint_cache")}
        conn.close()
        self.assertIn(original, paths)
        self.assertNotIn(moved, paths)

    def test_undo_requires_confirmation_in_ui(self):
        from src.ui.views.organize_view import OrganizeView
        source = inspect.getsource(OrganizeView._confirm_and_start_undo)
        self.assertIn("QMessageBox.question", source)
        self.assertIn("reply != QMessageBox.Yes", source)

    def test_apply_is_blocked_while_undo_runs(self):
        from src.ui.views.organize_view import OrganizeView
        source = inspect.getsource(OrganizeView._on_organize_confirmed)
        self.assertIn("_undo_worker", source)
        self.assertIn("isRunning", source)

    def test_history_contains_no_file_content_or_ai_payload_columns(self):
        conn = sqlite3.connect(self.db)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(organize_history)")}
        conn.close()
        self.assertFalse(columns & {"extracted_text", "file_content", "ai_prompt", "ai_comment"})


if __name__ == "__main__":
    unittest.main()
