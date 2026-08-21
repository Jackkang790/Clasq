import os
import shutil
import sqlite3
import time
import unittest
from pathlib import Path
from unittest import mock

from src.utils.db_manager import FileRegistryManager
from src.utils.local_text_index import LocalTextIndexer
from src.utils.search_engine import SearchEngine
from src.utils.search_snapshot import invalidate_search_snapshot


class MutableExtractor:
    def __init__(self, text):
        self.text = text

    def extract_for_index(self, _path):
        return self.text, "SUCCESS"


class SearchSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("tests/fixtures/search_snapshot_runtime").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = str(self.root / "snapshot.db")
        self.registry = FileRegistryManager(self.db_path, duplicate_policy="keep")
        invalidate_search_snapshot(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _cache(self, path):
        stat = os.stat(path)
        self.registry.cache_file_fingerprints([{
            "file_path": str(path),
            "file_hash": self.registry.compute_file_hash(str(path)),
            "file_size": stat.st_size,
            "file_mtime_ns": stat.st_mtime_ns,
        }])

    def test_cold_build_then_warm_search_avoids_db_and_file_normalization(self):
        path = self.root / "LoboDoc_plan.txt"
        path.write_text("business plan", encoding="utf-8")
        self._cache(path)
        engine = SearchEngine(self.db_path)
        engine.search_files_smart(["lobodoc"])
        self.assertTrue(engine.last_search_performance["snapshot_built"])

        with mock.patch("src.utils.search_snapshot.sqlite3.connect", wraps=sqlite3.connect) as connect:
            with mock.patch("src.utils.search_snapshot.search_variants") as normalize:
                rows, _ = engine.search_files_smart(["lobodoc"])
        self.assertEqual(rows[0][2], str(path))
        self.assertEqual(connect.call_count, 0)
        self.assertEqual(normalize.call_count, 0)
        self.assertFalse(engine.last_search_performance["snapshot_built"])

    def test_new_and_deleted_files_invalidate_snapshot(self):
        first = self.root / "first.txt"
        first.write_text("one", encoding="utf-8")
        self._cache(first)
        engine = SearchEngine(self.db_path)
        engine.search_files_smart(["first"])

        second = self.root / "second.txt"
        second.write_text("two", encoding="utf-8")
        self._cache(second)
        rows, _ = engine.search_files_smart(["second"])
        self.assertEqual([row[2] for row in rows], [str(second)])
        self.assertTrue(engine.last_search_performance["snapshot_built"])

        second.unlink()
        # Folder scan completion invalidates/prewarms in product.
        engine.invalidate_snapshot()
        rows, _ = engine.search_files_smart(["second"])
        self.assertEqual(rows, [])

    def test_text_index_change_replaces_old_searchable_body(self):
        path = self.root / "body.md"
        path.write_text("fixture", encoding="utf-8")
        self._cache(path)
        extractor = MutableExtractor("old searchable phrase")
        indexer = LocalTextIndexer(self.db_path, extractor=extractor)
        indexer.synchronize([str(path)])
        engine = SearchEngine(self.db_path)
        self.assertTrue(engine.search_files_smart(["old"])[0])

        extractor.text = "new searchable phrase"
        path.write_text("changed", encoding="utf-8")
        future = max(os.stat(path).st_mtime_ns + 1_000_000, time.time_ns())
        os.utime(path, ns=(future, future))
        indexer.synchronize([str(path)])
        self.assertEqual(engine.search_files_smart(["old"])[0], [])
        self.assertTrue(engine.search_files_smart(["new"])[0])

    def test_ai_metadata_save_invalidates_snapshot(self):
        path = self.root / "metadata.txt"
        path.write_text("plain", encoding="utf-8")
        self._cache(path)
        engine = SearchEngine(self.db_path)
        engine.search_files_smart(["consulting"])

        result = self.registry.save_file_result(str(path), {
            "metadata": {"ai_comment": "special consulting summary", "tags": ["business"]}
        })
        self.assertTrue(result["success"])
        rows, _ = engine.search_files_smart(["consulting"])
        self.assertEqual(rows[0][2], str(path))
        self.assertIn("ai_metadata", engine.get_result_metadata(str(path))["match_source"])

    def test_cached_and_reference_scoring_are_identical(self):
        paths = []
        for name, text in (("LoboDoc_plan.txt", "business plan"),
                           ("other.txt", "LoboDoc business plan")):
            path = self.root / name
            path.write_text(text, encoding="utf-8")
            self._cache(path)
            paths.append(path)
        LocalTextIndexer(self.db_path).synchronize([str(path) for path in paths])
        engine = SearchEngine(self.db_path)
        rows, _ = engine.search_files_smart(["lobodoc", "plan"])
        optimized = [(row[2], engine.get_result_metadata(row[2])) for row in rows]

        snapshot = engine.refresh_snapshot()
        groups, rarity = engine._prepare_keyword_groups(snapshot, ["lobodoc", "plan"])
        reference = []
        for record in snapshot.records:
            matched, score, sources, breakdown = engine._score_record(
                record, groups, rarity
            )
            if matched:
                reference.append((matched, score, record.file_name.casefold(),
                                  record.file_path.casefold(), record.file_path,
                                  sorted(sources), breakdown))
        reference.sort(key=lambda value: (-value[0], -value[1], value[2], value[3]))
        self.assertEqual([item[0] for item in optimized], [item[4] for item in reference])
        for (path, metadata), expected in zip(optimized, reference):
            self.assertEqual(metadata["relevance_score"], expected[1])
            self.assertEqual(metadata["match_source"], expected[5])
            self.assertEqual(metadata["score_breakdown"], expected[6])

    def test_rare_body_term_receives_bounded_discrimination_bonus(self):
        paths = []
        for index, text in enumerate((
                "자료 설명 설정 게이트웨이",
                "자료 설명 설정",
                "자료 설명 설정")):
            path = self.root / f"body_{index}.txt"
            path.write_text(text, encoding="utf-8")
            self._cache(path)
            paths.append(path)
        LocalTextIndexer(self.db_path).synchronize([str(path) for path in paths])
        engine = SearchEngine(self.db_path)
        rows, _ = engine.search_files_smart(["게이트웨이", "설정", "설명"])
        self.assertEqual(rows[0][2], str(paths[0]))
        bonus = engine.get_result_metadata(str(paths[0]))["score_breakdown"][
            "discrimination_bonus"
        ]
        self.assertGreater(bonus, 0)
        self.assertLessEqual(bonus, 30)


if __name__ == "__main__":
    unittest.main()
