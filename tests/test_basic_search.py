import os
import shutil
import sqlite3
import time
import unittest
from pathlib import Path

from pptx import Presentation

from src.ui.views.search_view import SearchView
from src.utils.db_manager import FileRegistryManager
from src.utils.local_text_index import LocalTextIndexer
from src.utils.search_engine import SearchEngine


class CountingPptxExtractor:
    def __init__(self):
        self.calls = 0

    def _read_pptx(self, path):
        self.calls += 1
        presentation = Presentation(path)
        return "\n".join(
            shape.text.strip()
            for slide in presentation.slides
            for shape in slide.shapes
            if hasattr(shape, "text") and shape.text.strip()
        )


class BasicSearchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("tests/fixtures/basic_search_runtime").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = str(self.root / "search.db")
        self.registry = FileRegistryManager(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _pptx(self, name="random_1234.pptx", text="중소기업 경영 컨설팅 결과 보고서"):
        path = self.root / name
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        textbox = slide.shapes.add_textbox(0, 0, 5000000, 1000000)
        textbox.text = text
        presentation.save(path)
        return str(path)

    def _cache(self, path):
        stat = os.stat(path)
        self.registry.cache_file_fingerprints([{
            "file_path": path,
            "file_hash": self.registry.compute_file_hash(path),
            "file_size": stat.st_size,
            "file_mtime_ns": stat.st_mtime_ns,
        }])

    def test_rules_parser_normalizes_presentation_aliases(self):
        expected = {"query_keywords": ["컨설팅"], "target_extension": ["ppt", "pptx"]}
        for query in ("컨설팅 관련 피피티 찾아줘", "컨설팅 PPT 찾아줘", "컨설팅 파워포인트"):
            parsed = SearchView._parse_natural_query(None, query)
            self.assertEqual(parsed["query_keywords"], expected["query_keywords"])
            self.assertEqual(parsed["target_extension"], expected["target_extension"])
        parsed = SearchView._parse_natural_query(None, "PPTX 찾아줘")
        self.assertEqual(parsed["query_keywords"], [])
        self.assertEqual(parsed["target_extension"], ["pptx"])
        self.assertEqual(
            SearchView._parse_natural_query(None, "발표자료 찾아줘")["query_keywords"],
            ["발표자료"],
        )

    def test_cache_only_pptx_is_found_by_local_slide_text(self):
        path = self._pptx()
        self._cache(path)
        stats = LocalTextIndexer(self.db_path).synchronize([path])
        self.assertEqual(stats["success"], 1)
        engine = SearchEngine(self.db_path)
        parsed = SearchView._parse_natural_query(None, "컨설팅 관련 피피티 찾아줘")
        result = engine.process_query_result(parsed)
        self.assertEqual([row[2] for row in result["data"]], [path])
        metadata = engine.get_result_metadata(path)
        self.assertEqual(metadata["analysis_status"], "pending")
        self.assertIn("text", metadata["match_source"])

    def test_path_search_and_files_row_deduplication(self):
        folder = self.root / "컨설팅"
        folder.mkdir()
        path = self._pptx(name="컨설팅/ordinary.pptx", text="unrelated")
        self._cache(path)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO files(file_name,file_path,ai_comment,category) VALUES(?,?,?,?)",
                ("ordinary.pptx", path, "분석됨", "#업무"),
            )
            conn.commit()
        finally:
            conn.close()
        engine = SearchEngine(self.db_path)
        rows, _ = engine.search_files_smart(["컨설팅"], ["pptx"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(engine.get_result_metadata(path)["analysis_status"], "analyzed")
        self.assertIn("path", engine.get_result_metadata(path)["match_source"])

    def test_text_index_is_incremental_and_prunes_deleted_files(self):
        path = self._pptx()
        self._cache(path)
        extractor = CountingPptxExtractor()
        indexer = LocalTextIndexer(self.db_path, extractor=extractor)
        first = indexer.synchronize([path])
        second = indexer.synchronize([path])
        self.assertEqual(first["indexed"], 1)
        self.assertEqual(second["indexed"], 0)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(extractor.calls, 1)

        presentation = Presentation(path)
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.add_textbox(0, 0, 5000000, 1000000).text = "변경된 본문"
        presentation.save(path)
        future_ns = max(os.stat(path).st_mtime_ns + 1_000_000, time.time_ns())
        os.utime(path, ns=(future_ns, future_ns))
        changed = indexer.synchronize([path])
        self.assertEqual(changed["indexed"], 1)
        self.assertEqual(extractor.calls, 2)

        os.unlink(path)
        deleted = indexer.synchronize([])
        self.assertEqual(deleted["deleted"], 1)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM file_text_index").fetchone()[0], 0)
        finally:
            conn.close()

    def test_legacy_ppt_is_searchable_by_name_without_content_extraction(self):
        path = self.root / "컨설팅_구형자료.ppt"
        path.write_bytes(b"legacy-ppt-placeholder")
        stats = LocalTextIndexer(self.db_path).synchronize([str(path)])
        self.assertEqual(stats["unsupported"], 1)
        engine = SearchEngine(self.db_path)
        rows, _ = engine.search_files_smart(["컨설팅"], ["ppt", "pptx"])
        self.assertEqual([row[2] for row in rows], [str(path)])
        self.assertEqual(engine.get_result_metadata(str(path))["extract_status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
