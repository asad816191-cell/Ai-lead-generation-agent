"""Google Places API (New) provider - structured business listings."""
from __future__ import annotations

from .base import LeadProvider, ProgressCallback
from .http_utils import HttpError, request_json
from .models import SearchSpec, new_lead

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.addressComponents",
    "places.nationalPhoneNumber", "places.internationalPhoneNumber", "places.websiteUri",
    "places.googleMapsUri", "places.primaryTypeDisplayName", "places.businessStatus",
    "nextPageToken",
])
PAGE_SIZE = 20
MAX_PAGES_PER_QUERY = 3  # Google returns at most ~60 results per query


def _component(components: list[dict], *types: str) -> str | None:
    for wanted in types:
        for comp in components or []:
            if wanted in comp.get("types", []):
                return comp.get("longText") or comp.get("shortText")
    return None


class GooglePlacesProvider(LeadProvider):
    name = "Google Places"
    source_type = "Google Places API"

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def is_configured(self) -> bool:
        return bool(self.settings.GOOGLE_PLACES_API_KEY)

    def _queries(self, spec: SearchSpec) -> list[str]:
        ind, loc = spec.industry, spec.location
        extra = " ".join(spec.filters)
        queries = [f"{ind} in {loc}"]
        if extra:
            queries.append(f"{ind} {extra} in {loc}")
        queries += [f"{area} {ind} {loc}" for area in spec.areas]
        queries += [f"{ind} companies in {loc}", f"{ind} offices in {loc}", f"best {ind} {loc}"]
        seen, ordered = set(), []
        for q in queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                ordered.append(q)
        return ordered

    def _to_lead(self, place: dict, spec: SearchSpec) -> dict | None:
        if place.get("businessStatus") == "CLOSED_PERMANENTLY":
            return None
        name = (place.get("displayName") or {}).get("text")
        if not name:
            return None
        comps = place.get("addressComponents") or []
        maps_url = place.get("googleMapsUri")
        lead = new_lead(
            company_name=name,
            industry=(place.get("primaryTypeDisplayName") or {}).get("text") or spec.industry,
            phone=place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber"),
            website=place.get("websiteUri"),
            address=place.get("formattedAddress"),
            city=_component(comps, "locality", "postal_town", "sublocality", "administrative_area_level_3"),
            region=_component(comps, "administrative_area_level_1"),
            country=_component(comps, "country"),
            maps_url=maps_url,
            source_url=maps_url,
            source_type=self.source_type,
        )
        lead["_google"] = True
        lead["_place_id"] = place.get("id")
        return lead

    def search(self, spec: SearchSpec, target: int, progress: ProgressCallback | None = None) -> list[dict]:
        headers = {
            "X-Goog-Api-Key": self.settings.GOOGLE_PLACES_API_KEY,
            "X-Goog-FieldMask": FIELD_MASK,
            "Content-Type": "application/json",
        }
        leads: list[dict] = []
        seen_ids: set[str] = set()
        for query in self._queries(spec):
            token = None
            for _page in range(MAX_PAGES_PER_QUERY):
                body = {"textQuery": query, "pageSize": PAGE_SIZE}
                if token:
                    body["pageToken"] = token
                try:
                    data = request_json("POST", SEARCH_URL, headers=headers, json_body=body,
                                        timeout=self.settings.REQUEST_TIMEOUT)
                except HttpError as exc:
                    self.warnings.append(f"Google Places stopped early: {exc}")
                    if exc.status in (400, 401, 403, 429, None):
                        return leads
                    break
                for place in data.get("places", []):
                    pid = place.get("id")
                    if pid and pid in seen_ids:
                        continue
                    if pid:
                        seen_ids.add(pid)
                    lead = self._to_lead(place, spec)
                    if lead:
                        leads.append(lead)
                if progress:
                    progress(f"Google Places: {len(leads)} listings found")
                if len(leads) >= target:
                    return leads
                token = data.get("nextPageToken")
                if not token:
                    break
        return leads
