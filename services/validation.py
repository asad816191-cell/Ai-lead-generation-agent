"""Validation of user input for the search endpoint."""
from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ValidationError(ValueError):
    pass


def _text(value, name: str, max_len: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be text.")
    value = _CONTROL.sub("", value).strip()
    if len(value) > max_len:
        raise ValidationError(f"{name} is too long (maximum {max_len} characters).")
    return value


def validate_search_payload(data, max_leads: int) -> dict:
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object.")
    prompt = _text(data.get("prompt"), "Prompt", 1000)
    industry = _text(data.get("industry"), "Industry", 120)
    location = _text(data.get("location"), "Location", 120)

    count = data.get("count")
    if count in (None, ""):
        count = None
    else:
        try:
            count = int(count)
        except (TypeError, ValueError):
            raise ValidationError("Lead quantity must be a whole number.") from None
        if not 1 <= count <= max_leads:
            raise ValidationError(f"Lead quantity must be between 1 and {max_leads}.")

    if not prompt and not (industry and location):
        raise ValidationError("Enter a prompt, or fill in both Industry and Location.")
    if prompt and len(prompt) < 5:
        raise ValidationError("The prompt is too short to understand.")
    enrich = data.get("enrich", True)
    return {"prompt": prompt, "industry": industry, "location": location, "count": count,
            "enrich": bool(enrich)}
