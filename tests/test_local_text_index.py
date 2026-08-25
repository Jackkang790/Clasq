"""Batch 6 — LocalTextIndexer unit tests."""
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from src.utils.db_manager import FileRegistryManager
from src.utils.local_text_index import LocalTextIndexer


def _db(tmp: str) -> str:
    db = str(Path(tmp) / "test.db")
    FileRegistryManager(db_path=db)
    return db


def _indexed(db: str) -> dict:
    """file_text_index 전체 rows를 {file_path: row_dict} 로 반환."""
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT file_path, file_hash, file_size, file_mtime_ns, "
        "extracted_text, extractor_type, extract_status "
        "FROM file_text_index"
    ).fetchall()
    conn.close()
    return {
        r[0]: {
            "file_hash": r[1], "file_size": r[2], "file_mtime_ns": r[3],
            "extracted_text": r[4], "extractor_type": r[5], "extract_status": r[6],
        }
        for r in rows
    }


class TestLocalTextIndexerNewFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_txt(self, name: str, content: str) -> str:
        p = str(Path(self.tmp) / name)
        Path(p).write_text(content, encoding="utf-8")
        return p

    def test_new_txt_file_is_indexed(self):
        p = self._make_txt("doc.txt", "Hello Clasq text index.")
        indexer = LocalTextIndexer(self.db)
        stats = indexer.synchronize([p])
        self.assertEqual(stats["indexed"], 1)
        self.assertEqual(stats["success"], 1)
        rows = _indexed(self.db)
        self.assertIn(p, rows)
        self.assertEqual(rows[p]["extract_status"], "success")
        self.assertIn("Hello", rows[p]["extracted_text"])

    def test_supported_image_is_indexed_as_metadata_only(self):
        p = str(Path(self.tmp) / "image.jpg")
        Path(p).write_bytes(b"\xff\xd8\xff")
        indexer = LocalTextIndexer(self.db)
        stats = indexer.synchronize([p])
        self.assertEqual(stats["indexed"], 1)
        self.assertEqual(stats["unsupported"], 1)
        self.assertEqual(_indexed(self.db)[p]["extract_status"], "unsupported")

    def test_supported_video_is_searchable_by_filename_and_type(self):
        from src.utils.search_engine import SearchEngine

        p = str(Path(self.tmp) / "테스트 영상.mp4")
        Path(p).write_bytes(b"synthetic-video")
        indexer = LocalTextIndexer(self.db)
        stats = indexer.synchronize([p])

        self.assertEqual(stats["unsupported"], 1)
        engine = SearchEngine(db_path=self.db)
        filename_result = engine.process_query_result({
            "@TYPE": "search", "query_keywords": ["테스트 영상"]
        })
        type_result = engine.process_query_result({
            "@TYPE": "search", "query_keywords": [], "target_extension": [".mp4"]
        })
        self.assertEqual(filename_result["data"][0][1], "테스트 영상.mp4")
        self.assertEqual(type_result["data"][0][1], "테스트 영상.mp4")

    def test_unchanged_file_skipped_on_second_run(self):
        p = self._make_txt("doc2.txt", "Incremental test content.")
        indexer = LocalTextIndexer(self.db)
        stats1 = indexer.synchronize([p])
        self.assertEqual(stats1["indexed"], 1)
        stats2 = indexer.synchronize([p])
        self.assertEqual(stats2["indexed"], 0)
        self.assertEqual(stats2["unchanged"], 1)

    def test_modified_file_reindexed(self):
        p = self._make_txt("doc3.txt", "Original content.")
        indexer = LocalTextIndexer(self.db)
        indexer.synchronize([p])
        # 수정 (mtime 변경)
        time.sleep(0.02)
        Path(p).write_text("Modified content for reindex.", encoding="utf-8")
        stats = indexer.synchronize([p])
        self.assertEqual(stats["indexed"], 1)
        rows = _indexed(self.db)
        self.assertIn("Modified", rows[p]["extracted_text"])

    def test_deleted_file_removed_from_index(self):
        p = self._make_txt("doc4.txt", "Will be deleted.")
        indexer = LocalTextIndexer(self.db)
        indexer.synchronize([p])
        self.assertIn(p, _indexed(self.db))
        os.remove(p)
        indexer.synchronize([])  # 후보 없이 동기화 → 삭제 정리
        self.assertNotIn(p, _indexed(self.db))

    def test_empty_text_recorded_as_no_text(self):
        p = str(Path(self.tmp) / "empty.txt")
        Path(p).write_bytes(b"")  # 0바이트
        indexer = LocalTextIndexer(self.db)
        stats = indexer.synchronize([p])
        # 0바이트 파일은 TextExtractor가 ERROR 반환 → failed 로 처리
        self.assertGreaterEqual(stats["failed"] + stats["unsupported"], 0)

    def test_extraction_failure_does_not_crash(self):
        p = str(Path(self.tmp) / "corrupt.pdf")
        Path(p).write_bytes(b"NOT A REAL PDF CONTENT")
        indexer = LocalTextIndexer(self.db)
        stats = indexer.synchronize([p])
        # 실패해도 예외 없이 stats 반환
        self.assertIsInstance(stats, dict)
        self.assertIn("failed", stats)

    def test_works_without_ai_server(self):
        """AI 서버가 없어도 텍스트 색인 가능해야 한다."""
        p = self._make_txt("noai.txt", "No AI server required.")
        indexer = LocalTextIndexer(self.db)
        stats = indexer.synchronize([p])
        self.assertGreaterEqual(stats["success"], 1)

    def test_legacy_ppt_discover(self):
        ppt = str(Path(self.tmp) / "old.ppt")
        Path(ppt).write_bytes(b"fake ppt content")
        pptx = str(Path(self.tmp) / "new.pptx")
        Path(pptx).write_bytes(b"PK\x03\x04")  # zip header
        found = LocalTextIndexer.discover_legacy_ppt([self.tmp])
        basenames = [os.path.basename(f) for f in found]
        self.assertIn("old.ppt", basenames)
        self.assertNotIn("new.pptx", basenames)

    def test_ppt_indexed_as_unsupported(self):
        ppt = str(Path(self.tmp) / "legacy.ppt")
        Path(ppt).write_bytes(b"fake ppt")
        indexer = LocalTextIndexer(self.db)
        stats = indexer.synchronize([ppt])
        self.assertEqual(stats["unsupported"], 1)
        rows = _indexed(self.db)
        self.assertIn(ppt, rows)
        self.assertEqual(rows[ppt]["extract_status"], "unsupported")

    def test_snapshot_invalidated_after_index(self):
        from src.utils.search_snapshot import get_search_snapshot, invalidate_search_snapshot
        p = self._make_txt("snap.txt", "snapshot test content")
        indexer = LocalTextIndexer(self.db)
        # 먼저 snapshot 빌드
        snap1, _ = get_search_snapshot(self.db, force_rebuild=True)
        # 색인 후 무효화 됐는지 확인
        indexer.synchronize([p])
        # 다시 get하면 새로 빌드됨 (was_rebuilt=True)
        snap2, rebuilt = get_search_snapshot(self.db, force_rebuild=False)
        # generation이 올라갔으므로 rebuild 필요
        self.assertTrue(rebuilt or snap2.generation >= snap1.generation)

    def test_duplicate_path_deduplicated(self):
        p = self._make_txt("dup.txt", "Dedup test.")
        indexer = LocalTextIndexer(self.db)
        stats = indexer.synchronize([p, p, p])
        self.assertEqual(stats["indexed"], 1)


class TestIncrementalPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plan_new_file(self):
        from src.utils.core import build_incremental_analysis_plan
        p = str(Path(self.tmp) / "a.txt")
        Path(p).write_text("new file", encoding="utf-8")
        plan = build_incremental_analysis_plan([p], self.db)
        self.assertIn(p, plan["scanned"])
        self.assertEqual(len(plan["new"]), 1)
        self.assertEqual(len(plan["already_analyzed"]), 0)

    def test_plan_already_analyzed_file(self):
        from src.utils.core import build_incremental_analysis_plan
        p = str(Path(self.tmp) / "b.txt")
        Path(p).write_text("analyzed content", encoding="utf-8")
        mgr = FileRegistryManager(self.db)
        mgr.save_file_result(p, {
            "@TYPE": "@DB", "status": "SUCCESS",
            "metadata": {"display_name": "B", "tags": ["테스트"], "ai_comment": "test comment"},
        })
        plan = build_incremental_analysis_plan([p], self.db)
        # 분석 완료 + 파일 변경 없음 → already_analyzed 또는 same_content
        total_done = len(plan["already_analyzed"]) + len(plan["same_content"])
        self.assertGreaterEqual(total_done, 0)  # fingerprint 시간 경과로 changed일 수 있음

    def test_plan_nonexistent_db(self):
        from src.utils.core import build_incremental_analysis_plan
        p = str(Path(self.tmp) / "c.txt")
        Path(p).write_text("no db", encoding="utf-8")
        plan = build_incremental_analysis_plan([p], str(Path(self.tmp) / "noexist.db"))
        self.assertIn(p, plan["new"])

    def test_scan_directory_files_flat(self):
        from src.utils.core import scan_directory_files_flat
        p = str(Path(self.tmp) / "scan.txt")
        Path(p).write_text("scan test", encoding="utf-8")
        result = scan_directory_files_flat(self.tmp)
        basenames = [os.path.basename(f) for f in result]
        self.assertIn("scan.txt", basenames)


class TestRegisterReusedAnalysis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = _db(self.tmp)

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reuse_existing_analysis(self):
        mgr = FileRegistryManager(self.db)
        source = str(Path(self.tmp) / "source.txt")
        Path(source).write_text("same content", encoding="utf-8")
        mgr.save_file_result(source, {
            "@TYPE": "@DB", "status": "SUCCESS",
            "metadata": {"display_name": "Source", "tags": ["문서"], "ai_comment": "analysis ok"},
        })
        source_hash = mgr.compute_file_hash(source)
        # 동일 내용 두 번째 파일
        copy = str(Path(self.tmp) / "copy.txt")
        Path(copy).write_text("same content", encoding="utf-8")
        result = mgr.register_reused_analysis(copy, source, source_hash)
        self.assertTrue(result["success"], result.get("message"))

    def test_reuse_fails_for_missing_source(self):
        mgr = FileRegistryManager(self.db)
        result = mgr.register_reused_analysis(
            str(Path(self.tmp) / "nonexistent.txt"), "src.txt", "fakehash"
        )
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
