import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from src.ai.model_downloader import ModelDownloader, _is_https_url
from src.ai.runtime_profile import RuntimeProfile, PROFILE_QWEN3VL_8B_Q4KM_CUDA
from src.utils import app_paths


TEST_TMP = Path(__file__).resolve().parents[1] / ".tmp" / "batch29"
TEST_TMP.mkdir(parents=True, exist_ok=True)


def temp_dir(prefix="cache "):
    return tempfile.TemporaryDirectory(prefix=prefix, dir=TEST_TMP)


MAIN = b"batch29-main-model"
MMPROJ = b"batch29-mmproj"


def fixture_profile(**overrides):
    values = dict(
        name="batch29", description="fixture",
        model_filename="model.gguf", model_url="https://example.test/model.gguf",
        model_sha256=hashlib.sha256(MAIN).hexdigest(), model_size_bytes=len(MAIN),
        mmproj_filename="mmproj.gguf", mmproj_url="https://example.test/mmproj.gguf",
        mmproj_sha256=hashlib.sha256(MMPROJ).hexdigest(), mmproj_size_bytes=len(MMPROJ),
        n_gpu_layers=1, context_size=128,
    )
    values.update(overrides)
    return RuntimeProfile(**values)


class FakeResponse:
    def __init__(self, payload=b"", status=200, url="https://cdn.example.test/file"):
        self.payload = payload
        self.status_code = status
        self.url = url
        self.headers = {"content-length": str(len(payload))}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size):
        yield from (self.payload[index:index + 3] for index in range(0, len(self.payload), 3))


class Batch29IdentityAndCacheTests(unittest.TestCase):
    def setUp(self):
        self.disk = patch("src.ai.model_downloader._free_space_bytes", return_value=10**12)
        self.disk.start()

    def tearDown(self):
        self.disk.stop()

    def test_production_identity_is_pinned(self):
        profile = PROFILE_QWEN3VL_8B_Q4KM_CUDA
        self.assertEqual(profile.model_filename, "qwen3vl-8b-q4_k_m.gguf")
        self.assertEqual(profile.model_size_bytes, 5_027_785_568)
        self.assertEqual(profile.model_sha256, "108e7ff92b78eefd3db4741885104acba514255c11b617d3c7b197a5f46efe89")
        self.assertEqual(profile.mmproj_filename, "mmproj-bf16.gguf")
        self.assertEqual(profile.mmproj_size_bytes, 1_162_569_280)
        self.assertEqual(profile.mmproj_sha256, "6516bb64bae1503a0fcd7ec9fa39655f8c481580be0a0a066397941d9761c9f4")

    def test_cache_override_supports_unicode_and_spaces(self):
        with temp_dir("한글 모델 cache ") as root, patch.dict(os.environ, {"CLASQ_MODEL_CACHE_DIR": root}):
            self.assertEqual(app_paths.models_dir(), root)

    def test_empty_and_partial_cache_are_missing(self):
        with temp_dir() as root:
            downloader = ModelDownloader(fixture_profile(), Path(root))
            (Path(root) / "model.gguf.part").write_bytes(b"partial")
            self.assertEqual(downloader.cache_state(), {"main": "missing", "mmproj": "missing"})

    def test_state_matrix(self):
        with temp_dir() as root:
            path = Path(root)
            downloader = ModelDownloader(fixture_profile(), path)
            path.joinpath("model.gguf").write_bytes(MAIN)
            self.assertEqual(downloader.cache_state()["main"], "needs_validation")
            self.assertEqual(downloader.cache_state()["mmproj"], "missing")
            path.joinpath("mmproj.gguf").write_bytes(b"bad")
            self.assertEqual(downloader.cache_state()["mmproj"], "invalid_size")

    def test_size_match_hash_mismatch_is_rejected(self):
        with temp_dir() as root:
            path = Path(root) / "model.gguf"
            path.write_bytes(b"x" * len(MAIN))
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=lambda *a, **k: FakeResponse(MAIN))
            self.assertTrue(downloader._ensure_file("model.gguf", fixture_profile().model_sha256, len(MAIN), fixture_profile().model_url))
            self.assertEqual(path.read_bytes(), MAIN)


class Batch29DownloadTests(unittest.TestCase):
    def setUp(self):
        self.disk = patch("src.ai.model_downloader._free_space_bytes", return_value=10**12)
        self.disk.start()

    def tearDown(self):
        self.disk.stop()

    def test_two_files_download_validate_and_finalize(self):
        calls = []
        def get(url, **kwargs):
            calls.append(url)
            return FakeResponse(MAIN if url.endswith("model.gguf") else MMPROJ)
        with temp_dir() as root:
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=get)
            self.assertTrue(downloader.ensure_ready())
            self.assertEqual(len(calls), 2)
            self.assertFalse(any(Path(root).glob("*.part")))
            manifest = json.loads((Path(root) / ".clasq_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(set(manifest), {"model.gguf", "mmproj.gguf"})

    def test_second_launch_is_zero_network(self):
        with temp_dir() as root:
            first = ModelDownloader(fixture_profile(), Path(root), request_get=lambda url, **k: FakeResponse(MAIN if url.endswith("model.gguf") else MMPROJ))
            self.assertTrue(first.ensure_ready())
            second = ModelDownloader(fixture_profile(), Path(root), request_get=Mock(side_effect=AssertionError("network used")))
            self.assertTrue(second.ensure_ready())

    def test_main_success_mmproj_failure_preserves_main(self):
        def get(url, **kwargs):
            if url.endswith("model.gguf"):
                return FakeResponse(MAIN)
            raise requests.ConnectionError("offline")
        with temp_dir() as root:
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=get, max_attempts=2)
            self.assertFalse(downloader.ensure_ready())
            self.assertEqual((Path(root) / "model.gguf").read_bytes(), MAIN)
            self.assertFalse((Path(root) / "mmproj.gguf").exists())

    def test_retry_is_bounded_and_can_succeed(self):
        calls = 0
        def get(url, **kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise requests.Timeout("synthetic secret must not surface")
            return FakeResponse(MAIN)
        with temp_dir() as root:
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=get, max_attempts=3)
            self.assertTrue(downloader._ensure_file("model.gguf", fixture_profile().model_sha256, len(MAIN), fixture_profile().model_url))
            self.assertEqual(calls, 3)

    def test_http_404_is_not_retried(self):
        get = Mock(return_value=FakeResponse(status=404))
        with temp_dir() as root:
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=get)
            self.assertFalse(downloader._ensure_file("model.gguf", fixture_profile().model_sha256, len(MAIN), fixture_profile().model_url))
            self.assertEqual(get.call_count, 1)

    def test_http_500_is_bounded(self):
        get = Mock(return_value=FakeResponse(status=500))
        with temp_dir() as root:
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=get, max_attempts=3)
            self.assertFalse(downloader._ensure_file("model.gguf", fixture_profile().model_sha256, len(MAIN), fixture_profile().model_url))
            self.assertEqual(get.call_count, 3)

    def test_truncated_download_never_becomes_final(self):
        with temp_dir() as root:
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=lambda *a, **k: FakeResponse(MAIN[:-1]))
            self.assertFalse(downloader._ensure_file("model.gguf", fixture_profile().model_sha256, len(MAIN), fixture_profile().model_url))
            self.assertFalse((Path(root) / "model.gguf").exists())
            self.assertFalse((Path(root) / "model.gguf.part").exists())

    def test_hash_mismatch_never_becomes_final(self):
        wrong = b"x" * len(MAIN)
        with temp_dir() as root:
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=lambda *a, **k: FakeResponse(wrong))
            self.assertFalse(downloader._ensure_file("model.gguf", fixture_profile().model_sha256, len(MAIN), fixture_profile().model_url))
            self.assertFalse((Path(root) / "model.gguf").exists())

    def test_atomic_replace_failure_isolated(self):
        with temp_dir() as root, patch("src.ai.model_downloader.os.replace", side_effect=PermissionError):
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=lambda *a, **k: FakeResponse(MAIN))
            self.assertFalse(downloader._ensure_file("model.gguf", fixture_profile().model_sha256, len(MAIN), fixture_profile().model_url))
            self.assertFalse((Path(root) / "model.gguf").exists())

    def test_insufficient_disk_space_prevents_network(self):
        get = Mock()
        with temp_dir() as root, patch("src.ai.model_downloader._free_space_bytes", return_value=0):
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=get)
            self.assertFalse(downloader.ensure_ready())
            get.assert_not_called()

    def test_cancel_removes_partial(self):
        event = threading.Event()
        event.set()
        with temp_dir() as root:
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=lambda *a, **k: FakeResponse(MAIN), cancel_event=event)
            self.assertFalse(downloader._ensure_file("model.gguf", fixture_profile().model_sha256, len(MAIN), fixture_profile().model_url))
            self.assertFalse((Path(root) / "model.gguf.part").exists())

    def test_https_only_and_insecure_redirect_blocked(self):
        self.assertTrue(_is_https_url("https://huggingface.co/a"))
        self.assertFalse(_is_https_url("http://huggingface.co/a"))
        with temp_dir() as root:
            profile = fixture_profile(model_url="http://example.test/model.gguf")
            downloader = ModelDownloader(profile, Path(root), request_get=Mock())
            self.assertFalse(downloader._ensure_file(profile.model_filename, profile.model_sha256, profile.model_size_bytes, profile.model_url))

    def test_path_traversal_filename_rejected(self):
        profile = fixture_profile(model_filename="../model.gguf")
        with temp_dir() as root:
            downloader = ModelDownloader(profile, Path(root), request_get=Mock())
            self.assertFalse(downloader.ensure_ready())
            self.assertFalse((Path(root).parent / "model.gguf").exists())

    def test_concurrent_download_lock_prevents_shared_partial_write(self):
        with temp_dir() as root:
            lock = Path(root) / "model.gguf.download.lock"
            lock.write_text("other instance", encoding="utf-8")
            get = Mock()
            downloader = ModelDownloader(fixture_profile(), Path(root), request_get=get)
            self.assertFalse(downloader._ensure_file("model.gguf", fixture_profile().model_sha256, len(MAIN), fixture_profile().model_url))
            get.assert_not_called()

    def test_error_does_not_expose_url_or_remote_body(self):
        secret_url = "https://example.test/model.gguf?token=BATCH29_SECRET"
        profile = fixture_profile(model_url=secret_url)
        with temp_dir() as root:
            downloader = ModelDownloader(profile, Path(root), request_get=Mock(side_effect=requests.ConnectionError("Authorization: Bearer SECRET")), max_attempts=1)
            self.assertFalse(downloader.ensure_ready())
            self.assertNotIn("SECRET", downloader.error)
            self.assertNotIn("token=", downloader.error)


class Batch29IntegrationPolicyTests(unittest.TestCase):
    def test_models_are_not_bundled_and_diagnostic_is_allowlisted(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "clasq.spec").read_text(encoding="utf-8")
        diagnostic = (root / "src/utils/diagnostic_bundle.py").read_text(encoding="utf-8")
        self.assertIn("GGUF files are deliberately not bundled", spec)
        self.assertIn('"model_binaries_included": False', diagnostic)
        self.assertNotIn("rglob", diagnostic)

    def test_previous_pruning_and_ffmpeg_pin_remain(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "clasq.spec").read_text(encoding="utf-8")
        for name in ("filter_runtime_binaries", "filter_qt_runtime", "filter_pillow_runtime", "filter_qtpdf_runtime"):
            self.assertIn(name, spec)
        ffmpeg = json.loads((root / "packaging/ffmpeg-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(ffmpeg["sha256"], "ad8f211bc894755e0061c55ab280ae00e8d3d4f15a8cc4372b24cfa247b5942e")

    def test_db_schema_remains_v3_without_model_tables(self):
        source = (Path(__file__).resolve().parents[1] / "src/utils/db_manager.py").read_text(encoding="utf-8")
        self.assertIn("_CURRENT_VERSION = 3", source)
        self.assertNotIn("model_download_history", source)

    def test_first_run_consent_copy_and_later_path_exist(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn("약 6.2GB", source)
        self.assertIn("지금 다운로드하시겠습니까?", source)
        self.assertIn("나중에 진행", source)

    def test_startup_does_not_block_valid_cache_on_download_space(self):
        source = (Path(__file__).resolve().parents[1] / "src/ai/startup_worker.py").read_text(encoding="utf-8")
        self.assertNotIn("if free < needed", source)
        self.assertIn("Space is checked per missing artifact", source)


if __name__ == "__main__":
    unittest.main()
