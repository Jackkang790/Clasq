"""Validate and atomically stage Clasq's pinned FFmpeg build input."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


MANIFEST_RELATIVE_PATH = Path("packaging/ffmpeg-manifest.json")
STAGED_RELATIVE_PATH = Path(".build/runtime/ffmpeg/ffmpeg.exe")
OVERRIDE_ENV = "CLASQ_FFMPEG_EXE"


class FFmpegArtifactError(RuntimeError):
    """The pinned build input is absent or does not match its manifest."""


@dataclass(frozen=True)
class FFmpegManifest:
    name: str
    filename: str
    version: str
    architecture: str
    size: int
    sha256: str
    provider: str
    provider_url: str
    build_type: str
    license_facts: tuple[str, ...]


def load_manifest(path: Path) -> FFmpegManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FFmpegArtifactError(f"Cannot read FFmpeg manifest {path}: {exc}") from exc
    required = {
        "name", "filename", "version", "architecture", "size", "sha256",
        "provider", "provider_url", "build_type", "license_facts",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise FFmpegArtifactError(f"FFmpeg manifest is missing fields: {missing}")
    digest = str(raw["sha256"]).lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise FFmpegArtifactError("FFmpeg manifest SHA-256 must be 64 hexadecimal characters")
    if raw["filename"] != "ffmpeg.exe" or int(raw["size"]) <= 0:
        raise FFmpegArtifactError("FFmpeg manifest filename or size is invalid")
    return FFmpegManifest(
        name=str(raw["name"]), filename=str(raw["filename"]),
        version=str(raw["version"]), architecture=str(raw["architecture"]),
        size=int(raw["size"]), sha256=digest, provider=str(raw["provider"]),
        provider_url=str(raw["provider_url"]), build_type=str(raw["build_type"]),
        license_facts=tuple(map(str, raw["license_facts"])),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pe_machine(path: Path) -> int:
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise FFmpegArtifactError(f"FFmpeg artifact is not a Windows PE file: {path}")
            stream.seek(0x3C)
            pe_offset = struct.unpack("<I", stream.read(4))[0]
            stream.seek(pe_offset)
            if stream.read(4) != b"PE\0\0":
                raise FFmpegArtifactError(f"FFmpeg artifact has an invalid PE header: {path}")
            return struct.unpack("<H", stream.read(2))[0]
    except (OSError, struct.error) as exc:
        raise FFmpegArtifactError(f"Cannot inspect FFmpeg architecture: {path}") from exc


def validate_artifact(path: Path, manifest: FFmpegManifest, *, check_version: bool = True) -> dict[str, object]:
    path = path.resolve()
    if not path.is_file():
        raise FFmpegArtifactError(f"FFmpeg artifact not found: {path}")
    if path.name.casefold() != manifest.filename.casefold():
        raise FFmpegArtifactError(f"Expected FFmpeg filename {manifest.filename}, got {path.name}")
    actual_size = path.stat().st_size
    if actual_size != manifest.size:
        raise FFmpegArtifactError(f"FFmpeg size mismatch: expected {manifest.size}, got {actual_size}")
    actual_hash = sha256_file(path)
    if actual_hash != manifest.sha256:
        raise FFmpegArtifactError(
            f"FFmpeg SHA-256 mismatch: expected {manifest.sha256}, got {actual_hash}"
        )
    if manifest.architecture == "windows-x86_64" and _pe_machine(path) != 0x8664:
        raise FFmpegArtifactError("FFmpeg architecture mismatch: expected Windows x86-64")
    version_line: Optional[str] = None
    if check_version:
        try:
            result = subprocess.run(
                [str(path), "-version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FFmpegArtifactError(f"Cannot execute FFmpeg version check: {exc}") from exc
        output = (result.stdout or "") + (result.stderr or "")
        version_line = output.splitlines()[0] if output.splitlines() else ""
        if result.returncode != 0 or manifest.version not in version_line:
            raise FFmpegArtifactError(
                f"FFmpeg version mismatch: expected {manifest.version}, got {version_line!r}"
            )
    return {"path": str(path), "size": actual_size, "sha256": actual_hash,
            "version_line": version_line}


def stage_artifact(
    source: Path, destination: Path, manifest: FFmpegManifest, *, check_version: bool = True
) -> Path:
    source = source.resolve()
    destination = destination.resolve()
    validate_artifact(source, manifest, check_version=check_version)
    if source == destination:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(source, temporary)
        if temporary.stat().st_size != manifest.size or sha256_file(temporary) != manifest.sha256:
            raise FFmpegArtifactError("Staged FFmpeg changed during copy")
        if manifest.architecture == "windows-x86_64" and _pe_machine(temporary) != 0x8664:
            raise FFmpegArtifactError("Staged FFmpeg architecture mismatch")
        os.replace(temporary, destination)
        validate_artifact(destination, manifest, check_version=check_version)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def resolve_build_ffmpeg(
    project: Path, *, environ: Optional[Mapping[str, str]] = None,
    check_version: bool = True,
) -> Path:
    """Resolve only an explicit override or the dedicated verified staging file."""
    project = project.resolve()
    environment = os.environ if environ is None else environ
    manifest = load_manifest(project / MANIFEST_RELATIVE_PATH)
    staged = project / STAGED_RELATIVE_PATH
    override = environment.get(OVERRIDE_ENV, "").strip()
    if override:
        return stage_artifact(Path(override), staged, manifest, check_version=check_version)
    try:
        validate_artifact(staged, manifest, check_version=check_version)
    except FFmpegArtifactError as exc:
        raise FFmpegArtifactError(
            f"Verified staged FFmpeg is required at {staged}. Run "
            f"'py -3.13 scripts/prepare_ffmpeg.py --source <ffmpeg.exe>' or set "
            f"{OVERRIDE_ENV} to the pinned artifact. {exc}"
        ) from exc
    return staged
