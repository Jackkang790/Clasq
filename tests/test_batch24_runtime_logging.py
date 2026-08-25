from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.utils import logging_setup as ls


class Batch24RuntimeLoggingTests(unittest.TestCase):
    def setUp(self):
        ls.shutdown_runtime_logging()
        temp_parent = Path.cwd() / ".tmp" / "batch24"
        temp_parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_parent)
        self.root = Path(self.temp.name)

    def tearDown(self):
        ls.shutdown_runtime_logging()
        self.temp.cleanup()

    def _read(self) -> str:
        path = self.root / ls.LOG_FILENAME
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_default_log_location_uses_local_appdata(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            self.assertEqual(Path(ls.logs_dir()), self.root / "Clasq" / "logs")

    def test_log_directory_created(self):
        target = self.root / "nested" / "logs"
        self.assertEqual(ls.initialize_runtime_logging(log_directory=target), target / "clasq.log")
        logging.getLogger("batch24").info("created")
        self.assertTrue((target / "clasq.log").is_file())

    def test_explicit_test_directory_does_not_use_executable_directory(self):
        path = ls.initialize_runtime_logging(log_directory=self.root)
        self.assertEqual(path.parent, self.root)
        self.assertNotEqual(path.parent, Path(os.path.dirname(os.path.abspath(os.sys.executable))))

    def test_initialization_is_idempotent(self):
        ls.initialize_runtime_logging(log_directory=self.root)
        ls.initialize_runtime_logging(log_directory=self.root)
        handlers = [h for h in logging.getLogger().handlers if getattr(h, ls._HANDLER_MARKER, False)]
        self.assertEqual(len(handlers), 1)

    def test_duplicate_handler_does_not_duplicate_message(self):
        ls.initialize_runtime_logging(log_directory=self.root)
        ls.initialize_runtime_logging(log_directory=self.root)
        logging.getLogger("batch24").info("one-copy-marker")
        ls.shutdown_runtime_logging()
        self.assertEqual(self._read().count("one-copy-marker"), 1)

    def test_utf8_logging(self):
        ls.initialize_runtime_logging(log_directory=self.root)
        logging.getLogger("batch24").info("한글 로그 정상")
        ls.shutdown_runtime_logging()
        self.assertIn("한글 로그 정상", self._read())

    def test_info_is_default_level(self):
        self.assertEqual(ls.resolve_log_level(None), logging.INFO)

    def test_debug_is_explicit_opt_in(self):
        self.assertEqual(ls.resolve_log_level("DEBUG"), logging.DEBUG)

    def test_invalid_level_falls_back_to_info(self):
        self.assertEqual(ls.resolve_log_level("VERBOSE"), logging.INFO)

    def test_rotation_creates_bounded_backups(self):
        ls.initialize_runtime_logging(log_directory=self.root, max_bytes=180, backup_count=2)
        logger = logging.getLogger("batch24.rotation")
        for index in range(80):
            logger.info("rotation-entry-%03d %s", index, "x" * 40)
        ls.shutdown_runtime_logging()
        names = sorted(p.name for p in self.root.glob("clasq.log*"))
        self.assertIn("clasq.log", names)
        self.assertLessEqual(len(names), 3)
        self.assertTrue(any(name.endswith(".1") for name in names))

    def test_retention_constant_includes_active_file(self):
        self.assertEqual(ls.MAX_RETAINED_BYTES, 20 * 1024 * 1024)

    def test_shutdown_flushes_and_releases_file(self):
        ls.initialize_runtime_logging(log_directory=self.root)
        logging.getLogger("batch24").info("flush-marker")
        ls.shutdown_runtime_logging()
        original = self.root / ls.LOG_FILENAME
        renamed = self.root / "renamed.log"
        original.rename(renamed)
        self.assertIn("flush-marker", renamed.read_text(encoding="utf-8"))

    def test_directory_creation_failure_is_nonfatal(self):
        with mock.patch("src.utils.logging_setup.Path.mkdir", side_effect=PermissionError("denied")):
            self.assertIsNone(ls.initialize_runtime_logging(log_directory=self.root / "blocked"))

    def test_handler_write_failure_is_nonfatal(self):
        ls.initialize_runtime_logging(log_directory=self.root)
        handler = next(h for h in logging.getLogger().handlers if getattr(h, ls._HANDLER_MARKER, False))
        with mock.patch.object(logging.handlers.RotatingFileHandler, "emit", side_effect=OSError("disk full")):
            logging.getLogger("batch24").error("must not crash")
        self.assertTrue(handler.disabled)

    def test_bearer_token_is_redacted(self):
        self.assertNotIn("SECRET_TOKEN_BATCH24", ls.redact_text("Authorization: Bearer SECRET_TOKEN_BATCH24"))

    def test_password_assignment_is_redacted(self):
        self.assertEqual(ls.redact_text("password=SECRET_TOKEN_BATCH24"), "password=<redacted>")

    def test_api_key_query_is_redacted(self):
        value = ls.redact_text("https://example.test/a?api_key=SECRET_TOKEN_BATCH24&x=1")
        self.assertNotIn("SECRET_TOKEN_BATCH24", value)

    def test_windows_user_path_is_redacted(self):
        value = ls.redact_text(r"failed C:\Users\TestUser\Secret\Client\document.pdf")
        self.assertNotIn("TestUser", value)
        self.assertIn("<user-path>", value)

    def test_safe_filename_removes_parent_path(self):
        self.assertEqual(ls.safe_filename(r"C:\Users\TestUser\Secret\document.pdf"), "document.pdf")

    def test_safe_filename_prevents_newline_injection(self):
        value = ls.safe_filename("folder/bad\nname.pdf")
        self.assertNotIn("\n", value)
        self.assertIn("\\n", value)

    def test_formatter_redacts_after_message_formatting(self):
        ls.initialize_runtime_logging(log_directory=self.root)
        logging.getLogger("batch24").warning("token=%s", "SECRET_TOKEN_BATCH24")
        ls.shutdown_runtime_logging()
        self.assertNotIn("SECRET_TOKEN_BATCH24", self._read())

    def test_formatter_contains_operational_fields(self):
        ls.initialize_runtime_logging(log_directory=self.root)
        logging.getLogger("batch24.fields").info("fields")
        ls.shutdown_runtime_logging()
        text = self._read()
        self.assertIn("INFO batch24.fields pid=", text)
        self.assertIn("thread=MainThread session=", text)

    def test_qwen_logs_metadata_not_prompt_or_response(self):
        from src.ai.qwen_client import QwenClient

        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"choices": [{"message": {"content": "AI_RESPONSE_BATCH24"}}]}

        session = mock.Mock()
        session.post.return_value = Response()
        ls.initialize_runtime_logging(log_directory=self.root)
        result = QwenClient(session=session).request_text("PROMPT_SECRET_BATCH24")
        ls.shutdown_runtime_logging()
        text = self._read()
        self.assertEqual(result, "AI_RESPONSE_BATCH24")
        self.assertIn("AI inference completed", text)
        self.assertNotIn("PROMPT_SECRET_BATCH24", text)
        self.assertNotIn("AI_RESPONSE_BATCH24", text)

    def test_server_command_log_does_not_join_full_command(self):
        source = Path("src/ai/server_manager.py").read_text(encoding="utf-8")
        self.assertNotIn('log.info("Starting llama-server: %s", " ".join(cmd))', source)
        self.assertIn("llama-server start requested profile=%s", source)

    def test_apply_undo_logs_are_summary_only(self):
        source = Path("src/utils/workers.py").read_text(encoding="utf-8")
        self.assertIn("organize apply completed success=%d failed=%d", source)
        self.assertIn("organize undo completed success=%d failed=%d", source)
        self.assertNotIn('log.info("organize apply file=', source)

    def test_index_and_search_logging_is_summary_only(self):
        source = Path("src/utils/workers.py").read_text(encoding="utf-8")
        self.assertIn("index and search synchronization completed candidates=%d", source)
        self.assertNotIn('log.info("indexing file=', source)

    def test_no_runtime_log_network_or_shell_execution(self):
        source = Path("src/utils/logging_setup.py").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("requests", source)

    def test_main_records_startup_and_shutdown_boundaries(self):
        source = Path("main.py").read_text(encoding="utf-8")
        self.assertIn("application startup mode=%s", source)
        self.assertIn("application shutdown requested", source)
        self.assertIn("application exiting", source)

    def test_ai_lifecycle_has_bounded_event_logging(self):
        source = Path("src/ai/server_manager.py").read_text(encoding="utf-8")
        for marker in (
            "llama-server process started", "llama-server ready", "readiness timeout",
            "runtime crash detected", "runtime recovery result", "shutdown requested",
        ):
            self.assertIn(marker, source)

    def test_stderr_tail_remains_bounded(self):
        source = Path("src/ai/server_manager.py").read_text(encoding="utf-8")
        self.assertIn("deque(maxlen=40)", source)

    def test_readiness_poll_does_not_log_at_info_each_iteration(self):
        source = Path("src/ai/server_manager.py").read_text(encoding="utf-8")
        loop = source[source.index("while monotonic() < deadline"):source.index("if self._shutting_down:", source.index("while monotonic() < deadline"))]
        self.assertEqual(loop.count("log.info"), 1)  # terminal ready event only
        self.assertNotIn("readiness poll", loop)

    def test_db_schema_not_changed_by_logging(self):
        source = Path("src/utils/logging_setup.py").read_text(encoding="utf-8")
        self.assertNotIn("sqlite", source.lower())


if __name__ == "__main__":
    unittest.main()
