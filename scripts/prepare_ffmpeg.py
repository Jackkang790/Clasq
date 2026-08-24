"""CLI for validating and staging Clasq's pinned FFmpeg artifact."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from scripts.ffmpeg_artifact import (
        FFmpegArtifactError, MANIFEST_RELATIVE_PATH, OVERRIDE_ENV,
        STAGED_RELATIVE_PATH, load_manifest, stage_artifact,
    )
except ModuleNotFoundError:  # Direct ``py scripts/prepare_ffmpeg.py`` execution.
    from ffmpeg_artifact import (
        FFmpegArtifactError, MANIFEST_RELATIVE_PATH, OVERRIDE_ENV,
        STAGED_RELATIVE_PATH, load_manifest, stage_artifact,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    source = args.source or (Path(os.environ[OVERRIDE_ENV]) if os.environ.get(OVERRIDE_ENV) else None)
    if source is None:
        parser.error(f"provide --source or {OVERRIDE_ENV}")
    project = args.project.resolve()
    try:
        manifest = load_manifest(project / MANIFEST_RELATIVE_PATH)
        staged = stage_artifact(source, project / STAGED_RELATIVE_PATH, manifest)
    except FFmpegArtifactError as exc:
        parser.error(str(exc))
    print(f"Verified and staged FFmpeg: {staged}")
    print(f"sha256={manifest.sha256} size={manifest.size} version={manifest.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
