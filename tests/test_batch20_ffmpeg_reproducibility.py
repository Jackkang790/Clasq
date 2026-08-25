from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.ffmpeg_artifact import (
    FFmpegArtifactError, MANIFEST_RELATIVE_PATH, STAGED_RELATIVE_PATH,
    load_manifest, resolve_build_ffmpeg, sha256_file, stage_artifact,
    validate_artifact,
)
from src.ai.video_analyzer import FFmpegExecutionError, VideoAnalyzer


def pe_bytes(payload: bytes = b"payload", machine: int = 0x8664) -> bytes:
    data = bytearray(128)
    data[:2] = b"MZ"
    data[0x3C:0x40] = (64).to_bytes(4, "little")
    data[64:68] = b"PE\0\0"
    data[68:70] = machine.to_bytes(2, "little")
    return bytes(data) + payload


class Batch20FFmpegReproducibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "packaging").mkdir()
        self.good = pe_bytes()
        self.digest = hashlib.sha256(self.good).hexdigest()
        self.manifest_data = {
            "name": "ffmpeg", "filename": "ffmpeg.exe", "version": "8.1.2-test",
            "architecture": "windows-x86_64", "size": len(self.good),
            "sha256": self.digest, "provider": "fixture",
            "provider_url": "https://example.invalid/", "build_type": "static",
            "license_facts": ["--enable-gpl"],
        }
        self.manifest_path = self.project / MANIFEST_RELATIVE_PATH
        self.manifest_path.write_text(json.dumps(self.manifest_data), encoding="utf-8")
        self.manifest = load_manifest(self.manifest_path)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative: str, data: bytes) -> Path:
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_repository_manifest_parses_expected_identity(self):
        manifest = load_manifest(Path("packaging/ffmpeg-manifest.json"))
        self.assertEqual(manifest.filename, "ffmpeg.exe")
        self.assertEqual(manifest.version, "8.1.2-full_build-www.gyan.dev")
        self.assertEqual(manifest.size, 242496512)
        self.assertEqual(manifest.sha256, "ad8f211bc894755e0061c55ab280ae00e8d3d4f15a8cc4372b24cfa247b5942e")

    def test_manifest_rejects_invalid_sha256(self):
        self.manifest_data["sha256"] = "bad"
        self.manifest_path.write_text(json.dumps(self.manifest_data), encoding="utf-8")
        with self.assertRaisesRegex(FFmpegArtifactError, "SHA-256"):
            load_manifest(self.manifest_path)

    def test_valid_artifact_passes_size_hash_and_architecture(self):
        source = self.write("input/ffmpeg.exe", self.good)
        result = validate_artifact(source, self.manifest, check_version=False)
        self.assertEqual(result["sha256"], self.digest)

    def test_missing_artifact_fails(self):
        with self.assertRaisesRegex(FFmpegArtifactError, "not found"):
            validate_artifact(self.project / "missing/ffmpeg.exe", self.manifest, check_version=False)

    def test_wrong_filename_fails(self):
        source = self.write("input/not-ffmpeg.exe", self.good)
        with self.assertRaisesRegex(FFmpegArtifactError, "filename"):
            validate_artifact(source, self.manifest, check_version=False)

    def test_wrong_size_fails(self):
        source = self.write("input/ffmpeg.exe", self.good + b"x")
        with self.assertRaisesRegex(FFmpegArtifactError, "size mismatch"):
            validate_artifact(source, self.manifest, check_version=False)

    def test_wrong_hash_fails(self):
        source = self.write("input/ffmpeg.exe", self.good[:-1] + b"x")
        with self.assertRaisesRegex(FFmpegArtifactError, "SHA-256 mismatch"):
            validate_artifact(source, self.manifest, check_version=False)

    def test_wrong_architecture_fails(self):
        wrong = pe_bytes(machine=0x014C)
        self.manifest_data.update(size=len(wrong), sha256=hashlib.sha256(wrong).hexdigest())
        self.manifest_path.write_text(json.dumps(self.manifest_data), encoding="utf-8")
        source = self.write("input/ffmpeg.exe", wrong)
        with self.assertRaisesRegex(FFmpegArtifactError, "architecture"):
            validate_artifact(source, load_manifest(self.manifest_path), check_version=False)

    def test_atomic_staging_preserves_hash(self):
        source = self.write("input/ffmpeg.exe", self.good)
        destination = self.project / STAGED_RELATIVE_PATH
        staged = stage_artifact(source, destination, self.manifest, check_version=False)
        self.assertEqual(staged, destination.resolve())
        self.assertEqual(sha256_file(staged), self.digest)
        self.assertFalse(any(destination.parent.glob(".*.tmp")))

    def test_failed_staging_does_not_create_destination(self):
        source = self.write("input/ffmpeg.exe", self.good + b"bad")
        destination = self.project / STAGED_RELATIVE_PATH
        with self.assertRaises(FFmpegArtifactError):
            stage_artifact(source, destination, self.manifest, check_version=False)
        self.assertFalse(destination.exists())

    def test_validated_override_is_staged(self):
        source = self.write("override/ffmpeg.exe", self.good)
        result = resolve_build_ffmpeg(
            self.project, environ={"CLASQ_FFMPEG_EXE": str(source)}, check_version=False
        )
        self.assertEqual(result, (self.project / STAGED_RELATIVE_PATH).resolve())

    def test_override_hash_mismatch_fails(self):
        source = self.write("override/ffmpeg.exe", self.good[:-1] + b"x")
        with self.assertRaisesRegex(FFmpegArtifactError, "SHA-256 mismatch"):
            resolve_build_ffmpeg(
                self.project, environ={"CLASQ_FFMPEG_EXE": str(source)}, check_version=False
            )

    def test_existing_staged_artifact_supports_offline_resolution(self):
        destination = self.write(STAGED_RELATIVE_PATH.as_posix(), self.good)
        result = resolve_build_ffmpeg(self.project, environ={}, check_version=False)
        self.assertEqual(result, destination.resolve())

    def test_path_and_c_drive_are_not_searched(self):
        path_ffmpeg = self.write("path-bin/ffmpeg.exe", self.good)
        with patch.dict(os.environ, {"PATH": str(path_ffmpeg.parent)}, clear=False):
            with self.assertRaisesRegex(FFmpegArtifactError, "Verified staged"):
                resolve_build_ffmpeg(self.project, environ={}, check_version=False)

    def test_previous_dist_is_not_a_build_input(self):
        self.write("dist-batch19/Clasq/_internal/runtime/ffmpeg.exe", self.good)
        with self.assertRaisesRegex(FFmpegArtifactError, "prepare_ffmpeg.py"):
            resolve_build_ffmpeg(self.project, environ={}, check_version=False)

    def test_spec_keeps_runtime_destination_and_verified_resolver(self):
        spec = Path("clasq.spec").read_text(encoding="utf-8")
        self.assertIn("resolve_build_ffmpeg(project)", spec)
        self.assertIn("binaries.append((str(ffmpeg_source), \"runtime\"))", spec)
        self.assertNotIn(r"C:\ffmpeg\bin\ffmpeg.exe", spec)

    def video_analyzer(self) -> VideoAnalyzer:
        analyzer = VideoAnalyzer.__new__(VideoAnalyzer)
        analyzer.config = SimpleNamespace(
            video_scene_threshold=0.30, video_max_gap_seconds=10,
            video_image_width=640, video_max_frames=24, ffmpeg_timeout=600,
        )
        analyzer.find_ffmpeg = lambda: r"C:\staged runtime\ffmpeg.exe"
        return analyzer

    def test_video_extraction_uses_argument_list_for_unicode_and_spaces(self):
        video = self.write("테스트 영상/샘플 영상.mp4", b"video")
        output = self.project / "output frames"
        output.mkdir()
        (output / "frame_0001.jpg").write_bytes(b"jpg")
        completed = SimpleNamespace(returncode=0, stderr="showinfo pts_time:0.0", stdout="")
        with patch("src.ai.video_analyzer.subprocess.run", return_value=completed) as run:
            frames, timestamps = self.video_analyzer().extract_representative_frames(str(video), output)
        command = run.call_args.args[0]
        self.assertIn(str(video), command)
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual((frames, timestamps), ([output / "frame_0001.jpg"], [0.0]))

    def test_video_nonzero_exit_is_typed(self):
        video = self.write("video.mp4", b"video")
        output = self.project / "frames"
        output.mkdir()
        completed = SimpleNamespace(returncode=2, stderr="invalid input", stdout="")
        with patch("src.ai.video_analyzer.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(FFmpegExecutionError, "invalid input"):
                self.video_analyzer().extract_representative_frames(str(video), output)

    def test_video_timeout_behavior_is_unchanged(self):
        import subprocess
        video = self.write("video.mp4", b"video")
        output = self.project / "frames"
        output.mkdir()
        with patch(
            "src.ai.video_analyzer.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["ffmpeg"], 600),
        ):
            with self.assertRaises(FFmpegExecutionError):
                self.video_analyzer().extract_representative_frames(str(video), output)


if __name__ == "__main__":
    unittest.main()
