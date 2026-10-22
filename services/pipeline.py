"""The search pipeline: discover -> deduplicate -> enrich from official sites -> label -> save."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from .base import LeadProvider, ProviderError
from .business_directory import GooglePlacesProvider
from .deduplication import assign_verification_status, completeness, deduplicate
from .lead_extractor import WebsiteEnricher
from .llm import LLMClient, suggest_areas
from .models import SearchSpec, is_missing
from .web_search import TavilyProvider

log = logging.getLogger(__name__)


def build_llm(settings) -> LLMClient | None:
    if not settings.OPENAI_API_KEY:
        return None
    return LLMClient(settings.OPENAI_API_KEY, settings.OPENAI_MODEL, settings.OPENAI_BASE_URL)


def build_providers(settings, llm: LLMClient | None) -> list[LeadProvider]:
    """Order matters: structured listings first, open-web discovery fills any shortfall.
    To add a source, create a LeadProvider subclass and add it here."""
    providers: list[LeadProvider] = [GooglePlacesProvider(settings), TavilyProvider(settings, llm)]
    return [p for p in providers if p.is_configured()]


def run_search(search_id: int, spec: SearchSpec, settings, db, *, enrich: bool = True,
               providers: list[LeadProvider] | None = None, enricher=None,
               llm: LLMClient | None = None) -> None:
    def report(pct: int, message: str, stage: str = "running") -> None:
        db.update_search(search_id, status="running", stage=stage, progress=int(pct), message=message)

    def fail(message: str, warnings: list[str]) -> None:
        db.update_search(search_id, status="failed", stage="failed", message=message,
                         warnings=warnings, finished_at=_now())

    try:
        llm = llm if llm is not None else build_llm(settings)
        providers = providers if providers is not None else build_providers(settings, llm)
        if not providers:
            fail("No search provider is configured. Add GOOGLE_PLACES_API_KEY and/or TAVILY_API_KEY "
                 "to your .env file and restart the app.", [])
            return

        warnings: list[str] = []
        target = spec.count
        want = int(target * 1.5) + 5
        report(3, "Understanding the request")
        if llm and llm.available and not spec.areas and target > 40:
            report(5, "Splitting the location into areas for broader coverage")
            spec.areas = suggest_areas(llm, spec)

        raw: list[dict] = []
        for i, provider in enumerate(providers):
            if i > 0 and len(deduplicate(raw)) >= int(target * 1.2):
                continue  # enough candidates already; save API credits
            base_pct = 8 + int(40 * i / max(len(providers), 1))
            report(base_pct, f"Searching {provider.name}")
            try:
                found = provider.search(spec, want, lambda m, p=base_pct: report(p + 10, m))
            except ProviderError as exc:
                warnings.append(f"{provider.name}: {exc}")
                found = []
            except Exception as exc:  # a provider bug must not kill the whole search
                log.exception("Provider %s crashed", provider.name)
                warnings.append(f"{provider.name} failed unexpectedly ({exc.__class__.__name__}).")
                found = []
            warnings.extend(provider.warnings)
            raw.extend(found)

        report(52, f"Removing duplicates from {len(raw)} raw results")
        leads = deduplicate(raw)
        if not leads:
            if warnings:
                fail("No leads could be collected: " + warnings[0], warnings)
            else:
                db.update_search(search_id, status="completed", stage="done", progress=100,
                                 message="The sources returned no matching businesses.",
                                 warnings=warnings, result_count=0, finished_at=_now())
            return

        leads.sort(key=completeness, reverse=True)  # stable: keeps source order among equals
        leads = leads[:target]

        if enrich:
            enricher = enricher or WebsiteEnricher(settings)
            to_visit = [lead for lead in leads if not is_missing(lead.get("website"))]
            total = len(to_visit)
            done = 0
            batch = settings.ENRICH_BATCH_SIZE

            def safe_enrich(lead: dict) -> None:
                try:
                    enricher.enrich(lead)
                except Exception:
                    log.exception("Enrichment failed for %s", lead.get("company_name"))

            with ThreadPoolExecutor(max_workers=settings.ENRICH_WORKERS) as pool:
                for start in range(0, total, batch):
                    chunk = to_visit[start:start + batch]
                    list(pool.map(safe_enrich, chunk))
                    done += len(chunk)
                    report(55 + int(35 * done / max(total, 1)),
                           f"Checking official websites ({done}/{total})")
            leads = deduplicate(leads)

        report(94, "Saving results")
        for lead in leads:
            assign_verification_status(lead)
        db.save_leads(search_id, leads)

        if len(leads) < target:
            warnings.append(f"Found {len(leads)} unique businesses, fewer than the {target} requested. "
                            "The sources did not return more matches for this query.")
        db.update_search(search_id, status="completed", stage="done", progress=100,
                         message=f"Done - {len(leads)} leads", warnings=warnings,
                         result_count=len(leads), finished_at=_now())
    except Exception as exc:
        log.exception("Search %s crashed", search_id)
        fail(f"Unexpected error ({exc.__class__.__name__}). See the server log for details.", [])


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
