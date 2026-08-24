import hashlib
import json
import logging
import os
import shutil
import unittest
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.utils.diagnostic_bundle import (
    BUNDLE_FORMAT_VERSION,
    LOG_ARCHIVE_NAMES,
    SUMMARY_SCHEMA_VERSION,
    DiagnosticExportError,
    build_diagnostic_summary,
    default_bundle_filename,
    export_diagnostic_bundle,
)
from src.utils.logging_setup import initialize_runtime_logging, shutdown_runtime_logging


class TestBatch25DiagnosticBundle(unittest.TestCase):
    def setUp(self):
        self.root = Path(".tmp") / "batch25" / uuid.uuid4().hex
        self.logs = self.root / "logs"
        self.out = self.root / "exports"
        self.logs.mkdir(parents=True)
        self.out.mkdir(parents=True)
        self.logs.joinpath("clasq.log").write_text("startup ok\n", encoding="utf-8")

    def tearDown(self):
        shutdown_runtime_logging()
        shutil.rmtree(self.root, ignore_errors=True)

    def export(self, name="diagnostics.zip", **kwargs):
        return export_diagnostic_bundle(self.out / name, log_directory=self.logs, **kwargs)

    def read(self, result):
        with zipfile.ZipFile(result.path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def test_default_filename_is_utc_and_identifier_free(self):
        name = default_bundle_filename(datetime(2026, 8, 24, 8, 30, tzinfo=timezone.utc))
        self.assertEqual(name, "Clasq-Diagnostics-20260824-083000Z.zip")

    def test_summary_schema_and_privacy_flags(self):
        summary = build_diagnostic_summary(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(summary["schema_version"], SUMMARY_SCHEMA_VERSION)
        self.assertEqual(summary["database"], {"schema_version": 3, "included": False})
        self.assertFalse(any(summary["privacy"].values()))

    def test_summary_has_no_username_hostname_or_environment_dump(self):
        with mock.patch.dict(os.environ, {"USERNAME": "Batch25User", "COMPUTERNAME": "Batch25Host", "SECRET": "x"}):
            raw = json.dumps(build_diagnostic_summary())
        self.assertNotIn("Batch25User", raw)
        self.assertNotIn("Batch25Host", raw)
        self.assertNotIn('"SECRET"', raw)

    def test_hardware_allowlist(self):
        info = SimpleNamespace(gpu_available=True, gpu_name="RTX Test", gpu_vram_mb=12288,
                               gpu_vram_free_mb=10000, serial="forbidden")
        hardware = build_diagnostic_summary(hardware_detector=lambda: info)["hardware"]
        self.assertEqual(hardware["gpu_model"], "RTX Test")
        self.assertNotIn("serial", hardware)

    def test_hardware_query_failure_isolated(self):
        def fail():
            raise RuntimeError("private traceback detail")
        raw = json.dumps(build_diagnostic_summary(hardware_detector=fail))
        self.assertIn("query_failed", raw)
        self.assertNotIn("private traceback detail", raw)

    def test_startup_hardware_snapshot_precedes_transient_requery(self):
        info = SimpleNamespace(gpu_available=True, gpu_name="RTX Snapshot", gpu_vram_mb=24576,
                               gpu_vram_free_mb=22000)
        manager = SimpleNamespace(hardware_info=info, _profile=None, _proc=None, is_available=True)
        summary = build_diagnostic_summary(
            server_manager=manager,
            hardware_detector=lambda: (_ for _ in ()).throw(RuntimeError("transient")),
        )
        self.assertEqual(summary["hardware"]["gpu_model"], "RTX Snapshot")

    def test_profile_uses_basenames_not_paths(self):
        profile = SimpleNamespace(name="12GB", context_size=8192,
                                  model_filename="model.gguf", mmproj_filename="mmproj.gguf",
                                  model_url="https://huggingface.co/example/model/resolve/main/model.gguf",
                                  model_sha256="a" * 64, mmproj_sha256="b" * 64)
        summary = build_diagnostic_summary(server_manager=SimpleNamespace(
            _profile=profile, _proc=None, is_available=False))
        raw = json.dumps(summary)
        self.assertIn("model.gguf", raw)
        self.assertIn("example/model", raw)
        self.assertNotIn("models_dir", raw)

    def test_bundle_structure_is_allowlisted(self):
        result = self.export()
        self.assertEqual(set(result.archive_files), {
            "README.txt", "diagnostic-summary.json", "manifest.json", "logs/clasq.log"
        })

    def test_active_and_existing_rotated_logs_included(self):
        self.logs.joinpath("clasq.log.1").write_text("old", encoding="utf-8")
        self.logs.joinpath("clasq.log.4").write_text("older", encoding="utf-8")
        names = set(self.export().archive_files)
        self.assertIn("logs/clasq.log.1", names)
        self.assertIn("logs/clasq.log.4", names)

    def test_missing_rotations_are_allowed(self):
        self.assertNotIn("logs/clasq.log.1", self.export().archive_files)

    def test_unlisted_files_and_directories_never_included(self):
        self.logs.joinpath("user.db").write_bytes(b"private")
        self.logs.joinpath("model.gguf").write_bytes(b"model")
        (self.logs / "cache").mkdir()
        self.logs.joinpath("cache", "document.pdf").write_bytes(b"document")
        names = "\n".join(self.export().archive_files)
        self.assertNotIn("user.db", names)
        self.assertNotIn("gguf", names)
        self.assertNotIn("document.pdf", names)

    def test_secret_and_authorization_redaction(self):
        self.logs.joinpath("clasq.log").write_text(
            "api_key=API_SECRET_BATCH25 Authorization: Bearer BEARER_SECRET_BATCH25\n"
            "CLASQ_SIGN_PFX_PASSWORD=PFX_SECRET_BATCH25\n", encoding="utf-8")
        data = self.read(self.export())["logs/clasq.log"].decode()
        for secret in ("API_SECRET_BATCH25", "BEARER_SECRET_BATCH25", "PFX_SECRET_BATCH25"):
            self.assertNotIn(secret, data)

    def test_full_windows_user_path_redacted(self):
        path = r"C:\Users\TestUser\Confidential\Client\secret.pdf"
        self.logs.joinpath("clasq.log").write_text(f"failed path={path}\n", encoding="utf-8")
        data = self.read(self.export())["logs/clasq.log"].decode()
        self.assertNotIn(path, data)
        self.assertIn("<user-path>", data)

    def test_username_and_hostname_in_log_are_redacted(self):
        with mock.patch.dict(os.environ, {"USERNAME": "Batch25User", "COMPUTERNAME": "Batch25Host"}):
            self.logs.joinpath("clasq.log").write_text("Batch25User Batch25Host", encoding="utf-8")
            data = self.read(self.export())["logs/clasq.log"].decode()
        self.assertNotIn("Batch25User", data)
        self.assertNotIn("Batch25Host", data)

    def test_newline_is_preserved_without_log_injection_reassembly(self):
        self.logs.joinpath("clasq.log").write_text("one\ntwo\n", encoding="utf-8")
        self.assertEqual(self.read(self.export())["logs/clasq.log"], b"one\ntwo\n")

    def test_manifest_schema_paths_sizes_and_hashes(self):
        files = self.read(self.export())
        manifest = json.loads(files["manifest.json"])
        self.assertEqual(manifest["bundle_format_version"], BUNDLE_FORMAT_VERSION)
        self.assertEqual(manifest["file_count"], len(manifest["files"]))
        for item in manifest["files"]:
            self.assertFalse(Path(item["archive_path"]).is_absolute())
            self.assertEqual(item["byte_size"], len(files[item["archive_path"]]))
            self.assertEqual(item["sha256"], hashlib.sha256(files[item["archive_path"]]).hexdigest())

    def test_final_zip_sha_and_crc(self):
        result = self.export()
        self.assertEqual(result.sha256, hashlib.sha256(result.path.read_bytes()).hexdigest())
        with zipfile.ZipFile(result.path) as archive:
            self.assertIsNone(archive.testzip())

    def test_flush_before_snapshot_and_logging_continues(self):
        shutdown_runtime_logging()
        self.logs.joinpath("clasq.log").unlink()
        initialize_runtime_logging(log_directory=self.logs)
        logging.getLogger("batch25").info("before export")
        result = self.export()
        logging.getLogger("batch25").info("after export")
        from src.utils.logging_setup import flush_runtime_logging
        flush_runtime_logging()
        self.assertIn(b"before export", self.read(result)["logs/clasq.log"])
        self.assertIn("after export", self.logs.joinpath("clasq.log").read_text(encoding="utf-8"))

    def test_source_logs_are_not_modified(self):
        before = self.logs.joinpath("clasq.log").read_bytes()
        self.export()
        self.assertEqual(before, self.logs.joinpath("clasq.log").read_bytes())

    def test_unicode_and_space_destination(self):
        folder = self.out / "한글 진단 폴더"
        folder.mkdir()
        result = export_diagnostic_bundle(folder / "진단 결과.zip", log_directory=self.logs)
        self.assertTrue(result.path.is_file())

    def test_invalid_destination_rejected(self):
        with self.assertRaises(DiagnosticExportError):
            self.export("not-a-zip.txt")

    def test_missing_parent_rejected(self):
        with self.assertRaises(DiagnosticExportError):
            export_diagnostic_bundle(self.out / "missing" / "x.zip", log_directory=self.logs)

    def test_existing_destination_requires_explicit_overwrite(self):
        target = self.out / "existing.zip"
        target.write_bytes(b"old")
        with self.assertRaises(FileExistsError):
            export_diagnostic_bundle(target, log_directory=self.logs)
        self.assertEqual(target.read_bytes(), b"old")
        self.assertTrue(export_diagnostic_bundle(target, log_directory=self.logs, overwrite=True).path.exists())

    def test_write_failure_leaves_no_partial_zip(self):
        target = self.out / "failed.zip"
        with mock.patch("src.utils.diagnostic_bundle.zipfile.ZipFile", side_effect=OSError("disk full")):
            with self.assertRaises(DiagnosticExportError):
                export_diagnostic_bundle(target, log_directory=self.logs)
        self.assertFalse(target.exists())
        self.assertFalse(any(p.name.endswith(".tmp") for p in self.out.iterdir()))

    def test_replace_failure_preserves_existing_destination(self):
        target = self.out / "existing.zip"
        target.write_bytes(b"old")
        with mock.patch("src.utils.diagnostic_bundle.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(DiagnosticExportError):
                export_diagnostic_bundle(target, log_directory=self.logs, overwrite=True)
        self.assertEqual(target.read_bytes(), b"old")

    def test_rotation_race_missing_source_is_tolerated(self):
        original = shutil.copyfile
        def rotating_copy(source, destination):
            if str(source).endswith("clasq.log.1"):
                Path(source).unlink(missing_ok=True)
                raise FileNotFoundError
            return original(source, destination)
        self.logs.joinpath("clasq.log.1").write_text("old", encoding="utf-8")
        with mock.patch("src.utils.diagnostic_bundle.shutil.copyfile", side_effect=rotating_copy):
            result = self.export()
        self.assertNotIn("logs/clasq.log.1", result.archive_files)

    def test_export_has_no_network_or_shell_execution(self):
        source = Path("src/utils/diagnostic_bundle.py").read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "socket", "shell=True", "subprocess"):
            self.assertNotIn(forbidden, source)

    def test_bundle_size_is_bounded_by_allowlisted_inputs(self):
        self.logs.joinpath("unlisted.bin").write_bytes(b"x" * 2_000_000)
        result = self.export()
        self.assertLess(result.byte_size, 100_000)

    def test_ui_exposes_explicit_local_export_action(self):
        source = Path("src/ui/views/settings_view.py").read_text(encoding="utf-8")
        self.assertIn('QPushButton("진단 정보 내보내기")', source)
        self.assertIn("diagnostics_btn.clicked.connect(self.export_diagnostics)", source)
        self.assertIn("QFileDialog.getSaveFileName", source)

    def test_ui_requires_confirmation_and_has_no_upload_action(self):
        source = Path("src/ui/views/settings_view.py").read_text(encoding="utf-8")
        self.assertIn("QMessageBox.question", source)
        self.assertNotIn("requests.", source)
        self.assertNotIn("upload", source.lower())

    def test_main_window_passes_runtime_state_without_startup_export(self):
        source = Path("main.py").read_text(encoding="utf-8")
        self.assertIn("server_manager=self._server_manager", source)
        self.assertNotIn("export_diagnostic_bundle", source)


if __name__ == "__main__":
    unittest.main()
