"""SQLite-backed basic file search without Qwen, embeddings, or RAG."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from .db_manager import FileRegistryManager


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

    def __init__(self, db_path: str = "file_manager.db", result_limit: int = 100):
        self.db_path = db_path
        self.result_limit = max(1, int(result_limit))
        self.last_result_metadata: Dict[str, Dict[str, Any]] = {}
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
            keywords = [word.strip().casefold() for word in split
                        if word.strip() and word.strip().casefold() not in self.STOP_WORDS]
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
        conn = sqlite3.connect(self.db_path)
        try:
            files = conn.execute(
                "SELECT id, file_name, file_path, ai_comment, category FROM files").fetchall()
            cached = conn.execute("SELECT file_path FROM file_fingerprint_cache").fetchall()
            indexed = conn.execute(
                "SELECT file_path, extracted_text, extract_status FROM file_text_index").fetchall()
        finally:
            conn.close()
        candidates = {}
        for file_id, name, path, comment, category in files:
            candidates[self._normalized_path(path)] = {
                "id": file_id, "file_name": name or os.path.basename(path), "file_path": path,
                "ai_comment": comment or "", "category": category or "",
                "analysis_status": "analyzed", "extracted_text": "", "extract_status": "",
            }
        for (path,) in cached:
            candidates.setdefault(self._normalized_path(path), {
                "id": None, "file_name": os.path.basename(path), "file_path": path,
                "ai_comment": "", "category": "", "analysis_status": "pending",
                "extracted_text": "", "extract_status": "",
            })
        for path, text, status in indexed:
            item = candidates.setdefault(self._normalized_path(path), {
                "id": None, "file_name": os.path.basename(path), "file_path": path,
                "ai_comment": "", "category": "", "analysis_status": "pending",
                "extracted_text": "", "extract_status": "",
            })
            item["extracted_text"] = (text or "") if status == "success" else ""
            item["extract_status"] = status or ""
        return list(candidates.values())

    @staticmethod
    def _extensions(values: List[str] | None) -> set[str]:
        return {f".{str(value).strip().casefold().lstrip('.')}"
                for value in (values or []) if str(value).strip()}

    def _score(self, item: dict, keywords: List[str]) -> tuple[int, int, set[str]]:
        name, stem = item["file_name"].casefold(), Path(item["file_name"]).stem.casefold()
        path, text = item["file_path"].casefold(), item["extracted_text"].casefold()
        metadata = f"{item['ai_comment']} {item['category']}".casefold()
        matched, score, sources = 0, 0, set()
        for keyword in keywords:
            best, group_sources = 0, set()
            for synonym in self.SYNONYM_MAP.get(keyword, [keyword]):
                token = synonym.casefold()
                if stem == token or name == token:
                    best, group_sources = max(best, 100), group_sources | {"filename"}
                elif token in name:
                    best, group_sources = max(best, 60), group_sources | {"filename"}
                if token in path:
                    best, group_sources = max(best, 40), group_sources | {"path"}
                if token and token in text:
                    best, group_sources = max(best, 25), group_sources | {"text"}
                if token and token in metadata:
                    best, group_sources = max(best, 15), group_sources | {"ai_metadata"}
            if best:
                matched, score, sources = matched + 1, score + best, sources | group_sources
        return matched, score, sources

    def search_files_smart(self, keywords: List[str], exts: List[str] | None = None):
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
        self.last_result_metadata = {}
        rows = []
        for score, item, sources in scored[:self.result_limit]:
            self.last_result_metadata[self._normalized_path(item["file_path"])] = {
                "analysis_status": item["analysis_status"], "match_source": sorted(sources),
                "relevance_score": score, "extract_status": item["extract_status"],
            }
            rows.append((item["id"], item["file_name"], item["file_path"],
                         item["ai_comment"], item["category"]))
        return rows, fallback

    def get_result_metadata(self, file_path: str) -> Dict[str, Any]:
        return self.last_result_metadata.get(self._normalized_path(file_path), {})

    def _execute_sql_query(self, keywords, exts=None, match_mode="AND"):
        return self.search_files_smart(keywords, exts)[0]
