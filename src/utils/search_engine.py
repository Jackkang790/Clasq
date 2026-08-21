"""SQLite-backed basic file search without Qwen, embeddings, or RAG."""
from __future__ import annotations

import os
import math
import time
from pathlib import Path
from typing import Any, Dict, List

from .db_manager import FileRegistryManager
from .search_aliases import build_search_alias_map, equivalent_terms
from .search_normalization import normalize_query_token, search_variants
from .search_snapshot import (
    SearchRecord,
    get_search_snapshot,
    invalidate_search_snapshot,
    refresh_search_snapshot,
)


class SearchEngine:
    STOP_WORDS = {
        "파일", "문서", "폴더", "데이터", "자료", "내용", "것", "찾아줘", "보여줘",
        "검색", "알려줘", "꺼내줘", "어디있어", "어디", "있나", "관련된", "관련",
        "중", "중에", "중에서", "이", "그", "저", "제일", "최근", "좀", "하나",
        "ppt", "pptx", "pdf", "hwp", "hwpx", "docx", "xlsx", "png", "jpg",
        "jpeg", "gif", "mp3", "mp4",
    }
    SYNONYM_MAP = {
        "실습": ["실습", "현장실습", "인턴", "교육"],
        "학교": ["학교", "캠퍼스", "학사"],
        "노래": ["노래", "음원", "가수", "음악", "작업"],
        "번역": ["번역", "번역문"],
        "이미지": ["이미지", "사진", "그림", "gif", "png", "jpg"],
        "보고서": ["보고서", "리포트", "과제", "기안서"],
        "회의": ["회의", "미팅", "회의록"],
        "졸업": ["졸업", "수료", "학위"],
    }

    def __init__(self, db_path: str = "file_manager.db", result_limit: int = 100,
                 project_aliases: dict[str, tuple[str, ...]] | None = None):
        self.db_path = db_path
        self.result_limit = max(1, int(result_limit))
        self.last_result_metadata: Dict[str, Dict[str, Any]] = {}
        self.last_search_performance: Dict[str, Any] = {}
        self.search_aliases = build_search_alias_map(project_aliases)
        FileRegistryManager(db_path=db_path)

    @staticmethod
    def _normalized_path(path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(path)))

    def process_query_result(self, parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        type_val = parsed_data.get("@TYPE")
        if type_val in {"search", "@검색"}:
            condition = parsed_data.get("condition", {})
            raw = condition.get("tags", []) if condition else parsed_data.get("query_keywords", [])
            split = [part for keyword in raw for part in str(keyword).split()]
            keywords = [normalize_query_token(word) for word in split]
            keywords = [word for word in keywords if word and word not in self.STOP_WORDS]
            results, fallback = self.search_files_smart(
                keywords, parsed_data.get("target_extension", [])
            )
            display = ", ".join(keywords) if keywords else "전체"
            message = (f"'{display}' 일부 키워드 검색 결과 {len(results)}건을 보여드립니다."
                       if fallback else f"'{display}' 검색 결과 {len(results)}건을 찾았습니다.")
            return {"action": "UPDATE_TABLE", "message": message, "data": results}
        if type_val == "@대화":
            return {"action": "SHOW_CHAT", "message": parsed_data.get(
                "reply_text", "무엇을 도와드릴까요?"), "data": []}
        return {"action": "ERROR", "message": parsed_data.get(
            "message", "알 수 없거나 올바르지 않은 요청입니다."), "data": []}

    def _load_candidates(self) -> list[dict]:
        snapshot, _built = get_search_snapshot(self.db_path)
        return [{
            "id": record.file_id, "file_name": record.file_name,
            "file_path": record.file_path, "ai_comment": record.ai_comment,
            "category": record.category, "analysis_status": record.analysis_status,
            "extracted_text": record.extracted_text, "extract_status": record.extract_status,
        } for record in snapshot.records]

    def invalidate_snapshot(self) -> None:
        invalidate_search_snapshot(self.db_path)

    def refresh_snapshot(self):
        return refresh_search_snapshot(self.db_path)

    @staticmethod
    def _extensions(values: List[str] | None) -> set[str]:
        return {f".{str(value).strip().casefold().lstrip('.')}"
                for value in (values or []) if str(value).strip()}

    def _score(self, item: dict, keywords: List[str]) -> tuple[int, int, set[str], dict]:
        name, name_compact = search_variants(item["file_name"])
        stem, stem_compact = search_variants(Path(item["file_name"]).stem)
        path, path_compact = search_variants(item["file_path"])
        # Body text can be large; separator/CamelCase normalization is useful
        # for names and paths but prohibitively expensive for every document.
        text = item["extracted_text"].casefold()
        metadata = f"{item['ai_comment']} {item['category']}".casefold()
        matched, score, sources = 0, 0, set()
        breakdown = {
            "filename_score": 0, "path_score": 0, "text_score": 0,
            "ai_metadata_score": 0, "evidence_bonus": 0,
            "keyword_coverage": 0.0, "coverage_bonus": 0,
            "phrase_bonus": 0, "final_score": 0,
            "discrimination_bonus": 0,
        }
        for keyword in keywords:
            field_scores = {"filename": 0, "path": 0, "text": 0, "ai_metadata": 0}
            for synonym in self.SYNONYM_MAP.get(keyword, [keyword]):
                token, token_compact = search_variants(synonym)
                if stem in {token, token_compact} or name in {token, token_compact}:
                    field_scores["filename"] = max(field_scores["filename"], 100)
                elif token in name or (token_compact and token_compact in name_compact):
                    field_scores["filename"] = max(field_scores["filename"], 60)
                if token in path or (token_compact and token_compact in path_compact):
                    field_scores["path"] = max(field_scores["path"], 40)
                if token and (token in text or token_compact in text):
                    field_scores["text"] = max(field_scores["text"], 25)
                if token and (token in metadata or token_compact in metadata):
                    field_scores["ai_metadata"] = max(field_scores["ai_metadata"], 15)
            group_sources = {field for field, value in field_scores.items() if value}
            best = max(field_scores.values())
            if best:
                evidence_bonus = min(10, max(0, len(group_sources) - 1) * 5)
                matched, score = matched + 1, score + best + evidence_bonus
                sources |= group_sources
                breakdown["filename_score"] += field_scores["filename"]
                breakdown["path_score"] += field_scores["path"]
                breakdown["text_score"] += field_scores["text"]
                breakdown["ai_metadata_score"] += field_scores["ai_metadata"]
                breakdown["evidence_bonus"] += evidence_bonus
        if keywords and matched:
            coverage = matched / len(keywords)
            coverage_bonus = round(40 * coverage)
            score += coverage_bonus
            if matched == len(keywords):
                coverage_bonus += 20
                score += 20
            breakdown["keyword_coverage"] = coverage
            breakdown["coverage_bonus"] = coverage_bonus
        breakdown["final_score"] = score
        return matched, score, sources, breakdown

    def _score_record(
        self,
        item: SearchRecord,
        keyword_groups: list[list[tuple[str, str]]],
        rarity_weights: list[float] | None = None,
    ) -> tuple[int, int, set[str], dict]:
        matched, score, sources = 0, 0, set()
        breakdown = {
            "filename_score": 0, "path_score": 0, "text_score": 0,
            "ai_metadata_score": 0, "evidence_bonus": 0,
            "keyword_coverage": 0.0, "coverage_bonus": 0,
            "phrase_bonus": 0, "final_score": 0,
            "discrimination_bonus": 0,
        }
        for group_index, synonyms in enumerate(keyword_groups):
            field_scores = {"filename": 0, "path": 0, "text": 0, "ai_metadata": 0}
            for token, token_compact in synonyms:
                if item.normalized_stem in {token, token_compact} \
                        or item.normalized_filename in {token, token_compact}:
                    field_scores["filename"] = max(field_scores["filename"], 100)
                elif token in item.normalized_filename or (
                        token_compact and token_compact in item.compact_filename):
                    field_scores["filename"] = max(field_scores["filename"], 60)
                if token in item.normalized_path or (
                        token_compact and token_compact in item.compact_path):
                    field_scores["path"] = max(field_scores["path"], 40)
                if token and (token in item.normalized_text
                              or token_compact in item.normalized_text):
                    field_scores["text"] = max(field_scores["text"], 25)
                if token and (token in item.normalized_ai_metadata
                              or token_compact in item.normalized_ai_metadata):
                    field_scores["ai_metadata"] = max(field_scores["ai_metadata"], 15)
            group_sources = {field for field, value in field_scores.items() if value}
            best = max(field_scores.values())
            if best:
                evidence_bonus = min(10, max(0, len(group_sources) - 1) * 5)
                discrimination_bonus = 0
                # Rarity is a small tie-breaker only when body text is the best
                # available evidence. Filename/path precedence remains intact.
                if best == field_scores["text"] and rarity_weights:
                    remaining_bonus = max(0, 30 - breakdown["discrimination_bonus"])
                    discrimination_bonus = min(
                        12, remaining_bonus, round(12 * rarity_weights[group_index])
                    )
                matched, score = (matched + 1,
                                  score + best + evidence_bonus + discrimination_bonus)
                sources |= group_sources
                breakdown["filename_score"] += field_scores["filename"]
                breakdown["path_score"] += field_scores["path"]
                breakdown["text_score"] += field_scores["text"]
                breakdown["ai_metadata_score"] += field_scores["ai_metadata"]
                breakdown["evidence_bonus"] += evidence_bonus
                breakdown["discrimination_bonus"] += discrimination_bonus
        if keyword_groups and matched:
            coverage = matched / len(keyword_groups)
            coverage_bonus = round(40 * coverage)
            score += coverage_bonus
            if matched == len(keyword_groups):
                coverage_bonus += 20
                score += 20
            breakdown["keyword_coverage"] = coverage
            breakdown["coverage_bonus"] = coverage_bonus
        breakdown["final_score"] = score
        return matched, score, sources, breakdown

    def _prepare_keyword_groups(self, snapshot, keywords: List[str]):
        """Build equivalent variants and per-query rarity without splitting coverage."""
        keyword_groups = []
        rarity_weights = []
        snapshot_size = max(1, len(snapshot.records))
        for keyword in keywords:
            legacy_terms = self.SYNONYM_MAP.get(keyword, [keyword])
            terms = tuple(dict.fromkeys(
                alias for legacy in legacy_terms
                for alias in equivalent_terms(legacy, self.search_aliases)
            ))
            variants = [search_variants(term) for term in terms]
            keyword_groups.append(variants)
            frequencies = [snapshot.document_frequency.get(value, snapshot_size)
                           for normalized, compact in variants
                           for value in {normalized, compact} if value]
            frequency = min(frequencies, default=snapshot_size)
            rarity_weights.append(
                math.log((snapshot_size + 1) / (frequency + 1))
                / math.log(snapshot_size + 1)
            )
        return keyword_groups, rarity_weights

    def search_files_smart(self, keywords: List[str], exts: List[str] | None = None):
        total_started = time.perf_counter()
        snapshot_started = time.perf_counter()
        snapshot, snapshot_built = get_search_snapshot(self.db_path)
        snapshot_elapsed = (time.perf_counter() - snapshot_started) * 1000

        normalization_started = time.perf_counter()
        extensions = self._extensions(exts)
        keyword_groups, rarity_weights = self._prepare_keyword_groups(snapshot, keywords)
        normalization_elapsed = (time.perf_counter() - normalization_started) * 1000

        matching_started = time.perf_counter()
        candidates = [item for item in snapshot.records
                      if not extensions or item.extension in extensions]
        scored = []
        for item in candidates:
            matched, score, sources, breakdown = self._score_record(
                item, keyword_groups, rarity_weights
            )
            if not keywords or matched:
                scored.append((matched, score, item, sources, breakdown))
        matching_elapsed = (time.perf_counter() - matching_started) * 1000

        ranking_started = time.perf_counter()
        has_full_match = not keywords or any(value[0] == len(keywords) for value in scored)
        fallback = bool(keywords and not has_full_match)
        scored.sort(key=lambda value: (-value[0], -value[1],
                                      value[2].file_name.casefold(),
                                      value[2].file_path.casefold()))
        self.last_result_metadata = {}
        rows = []
        for matched, score, item, sources, breakdown in scored[:self.result_limit]:
            self.last_result_metadata[item.normalized_absolute_path] = {
                "analysis_status": item.analysis_status, "match_source": sorted(sources),
                "relevance_score": score, "keyword_matches": matched,
                "keyword_count": len(keywords), "extract_status": item.extract_status,
                "score_breakdown": breakdown,
            }
            rows.append((item.file_id, item.file_name, item.file_path,
                         item.ai_comment, item.category))
        ranking_elapsed = (time.perf_counter() - ranking_started) * 1000
        self.last_search_performance = {
            "snapshot_built": snapshot_built,
            "snapshot_db_load_ms": snapshot_elapsed,
            "snapshot_build_ms": snapshot.build_time_ms if snapshot_built else 0.0,
            "query_normalization_ms": normalization_elapsed,
            "candidate_matching_ms": matching_elapsed,
            "ranking_ms": ranking_elapsed,
            "total_ms": (time.perf_counter() - total_started) * 1000,
            "snapshot_rows": len(snapshot.records),
            "snapshot_approximate_bytes": snapshot.approximate_bytes,
        }
        return rows, fallback

    def get_result_metadata(self, file_path: str) -> Dict[str, Any]:
        return self.last_result_metadata.get(self._normalized_path(file_path), {})

    def _execute_sql_query(self, keywords, exts=None, match_mode="AND"):
        return self.search_files_smart(keywords, exts)[0]
