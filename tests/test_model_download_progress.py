import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from src.ai.model_downloader import ModelDownloader, _parse_content_range
from src.ai.runtime_profile import RuntimeProfile
from src.ai.startup_worker import StartupWorker


PAYLOAD = b"0123456789"


def profile():
    return RuntimeProfile(
        name="download-test", description="test",
        model_filename="model.gguf", model_url="https://example.test/model.gguf",
        model_sha256=hashlib.sha256(PAYLOAD).hexdigest(), model_size_bytes=len(PAYLOAD),
        mmproj_filename="mmproj.gguf", mmproj_url="https://example.test/mmproj.gguf",
        mmproj_sha256="0" * 64, mmproj_size_bytes=1,
        n_gpu_layers=1, context_size=1,
    )


class Response:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {"content-length": str(len(payload))}
        self.url = "https://cdn.example.test/model.gguf?token=secret"

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    def iter_content(self, chunk_size):
        for index in range(0, len(self.payload), 2):
            yield self.payload[index:index + 2]


class ModelDownloadProgressTests(unittest.TestCase):
    def test_qt_signal_preserves_values_over_32_bit(self):
        QCoreApplication.instance() or QCoreApplication([])
        worker = StartupWorker()
        seen = []
        worker.progress_changed.connect(lambda name, current, total: seen.append((current, total)))
        worker.progress_changed.emit("model.gguf", 3_435_973_120, 5_242_880_000)
        self.assertEqual(seen, [(3_435_973_120, 5_242_880_000)])

    def test_content_range_supports_5gb_values(self):
        self.assertEqual(
            _parse_content_range("bytes 2147483648-5242879999/5242880000"),
            (2_147_483_648, 5_242_879_999, 5_242_880_000),
        )

    @patch("src.ai.model_downloader._free_space_bytes", return_value=10**9)
    def test_resume_uses_remote_total_and_starts_at_40_percent(self, _disk):
        progress = []
        calls = []

        def get(_url, **kwargs):
            calls.append(kwargs["headers"])
            return Response(PAYLOAD[4:], 206, {
                "content-length": "6", "content-range": "bytes 4-9/10", "accept-ranges": "bytes",
            })

        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            Path(root, "model.gguf.part").write_bytes(PAYLOAD[:4])
            downloader = ModelDownloader(profile(), Path(root), on_progress=lambda n, c, t: progress.append((c, t)), request_get=get)
            self.assertTrue(downloader._ensure_file("model.gguf", profile().model_sha256, 10, profile().model_url))
            self.assertEqual(calls[0], {"Range": "bytes=4-"})
            self.assertEqual(progress[0], (4, 10))
            self.assertEqual(Path(root, "model.gguf").read_bytes(), PAYLOAD)

    @patch("src.ai.model_downloader._free_space_bytes", return_value=10**9)
    def test_ignored_range_restarts_instead_of_appending(self, _disk):
        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            Path(root, "model.gguf.part").write_bytes(PAYLOAD[:4])
            downloader = ModelDownloader(profile(), Path(root), request_get=lambda *_a, **_k: Response(PAYLOAD))
            self.assertTrue(downloader._ensure_file("model.gguf", profile().model_sha256, 10, profile().model_url))
            self.assertEqual(Path(root, "model.gguf").read_bytes(), PAYLOAD)

    @patch("src.ai.model_downloader._free_space_bytes", return_value=10**9)
    def test_download_larger_than_total_is_rejected_and_not_finalized(self, _disk):
        with tempfile.TemporaryDirectory(dir=".tmp") as root:
            response = Response(PAYLOAD + b"x", headers={"content-length": "10"})
            downloader = ModelDownloader(profile(), Path(root), request_get=lambda *_a, **_k: response, max_attempts=1)
            with self.assertLogs("src.ai.model_downloader", "WARNING") as logs:
                self.assertFalse(downloader._ensure_file("model.gguf", profile().model_sha256, 10, profile().model_url))
            self.assertIn("downloaded exceeds total", " ".join(logs.output))
            self.assertFalse(Path(root, "model.gguf").exists())


if __name__ == "__main__":
    unittest.main()
