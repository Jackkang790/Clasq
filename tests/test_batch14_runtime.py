import os
import tempfile
import unittest
from pathlib import Path

from src.ai.config import AIConfig
from src.ai.qwen_client import AIResponseError, QwenClient
from src.ai.server_manager import LlamaServerManager
from src.utils.db_manager import FileRegistryManager


class TestBatch14DeterministicRuntime(unittest.TestCase):
    def test_real_analysis_save_populates_fingerprint_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = str(Path(temp) / "clasq.db")
            file_path = Path(temp) / "sample.txt"
            file_path.write_text("batch 14", encoding="utf-8")
            manager = FileRegistryManager(db_path)
            result = manager.save_file_result(str(file_path), {
                "metadata": {
                    "display_name": "Batch 14",
                    "tags": ["검증"],
                    "ai_comment": "real analysis shape",
                }
            })
            self.assertTrue(result["success"])
            connection = manager._connect()
            row = connection.execute(
                "SELECT file_hash, file_size, file_mtime_ns "
                "FROM file_fingerprint_cache WHERE file_path=?", (str(file_path),)
            ).fetchone()
            connection.close()
            self.assertIsNotNone(row)
            self.assertEqual(row[1], file_path.stat().st_size)

    def test_missing_server_executable_fails_without_process(self):
        config = AIConfig(
            llama_server_exe=os.path.join("missing", "llama-server.exe"),
            llama_model_path=os.path.join("missing", "model.gguf"),
            llama_mmproj_path=os.path.join("missing", "mmproj.gguf"),
        )
        manager = LlamaServerManager(config)
        self.assertFalse(manager._start())
        self.assertIsNone(manager._proc)
        self.assertIn("llama-server", manager.error)

    def test_malformed_response_is_rejected(self):
        with self.assertRaises(AIResponseError):
            QwenClient.parse_json_content("not JSON and no object")

    def test_empty_response_is_rejected(self):
        with self.assertRaises(AIResponseError):
            QwenClient.parse_json_content("")

    def test_schema_version_remains_three(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = FileRegistryManager(str(Path(temp) / "clasq.db"))
            connection = manager._connect()
            version = connection.execute(
                "SELECT MAX(version) FROM db_schema_version"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(version, 3)


if __name__ == "__main__":
    unittest.main()
