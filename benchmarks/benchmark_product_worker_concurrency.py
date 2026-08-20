"""Benchmark the real FolderScanAndTagWorker path without touching the product DB.

The same source paths are analyzed for concurrency 1 and 2. Each run uses an
isolated temporary SQLite DB and duplicate_policy=keep, so source files are
never moved or renamed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.core import scan_directory_files
from src.utils.file_pipeline import FileAnalyzer, TextExtractor
from src.utils.main_processor import MainProcessor
from src.utils.query_parser import SearchQueryParser
from src.utils.workers import FolderScanAndTagWorker


TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".xml", ".yaml", ".yml",
    ".csv", ".html", ".htm",
}


def select_samples(root: str, count: int) -> list[str]:
    samples = [
        path for path in scan_directory_files(root)
        if Path(path).suffix.lower() in TEXT_EXTENSIONS
    ]
    if len(samples) < count:
        raise RuntimeError(f"지원되는 텍스트 파일이 {len(samples)}개뿐입니다 (필요: {count})")
    return samples[:count]


def coordinator(db_path: str) -> MainProcessor:
    analyzer = FileAnalyzer()
    processor = MainProcessor(
        TextExtractor(), analyzer, SearchQueryParser(client=analyzer.client), db_path=db_path,
    )
    processor.registry.duplicate_policy = "keep"
    return processor


def run_once(paths: list[str], concurrency: int, db_path: str) -> dict:
    worker = FolderScanAndTagWorker(
        paths,
        main_processor=coordinator(db_path),
        batch_limit=None,
        total_pending=len(paths),
        concurrency=concurrency,
    )
    worker.run()
    if worker.last_stats is None:
        raise RuntimeError("FolderScanAndTagWorker가 결과 통계를 반환하지 않았습니다")
    result = dict(worker.last_stats)
    result["concurrency"] = concurrency
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path.home() / "Downloads"))
    parser.add_argument("--files", type=int, default=50)
    parser.add_argument(
        "--output", default="benchmarks/product_worker_concurrency_results.json"
    )
    args = parser.parse_args()

    paths = select_samples(args.root, args.files)
    print(f"[SAMPLES] root={args.root} files={len(paths)}", flush=True)
    results = []
    run_id = uuid.uuid4().hex
    db_paths = [PROJECT_ROOT / f".benchmark-worker-{run_id}-c{value}.db" for value in (1, 2)]
    try:
        for concurrency, db_path in zip((1, 2), db_paths):
            db_path = str(db_path)
            results.append(run_once(paths, concurrency, db_path))
    finally:
        for db_path in db_paths:
            for suffix in ("", "-wal", "-shm"):
                try:
                    Path(f"{db_path}{suffix}").unlink()
                except FileNotFoundError:
                    pass

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"samples": paths, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[RESULT] {output.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
