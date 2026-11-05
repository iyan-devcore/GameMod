"""Google Gemini translator."""
from __future__ import annotations

import json
import os
import re
from typing import Sequence

import requests

from .base import ContentBlocked, Overloaded, RateLimited, RequestLine, TranslatorError, Usage

_BASE = "https://generativelanguage.googleapis.com/v1beta"
# Each of these has its own free daily limit. gemini-3.7-flash and gemini-3.5-flash are left
# out because they report the same quota as these two, so they add nothing.
# "Lite" models are left out because they got speaker gender wrong in testing.
DEFAULT_MODELS = ("gemini-3.8-flash", "gemini-3.6-flash")

# This is a mature game about survival. Its dialogue has violence and swearing,
# which the default filters can refuse even though translating it is harmless.
_SAFETY = [
    {"category": category, "threshold": "BLOCK_NONE"}
    for category in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]
_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {"id": {"type": "INTEGER"}, "hi": {"type": "STRING"}},
        "required": ["id", "hi"],
    },
}


class GeminiTranslator:
    def __init__(
        self,
        api_key: str | None = None,
        models: Sequence[str] = DEFAULT_MODELS,
        timeout: float = 600,
    ):
        self._key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        if not self._key:
            raise TranslatorError("No Gemini API key is set.")
        self._models = list(models)
        self._out_of_quota: set[str] = set()
        self.model = self._models[0]  # the model to use next
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"x-goog-api-key": self._key, "Content-Type": "application/json"})

    def _clean(self, message: str) -> str:
        return message.replace(self._key, "***")

    def check(self) -> None:
        """Confirm the key works. Listing models uses none of the daily translation quota."""
        try:
            response = self._session.get(f"{_BASE}/models?pageSize=1", timeout=30)
        except requests.RequestException as exc:
            raise Overloaded(f"Could not reach Gemini: {type(exc).__name__}") from None
        self._raise_for_status(response)

    def _post(
        self,
        model: str,
        lines: Sequence[RequestLine],
        system_prompt: str,
        context: Sequence[tuple[str, str]],
    ) -> requests.Response:
        payload: dict = {"lines": [_line_json(line) for line in lines]}
        if context:
            payload["previous_lines"] = [{"en": en, "hi": hi} for en, hi in context]
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
            "generationConfig": {
                "temperature": 0.3,
                "responseMimeType": "application/json",
                "responseSchema": _SCHEMA,
                "thinkingConfig": {"thinkingLevel": "low"},
            },
            "safetySettings": _SAFETY,
        }
        try:
            return self._session.post(
                f"{_BASE}/models/{model}:generateContent", json=body, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise Overloaded(f"Could not reach Gemini: {type(exc).__name__}") from None

    def translate(
        self,
        system_prompt: str,
        lines: Sequence[RequestLine],
        context: Sequence[tuple[str, str]],
    ) -> tuple[dict[int, str], Usage]:
        while True:
            available = [m for m in self._models if m not in self._out_of_quota]
            if not available:
                raise RateLimited("Today's Gemini limit has been reached for every model.", 3600, daily=True)
            if self.model not in available:
                self.model = available[0]
            try:
                return self._translate_with(self.model, system_prompt, lines, context)
            except RateLimited as limit:
                if not limit.daily:
                    raise
                self._out_of_quota.add(self.model)
            except Overloaded:
                # "Busy" replies count against the daily limit, so never retry straight away.
                # The caller waits, and the next attempt uses the other model.
                self.model = available[(available.index(self.model) + 1) % len(available)]
                raise

    def _translate_with(
        self,
        model: str,
        system_prompt: str,
        lines: Sequence[RequestLine],
        context: Sequence[tuple[str, str]],
    ) -> tuple[dict[int, str], Usage]:
        response = self._post(model, lines, system_prompt, context)
        self._raise_for_status(response)
        data = response.json()

        meta = data.get("usageMetadata", {})
        usage = Usage(
            meta.get("promptTokenCount", 0),
            meta.get("candidatesTokenCount", 0),
            meta.get("thoughtsTokenCount", 0),
        )
        candidates = data.get("candidates") or []
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "no reply")
            raise ContentBlocked(f"Gemini returned nothing ({reason})")
        candidate = candidates[0]
        text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []))
        if candidate.get("finishReason") not in (None, "STOP") and not text:
            raise ContentBlocked(f"Gemini stopped early ({candidate.get('finishReason')})")
        return _parse_reply(text), usage

    def _raise_for_status(self, response: requests.Response) -> None:
        status = response.status_code
        if status == 200:
            return
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        message = self._clean(error.get("message", "") or f"HTTP {status}")
        if status == 429:
            raise RateLimited(message, _retry_after(error), _is_daily(error, message))
        if status in (500, 502, 503, 504):
            raise Overloaded(message)
        raise TranslatorError(f"Gemini error {status}: {message[:300]}")


def _line_json(line: RequestLine) -> dict:
    out: dict = {"id": line.id, "en": line.text}
    if line.hint:
        out["ru"] = line.hint
    if line.problem:
        out["earlier_attempt_problem"] = line.problem
    return out


def _parse_reply(text: str) -> dict[int, str]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        rows = json.loads(cleaned)
    except ValueError:
        return {}
    out: dict[int, str] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("id"), int) and isinstance(row.get("hi"), str):
                out[row["id"]] = row["hi"]
    return out


def _retry_after(error: dict) -> float:
    for detail in error.get("details", []):
        delay = detail.get("retryDelay")
        if delay:
            match = re.match(r"([\d.]+)s", delay)
            if match:
                return float(match.group(1))
    return 30.0


def _is_daily(error: dict, message: str) -> bool:
    for detail in error.get("details", []):
        for violation in detail.get("violations", []):
            if "perday" in violation.get("quotaId", "").lower():
                return True
    return "per day" in message.lower() or "limit: 0" in message.lower()
