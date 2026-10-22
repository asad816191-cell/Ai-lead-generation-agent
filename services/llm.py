"""Minimal OpenAI-compatible chat client (works with OpenAI or any compatible endpoint)."""
from __future__ import annotations

import json
import re

from .http_utils import HttpError, request_json
from .models import SearchSpec


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, api_key: str, model: str, base_url: str, timeout: int = 40):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def json_chat(self, system: str, user: str, max_tokens: int = 1500) -> dict:
        if not self.available:
            raise LLMError("No OpenAI API key configured")
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            data = request_json("POST", f"{self.base_url}/chat/completions",
                                headers={"Authorization": f"Bearer {self.api_key}"},
                                json_body=body, timeout=self.timeout, attempts=2)
            content = data["choices"][0]["message"]["content"]
        except HttpError as exc:
            raise LLMError(str(exc)) from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected LLM response format") from exc
        try:
            return json.loads(content)
        except (TypeError, ValueError):
            match = re.search(r"\{.*\}", content or "", re.S)
            if match:
                try:
                    return json.loads(match.group(0))
                except ValueError:
                    pass
        raise LLMError("LLM did not return valid JSON")


def suggest_areas(llm: LLMClient | None, spec: SearchSpec, limit: int = 12) -> list[str]:
    """Ask the LLM for neighbourhoods/districts of the target location so a big search can be
    split into many smaller map searches. Only geography is requested - never company data."""
    if not llm or not llm.available:
        return []
    try:
        data = llm.json_chat(
            "You are a geography assistant. Reply with JSON only.",
            f'List up to {limit} well-known neighbourhoods, districts or suburbs of "{spec.location}" '
            'that are real places. Return {"areas": ["name", ...]}. Do not list businesses.',
            max_tokens=300,
        )
    except LLMError:
        return []
    areas = data.get("areas") if isinstance(data, dict) else None
    if not isinstance(areas, list):
        return []
    cleaned = []
    for area in areas:
        if isinstance(area, str) and 2 <= len(area.strip()) <= 60 and area.strip() not in cleaned:
            cleaned.append(area.strip())
    return cleaned[:limit]
