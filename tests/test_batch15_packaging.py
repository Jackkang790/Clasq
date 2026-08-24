import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.ai.config import AIConfig
from src.ai.model_downloader import ModelDownloader
from src.ai.runtime_profile import PROFILE_QWEN3VL_8B_Q4KM_CUDA
from src.ai.server_manager import LlamaServerManager
from src.utils import app_paths
from src.utils.db_manager import FileRegistryManager

TEST_TMP = Path(__file__).resolve().parents[1] / ".tmp" / "batch15"
TEST_TMP.mkdir(parents=True, exist_ok=True)


def temp_dir(*, prefix="tmp"):
    return tempfile.TemporaryDirectory(prefix=prefix, dir=TEST_TMP)


class Batch15PackagingTests(unittest.TestCase):
    def test_source_resource_paths_are_project_relative(self):
        self.assertTrue(Path(app_paths.assets_dir(), "styles", "light.qss").is_file())

    def test_frozen_resource_paths_use_meipass(self):
        with temp_dir(prefix="Clasq frozen 한글 ") as root:
            with patch.object(sys, "frozen", True, create=True), patch.object(
                sys, "_MEIPASS", root, create=True
            ):
                self.assertEqual(app_paths.runtime_dir(), str(Path(root) / "runtime"))
                self.assertEqual(app_paths.assets_dir(), str(Path(root) / "assets"))

    def test_frozen_base_path_is_executable_directory(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", r"C:\Program Files\Clasq\Clasq.exe"
        ):
            self.assertEqual(app_paths.app_base_dir(), r"C:\Program Files\Clasq")

    def test_all_writable_paths_are_below_localappdata(self):
        with temp_dir(prefix="Clasq data 한글 ") as root, patch.dict(
            os.environ, {"LOCALAPPDATA": root}
        ):
            paths = [
                app_paths.database_path(), app_paths.settings_dir(),
                app_paths.logs_dir(), app_paths.models_dir(),
            ]
            for value in paths:
                self.assertTrue(Path(value).resolve().is_relative_to(Path(root).resolve()))

    def test_default_model_paths_use_user_data(self):
        with temp_dir(prefix="Clasq model path ") as root, patch.dict(
            os.environ, {"LOCALAPPDATA": root}, clear=False
        ):
            for key in ("LLAMA_MODEL_PATH", "LLAMA_MMPROJ_PATH"):
                os.environ.pop(key, None)
            cfg = AIConfig()
            self.assertEqual(Path(cfg.llama_model_path).parent, Path(root) / "Clasq" / "models")
            self.assertEqual(Path(cfg.llama_mmproj_path).parent, Path(root) / "Clasq" / "models")

    def test_bundled_executable_and_ffmpeg_resolution(self):
        with temp_dir(prefix="Clasq runtime ") as root:
            runtime = Path(root) / "runtime"
            runtime.mkdir()
            (runtime / "llama-server.exe").touch()
            (runtime / "ffmpeg.exe").touch()
            with patch("src.ai.config._runtime_dir", return_value=str(runtime)):
                cfg = AIConfig()
            self.assertEqual(cfg.llama_server_exe, str(runtime / "llama-server.exe"))
            self.assertEqual(cfg.ffmpeg_path, str(runtime / "ffmpeg.exe"))

    def test_missing_bundled_server_has_explicit_nonexistent_path(self):
        with temp_dir() as root, patch.dict(
            os.environ, {"LLAMA_SERVER_EXE": str(Path(root) / "missing.exe")}
        ):
            cfg = AIConfig()
            manager = LlamaServerManager(cfg)
            self.assertFalse(manager._start())
            self.assertIn("missing.exe", manager.error)

    def test_subprocess_uses_argument_list_for_unicode_and_spaces(self):
        with temp_dir(prefix="Clasq 실행 한글 ") as root:
            root_path = Path(root)
            paths = [root_path / "llama server.exe", root_path / "모델 파일.gguf", root_path / "mm proj.gguf"]
            for path in paths:
                path.touch()
            cfg = SimpleNamespace(
                llama_server_exe=str(paths[0]), llama_model_path=str(paths[1]),
                llama_mmproj_path=str(paths[2]), llama_n_gpu_layers=99,
                llama_context_size=32768, llama_host="127.0.0.1", llama_port=18080,
                llama_startup_timeout=1,
            )
            proc = Mock(pid=1234)
            proc.poll.return_value = None
            with patch("src.ai.server_manager.subprocess.Popen", return_value=proc) as popen:
                manager = LlamaServerManager(cfg)
                manager._check_health = Mock(return_value=True)
                self.assertTrue(manager._start())
            command = popen.call_args.args[0]
            self.assertIsInstance(command, list)
            self.assertEqual(command[:5], [str(paths[0]), "-m", str(paths[1]), "--mmproj", str(paths[2])])

    def test_cached_model_is_reused_from_manifest(self):
        profile = PROFILE_QWEN3VL_8B_Q4KM_CUDA
        with temp_dir() as root:
            target = Path(root) / profile.model_filename
            target.write_bytes(b"cached")
            stat = target.stat()
            manifest = {target.name: {"sha256": profile.model_sha256, "size": stat.st_size, "mtime": stat.st_mtime}}
            (Path(root) / ".clasq_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            downloader = ModelDownloader(profile, models_dir=Path(root))
            self.assertTrue(downloader._is_valid_cached(target, profile.model_sha256, profile.model_size_bytes))

    def test_partial_file_is_never_treated_as_final_model(self):
        profile = PROFILE_QWEN3VL_8B_Q4KM_CUDA
        with temp_dir() as root:
            downloader = ModelDownloader(profile, models_dir=Path(root))
            partial = Path(root) / f"{profile.model_filename}.tmp"
            partial.write_bytes(b"partial")
            self.assertFalse(downloader._is_valid_cached(partial, profile.model_sha256, profile.model_size_bytes))
            self.assertFalse((Path(root) / profile.model_filename).exists())

    def test_schema_remains_v3_for_clean_database(self):
        with temp_dir() as root:
            db = Path(root) / "clean.db"
            FileRegistryManager(str(db))
            connection = sqlite3.connect(db)
            try:
                version = connection.execute("SELECT MAX(version) FROM db_schema_version").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(version, 3)

    def test_packaging_files_do_not_bundle_gguf_or_change_schema(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "clasq.spec").read_text(encoding="utf-8")
        installer = (root / "installer" / "Clasq.iss").read_text(encoding="utf-8")
        self.assertNotIn(".gguf\"", spec)
        self.assertIn("PrivilegesRequired=lowest", installer)
        self.assertNotIn("file_manager.db", installer)


if __name__ == "__main__":
    unittest.main()
