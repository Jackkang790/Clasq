"""Compare local, flat-Qwen, and family-aware Qwen folder reranking.

Read-only with respect to user files and Clasq DB. The JSON output is a
checkpoint/result artifact so interrupted Qwen runs can be resumed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ai.qwen_client import QwenClient
from src.recommendation.family import FolderFamilyResolver
from src.recommendation.folder_repository import FolderProfileRepository
from src.recommendation.profile_builder import FolderProfileBuilder
from src.recommendation.qwen_reranker import QwenFolderReranker
from src.recommendation.retriever import FolderCandidateRetriever
from src.recommendation.scope_policy import RootInboxOrganizationPolicy
from src.utils.core import scan_directory_files


NONE_THRESHOLD = 0.30


@dataclass(frozen=True)
class RerankDecision:
    selected_folder_id: str
    status: str
    reason: str
    confidence: float | None
    latency_sec: float


def _strict_family_rerank(client, context, record, candidates, profiles, root):
    started = time.perf_counter()
    families = []
    grouped = {}
    for candidate in candidates:
        if candidate.family_id not in grouped and len(families) < 3:
            grouped[candidate.family_id] = []
            families.append(candidate.family_id)
        if candidate.family_id in grouped and len(grouped[candidate.family_id]) < 3:
            profile = profiles[candidate.folder_id]
            grouped[candidate.family_id].append({
                "folder_id": candidate.folder_id,
                "relative_path": os.path.relpath(profile.folder_path, root),
                "folder_name": profile.folder_name,
                "parent_name": Path(profile.parent_path).name,
                "depth": candidate.depth,
                "direct_file_count": profile.direct_file_count,
                "representative_filenames": list(profile.filename_keywords[:10]),
                "top_tags": list(profile.tag_distribution[:8]),
                "top_categories": list(profile.category_distribution[:5]),
                "representative_keywords": list(profile.text_keywords[:10]),
                "local_score": candidate.local_score,
                "score_breakdown": dict(candidate.score_breakdown),
                "family_id": candidate.family_id,
            })
    payload = [{"family_id": family_id, "folders": grouped[family_id]}
               for family_id in families]
    prompt = f"""
You select an existing destination folder for a new inbox file.
First choose the best family, then the best actual folder inside that family.
You MUST return only a supplied folder_id or NONE.
Never invent a path, folder name, or folder_id.

New file (its original parent path is intentionally unavailable):
{json.dumps({
    'file_name': context.file_name,
    'extension': context.extension,
    'tags': list(context.tags),
    'category': context.category,
    'ai_comment': context.summary[:1200],
    'local_text_keywords': list(record.text_keywords[:20]),
}, ensure_ascii=False)}

Candidate families and existing folders:
{json.dumps(payload, ensure_ascii=False)}

Return ONLY this JSON shape:
{{"selected_folder_id":"F_... or NONE","reason":"Korean reason","confidence":0.0}}
"""
    try:
        raw = client.request_text(
            prompt, timeout=client.config.timeout,
            max_tokens=min(500, client.config.max_tokens), temperature=0.1,
        )
        parsed = client.parse_json_content(raw)
        if not isinstance(parsed, dict):
            raise ValueError("response is not a JSON object")
        allowed_keys = {"selected_folder_id", "reason", "confidence"}
        if set(parsed) - allowed_keys:
            return RerankDecision(
                "NONE", "INVALID", "Unexpected response fields.", None,
                time.perf_counter() - started,
            )
        selected = str(parsed.get("selected_folder_id", "NONE")).strip()
        allowed_ids = {candidate.folder_id for candidate in candidates}
        reason = str(parsed.get("reason", "")).strip()
        confidence_raw = parsed.get("confidence")
        try:
            confidence = float(confidence_raw) if confidence_raw is not None else None
        except (TypeError, ValueError):
            confidence = None
        if selected == "NONE":
            status = "NONE"
        elif selected not in allowed_ids:
            selected, status = "NONE", "INVALID"
            reason = reason or "Candidate set outside ID or path was returned."
        else:
            status = "SELECTED"
        return RerankDecision(
            selected, status, reason, confidence, time.perf_counter() - started,
        )
    except Exception as exc:
        return RerankDecision(
            "NONE", "ERROR", str(exc), None, time.perf_counter() - started,
        )


def _reranked(candidates, selected_id):
    if not selected_id or selected_id == "NONE":
        return tuple(candidates)
    selected = next((item for item in candidates if item.folder_id == selected_id), None)
    if selected is None:
        return ()
    return (selected,) + tuple(item for item in candidates if item.folder_id != selected_id)


def _classify_failure(selected_id, status, expected_profile, expected_info,
                      candidates, family_map):
    if status in {"INVALID", "ERROR"}:
        return "invalid output" if status == "INVALID" else "request error"
    if not selected_id or selected_id == "NONE":
        return "NONE"
    if expected_profile and selected_id == expected_profile.folder_id:
        return "correct"
    selected_info = family_map.get(selected_id)
    if expected_info and selected_info and selected_info.family_id == expected_info.family_id:
        if (expected_profile.folder_id in selected_info.ancestor_folder_ids
                or selected_id in expected_info.ancestor_folder_ids):
            return "correct family, wrong depth"
        return "correct family, duplicate sibling"
    return "wrong family"


def _candidate_dict(candidate):
    return {
        "folder_id": candidate.folder_id,
        "folder": candidate.folder_path,
        "relative_rank": candidate.rank,
        "family_id": candidate.family_id,
        "score": candidate.local_score,
        "score_breakdown": dict(candidate.score_breakdown),
    }


def _mode_metrics(rows, mode):
    count = len(rows)
    exact1 = exact3 = family1 = family3 = none = invalid = errors = conditional = 0
    conditional_total = 0
    latencies = []
    failures = Counter()
    for row in rows:
        data = row[mode]
        exact1 += data["selected_folder_id"] == row["correct_folder_id"]
        exact3 += bool(data["exact_rank"] and data["exact_rank"] <= 3)
        family1 += data["selected_family_id"] == row["correct_family"]
        family3 += bool(data["family_rank"] and data["family_rank"] <= 3)
        none += data["status"] == "NONE"
        invalid += data["status"] == "INVALID"
        errors += data["status"] == "ERROR"
        if mode != "local" and row["local"]["family_rank"] in {1, 2, 3}:
            conditional_total += 1
            conditional += data["selected_folder_id"] == row["correct_folder_id"]
        if data.get("latency_sec") is not None:
            latencies.append(data["latency_sec"])
        failures[data["failure_type"]] += 1
    return {
        "files": count,
        "exact_top1": exact1 / count if count else 0.0,
        "exact_top3": exact3 / count if count else 0.0,
        "family_top1": family1 / count if count else 0.0,
        "family_top3": family3 / count if count else 0.0,
        "none_rate": none / count if count else 0.0,
        "invalid_response_rate": invalid / count if count else 0.0,
        "request_error_rate": errors / count if count else 0.0,
        "average_qwen_latency_sec": sum(latencies) / len(latencies) if latencies else 0.0,
        "conditional_exact_accuracy": (
            conditional / conditional_total if conditional_total else 0.0
        ),
        "conditional_files": conditional_total,
        "failure_types": dict(failures),
    }


def benchmark(root, db_path, output_path, limit=0):
    root = os.path.abspath(root)
    policy = RootInboxOrganizationPolicy()
    scanned = scan_directory_files(root)
    repository = FolderProfileRepository(root, db_path, scope_policy=policy)
    records = repository.load_records(scanned)
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
    if limit > 0:
        eligible = eligible[:limit]

    output = Path(output_path)
    checkpoint = {}
    if output.exists():
        try:
            old = json.loads(output.read_text(encoding="utf-8"))
            checkpoint = {row["file"]: row for row in old.get("rows", [])}
        except (OSError, ValueError, KeyError):
            checkpoint = {}

    builder = FolderProfileBuilder(repository)
    retriever = FolderCandidateRetriever()
    client = QwenClient()
    flat_reranker = QwenFolderReranker(client)
    rows = []
    for index, record in enumerate(eligible, start=1):
        cached = checkpoint.get(record.file_path)
        if cached and "flat" in cached and "family" in cached:
            rows.append(cached)
            print(f"[RESUME] {index}/{len(eligible)} {record.file_name}")
            continue
        context = repository.context_from_record(record)
        context = replace(
            context,
            file_path=os.path.join(root, record.file_name),
            current_folder=root,
        )
        profiles = builder.build(records, exclude_paths=(record.file_path,))
        candidates = retriever.retrieve(context, profiles, 5)
        family_map = FolderFamilyResolver().resolve(profiles)
        expected_path = repository.normalized(os.path.dirname(record.file_path))
        expected_profile = next(
            (profile for profile in profiles.values()
             if repository.normalized(profile.folder_path) == expected_path), None
        )
        expected_info = family_map.get(expected_profile.folder_id) if expected_profile else None

        def outcome(selected_id, status, reason, latency=None):
            ordered = _reranked(candidates, selected_id)
            exact_rank = next(
                (rank for rank, item in enumerate(ordered, start=1)
                 if expected_profile and item.folder_id == expected_profile.folder_id), 0
            )
            family_rank = next(
                (rank for rank, item in enumerate(ordered, start=1)
                 if expected_info and item.family_id == expected_info.family_id), 0
            )
            return {
                "selected_folder_id": selected_id,
                "selected_family_id": next(
                    (item.family_id for item in candidates
                     if item.folder_id == selected_id), ""
                ),
                "status": status,
                "reason": reason,
                "exact_rank": exact_rank,
                "family_rank": family_rank,
                "failure_type": _classify_failure(
                    selected_id, status, expected_profile, expected_info,
                    candidates, family_map,
                ),
                "latency_sec": latency,
            }

        local_selected = candidates[0].folder_id if candidates else "NONE"
        local_status = (
            "SELECTED" if candidates and candidates[0].local_score >= NONE_THRESHOLD
            else "NONE"
        )
        local = outcome(local_selected, local_status, "local retrieval")

        flat_raw = flat_reranker.rerank(context, candidates, profiles)
        flat = outcome(
            flat_raw.selected_folder_id, flat_raw.status, flat_raw.reason,
            flat_raw.elapsed_sec,
        )
        family_raw = _strict_family_rerank(
            client, context, record, candidates, profiles, root,
        )
        family = outcome(
            family_raw.selected_folder_id, family_raw.status,
            family_raw.reason, family_raw.latency_sec,
        )
        row = {
            "file": record.file_path,
            "virtual_source": context.file_path,
            "correct_folder_id": expected_profile.folder_id if expected_profile else "",
            "correct_folder": expected_profile.folder_path if expected_profile else "",
            "correct_family": expected_info.family_id if expected_info else "",
            "local_candidates": [_candidate_dict(item) for item in candidates],
            "local": local,
            "flat": flat,
            "family": family,
        }
        rows.append(row)
        partial = {"rows": rows, "complete": False}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"[QWEN FAMILY BENCH] {index}/{len(eligible)} "
            f"flat={flat['failure_type']} family={family['failure_type']}"
        )

    result = {
        "scope": {
            "managed_root": root,
            "scanned_files": len(scanned),
            "analyzed_evaluation_files": len(eligible),
            "metadata_light_included": 0,
        },
        "metric_definition": {
            "top1": "selected folder",
            "top3": "selected folder promoted to rank 1, remaining local order retained",
            "conditional_exact_accuracy": (
                "exact selections among files whose correct family was in local Top-3"
            ),
        },
        "local": _mode_metrics(rows, "local"),
        "flat_qwen": _mode_metrics(rows, "flat"),
        "family_qwen": _mode_metrics(rows, "family"),
        "rows": rows,
        "complete": True,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path.home() / "Downloads"))
    parser.add_argument("--db", default="file_manager.db")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--output", default="benchmarks/qwen_family_rerank_results.json"
    )
    args = parser.parse_args()
    result = benchmark(args.root, args.db, args.output, args.limit)
    print(json.dumps({key: value for key, value in result.items() if key != "rows"},
                     ensure_ascii=False, indent=2))
    print(f"[RESULT] {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
