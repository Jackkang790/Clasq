"""Offline, local-only leave-one-out evaluation for folder retrieval.

The evaluated file is treated as if it were placed directly in the managed
root. No Qwen calls, database writes, directory creation, or file moves occur.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.recommendation.folder_repository import FolderProfileRepository
from src.recommendation.family import FolderFamilyResolver
from src.recommendation.profile_builder import FolderProfileBuilder
from src.recommendation.retriever import FolderCandidateRetriever, RetrievalConfig
from src.recommendation.scope_policy import RootInboxOrganizationPolicy
from src.utils.core import scan_directory_files


CONFIGURATIONS = (
    RetrievalConfig(
        name="legacy",
        folder_name_weight=0.25, filename_weight=0.20, tag_weight=0.20,
        category_weight=0.15, extension_weight=0.10, local_text_weight=0.10,
    ),
    RetrievalConfig(
        name="semantic_balanced",
        folder_name_weight=0.30, filename_weight=0.25, tag_weight=0.18,
        category_weight=0.05, extension_weight=0.03, local_text_weight=0.19,
    ),
    RetrievalConfig(
        name="name_text_heavy",
        folder_name_weight=0.35, filename_weight=0.30, tag_weight=0.12,
        category_weight=0.03, extension_weight=0.02, local_text_weight=0.18,
    ),
    RetrievalConfig(
        name="content_balanced",
        folder_name_weight=0.25, filename_weight=0.25, tag_weight=0.20,
        category_weight=0.05, extension_weight=0.05, local_text_weight=0.20,
    ),
)
NONE_THRESHOLD = 0.30


def _file_type(extension: str) -> str:
    extension = extension.casefold()
    if extension in {"ppt", "pptx"}:
        return "pptx"
    if extension == "pdf":
        return "pdf"
    if extension in {"doc", "docx"}:
        return "docx"
    if extension == "txt":
        return "txt"
    if extension in {"md", "markdown"}:
        return "md"
    if extension in {"jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff"}:
        return "image"
    if extension in {"mp4", "mkv", "avi"}:
        return "video"
    return "other"


def _candidate_payload(candidates):
    return [
        {
            "rank": item.rank,
            "folder": item.folder_path,
            "family": item.family_id,
            "total_score": item.local_score,
            "score_breakdown": dict(item.score_breakdown),
            "structural_penalty": item.structural_penalty,
        }
        for item in candidates[:5]
    ]


def evaluate(root: str, db_path: str, sample_count: int, seed: int = 42):
    started = time.perf_counter()
    root = os.path.abspath(root)
    policy = RootInboxOrganizationPolicy()
    scanned = scan_directory_files(root)
    repository = FolderProfileRepository(root, db_path, scope_policy=policy)
    records = repository.load_records(scanned)
    builder = FolderProfileBuilder(repository)
    full_profiles = builder.build(records)

    direct_counts = Counter(os.path.dirname(record.file_path) for record in records)
    eligible = [
        record for record in records
        if record.analyzed
        and os.path.dirname(record.file_path) != root
        and policy.is_destination_folder(os.path.dirname(record.file_path), root)
        and direct_counts[os.path.dirname(record.file_path)] >= 2
        and os.path.isfile(record.file_path)
    ]
    eligible.sort(key=lambda record: record.file_path.casefold())
    if sample_count <= 0 or sample_count >= len(eligible):
        targets = eligible
    else:
        targets = random.Random(seed).sample(eligible, sample_count)
        targets.sort(key=lambda record: record.file_path.casefold())

    all_directories = []
    excluded_package = []
    for current, directories, _files in os.walk(root, followlinks=False):
        all_directories.append(current)
        for name in list(directories):
            path = os.path.join(current, name)
            if policy.is_package_or_vendor_folder(path, root):
                excluded_package.append(path)

    metrics = {
        config.name: {
            "config": config,
            "top1": 0, "top3": 0, "top5": 0,
            "family_top1": 0, "family_top3": 0, "family_top5": 0,
            "hierarchy_acceptable": 0,
            "reciprocal_rank": 0.0, "none": 0,
            "unique_family_counts": [],
            "retrieval_times": [], "details": [],
        }
        for config in CONFIGURATIONS
    }

    for record in targets:
        original_context = repository.context_from_record(record)
        virtual_path = os.path.join(root, record.file_name)
        # Source parent/path is deliberately removed. Retrieval sees only data
        # available for a new root-level inbox file.
        context = replace(
            original_context,
            file_path=virtual_path,
            current_folder=root,
        )
        profiles = builder.build(records, exclude_paths=(record.file_path,))
        expected = repository.normalized(os.path.dirname(record.file_path))
        expected_profile = next(
            (profile for profile in profiles.values()
             if repository.normalized(profile.folder_path) == expected), None
        )
        family_map = FolderFamilyResolver().resolve(profiles)
        expected_info = family_map.get(expected_profile.folder_id) if expected_profile else None
        for values in metrics.values():
            retriever = FolderCandidateRetriever(values["config"])
            candidates = retriever.retrieve(context, profiles, top_k=5)
            values["retrieval_times"].append(retriever.last_elapsed_sec)
            paths = [repository.normalized(item.folder_path) for item in candidates]
            rank = paths.index(expected) + 1 if expected in paths else 0
            family_rank = next(
                (index for index, item in enumerate(candidates, start=1)
                 if expected_info and item.family_id == expected_info.family_id), 0
            )
            values["top1"] += rank == 1
            values["top3"] += bool(rank and rank <= 3)
            values["top5"] += bool(rank and rank <= 5)
            values["family_top1"] += family_rank == 1
            values["family_top3"] += bool(family_rank and family_rank <= 3)
            values["family_top5"] += bool(family_rank and family_rank <= 5)
            top = candidates[0] if candidates else None
            hierarchy_ok = bool(
                top and expected_profile and (
                    expected_profile.folder_id in top.ancestor_folder_ids
                    or top.folder_id in expected_info.ancestor_folder_ids
                )
            )
            values["hierarchy_acceptable"] += hierarchy_ok
            values["reciprocal_rank"] += 1.0 / rank if rank else 0.0
            values["none"] += not candidates or candidates[0].local_score < NONE_THRESHOLD
            values["unique_family_counts"].append(len({item.family_id for item in candidates}))
            values["details"].append({
                "file": record.file_path,
                "virtual_source": virtual_path,
                "correct_folder": os.path.dirname(record.file_path),
                "correct_family": expected_info.family_id if expected_info else "",
                "correct_rank": rank,
                "family_rank": family_rank,
                "hierarchy_acceptable": hierarchy_ok,
                "top5_candidates": _candidate_payload(candidates),
            })

    count = len(targets)
    config_results = []
    for name, values in metrics.items():
        config = values["config"]
        config_results.append({
            "name": name,
            "weights": {
                "folder_path": config.folder_name_weight,
                "filename": config.filename_weight,
                "tag": config.tag_weight,
                "text": config.local_text_weight,
                "category": config.category_weight,
                "extension": config.extension_weight,
            },
            "top1_accuracy": values["top1"] / count if count else 0.0,
            "top3_recall": values["top3"] / count if count else 0.0,
            "top5_recall": values["top5"] / count if count else 0.0,
            "family_top1_accuracy": values["family_top1"] / count if count else 0.0,
            "family_top3_recall": values["family_top3"] / count if count else 0.0,
            "family_top5_recall": values["family_top5"] / count if count else 0.0,
            "ancestor_descendant_acceptable_rate": (
                values["hierarchy_acceptable"] / count if count else 0.0
            ),
            "mrr": values["reciprocal_rank"] / count if count else 0.0,
            "none_rate": values["none"] / count if count else 0.0,
            "average_local_retrieval_ms": (
                1000 * sum(values["retrieval_times"]) / len(values["retrieval_times"])
                if values["retrieval_times"] else 0.0
            ),
            "average_unique_families_in_top5": (
                sum(values["unique_family_counts"]) / len(values["unique_family_counts"])
                if values["unique_family_counts"] else 0.0
            ),
        })
    best = max(
        config_results,
        key=lambda item: (
            item["top3_recall"], item["top5_recall"], item["top1_accuracy"],
            item["family_top3_recall"], item["mrr"], -item["none_rate"],
        ),
        default=None,
    )
    best_details = metrics[best["name"]]["details"] if best else []

    metadata_light_eligible = [
        record for record in records
        if not record.analyzed
        and os.path.dirname(record.file_path) != root
        and policy.is_destination_folder(os.path.dirname(record.file_path), root)
        and direct_counts[os.path.dirname(record.file_path)] >= 2
        and os.path.isfile(record.file_path)
    ]
    metadata_light_eligible.sort(key=lambda record: record.file_path.casefold())
    light_sample_count = min(50, len(metadata_light_eligible))
    light_targets = random.Random(seed + 1).sample(
        metadata_light_eligible, light_sample_count
    ) if light_sample_count else []
    light_metrics = {
        "top1": 0, "top3": 0, "top5": 0,
        "family_top1": 0, "family_top3": 0, "family_top5": 0,
        "mrr": 0.0, "none": 0,
    }
    best_config = next(
        config for config in CONFIGURATIONS if best and config.name == best["name"]
    ) if best else RetrievalConfig()
    for record in light_targets:
        original_context = repository.context_from_record(record)
        context = replace(
            original_context,
            file_path=os.path.join(root, record.file_name),
            current_folder=root,
        )
        profiles = builder.build(records, exclude_paths=(record.file_path,))
        family_map = FolderFamilyResolver().resolve(profiles)
        expected = repository.normalized(os.path.dirname(record.file_path))
        expected_profile = next(
            (profile for profile in profiles.values()
             if repository.normalized(profile.folder_path) == expected), None
        )
        expected_info = family_map.get(expected_profile.folder_id) if expected_profile else None
        candidates = FolderCandidateRetriever(best_config).retrieve(context, profiles, 5)
        paths = [repository.normalized(item.folder_path) for item in candidates]
        rank = paths.index(expected) + 1 if expected in paths else 0
        family_rank = next(
            (index for index, item in enumerate(candidates, start=1)
             if expected_info and item.family_id == expected_info.family_id), 0
        )
        light_metrics["top1"] += rank == 1
        light_metrics["top3"] += bool(rank and rank <= 3)
        light_metrics["top5"] += bool(rank and rank <= 5)
        light_metrics["family_top1"] += family_rank == 1
        light_metrics["family_top3"] += bool(family_rank and family_rank <= 3)
        light_metrics["family_top5"] += bool(family_rank and family_rank <= 5)
        light_metrics["mrr"] += 1.0 / rank if rank else 0.0
        light_metrics["none"] += not candidates or candidates[0].local_score < NONE_THRESHOLD

    analyzed_count = sum(record.analyzed for record in records)
    metadata = {
        "analyzed_files": analyzed_count,
        "tags_coverage": (
            sum(bool(record.tags) for record in records if record.analyzed) / analyzed_count
            if analyzed_count else 0.0
        ),
        "category_coverage": (
            sum(bool(record.category) for record in records if record.analyzed) / analyzed_count
            if analyzed_count else 0.0
        ),
        "local_text_coverage": (
            sum(bool(record.text_keywords) for record in records if record.analyzed) / analyzed_count
            if analyzed_count else 0.0
        ),
        "ai_comment_coverage": (
            sum(bool(record.summary) for record in records if record.analyzed) / analyzed_count
            if analyzed_count else 0.0
        ),
    }
    return {
        "scope": {
            "managed_root": root,
            "scanned_files": len(scanned),
            "root_direct_files": sum(policy.is_organizable_file(path, root) for path in scanned),
            "destination_folders": sum(not profile.is_managed_root
                                       for profile in full_profiles.values()),
            "excluded_package_folders": len(set(excluded_package)),
            "all_walked_directories": len(all_directories),
        },
        "evaluation": {
            "eligible_evaluation_files": len(eligible),
            "sampled_files": count,
            "file_type_distribution": dict(sorted(Counter(
                _file_type(record.extension) for record in targets
            ).items())),
            "qwen_usage_rate": 0.0,
        },
        "metadata_light_evaluation": {
            "eligible_evaluation_files": len(metadata_light_eligible),
            "sampled_files": light_sample_count,
            "top1_accuracy": light_metrics["top1"] / light_sample_count if light_sample_count else 0.0,
            "top3_recall": light_metrics["top3"] / light_sample_count if light_sample_count else 0.0,
            "top5_recall": light_metrics["top5"] / light_sample_count if light_sample_count else 0.0,
            "family_top1_accuracy": light_metrics["family_top1"] / light_sample_count if light_sample_count else 0.0,
            "family_top3_recall": light_metrics["family_top3"] / light_sample_count if light_sample_count else 0.0,
            "family_top5_recall": light_metrics["family_top5"] / light_sample_count if light_sample_count else 0.0,
            "mrr": light_metrics["mrr"] / light_sample_count if light_sample_count else 0.0,
            "none_rate": light_metrics["none"] / light_sample_count if light_sample_count else 0.0,
            "qwen_usage_rate": 0.0,
        },
        "metadata_coverage": metadata,
        "configurations": config_results,
        "best_configuration": best,
        "success_examples": [item for item in best_details if item["correct_rank"] == 1][:10],
        "failure_examples": [item for item in best_details if item["correct_rank"] != 1][:10],
        "exact_failure_family_success_examples": [
            item for item in best_details
            if item["correct_rank"] != 1 and item["family_rank"] == 1
        ][:10],
        "elapsed_sec": time.perf_counter() - started,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path.home() / "Downloads"))
    parser.add_argument("--db", default="file_manager.db")
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="benchmarks/folder_recommendation_evaluation.json")
    args = parser.parse_args()
    result = evaluate(args.root, args.db, args.samples, args.seed)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {key: value for key, value in result.items()
               if key not in {"success_examples", "failure_examples",
                              "exact_failure_family_success_examples"}}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n[SUCCESS EXAMPLES]")
    print(json.dumps(result["success_examples"], ensure_ascii=False, indent=2))
    print("\n[FAILURE EXAMPLES]")
    print(json.dumps(result["failure_examples"], ensure_ascii=False, indent=2))
    print("\n[EXACT FAILURE / FAMILY SUCCESS]")
    print(json.dumps(result["exact_failure_family_success_examples"],
                     ensure_ascii=False, indent=2))
    print(f"[RESULT] {output.resolve()}")


if __name__ == "__main__":
    main()
