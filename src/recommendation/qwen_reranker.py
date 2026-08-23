from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from src.ai.qwen_client import QwenClient
from .models import FileRecommendationContext, FolderCandidate, FolderProfile


@dataclass(frozen=True)
class QwenRerankDecision:
    selected_folder_id: str
    status: str
    reason: str
    confidence: Optional[float]
    elapsed_sec: float


class QwenFolderReranker:
    def __init__(self, client: Optional[QwenClient] = None):
        self.client = client or QwenClient()

    def rerank(
        self,
        context: FileRecommendationContext,
        candidates: Tuple[FolderCandidate, ...],
        profiles: Mapping[str, FolderProfile],
    ) -> QwenRerankDecision:
        started = time.perf_counter()
        candidate_payload = []
        for candidate in candidates[:5]:
            profile = profiles[candidate.folder_id]
            candidate_payload.append({
                "folder_id": candidate.folder_id,
                "folder_name": profile.folder_name,
                "parent_name": Path(profile.parent_path).name,
                "local_rank": candidate.rank,
                "local_score": candidate.local_score,
                "top_extensions": list(profile.extension_distribution[:5]),
                "top_categories": list(profile.category_distribution[:5]),
                "top_tags": list(profile.tag_distribution[:8]),
                "representative_keywords": list(profile.filename_keywords[:10]),
            })
        prompt = f"""
You are reranking existing folder candidates for a local file organizer.
You MUST choose only one folder_id from the supplied candidates or NONE.
Never create a path, folder name, or new candidate.

File:
{json.dumps({
    'file_name': context.file_name,
    'extension': context.extension,
    'tags': list(context.tags),
    'category': context.category,
    'filename_keywords': list(context.filename_keywords),
    'text_keywords': list(context.text_keywords[:20]),
    'summary': context.summary[:1000],
}, ensure_ascii=False)}

Candidates:
{json.dumps(candidate_payload, ensure_ascii=False)}

Return ONLY JSON:
{{"selected_folder_id":"F_xxx or NONE","confidence":0.0,"reason":"Korean reason"}}
"""
        try:
            raw = self.client.request_text(
                prompt, timeout=self.client.config.timeout,
                max_tokens=min(400, self.client.config.max_tokens), temperature=0.1,
            )
            parsed = self.client.parse_json_content(raw)
            selected = str(parsed.get("selected_folder_id", "NONE")).strip()
            reason = str(parsed.get("reason", "")).strip()
            confidence_raw = parsed.get("confidence")
            try:
                confidence = float(confidence_raw) if confidence_raw is not None else None
            except (TypeError, ValueError):
                confidence = None

            # Security: only allow IDs that were in the candidate list
            valid_ids = {c.folder_id for c in candidates}
            if selected not in valid_ids and selected != "NONE":
                return QwenRerankDecision(
                    "NONE", "INVALID",
                    f"Qwen이 유효하지 않은 folder_id를 반환했습니다: {selected!r}",
                    None, time.perf_counter() - started,
                )
            status = "SELECTED" if selected in valid_ids else "NONE"
            return QwenRerankDecision(selected, status, reason, confidence,
                                      time.perf_counter() - started)
        except Exception as exc:
            return QwenRerankDecision(
                "NONE", "ERROR", str(exc), None, time.perf_counter() - started
            )
