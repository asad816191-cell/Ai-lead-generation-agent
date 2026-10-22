"""Turn a natural-language request into a SearchSpec.

The LLM (if configured) does the understanding; a deterministic regex parser is always run first
so the app still works without an OpenAI key and so LLM output can be sanity-checked.
"""
from __future__ import annotations

import re

from .llm import LLMClient, LLMError
from .models import SearchSpec

_VERBS = r"(?:find|get|list|give\s+me|show\s+me|search\s+for|collect|generate|compile|look\s+for|i\s+need|i\s+want)"
_PREP = r"(?:in|near|around|within|from|based\s+in|located\s+in)"
_FIELD_CLAUSE = r"(?:including|include|includes|with\s+their|with\s+company|with\s+names?|with\s+phone|with\s+email|along\s+with|and\s+their|containing|plus)"
_FILTER_CLAUSE = r"(?:that|which|who|having|where|with|specializing|specialising)"

_MAIN = re.compile(
    rf"\b{_VERBS}\s+(?:me\s+)?(?:(?:the\s+)?(?:top|best)\s+)?(?:up\s+to\s+)?(?:(\d[\d,]*)\s+)?(.+?)\s+{_PREP}\s+(.+)$",
    re.I | re.S,
)
_SIMPLE = re.compile(rf"^\s*(?:(\d[\d,]*)\s+)?(.+?)\s+{_PREP}\s+(.+)$", re.I | re.S)
_TAIL_SPLIT = re.compile(rf"\s*,?\s*\b({_FIELD_CLAUSE}|{_FILTER_CLAUSE})\b", re.I)
_LEADING_NOISE = re.compile(r"^(?:all|some|any|the|a|an)\s+", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" \t\r\n.,;:!?\"'")


def _rule_parse(prompt: str, default_count: int) -> dict:
    match = _MAIN.search(prompt) or _SIMPLE.match(prompt)
    if not match:
        return {}
    count_raw, industry, tail = match.groups()
    filters: list[str] = []
    location = tail
    split = _TAIL_SPLIT.search(tail)
    if split:
        location = tail[: split.start()]
        remainder = tail[split.end():]
        keyword = split.group(1).lower()
        if re.fullmatch(_FILTER_CLAUSE, keyword) and not re.match(
            r"\s*(?:their|company|names?|phone|emails?|websites?|addresses|address)\b", remainder, re.I
        ):
            filters.append(_clean(f"{keyword} {remainder}"))
    industry = _LEADING_NOISE.sub("", _clean(industry))
    result = {
        "industry": industry,
        "location": _clean(location),
        "count": int(count_raw.replace(",", "")) if count_raw else default_count,
        "filters": [f for f in filters if f],
    }
    return result


def _valid_count(value, max_leads: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= max_leads else None


def parse_prompt(prompt: str, *, llm: LLMClient | None = None, default_count: int = 50,
                 max_leads: int = 500, overrides: dict | None = None) -> SearchSpec:
    """Return a SearchSpec. `overrides` (industry/location/count from the form) always win.

    Raises ValueError if industry or location cannot be determined.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
    data = _rule_parse(prompt or "", default_count) if prompt else {}
    parser = "rules"

    if prompt and llm and llm.available:
        try:
            result = llm.json_chat(
                "You extract lead-generation search parameters from a user's request. Reply with JSON only: "
                '{"industry": string, "location": string (city, region, country as written), '
                '"count": integer or null, "filters": [strings]}. '
                "Use null/[] when something is not stated. Never invent values.",
                prompt,
                max_tokens=300,
            )
            if isinstance(result, dict):
                if isinstance(result.get("industry"), str) and result["industry"].strip():
                    data["industry"] = _clean(result["industry"])
                if isinstance(result.get("location"), str) and result["location"].strip():
                    data["location"] = _clean(result["location"])
                count = _valid_count(result.get("count"), max_leads)
                if count:
                    data["count"] = count
                if isinstance(result.get("filters"), list):
                    data["filters"] = [_clean(f) for f in result["filters"] if isinstance(f, str) and _clean(f)][:5]
                parser = "llm"
        except LLMError:
            pass  # silently fall back to the rule-based result

    for key in ("industry", "location"):
        if key in overrides:
            data[key] = _clean(str(overrides[key]))
    if "count" in overrides:
        data["count"] = int(overrides["count"])

    industry, location = data.get("industry", ""), data.get("location", "")
    if not industry or not location:
        raise ValueError(
            "Could not work out the industry and location. Try a prompt like "
            "'Find 100 real estate companies in Toronto, Ontario, Canada' or fill in the "
            "Industry and Location fields."
        )
    count = data.get("count") or default_count
    count = max(1, min(int(count), max_leads))
    return SearchSpec(industry=industry, location=location, count=count,
                      filters=data.get("filters", []), prompt=prompt or "", parser=parser)
