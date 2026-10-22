"""Tavily Search API provider - discovers companies and official sites on the open web."""
from __future__ import annotations

from .base import LeadProvider, ProgressCallback
from .http_utils import HttpError, request_json
from .lead_extractor import extract_companies_from_results
from .llm import LLMClient
from .models import SearchSpec

TAVILY_URL = "https://api.tavily.com/search"


class TavilyProvider(LeadProvider):
    name = "Tavily"
    source_type = "Web search (Tavily)"

    def __init__(self, settings, llm: LLMClient | None = None):
        super().__init__()
        self.settings = settings
        self.llm = llm

    def is_configured(self) -> bool:
        return bool(self.settings.TAVILY_API_KEY)

    def _queries(self, spec: SearchSpec) -> list[str]:
        ind, loc = spec.industry, spec.location
        queries = [
            f"{ind} companies in {loc}",
            f"{ind} in {loc} official website contact",
            f"{ind} {loc} directory phone address",
            f"top {ind} firms {loc}",
        ]
        if spec.filters:
            queries.insert(0, f"{ind} {' '.join(spec.filters)} in {loc}")
        queries += [f"{ind} in {area} {loc}" for area in spec.areas]
        return queries

    def search(self, spec: SearchSpec, target: int, progress: ProgressCallback | None = None) -> list[dict]:
        headers = {"Authorization": f"Bearer {self.settings.TAVILY_API_KEY}",
                   "Content-Type": "application/json"}
        leads: list[dict] = []
        seen_urls: set[str] = set()
        for query in self._queries(spec):
            body = {"query": query, "search_depth": "basic", "max_results": 20,
                    "include_answer": False, "include_raw_content": False}
            try:
                data = request_json("POST", TAVILY_URL, headers=headers, json_body=body,
                                    timeout=self.settings.REQUEST_TIMEOUT)
            except HttpError as exc:
                self.warnings.append(f"Tavily stopped early: {exc}")
                if exc.status in (400, 401, 403, 429, 432, 433, None):
                    break
                continue
            results = []
            for item in data.get("results", []):
                url = item.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(item)
            leads.extend(extract_companies_from_results(results, spec, self.llm,
                                                        source_type=self.source_type))
            if progress:
                progress(f"Tavily: {len(leads)} candidate companies from web results")
            if len(leads) >= target:
                break
        if not leads and not self.warnings:
            self.warnings.append("Tavily returned no usable company results for this query.")
        return leads
