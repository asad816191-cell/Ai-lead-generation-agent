"""Shared data model: lead fields, the search specification and small helpers."""
from __future__ import annotations

from dataclasses import dataclass, field

NOT_FOUND = "Not Found"

# (key, human-readable label) - this order is used for the table, CSV and Excel.
LEAD_FIELDS: list[tuple[str, str]] = [
    ("company_name", "Company Name"),
    ("industry", "Industry"),
    ("phone", "Public Business Phone"),
    ("email", "Public Business Email"),
    ("email_status", "Email Status"),
    ("website", "Official Website"),
    ("address", "Full Business Address"),
    ("city", "City"),
    ("region", "State/Province/Region"),
    ("country", "Country"),
    ("contact_person", "Publicly Listed Contact Person"),
    ("maps_url", "Google Maps Link"),
    ("source_url", "Source URL(s)"),
    ("source_type", "Source Type"),
    ("verification_status", "Verification Status"),
]
LEAD_KEYS = [k for k, _ in LEAD_FIELDS]
LEAD_LABELS = [label for _, label in LEAD_FIELDS]


def is_missing(value) -> bool:
    return value is None or str(value).strip() in ("", NOT_FOUND)


def new_lead(**values) -> dict:
    """A lead with every public field defaulting to 'Not Found'.

    Keys starting with an underscore are internal flags (never stored or exported).
    """
    lead = {key: NOT_FOUND for key in LEAD_KEYS}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        lead[key] = value
    return lead


def join_unique(existing: str, new: str, sep: str = " | ") -> str:
    """Merge two separator-joined strings, keeping order and dropping duplicates/'Not Found'."""
    parts: list[str] = []
    for chunk in (existing, new):
        if is_missing(chunk):
            continue
        for piece in str(chunk).split(sep.strip()):
            piece = piece.strip()
            if piece and piece != NOT_FOUND and piece not in parts:
                parts.append(piece)
    return sep.join(parts) if parts else NOT_FOUND


@dataclass
class SearchSpec:
    industry: str
    location: str
    count: int
    filters: list[str] = field(default_factory=list)
    prompt: str = ""
    areas: list[str] = field(default_factory=list)  # optional sub-areas used to widen a search
    parser: str = "rules"  # "rules" or "llm"
