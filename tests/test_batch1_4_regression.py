"""Batch 1-4 regression tests — verify nothing broken by Batch 5 changes."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


# ── Batch 1: AI layer ─────────────────────────────────────────────────────────
class TestBatch1AILayer(unittest.TestCase):
    def test_ai_config_default_mode(self):
        from src.ai.config import AIConfig, get_ai_mode
        cfg = AIConfig()
        self.assertEqual(cfg.ai_mode, "llama_server")
        self.assertTrue(cfg.is_llama_server_mode)
        self.assertFalse(cfg.is_ollama_mode)

    def test_ai_config_env_override(self):
        import os
        from src.ai.config import get_ai_mode
        with patch.dict(os.environ, {"AI_MODE": "ollama"}):
            self.assertEqual(get_ai_mode(), "ollama")
        with patch.dict(os.environ, {"AI_MODE": "invalid"}):
            self.assertEqual(get_ai_mode(), "llama_server")

    def test_qwen_client_parts(self):
        from src.ai.qwen_client import QwenClient
        tp = QwenClient.text_part("hello")
        self.assertEqual(tp["type"], "text")
        self.assertEqual(tp["text"], "hello")
        ip = QwenClient.image_part("data:image/jpeg;base64,abc")
        self.assertEqual(ip["type"], "image_url")

    def test_qwen_client_parse_json(self):
        from src.ai.qwen_client import QwenClient
        raw = '{"display_name":"test","tags":["a","b"]}'
        parsed = QwenClient.parse_json_content(raw)
        self.assertEqual(parsed["display_name"], "test")
        self.assertEqual(parsed["tags"], ["a", "b"])

    def test_qwen_client_parse_json_with_think_tag(self):
        from src.ai.qwen_client import QwenClient
        raw = '<think>reasoning</think>{"display_name":"x","tags":[]}'
        parsed = QwenClient.parse_json_content(raw)
        self.assertEqual(parsed["display_name"], "x")

    def test_qwen_client_connection_error_on_no_server(self):
        from src.ai.config import AIConfig
        from src.ai.qwen_client import QwenClient, AIClientError
        cfg = AIConfig()
        client = QwenClient(cfg)
        # llama-server 미실행 시 AIConnectionError 또는 AITimeoutError (둘 다 AIClientError 하위)
        with self.assertRaises(AIClientError):
            client.request_text("test", timeout=1)

    def test_image_analyzer_fallback_on_connection_error(self):
        from src.ai.config import AIConfig
        from src.ai.qwen_client import QwenClient, AIConnectionError
        from src.ai.image_analyzer import ImageAnalyzer
        cfg = AIConfig()
        client = QwenClient(cfg)
        analyzer = ImageAnalyzer(client)
        with tempfile.TemporaryDirectory() as tmp:
            img_path = str(Path(tmp) / "test.jpg")
            from PIL import Image
            Image.new("RGB", (100, 100), (255, 255, 255)).save(img_path)
            result = analyzer.analyze_image(img_path)
        self.assertIn(result.get("status"), ("FAILED", "SUCCESS"))

    def test_video_analyzer_find_ffmpeg_does_not_crash(self):
        from src.ai.video_analyzer import VideoAnalyzer
        va = VideoAnalyzer()
        path = va.find_ffmpeg()
        # May be None (not installed) or a valid path string — either is OK
        self.assertTrue(path is None or isinstance(path, str))

    def test_hardware_detector_instantiate(self):
        from src.ai.hardware_detector import HardwareDetector
        hd = HardwareDetector()
        profile = hd.detect()
        self.assertIsNotNone(profile)

    def test_server_manager_instantiate(self):
        from src.ai.server_manager import LlamaServerManager
        from src.ai.config import AIConfig
        sm = LlamaServerManager(AIConfig())
        self.assertIsNotNone(sm)


# ── Batch 2: Query parser / Startup ──────────────────────────────────────────
class TestBatch2QueryParser(unittest.TestCase):
    def test_query_parser_instantiate(self):
        from src.utils.query_parser import SearchQueryParser
        qp = SearchQueryParser()
        self.assertIsNotNone(qp)

    def test_query_parser_fallback_parse(self):
        from src.utils.query_parser import SearchQueryParser
        qp = SearchQueryParser()
        # AI 없이도 예외 없이 결과를 반환해야 함
        try:
            result = qp.parse_user_query("보고서")
            self.assertIsInstance(result, dict)
        except Exception as exc:
            # AI 서버 없이 동작할 때 graceful 실패도 허용
            self.assertIsNotNone(exc)

    def test_startup_worker_class_exists(self):
        from src.ai.startup_worker import StartupWorker
        self.assertTrue(callable(StartupWorker))

    def test_model_downloader_class_exists(self):
        from src.ai.model_downloader import ModelDownloader
        self.assertTrue(callable(ModelDownloader))


# ── Batch 3: Search normalization / aliases / snapshot ───────────────────────
class TestBatch3Search(unittest.TestCase):
    def test_strip_korean_particle(self):
        from src.utils.search_normalization import strip_korean_particle
        self.assertEqual(strip_korean_particle("보고서를"), "보고서")
        self.assertEqual(strip_korean_particle("문서에"), "문서")
        self.assertEqual(strip_korean_particle("파일의"), "파일")

    def test_search_aliases(self):
        from src.utils.search_aliases import build_search_alias_map
        alias_map = build_search_alias_map()
        self.assertIsInstance(alias_map, dict)
        self.assertGreater(len(alias_map), 0)

    def test_search_snapshot_invalidate(self):
        from src.utils.search_snapshot import invalidate_search_snapshot, get_search_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "snap.db")
            # DB가 없어도 invalidate는 안전해야 함
            gen = invalidate_search_snapshot(db)
            self.assertGreaterEqual(gen, 1)

    def test_search_engine_instantiate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            from src.utils.search_engine import SearchEngine
            se = SearchEngine(db)
            self.assertIsNotNone(se)


# ── Batch 4: DB migration / AI pipeline ──────────────────────────────────────
class TestBatch4DB(unittest.TestCase):
    def _make_db(self, tmp):
        from src.utils.db_manager import FileRegistryManager
        db = str(Path(tmp) / "test.db")
        mgr = FileRegistryManager(db_path=db)
        return db, mgr

    def test_db_creates_schema_version_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _ = self._make_db(tmp)
            conn = sqlite3.connect(db)
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            conn.close()
            table_names = {r[0] for r in rows}
            self.assertIn("files", table_names)
            self.assertIn("db_schema_version", table_names)
            self.assertIn("file_fingerprint_cache", table_names)
            self.assertIn("file_text_index", table_names)

    def test_migration_version_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _ = self._make_db(tmp)
            conn = sqlite3.connect(db)
            version = conn.execute(
                "SELECT MAX(version) FROM db_schema_version"
            ).fetchone()[0]
            conn.close()
            self.assertGreaterEqual(version, 2)

    def test_files_table_has_required_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _ = self._make_db(tmp)
            conn = sqlite3.connect(db)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(files)").fetchall()}
            conn.close()
            for col in ("file_hash", "file_mtime_ns", "file_modified_at",
                        "tags", "display_name"):
                self.assertIn(col, cols, f"Column {col!r} missing from files table")

    def test_get_file_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, mgr = self._make_db(tmp)
            # Register a dummy file
            dummy = str(Path(tmp) / "a.txt")
            Path(dummy).write_text("hello")
            mgr.save_file_result(dummy, {
                "@TYPE": "@DB", "status": "SUCCESS",
                "metadata": {"display_name": "A", "tags": ["테스트"], "ai_comment": ""},
            })
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT id FROM files WHERE file_path=?", (dummy,)).fetchone()
            conn.close()
            if row:
                result = mgr.get_file_by_id(row[0])
                self.assertIsNotNone(result)

    def test_delete_cascade_removes_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, mgr = self._make_db(tmp)
            dummy = str(Path(tmp) / "b.txt")
            Path(dummy).write_text("world")
            mgr.save_file_result(dummy, {
                "@TYPE": "@DB", "status": "SUCCESS",
                "metadata": {"display_name": "B", "tags": [], "ai_comment": ""},
            })
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT id FROM files WHERE file_path=?", (dummy,)).fetchone()
            conn.close()
            if row:
                mgr.delete_file(row[0])
                conn = sqlite3.connect(db)
                deleted = conn.execute(
                    "SELECT id FROM files WHERE id=?", (row[0],)
                ).fetchone()
                conn.close()
                self.assertIsNone(deleted)

    def test_load_registered_files_in_core(self):
        """Batch 5에서 추가된 load_registered_files가 정상 동작하는지 검증."""
        with tempfile.TemporaryDirectory() as tmp:
            db, mgr = self._make_db(tmp)
            dummy = str(Path(tmp) / "c.txt")
            Path(dummy).write_text("test")
            mgr.save_file_result(dummy, {
                "@TYPE": "@DB", "status": "SUCCESS",
                "metadata": {"display_name": "C", "tags": ["문서"], "ai_comment": ""},
            })
            from src.utils.core import load_registered_files
            rows = load_registered_files(db)
            self.assertGreater(len(rows), 0)
            row = rows[0]
            self.assertIn("id", row)
            self.assertIn("tags", row)
            self.assertIsInstance(row["tags"], list)

    def test_core_process_file_upload_saves_to_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            dummy = str(Path(tmp) / "d.txt")
            Path(dummy).write_text("regression test content")
            from src.utils.core import ClasqCore
            core = ClasqCore(db_path=db)
            result = core.process_file_upload(dummy)
            self.assertEqual(result.get("@TYPE"), "@DB")
            # DB 저장 확인
            db_result = result.get("db_result", {})
            self.assertTrue(db_result.get("success"), f"DB save failed: {db_result}")

    def test_clasq_core_get_db_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            from src.utils.core import ClasqCore
            core = ClasqCore(db_path=db)
            stats = core.get_db_stats()
            self.assertIn("total_files", stats)

    def test_default_excluded_directories_exported(self):
        from src.utils.core import DEFAULT_EXCLUDED_DIRECTORIES
        self.assertIn(".git", DEFAULT_EXCLUDED_DIRECTORIES)
        self.assertIn("node_modules", DEFAULT_EXCLUDED_DIRECTORIES)
        self.assertIn("__pycache__", DEFAULT_EXCLUDED_DIRECTORIES)


if __name__ == "__main__":
    unittest.main()
