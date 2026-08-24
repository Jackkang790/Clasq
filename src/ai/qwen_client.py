"""Small OpenAI-compatible HTTP client shared by all Clasq analyzers."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests

from .config import AIConfig

log = logging.getLogger(__name__)


_runtime_recovery: Optional[Callable[[], bool]] = None


def set_runtime_recovery(handler: Optional[Callable[[], bool]]) -> None:
    """Register the owned server manager's bounded recovery callback."""
    global _runtime_recovery
    _runtime_recovery = handler


class AIClientError(RuntimeError):
    """Base class for inference transport and response failures."""


class AIConnectionError(AIClientError):
    pass


class AITimeoutError(AIClientError):
    pass


class AIHTTPError(AIClientError):
    pass


class AIResponseError(AIClientError):
    pass


class AIJSONError(AIResponseError):
    pass


class QwenClient:
    def __init__(
        self,
        config: Optional[AIConfig] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.config = config or AIConfig()
        self.session = session or requests.Session()

    @staticmethod
    def text_part(text: str) -> Dict[str, Any]:
        return {"type": "text", "text": text}

    @staticmethod
    def image_part(image_url: str) -> Dict[str, Any]:
        return {"type": "image_url", "image_url": {"url": image_url}}

    def request_content(
        self,
        content: Sequence[Dict[str, Any]],
        *,
        timeout: Optional[int] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0,
        system_prompt: Optional[str] = None,
    ) -> str:
        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": list(content)})
        return self.chat(
            messages,
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def request_text(self, prompt: str, **kwargs: Any) -> str:
        return self.request_content([self.text_part(prompt)], **kwargs)

    def chat(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        timeout: Optional[int] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0,
    ) -> str:
        payload = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        started = time.monotonic()
        try:
            response = self.session.post(
                self.config.chat_completions_url,
                json=payload,
                timeout=timeout or self.config.timeout,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            log.warning("AI inference timeout timeout_seconds=%s", timeout or self.config.timeout)
            raise AITimeoutError("AI 요청 시간이 초과되었습니다.") from exc
        except requests.exceptions.ConnectionError as exc:
            # Runtime crash recovery is separate from startup profile fallback.
            # The manager bounds restarts; this request retries exactly once.
            log.warning("AI inference connection failure recovery_requested=%s", _runtime_recovery is not None)
            if _runtime_recovery is not None and _runtime_recovery():
                log.info("AI runtime recovery succeeded; retrying request once")
                try:
                    response = self.session.post(
                        self.config.chat_completions_url,
                        json=payload,
                        timeout=timeout or self.config.timeout,
                    )
                    response.raise_for_status()
                except requests.exceptions.Timeout as retry_exc:
                    log.warning("AI inference timeout after recovery")
                    raise AITimeoutError("AI 요청 시간이 초과되었습니다.") from retry_exc
                except requests.exceptions.RequestException as retry_exc:
                    log.error("AI inference retry failed after recovery")
                    raise AIConnectionError("AI 서버 복구 후 요청에 실패했습니다.") from retry_exc
            else:
                log.error("AI runtime recovery unavailable or exhausted")
                raise AIConnectionError("AI 서버에 연결할 수 없습니다.") from exc
        except requests.exceptions.HTTPError as exc:
            status = getattr(exc.response, "status_code", "unknown")
            raise AIHTTPError(f"AI 서버 HTTP 오류 ({status})") from exc
        except requests.exceptions.RequestException as exc:
            raise AIClientError(f"AI 요청 실패: {exc}") from exc

        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIResponseError("OpenAI-compatible 응답 형식이 올바르지 않습니다.") from exc

        if not isinstance(content, str) or not content.strip():
            raise AIResponseError("AI 응답 내용이 비어 있습니다.")
        content = content.strip()
        log.info(
            "AI inference completed latency_ms=%d response_chars=%d",
            int((time.monotonic() - started) * 1000), len(content),
        )
        return content

    @staticmethod
    def parse_json_content(content: str) -> Dict[str, Any]:
        if not content or not content.strip():
            raise AIJSONError("AI JSON 응답이 비어 있습니다.")

        stripped = content.strip()
        candidates = [stripped]
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped, re.IGNORECASE)
        if fenced:
            candidates.insert(0, fenced.group(1).strip())

        decoder = json.JSONDecoder()
        for candidate in candidates:
            try:
                value = json.loads(candidate)
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                pass

            for index, char in enumerate(candidate):
                if char != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(candidate[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value

        raise AIJSONError("AI 응답에서 유효한 JSON 객체를 찾을 수 없습니다.")

    def request_json(self, content: Sequence[Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
        return self.parse_json_content(self.request_content(content, **kwargs))

    def list_models(self, timeout: int = 10) -> Dict[str, Any]:
        try:
            response = self.session.get(self.config.models_url, timeout=timeout)
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout as exc:
            raise AITimeoutError("AI 모델 목록 요청 시간이 초과되었습니다.") from exc
        except requests.exceptions.ConnectionError as exc:
            raise AIConnectionError("AI 서버에 연결할 수 없습니다.") from exc
        except requests.exceptions.RequestException as exc:
            raise AIClientError(f"AI 모델 목록 요청 실패: {exc}") from exc
        except ValueError as exc:
            raise AIResponseError("모델 목록 응답이 JSON이 아닙니다.") from exc
        if not isinstance(result, dict):
            raise AIResponseError("모델 목록 응답 형식이 올바르지 않습니다.")
        return result
