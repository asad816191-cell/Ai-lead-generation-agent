"""Duplicate removal (normalised name / website domain), record merging and verification labels."""
from __future__ import annotations

import re

from .models import NOT_FOUND, is_missing, join_unique
from .url_utils import is_aggregator, normalize_domain

_LEGAL_SUFFIXES = {"inc", "incorporated", "ltd", "limited", "llc", "llp", "lp", "corp", "corporation",
                   "co", "company", "plc", "gmbh", "sa", "pty", "the"}


def normalize_name(name: str) -> str:
    if is_missing(name):
        return ""
    text = re.sub(r"&", " and ", str(name).lower())
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = [w for w in text.split() if w not in _LEGAL_SUFFIXES]
    return " ".join(words)


def lead_keys(lead: dict) -> list[tuple[str, str]]:
    keys = []
    domain = normalize_domain(lead.get("website") or "") if not is_missing(lead.get("website")) else ""
    if domain and not is_aggregator(domain):
        keys.append(("domain", domain))
    name = normalize_name(lead.get("company_name"))
    if name:
        keys.append(("name", name))
    return keys


def merge_leads(base: dict, other: dict) -> dict:
    """Merge `other` into `base`: fill gaps, combine sources, never overwrite real data."""
    for key, value in other.items():
        if key.startswith("_"):
            base[key] = base.get(key) or value
            continue
        if key in ("source_url",):
            base[key] = join_unique(base.get(key, NOT_FOUND), value, " | ")
        elif key == "source_type":
            base[key] = join_unique(base.get(key, NOT_FOUND), value, " + ")
        elif key == "email_status":
            continue  # handled together with email
        elif is_missing(base.get(key)) and not is_missing(value):
            base[key] = value
            if key == "email" and not is_missing(other.get("email_status")):
                base["email_status"] = other["email_status"]
    if is_missing(base.get("email")):
        base["email_status"] = NOT_FOUND
    return base


def deduplicate(leads: list[dict]) -> list[dict]:
    """Group leads sharing a website domain or normalised company name and merge each group."""
    groups: list[dict | None] = []
    index: dict[tuple[str, str], int] = {}
    for lead in leads:
        if is_missing(lead.get("company_name")):
            continue
        keys = lead_keys(lead)
        hits = sorted({index[k] for k in keys if k in index})
        if not hits:
            groups.append(dict(lead))
            gid = len(groups) - 1
        else:
            gid = hits[0]
            merge_leads(groups[gid], lead)
            for other_id in hits[1:]:  # this lead bridges two earlier groups
                merge_leads(groups[gid], groups[other_id])
                groups[other_id] = None
                for k, v in list(index.items()):
                    if v == other_id:
                        index[k] = gid
        for k in lead_keys(groups[gid]) + keys:
            index[k] = gid
    return [g for g in groups if g is not None]


def completeness(lead: dict) -> int:
    return sum(0 if is_missing(lead.get(k)) else 1 for k in ("phone", "email", "website", "address"))


def assign_verification_status(lead: dict) -> dict:
    """Honest provenance label. 'Verified' here only ever means 'found in an authoritative
    public source' - phone numbers and emails are never tested by calling or mailing them."""
    google, site = lead.get("_google"), lead.get("_site_checked")
    if google and site:
        status = "Cross-checked: Google Places listing + official website visited"
    elif google:
        status = "Listed on Google Places (official website contact page not confirmed)"
    elif site:
        status = "Official website visited (contact details copied from the site)"
    else:
        status = "Unverified: found in web search results only"
    lead["verification_status"] = status
    if is_missing(lead.get("email")):
        lead["email_status"] = NOT_FOUND
    elif is_missing(lead.get("email_status")):
        lead["email_status"] = "Found in source text (not verified)"
    return lead
