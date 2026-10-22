"""Central configuration. All secrets come from the .env file / environment."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _clean_key(value: str | None) -> str:
    """Treat blanks and the placeholder values from .env.example as 'not set'."""
    value = (value or "").strip().strip('"').strip("'")
    if not value or value.lower().startswith("your_") or value.lower().endswith("_here"):
        return ""
    return value


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int, lo: int, hi: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


@dataclass(frozen=True)
class Settings:
    SECRET_KEY: str = "dev-only-secret"
    DATABASE_PATH: str = str(BASE_DIR / "data" / "leads.db")

    TAVILY_API_KEY: str = ""
    GOOGLE_PLACES_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    MAX_LEADS_PER_SEARCH: int = 500
    DEFAULT_LEAD_COUNT: int = 50
    REQUEST_TIMEOUT: int = 15
    ENRICH_WORKERS: int = 5
    ENRICH_BATCH_SIZE: int = 10
    ENRICH_WEBSITES_DEFAULT: bool = True
    RESPECT_ROBOTS_TXT: bool = True
    CHECK_EMAIL_DOMAIN_MX: bool = True
    USER_AGENT: str = "LeadGenAgent/1.0 (public business contact lookup; respects robots.txt)"

    HOST: str = "127.0.0.1"
    PORT: int = 5000

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            SECRET_KEY=env("SECRET_KEY") or secrets.token_hex(16),
            DATABASE_PATH=env("DATABASE_PATH") or str(BASE_DIR / "data" / "leads.db"),
            TAVILY_API_KEY=_clean_key(env("TAVILY_API_KEY")),
            GOOGLE_PLACES_API_KEY=_clean_key(env("GOOGLE_PLACES_API_KEY")),
            OPENAI_API_KEY=_clean_key(env("OPENAI_API_KEY")),
            OPENAI_MODEL=env("OPENAI_MODEL") or "gpt-4o-mini",
            OPENAI_BASE_URL=(env("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
            MAX_LEADS_PER_SEARCH=_int(env("MAX_LEADS_PER_SEARCH"), 500, 1, 2000),
            DEFAULT_LEAD_COUNT=_int(env("DEFAULT_LEAD_COUNT"), 50, 1, 2000),
            REQUEST_TIMEOUT=_int(env("REQUEST_TIMEOUT"), 15, 3, 120),
            ENRICH_WORKERS=_int(env("ENRICH_WORKERS"), 5, 1, 16),
            ENRICH_BATCH_SIZE=_int(env("ENRICH_BATCH_SIZE"), 10, 1, 100),
            ENRICH_WEBSITES_DEFAULT=_bool(env("ENRICH_WEBSITES_DEFAULT"), True),
            RESPECT_ROBOTS_TXT=_bool(env("RESPECT_ROBOTS_TXT"), True),
            CHECK_EMAIL_DOMAIN_MX=_bool(env("CHECK_EMAIL_DOMAIN_MX"), True),
            HOST=env("HOST") or "127.0.0.1",
            PORT=_int(env("PORT"), 5000, 1, 65535),
        )
