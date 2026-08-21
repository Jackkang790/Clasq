"""Generate a weakly-labelled local-search evaluation set and compare rankings.

The generated labels are candidates for human review, not authoritative ground
truth. No Qwen, recommendation, file move, or database write is performed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.ui.views.search_view import SearchView
from src.utils.search_engine import SearchEngine


TOKEN_RE = re.compile(r"[가-힣A-Za-z][가-힣A-Za-z0-9]{2,}")
NOISY = {
    "파일", "문서", "자료", "최종", "관련", "그리고", "입니다", "합니다",
    "the", "and", "for", "with", "www", "http", "https", "chapter",
}


@dataclass
class EvaluationCase:
    category: str
    query: str
    expected_paths: list[str]
    confidence: str
    rationale: str
    ground_truth_status: str


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in TOKEN_RE.findall(text or "")
            if token.casefold() not in NOISY and not token.isdigit()
            and len(token) <= 40
            and sum(character.isdigit() for character in token) / len(token) < 0.35}


def _load_rows(db_path: str):
    engine = SearchEngine(db_path)
    return engine._load_candidates()  # benchmark-only read access


def _distinctive_token_cases(rows: list[dict], source: str, limit: int) -> list[EvaluationCase]:
    paths_by_token = defaultdict(set)
    for row in rows:
        if source == "filename":
            value = Path(row["file_name"]).stem
        elif source == "path":
            value = " ".join(Path(row["file_path"]).parts[:-1])
        elif source == "body":
            value = row["extracted_text"]
        else:
            value = f"{row['ai_comment']} {row['category']}"
        for token in _tokens(value):
            if source == "body" and token in _tokens(
                    f"{row['file_name']} {row['file_path']}"):
                continue
            if source == "ai_metadata" and token in _tokens(
                    f"{row['file_name']} {row['file_path']} {row['extracted_text']}"):
                continue
            paths_by_token[token].add(_norm(row["file_path"]))

    candidates = [
        (token, sorted(paths)) for token, paths in paths_by_token.items()
        if 1 <= len(paths) <= 5 and len(token) >= 3
    ]
    candidates.sort(key=lambda item: (len(item[1]), -len(item[0]), item[0]))
    return [EvaluationCase(
        category=source,
        query=f"{token} 파일 찾아줘",
        expected_paths=paths,
        confidence="low" if source in {"body", "ai_metadata"} else "medium",
        rationale=f"auto-generated distinctive {source} token; human review required",
        ground_truth_status="ambiguous" if source in {"body", "ai_metadata"} else "candidate",
    ) for token, paths in candidates[:limit]]


def build_cases(db_path: str) -> list[EvaluationCase]:
    rows = _load_rows(db_path)
    by_extension = defaultdict(list)
    for row in rows:
        by_extension[Path(row["file_path"]).suffix.casefold()].append(
            _norm(row["file_path"])
        )
    extension_specs = [
        ("피피티 찾아줘", {".ppt", ".pptx"}),
        ("PDF 문서 보여줘", {".pdf"}),
        ("마크다운 파일 찾아줘", {".md", ".markdown"}),
        ("이미지 파일 찾아줘", {".jpg", ".jpeg", ".png", ".gif", ".webp"}),
    ]
    cases = []
    for query, extensions in extension_specs:
        expected = sorted(path for ext in extensions for path in by_extension.get(ext, []))
        if expected:
            cases.append(EvaluationCase(
                "extension", query, expected, "high", "extension filter ground truth"
                , "verified"
            ))
    for source in ("filename", "path", "body", "ai_metadata"):
        cases.extend(_distinctive_token_cases(rows, source, 4))
    return cases[:20]


def _legacy_parse(text: str) -> dict:
    aliases = {"피피티": ["ppt", "pptx"], "ppt": ["ppt", "pptx"],
               "파워포인트": ["ppt", "pptx"], "프레젠테이션": ["ppt", "pptx"]}
    extensions, keywords = [], []
    extension_candidates = {
        "pdf", "hwp", "hwpx", "docx", "xlsx", "ppt", "pptx",
        "png", "jpg", "jpeg", "gif", "mp3", "mp4",
    }
    stopwords = {"관련", "관련된", "찾아줘", "찾아주세요", "검색", "검색해줘",
                 "보여줘", "보여주세요", "알려줘", "파일", "문서"}
    for word in text.split():
        token = word.strip(".,!?").casefold()
        if token == "pptx":
            extensions.append("pptx")
        elif token in aliases:
            extensions.extend(aliases[token])
        elif token in extension_candidates:
            extensions.append(token)
        elif token not in stopwords:
            keywords.append(word)
    return {"query_keywords": keywords, "target_extension": list(dict.fromkeys(extensions))}


class LegacySearchEngine(SearchEngine):
    def _score(self, item, keywords):
        name = item["file_name"].casefold()
        stem = Path(item["file_name"]).stem.casefold()
        path = item["file_path"].casefold()
        text = item["extracted_text"].casefold()
        metadata = f"{item['ai_comment']} {item['category']}".casefold()
        matched, score, sources = 0, 0, set()
        for keyword in keywords:
            best, field_sources = 0, set()
            for synonym in self.SYNONYM_MAP.get(keyword, [keyword]):
                token = synonym.casefold()
                if stem == token or name == token:
                    best, field_sources = max(best, 100), field_sources | {"filename"}
                elif token in name:
                    best, field_sources = max(best, 60), field_sources | {"filename"}
                if token in path:
                    best, field_sources = max(best, 40), field_sources | {"path"}
                if token and token in text:
                    best, field_sources = max(best, 25), field_sources | {"text"}
                if token and token in metadata:
                    best, field_sources = max(best, 15), field_sources | {"ai_metadata"}
            if best:
                matched, score, sources = matched + 1, score + best, sources | field_sources
        return matched, score, sources

    def search_files_smart(self, keywords, exts=None):
        extensions = self._extensions(exts)
        candidates = [item for item in self._load_candidates()
                      if not extensions or Path(item["file_path"]).suffix.casefold() in extensions]
        scored = []
        for item in candidates:
            matched, score, sources = self._score(item, keywords)
            if not keywords or matched == len(keywords):
                scored.append((score, item, sources))
        fallback = False
        if keywords and not scored:
            fallback = True
            for item in candidates:
                matched, score, sources = self._score(item, keywords)
                if matched:
                    scored.append((score, item, sources))
        scored.sort(key=lambda value: (-value[0], value[1]["file_name"].casefold(),
                                      value[1]["file_path"].casefold()))
        rows = [(item["id"], item["file_name"], item["file_path"],
                 item["ai_comment"], item["category"])
                for _score, item, _sources in scored[:self.result_limit]]
        return rows, fallback


def evaluate(db_path: str, cases: Iterable[EvaluationCase], legacy: bool = False) -> dict:
    engine = LegacySearchEngine(db_path) if legacy else SearchEngine(db_path)
    details, latencies = [], []
    for case in cases:
        parsed = _legacy_parse(case.query) if legacy else SearchView._parse_natural_query(
            None, case.query
        )
        started = time.perf_counter()
        rows, _fallback = engine.search_files_smart(
            parsed["query_keywords"], parsed["target_extension"]
        )
        latencies.append((time.perf_counter() - started) * 1000)
        ranked = [_norm(row[2]) for row in rows]
        expected = set(case.expected_paths)
        rank = next((index + 1 for index, path in enumerate(ranked) if path in expected), None)
        sources = []
        top_results = []
        if not legacy:
            sources = sorted({source for row in rows[:10]
                              for source in engine.get_result_metadata(row[2]).get(
                                  "match_source", [])})
            top_results = [{
                "file_path": row[2],
                "match_sources": engine.get_result_metadata(row[2]).get("match_source", []),
                "score_breakdown": engine.get_result_metadata(row[2]).get(
                    "score_breakdown", {}),
            } for row in rows[:10]]
        details.append({
            "category": case.category, "query": case.query,
            "expected_paths": case.expected_paths[:10],
            "expected_count": len(case.expected_paths),
            "confidence": case.confidence, "rationale": case.rationale,
            "ground_truth_status": case.ground_truth_status,
            "rank": rank, "top1": bool(rank and rank <= 1),
            "top3": bool(rank and rank <= 3), "top10": bool(rank and rank <= 10),
            "reciprocal_rank": 1 / rank if rank else 0, "no_result": not rows,
            "match_sources": sources, "returned": len(rows),
            "top_results": top_results,
        })

    def metrics(selected):
        count = len(selected)
        if not count:
            return {"queries": 0, "top1": None, "top3": None, "top10": None,
                    "mrr": None, "no_result_rate": None}
        return {
            "queries": count,
            "top1": sum(row["top1"] for row in selected) / count,
            "top3": sum(row["top3"] for row in selected) / count,
            "top10": sum(row["top10"] for row in selected) / count,
            "mrr": sum(row["reciprocal_rank"] for row in selected) / count,
            "no_result_rate": sum(row["no_result"] for row in selected) / count,
        }

    result = metrics(details)
    result["verified_metrics"] = metrics([
        row for row in details if row["ground_truth_status"] == "verified"
    ])
    result["average_latency_ms"] = statistics.mean(latencies) if latencies else 0
    result["details"] = details
    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="file_manager.db")
    parser.add_argument("--output", help="optional JSON output path")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    cases = build_cases(args.db)
    report = {
        "warning": "Auto-generated weak labels require human review.",
        "before": evaluate(args.db, cases, legacy=True),
        "after": evaluate(args.db, cases, legacy=False),
    }
    if args.summary_only:
        report = {
            "warning": report["warning"],
            "before": {key: value for key, value in report["before"].items()
                       if key != "details"},
            "after": {key: value for key, value in report["after"].items()
                      if key != "details"},
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
