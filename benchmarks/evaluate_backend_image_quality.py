"""Remote/local image quality comparison; outputs only to a requested path."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image

from benchmark_product_backends import anonymous_id, set_backend
from src.ai import ImageAnalyzer
from src.utils.core import scan_directory_files


def norm(value) -> str:
    return " ".join(str(value or "").casefold().split())


def similarity(left, right) -> float:
    return round(SequenceMatcher(None, norm(left), norm(right)).ratio(), 3)


def select_images(root: str, count: int) -> list[str]:
    candidates = []
    add_path = Path(root) / "Add.png"
    if add_path.is_file():
        candidates.append(str(add_path))
    valid = []
    root_path = Path(root).resolve()
    for raw in scan_directory_files(root):
        path = Path(raw)
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"} or path == add_path:
            continue
        try:
            size = path.stat().st_size
            if not (500 <= size <= 15 * 1024 * 1024):
                continue
            with Image.open(path) as image:
                width, height = image.size
            if width < 64 or height < 32:
                continue
        except Exception:
            continue
        digest = hashlib.sha256(os.path.normcase(str(path)).encode()).digest()
        try:
            relative = path.resolve().relative_to(root_path)
            parts = relative.parts[:-1]
        except (OSError, ValueError):
            parts = path.parts[:-1]
        # A pure hash sample can be dominated by thousands of sprites from one
        # extracted package.  Round-robin over path, size, and aspect buckets so
        # the quality review includes materially different image workloads.
        path_bucket = "/".join(parts[:2]).casefold() if parts else "__root__"
        pixels = width * height
        size_bucket = "small" if pixels < 256 * 256 else "medium" if pixels < 1280 * 720 else "large"
        ratio = width / max(height, 1)
        aspect_bucket = "wide" if ratio >= 1.5 else "tall" if ratio <= 0.67 else "square"
        valid.append(((path_bucket, size_bucket, aspect_bucket), digest, str(path)))
    valid.sort(key=lambda item: item[1])
    buckets: dict[tuple[str, str, str], list[tuple[bytes, str]]] = {}
    for bucket, digest, path in valid:
        buckets.setdefault(bucket, []).append((digest, path))
    ordered_buckets = sorted(buckets, key=lambda key: hashlib.sha256("|".join(key).encode()).digest())
    while ordered_buckets and len(candidates) < count:
        next_round = []
        for bucket in ordered_buckets:
            items = buckets[bucket]
            if items and len(candidates) < count:
                candidates.append(items.pop(0)[1])
            if items:
                next_round.append(bucket)
        ordered_buckets = next_round
    if len(candidates) < count:
        raise RuntimeError(f"only {len(candidates)} eligible images")
    return candidates[:count]


def analyze_images(paths: list[str], concurrency: int) -> dict[str, dict]:
    def one(path: str):
        result = ImageAnalyzer().analyze_image(path)
        return anonymous_id(path), result
    output = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(one, path) for path in paths]
        for index, future in enumerate(as_completed(futures), 1):
            key, result = future.result()
            output[key] = result
            print(f"completed={index}/{len(paths)}", flush=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=r"C:\Users\USER1\Downloads")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--remote-url", default="http://127.0.0.1:8100/v1")
    parser.add_argument("--remote-model", default="qwen3-vl-8b")
    parser.add_argument("--local-url", default="http://127.0.0.1:8202/v1")
    parser.add_argument("--local-model", default="qwen3-vl-8b-vllm-4bit")
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--csv-output", required=True)
    args = parser.parse_args()

    paths = select_images(args.root, args.count)
    set_backend(args.remote_url, args.remote_model)
    remote = analyze_images(paths, 2)
    set_backend(args.local_url, args.local_model)
    local = analyze_images(paths, 2)

    rows = []
    for path in paths:
        key = anonymous_id(path)
        remote_result, local_result = remote[key], local[key]
        a, b = remote_result.get("metadata", {}), local_result.get("metadata", {})
        tags_a, tags_b = {norm(x) for x in a.get("tags", [])}, {norm(x) for x in b.get("tags", [])}
        union = tags_a | tags_b
        remote_ocr, local_ocr = str(a.get("ocr_text", "")), str(b.get("ocr_text", ""))
        rows.append({
            "sample_id": key,
            "format": Path(path).suffix.lower(),
            "remote_ocr": remote_ocr,
            "local_ocr": local_ocr,
            "ocr_exact": norm(remote_ocr) == norm(local_ocr),
            "ocr_similarity": similarity(remote_ocr, local_ocr),
            "remote_display_name": a.get("display_name", ""),
            "local_display_name": b.get("display_name", ""),
            "display_name_similarity": similarity(a.get("display_name"), b.get("display_name")),
            "remote_category": a.get("category", ""),
            "local_category": b.get("category", ""),
            "category_match": norm(a.get("category")) == norm(b.get("category")),
            "remote_sub_category": a.get("sub_category", ""),
            "local_sub_category": b.get("sub_category", ""),
            "sub_category_match": norm(a.get("sub_category")) == norm(b.get("sub_category")),
            "remote_tags": " | ".join(map(str, a.get("tags", []))),
            "local_tags": " | ".join(map(str, b.get("tags", []))),
            "tag_jaccard": round(len(tags_a & tags_b) / len(union), 3) if union else 1.0,
            "remote_description": a.get("description", ""),
            "local_description": b.get("description", ""),
            "description_similarity": similarity(a.get("description"), b.get("description")),
            "remote_confidence": a.get("confidence", ""),
            "local_confidence": b.get("confidence", ""),
            "required_metadata_complete": all(a.get(x) and b.get(x) for x in ("display_name", "tags", "description")),
            "review_status": "pending",
            "review_note": "",
        })

    csv_path = Path(args.csv_output)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "samples": len(rows),
        "ocr_exact_rate": round(statistics.fmean(row["ocr_exact"] for row in rows), 3),
        "mean_ocr_similarity": round(statistics.fmean(row["ocr_similarity"] for row in rows), 3),
        "mean_display_name_similarity": round(statistics.fmean(row["display_name_similarity"] for row in rows), 3),
        "mean_tag_jaccard": round(statistics.fmean(row["tag_jaccard"] for row in rows), 3),
        "category_match_rate": round(statistics.fmean(row["category_match"] for row in rows), 3),
        "sub_category_match_rate": round(statistics.fmean(row["sub_category_match"] for row in rows), 3),
        "mean_description_similarity": round(statistics.fmean(row["description_similarity"] for row in rows), 3),
        "metadata_completeness_rate": round(statistics.fmean(row["required_metadata_complete"] for row in rows), 3),
        "review_pending": len(rows),
    }
    Path(args.json_output).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
