"""Provider interface. Add a new lead source by subclassing LeadProvider and
registering it in services/pipeline.py:build_providers()."""
from __future__ import annotations

from typing import Callable

from .models import SearchSpec

ProgressCallback = Callable[[str], None]


class ProviderError(Exception):
    """A provider could not run at all (bad key, quota, outage)."""


class LeadProvider:
    name = "base"
    source_type = "Unknown"

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def is_configured(self) -> bool:
        raise NotImplementedError

    def search(self, spec: SearchSpec, target: int,
               progress: ProgressCallback | None = None) -> list[dict]:
        """Return raw lead dicts (see models.new_lead). Never invent data."""
        raise NotImplementedError
