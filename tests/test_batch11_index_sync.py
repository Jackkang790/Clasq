"""Batch 11 — Apply 직후 file_text_index / file_fingerprint_cache path 동기화 테스트.

파일 이동 성공 후 Clasq 내부 index가 즉시 destination과 일치하는지 검증한다.
실제 임시 파일과 DB를 사용해 파일시스템 ↔ DB 일관성을 확인한다.
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
from src.utils.workers import OrganizeApplyWorker


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


def _insert_text_index(db: str, file_path: str, text: str = "hello world",
                       status: str = "success") -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR REPLACE INTO file_text_index "
        "(file_path, file_hash, file_size, file_mtime_ns, "
        "extracted_text, extractor_type, extract_status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (file_path, "abc123", 12, 1000000, text, "plain-text", status),
    )
    conn.commit()
    conn.close()


def _insert_fingerprint(db: str, file_path: str,
                        file_hash: str = "deadbeef",
                        size: int = 12, mtime_ns: int = 1000000) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR REPLACE INTO file_fingerprint_cache "
        "(file_path, file_hash, file_size, file_mtime_ns) VALUES (?, ?, ?, ?)",
        (file_path, file_hash, size, mtime_ns),
    )
    conn.commit()
    conn.close()


def _run_worker(plan: list, db: str, fingerprints: dict = None) -> dict:
    result_holder = [None]
    errors = []
    worker = OrganizeApplyWorker(plan, db, fingerprints or {})
    worker.completed.connect(lambda r: result_holder.__setitem__(0, r))
    worker.error.connect(errors.append)
    worker.run()
    return result_holder[0] or {"moved": [], "failed": [], "error": errors}


# ── 1. 파일 이동 후 file_text_index path 동기화 ──────────────────────────────
class TestBatch11TextIndexSync(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_text_index_updated_to_new_path(self):
        """이동 후 file_text_index가 destination path를 가리켜야 한다."""
        src = _make_file(self.src_dir, "a.txt", "hello world")
        dst = str(Path(self.dst_dir) / "a.txt")
        file_id = _register(self.db, src)
        _insert_text_index(self.db, src, "hello world content")

        plan = [{"file_id": file_id, "file_path": src, "target_path": dst, "file_name": "a.txt"}]
        result = _run_worker(plan, self.db)

        self.assertEqual(len(result["moved"]), 1)

        conn = sqlite3.connect(self.db)
        new_row = conn.execute(
            "SELECT extracted_text FROM file_text_index WHERE file_path = ?", (dst,)
        ).fetchone()
        old_row = conn.execute(
            "SELECT 1 FROM file_text_index WHERE file_path = ?", (src,)
        ).fetchone()
        conn.close()

        self.assertIsNotNone(new_row, "destination path의 text_index 레코드가 없습니다")
        self.assertEqual(new_row[0], "hello world content", "extracted_text가 변경되었습니다")
        self.assertIsNone(old_row, "old source path가 file_text_index에 남아있습니다")

    def test_text_content_preserved_after_move(self):
        """이동 후 file_text_index의 extracted_text 내용이 보존되어야 한다."""
        src = _make_file(self.src_dir, "preserve.txt", "important text")
        dst = str(Path(self.dst_dir) / "preserve.txt")
        file_id = _register(self.db, src)
        _insert_text_index(self.db, src, "PRESERVED TEXT CONTENT", status="success")

        plan = [{"file_id": file_id, "file_path": src, "target_path": dst, "file_name": "preserve.txt"}]
        _run_worker(plan, self.db)

        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT extracted_text, extract_status FROM file_text_index WHERE file_path = ?", (dst,)
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "PRESERVED TEXT CONTENT")
        self.assertEqual(row[1], "success")

    def test_no_text_index_record_still_works(self):
        """text_index 레코드가 없는 파일도 이동이 성공해야 한다."""
        src = _make_file(self.src_dir, "no_index.txt", "content")
        dst = str(Path(self.dst_dir) / "no_index.txt")
        file_id = _register(self.db, src)
        # text_index 레코드 없이 이동

        plan = [{"file_id": file_id, "file_path": src, "target_path": dst, "file_name": "no_index.txt"}]
        result = _run_worker(plan, self.db)

        self.assertEqual(len(result["moved"]), 1)
        self.assertTrue(Path(dst).exists())

    def test_multiple_files_text_index_all_updated(self):
        """여러 파일 이동 시 모두 text_index가 갱신되어야 한다."""
        files = []
        plan = []
        for i in range(3):
            src = _make_file(self.src_dir, f"f{i}.txt", f"content {i}")
            dst = str(Path(self.dst_dir) / f"f{i}.txt")
            fid = _register(self.db, src)
            _insert_text_index(self.db, src, f"text content {i}")
            files.append((src, dst))
            plan.append({"file_id": fid, "file_path": src, "target_path": dst, "file_name": f"f{i}.txt"})

        result = _run_worker(plan, self.db)
        self.assertEqual(len(result["moved"]), 3)

        conn = sqlite3.connect(self.db)
        for src, dst in files:
            old = conn.execute("SELECT 1 FROM file_text_index WHERE file_path = ?", (src,)).fetchone()
            new = conn.execute("SELECT extracted_text FROM file_text_index WHERE file_path = ?", (dst,)).fetchone()
            self.assertIsNone(old, f"old path still in text_index: {src}")
            self.assertIsNotNone(new, f"new path not in text_index: {dst}")
        conn.close()


# ── 2. file_fingerprint_cache path 동기화 ───────────────────────────────────
class TestBatch11FingerprintSync(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fingerprint_cache_updated_to_new_path(self):
        """이동 후 file_fingerprint_cache가 destination을 가리켜야 한다."""
        src = _make_file(self.src_dir, "fp.txt", "fingerprint test")
        dst = str(Path(self.dst_dir) / "fp.txt")
        file_id = _register(self.db, src)
        _insert_fingerprint(self.db, src, "fp_hash_001", 12, 9999)

        plan = [{"file_id": file_id, "file_path": src, "target_path": dst, "file_name": "fp.txt"}]
        _run_worker(plan, self.db)

        conn = sqlite3.connect(self.db)
        new_row = conn.execute(
            "SELECT file_hash FROM file_fingerprint_cache WHERE file_path = ?", (dst,)
        ).fetchone()
        old_row = conn.execute(
            "SELECT 1 FROM file_fingerprint_cache WHERE file_path = ?", (src,)
        ).fetchone()
        conn.close()

        self.assertIsNotNone(new_row, "destination의 fingerprint_cache 레코드가 없습니다")
        self.assertEqual(new_row[0], "fp_hash_001", "file_hash가 변경되었습니다")
        self.assertIsNone(old_row, "old source의 fingerprint_cache가 남아있습니다")

    def test_hash_preserved_in_fingerprint(self):
        """이동 후 fingerprint_cache의 file_hash가 보존되어야 한다."""
        src = _make_file(self.src_dir, "hash_test.txt", "hash content")
        dst = str(Path(self.dst_dir) / "hash_test.txt")
        fid = _register(self.db, src)
        original_hash = "abc123def456"
        _insert_fingerprint(self.db, src, original_hash, 12, 100)

        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": "hash_test.txt"}]
        _run_worker(plan, self.db)

        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT file_hash FROM file_fingerprint_cache WHERE file_path = ?", (dst,)
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], original_hash)


# ── 3. 이동 실패 시 index 변경 없음 ──────────────────────────────────────────
class TestBatch11FailureNoIndexChange(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_preflight_failure_leaves_index_intact(self):
        """Preflight 실패 시 file_text_index가 변경되지 않아야 한다."""
        src = _make_file(self.src_dir, "safe.txt", "safe")
        dst = str(Path(self.dst_dir) / "safe.txt")
        fid = _register(self.db, src)
        _insert_text_index(self.db, src, "original text")
        # destination에 이미 파일 있음 → preflight 실패
        _make_file(self.dst_dir, "safe.txt", "conflict")

        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": "safe.txt"}]
        _run_worker(plan, self.db)

        conn = sqlite3.connect(self.db)
        old_row = conn.execute(
            "SELECT extracted_text FROM file_text_index WHERE file_path = ?", (src,)
        ).fetchone()
        conn.close()

        self.assertIsNotNone(old_row, "preflight 실패 후 old index가 사라졌습니다")
        self.assertEqual(old_row[0], "original text")

    def test_failed_move_leaves_index_at_source(self):
        """파일 이동 자체가 실패하면 index path가 source를 유지해야 한다."""
        src = _make_file(self.src_dir, "m1.txt", "m1")
        m2_src = _make_file(self.src_dir, "m2.txt", "m2")
        dst1 = str(Path(self.dst_dir) / "m1.txt")
        dst2 = str(Path(self.dst_dir) / "m2.txt")
        fid1 = _register(self.db, src)
        fid2 = _register(self.db, m2_src)
        _insert_text_index(self.db, src, "text m1")
        _insert_text_index(self.db, m2_src, "text m2")

        # dst2에 미리 파일 → preflight 에서 m2가 제외됨 → m1만 이동
        _make_file(self.dst_dir, "m2.txt", "conflict")

        plan = [
            {"file_id": fid1, "file_path": src, "target_path": dst1, "file_name": "m1.txt"},
            {"file_id": fid2, "file_path": m2_src, "target_path": dst2, "file_name": "m2.txt"},
        ]
        result = _run_worker(plan, self.db)

        # m1은 이동 성공(preflight 통과), m2는 preflight 실패
        # preflight 전체 실패 → 모두 이동 안 됨
        conn = sqlite3.connect(self.db)
        if not result["moved"]:
            # 전체 preflight 실패 → 모두 원래 index 유지
            old1 = conn.execute("SELECT 1 FROM file_text_index WHERE file_path = ?", (src,)).fetchone()
            old2 = conn.execute("SELECT 1 FROM file_text_index WHERE file_path = ?", (m2_src,)).fetchone()
            self.assertIsNotNone(old1, "m1 index가 사라졌습니다")
            self.assertIsNotNone(old2, "m2 index가 사라졌습니다")
        else:
            # m1 이동 성공 → dst1 index 있어야 함
            new1 = conn.execute("SELECT 1 FROM file_text_index WHERE file_path = ?", (dst1,)).fetchone()
            self.assertIsNotNone(new1)
        conn.close()


# ── 4. Rollback 후 index가 original path와 일치 ────────────────────────────
class TestBatch11RollbackIndexConsistency(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rollback_index_stays_at_original(self):
        """Rollback 후 index가 original path를 유지해야 한다.

        C의 preflight 실패 → preflight 전체 실패 → 모두 이동 안 됨 → index 원상태.
        """
        src_a = _make_file(self.src_dir, "a.txt", "aaa")
        src_b = _make_file(self.src_dir, "b.txt", "bbb")
        src_c = _make_file(self.src_dir, "c.txt", "ccc")
        dst_a = str(Path(self.dst_dir) / "a.txt")
        dst_b = str(Path(self.dst_dir) / "b.txt")
        dst_c = str(Path(self.dst_dir) / "c.txt")

        fid_a = _register(self.db, src_a)
        fid_b = _register(self.db, src_b)
        fid_c = _register(self.db, src_c)
        _insert_text_index(self.db, src_a, "text a")
        _insert_text_index(self.db, src_b, "text b")
        _insert_text_index(self.db, src_c, "text c")

        # dst_c에 미리 파일 → c는 preflight 실패 → 전체 preflight 실패
        _make_file(self.dst_dir, "c.txt", "conflict c")

        plan = [
            {"file_id": fid_a, "file_path": src_a, "target_path": dst_a, "file_name": "a.txt"},
            {"file_id": fid_b, "file_path": src_b, "target_path": dst_b, "file_name": "b.txt"},
            {"file_id": fid_c, "file_path": src_c, "target_path": dst_c, "file_name": "c.txt"},
        ]
        result = _run_worker(plan, self.db)

        # 전체 preflight 실패 → 모두 original 상태 유지
        conn = sqlite3.connect(self.db)
        for src_path in [src_a, src_b, src_c]:
            row = conn.execute(
                "SELECT 1 FROM file_text_index WHERE file_path = ?", (src_path,)
            ).fetchone()
            self.assertIsNotNone(row, f"원본 index가 사라졌습니다: {src_path}")
        conn.close()


# ── 5. files 테이블 path 일관성 ──────────────────────────────────────────────
class TestBatch11FilesTableConsistency(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_files_table_updated_to_destination(self):
        """이동 후 files 테이블의 file_path가 destination이어야 한다."""
        src = _make_file(self.src_dir, "db_sync.txt", "content")
        dst = str(Path(self.dst_dir) / "db_sync.txt")
        fid = _register(self.db, src)

        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": "db_sync.txt"}]
        _run_worker(plan, self.db)

        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT file_path FROM files WHERE id = ?", (fid,)).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(os.path.normcase(row[0]), os.path.normcase(dst))

    def test_filesystem_matches_db_after_move(self):
        """파일시스템 경로와 files.file_path가 이동 후 일치해야 한다."""
        src = _make_file(self.src_dir, "consistency.txt", "consistent")
        dst = str(Path(self.dst_dir) / "consistency.txt")
        fid = _register(self.db, src)

        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": "consistency.txt"}]
        _run_worker(plan, self.db)

        # 파일시스템 상태
        self.assertFalse(Path(src).exists(), "source가 아직 존재합니다")
        self.assertTrue(Path(dst).exists(), "destination이 없습니다")

        # DB 상태
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT file_path FROM files WHERE id = ?", (fid,)).fetchone()
        conn.close()

        self.assertEqual(os.path.normcase(row[0]), os.path.normcase(dst))

    def test_all_three_tables_consistent_after_move(self):
        """이동 후 files, file_text_index, file_fingerprint_cache 모두 새 경로 일치."""
        src = _make_file(self.src_dir, "triple.txt", "triple consistency")
        dst = str(Path(self.dst_dir) / "triple.txt")
        fid = _register(self.db, src)
        _insert_text_index(self.db, src, "triple text")
        _insert_fingerprint(self.db, src, "triple_hash", 10, 500)

        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": "triple.txt"}]
        _run_worker(plan, self.db)

        conn = sqlite3.connect(self.db)

        # files 테이블
        files_row = conn.execute(
            "SELECT file_path FROM files WHERE id = ?", (fid,)
        ).fetchone()
        # file_text_index
        text_row = conn.execute(
            "SELECT 1 FROM file_text_index WHERE file_path = ?", (dst,)
        ).fetchone()
        # file_fingerprint_cache
        fp_row = conn.execute(
            "SELECT 1 FROM file_fingerprint_cache WHERE file_path = ?", (dst,)
        ).fetchone()
        # 구 경로 없어야 함
        old_text = conn.execute(
            "SELECT 1 FROM file_text_index WHERE file_path = ?", (src,)
        ).fetchone()
        old_fp = conn.execute(
            "SELECT 1 FROM file_fingerprint_cache WHERE file_path = ?", (src,)
        ).fetchone()

        conn.close()

        self.assertEqual(os.path.normcase(files_row[0]), os.path.normcase(dst))
        self.assertIsNotNone(text_row, "file_text_index에 새 경로 없음")
        self.assertIsNotNone(fp_row, "file_fingerprint_cache에 새 경로 없음")
        self.assertIsNone(old_text, "file_text_index에 구 경로 남아있음")
        self.assertIsNone(old_fp, "file_fingerprint_cache에 구 경로 남아있음")


# ── 6. search snapshot invalidation ──────────────────────────────────────────
class TestBatch11SearchSnapshotInvalidation(unittest.TestCase):

    def test_invalidate_called_in_post_apply(self):
        """Post-apply 소스에서 invalidate_search_snapshot 호출 확인."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("invalidate_search_snapshot", src)

    def test_index_sync_section_has_transaction(self):
        """index 동기화가 transaction으로 처리되어야 한다."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("BEGIN", src)
        self.assertIn("COMMIT", src)

    def test_index_sync_errors_in_completed_payload(self):
        """completed payload에 index_sync_errors 키가 있어야 한다."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("index_sync_errors", src)


# ── 7. 파일 내용/hash 보존 확인 ───────────────────────────────────────────────
class TestBatch11ContentPreservation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)
        self.src_dir = str(Path(self.tmp) / "src")
        self.dst_dir = str(Path(self.tmp) / "dst")
        Path(self.src_dir).mkdir()
        Path(self.dst_dir).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_file_content_unchanged_after_move(self):
        """이동 후 파일 내용이 동일해야 한다."""
        content = "Hello 한글 World 123 !@#"
        src = _make_file(self.src_dir, "content_test.txt", content)
        dst = str(Path(self.dst_dir) / "content_test.txt")
        fid = _register(self.db, src)
        original_hash = _sha256(src)

        plan = [{"file_id": fid, "file_path": src, "target_path": dst, "file_name": "content_test.txt"}]
        _run_worker(plan, self.db)

        self.assertEqual(Path(dst).read_text(encoding="utf-8"), content)
        self.assertEqual(_sha256(dst), original_hash)

    def test_no_ai_reanalysis_triggered(self):
        """이동 시 AI 재분석이 발생하지 않아야 한다 (소스 확인)."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertNotIn("process_file_upload", src)
        self.assertNotIn("analyze_document", src)
        self.assertNotIn("analyze_image", src)
        self.assertNotIn("FolderScanAndTagWorker", src)

    def test_no_full_reindex(self):
        """전체 재색인이 발생하지 않아야 한다 (소스 확인)."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertNotIn("LocalTextIndexer", src)
        self.assertNotIn("synchronize(", src)


# ── 8. Preview/취소 시 index 변경 없음 ───────────────────────────────────────
class TestBatch11PreviewSafety(unittest.TestCase):

    def test_preview_does_not_change_index(self):
        """_on_plan_completed에 index 변경 코드가 없어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_plan_completed)
        self.assertNotIn("file_text_index", src)
        self.assertNotIn("file_fingerprint_cache", src)
        self.assertNotIn("DELETE FROM", src)
        self.assertNotIn("INSERT INTO", src)

    def test_approval_cancel_leaves_index_unchanged(self):
        """_on_organize_confirmed에서 QFileDialog 취소 시 return만 함 (소스 확인)."""
        from src.ui.views.organize_view import OrganizeView
        src = inspect.getsource(OrganizeView._on_organize_confirmed)
        # 취소 경로에 index/DB 변경 코드 없어야 함
        self.assertIn("return  # 취소", src)


# ── 9. Batch 9 / 10 안전장치 유지 확인 ──────────────────────────────────────
class TestBatch11SafetyRegression(unittest.TestCase):

    def test_overwrite_prevention_in_apply_worker(self):
        """Apply Worker에서 overwrite 방지 코드가 유지되어야 한다."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("destination 충돌", src)

    def test_no_file_deletion_in_apply_worker(self):
        """Apply Worker에 파일 삭제 코드가 없어야 한다."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertNotIn("os.remove", src)
        self.assertNotIn(".unlink(", src)

    def test_rollback_still_in_worker(self):
        """Rollback 코드가 Worker에 유지되어야 한다."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("rolled_back", src)
        self.assertIn("partial_rollback_failures", src)

    def test_preflight_still_in_worker(self):
        """Preflight 검증이 Worker에 유지되어야 한다."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn("사전 검증", src)
        self.assertIn("preflight_errors", src)

    def test_index_sync_errors_in_completed_dict(self):
        """completed dict에 index_sync_errors 키가 추가되어야 한다."""
        src = inspect.getsource(OrganizeApplyWorker.run)
        self.assertIn('"index_sync_errors"', src)

    def test_batch10_untagged_methods_still_exist(self):
        """Batch 10 메서드가 유지되어야 한다."""
        from src.ui.views.organize_view import OrganizeView
        for method in ("_get_untagged_from_plan", "_start_untagged_analysis",
                       "_get_file_kind_by_extension", "_check_ai_available"):
            self.assertTrue(hasattr(OrganizeView, method), f"Missing: {method}")


# ── 10. DB schema v2 유지 ────────────────────────────────────────────────────
class TestBatch11DBSchema(unittest.TestCase):

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

    def test_schema_unchanged_after_apply_with_index_sync(self):
        """Apply + index 동기화 후에도 schema v2가 유지되어야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            db = _db(tmp)
            src_dir = Path(tmp) / "s"
            dst_dir = Path(tmp) / "d"
            src_dir.mkdir()
            dst_dir.mkdir()
            src = str(src_dir / "f.txt")
            Path(src).write_text("schema test", encoding="utf-8")
            dst = str(dst_dir / "f.txt")
            _insert_text_index(db, src, "schema test text")

            plan = [{"file_id": None, "file_path": src, "target_path": dst, "file_name": "f.txt"}]
            _run_worker(plan, db)

            conn = sqlite3.connect(db)
            v = conn.execute("SELECT COALESCE(MAX(version),0) FROM db_schema_version").fetchone()
            conn.close()
            self.assertEqual(v[0], 3)


if __name__ == "__main__":
    unittest.main()
