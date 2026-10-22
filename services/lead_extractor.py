"""Turning raw sources into leads.

1. extract_companies_from_results(): search-result snippets -> candidate companies.
   Uses the LLM when available (with every extracted value checked against the source text),
   otherwise a conservative title/domain heuristic.
2. WebsiteEnricher: visits a company's own public website (home + contact page, robots.txt
   respected, no logins/CAPTCHAs) and copies publicly listed email/phone/contact person.

Nothing here guesses or constructs contact details - values are copied from the source only.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from urllib.parse import unquote, urljoin, urldefrag, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from .llm import LLMClient, LLMError
from .models import NOT_FOUND, SearchSpec, is_missing, join_unique, new_lead
from .url_utils import ensure_scheme, is_aggregator, is_public_url, normalize_domain, root_url

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_JUNK_EMAIL_DOMAINS = {"sentry.io", "wixpress.com", "example.com", "domain.com", "email.com",
                       "yourdomain.com", "mysite.com", "sentry-next.wixpress.com"}
_JUNK_EMAIL_LOCALS = {"name", "email", "user", "username", "yourname", "you", "your", "john.doe",
                      "first.last", "example", "test"}
_BAD_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "woff", "woff2"}
_GENERIC_TITLE_PARTS = {"home", "homepage", "welcome", "contact", "contact us", "about", "about us",
                        "official site", "official website", "official", "index", "main"}
_LISTICLE_RE = re.compile(
    r"\b(top|best|leading|\d+\s+(best|top)|list of|directory|near me|review|reviews|guide|vs\.?|ranking|ranked)\b", re.I)
_CAPTCHA_HINTS = ("captcha", "are you a robot", "verify you are human", "access denied", "cf-chl")
MAX_PAGE_BYTES = 1_500_000


# --------------------------------------------------------------------------------------
# 1. Search results -> candidate companies
# --------------------------------------------------------------------------------------
def _clean_title(title: str) -> str:
    parts = [p.strip() for p in re.split(r"\s[|\-–—:·»]\s|\s::\s", title or "") if p.strip()]
    for part in parts:
        if part.lower() not in _GENERIC_TITLE_PARTS and 2 <= len(part) <= 80:
            return part
    return ""


def _heuristic_extract(results: list[dict], source_type: str) -> list[dict]:
    leads = []
    for item in results:
        url = item.get("url") or ""
        domain = normalize_domain(url)
        if not domain or is_aggregator(domain):
            continue
        title = item.get("title") or ""
        if _LISTICLE_RE.search(title):
            continue
        name = _clean_title(title)
        if not name:
            continue
        lead = new_lead(company_name=name, website=root_url(url), source_url=url, source_type=source_type)
        lead["_web"] = True
        leads.append(lead)
    return leads


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _grounded(value: str | None, blob: str, blob_digits: str, kind: str) -> bool:
    """Is `value` literally present in the source text? Guards against LLM hallucination."""
    if not value or not isinstance(value, str):
        return False
    value = value.strip()
    if kind == "phone":
        digits = _digits(value)
        return len(digits) >= 7 and digits in blob_digits
    if kind == "website":
        domain = normalize_domain(value)
        return bool(domain) and domain in blob
    return value.lower() in blob


def _llm_extract(results: list[dict], spec: SearchSpec, llm: LLMClient, source_type: str) -> list[dict]:
    leads: list[dict] = []
    for start in range(0, len(results), 8):
        batch = results[start:start + 8]
        docs = []
        for i, item in enumerate(batch):
            docs.append(f"[{i}] URL: {item.get('url')}\nTITLE: {item.get('title')}\n"
                        f"TEXT: {(item.get('content') or '')[:800]}")
        text = "\n\n".join(docs)
        blob = text.lower()
        blob_digits = _digits(text)
        try:
            data = llm.json_chat(
                "You extract business listings from web search results. Reply with JSON only: "
                '{"companies": [{"name": str, "website": str|null, "phone": str|null, "email": str|null, '
                '"address": str|null, "result_index": int}]}. '
                "Rules: only include real businesses that are explicitly named in the text and match the "
                f"requested industry ('{spec.industry}') and location ('{spec.location}'). Copy phone, email, "
                "address and website exactly as written in the text; use null when absent. NEVER guess or "
                "construct emails, phone numbers or websites. Skip articles, directories and list pages "
                "themselves - only include the companies they mention.",
                text, max_tokens=1500)
        except LLMError as exc:
            log.warning("LLM extraction failed, using heuristic for this batch: %s", exc)
            leads.extend(_heuristic_extract(batch, source_type))
            continue
        for company in (data.get("companies") if isinstance(data, dict) else None) or []:
            if not isinstance(company, dict):
                continue
            name = (company.get("name") or "").strip()
            if not name or name.lower() not in blob:
                continue
            idx = company.get("result_index")
            src = batch[idx] if isinstance(idx, int) and 0 <= idx < len(batch) else batch[0]
            website = company.get("website") if _grounded(company.get("website"), blob, blob_digits, "website") else None
            if website and is_aggregator(normalize_domain(website)):
                website = None
            email = company.get("email") if _grounded(company.get("email"), blob, blob_digits, "email") else None
            phone = company.get("phone") if _grounded(company.get("phone"), blob, blob_digits, "phone") else None
            address = company.get("address") if _grounded(company.get("address"), blob, blob_digits, "address") else None
            if not website:
                src_domain = normalize_domain(src.get("url") or "")
                if src_domain and not is_aggregator(src_domain) and name.split()[0].lower() in src_domain:
                    website = root_url(src.get("url"))
            lead = new_lead(company_name=name, website=ensure_scheme(website) if website else None,
                            email=email, phone=phone, address=address,
                            source_url=src.get("url"), source_type=source_type)
            if email:
                lead["email_status"] = "Found in search-result text (not verified)"
            lead["_web"] = True
            leads.append(lead)
    return leads


def extract_companies_from_results(results: list[dict], spec: SearchSpec, llm: LLMClient | None,
                                   source_type: str = "Web search") -> list[dict]:
    if not results:
        return []
    if llm and llm.available:
        return _llm_extract(results, spec, llm, source_type)
    return _heuristic_extract(results, source_type)


# --------------------------------------------------------------------------------------
# 2. Website parsing / enrichment
# --------------------------------------------------------------------------------------
def _valid_email(email: str) -> bool:
    email = email.strip().strip(".,;:()<>[]\"'").lower()
    if "@" not in email:
        return False
    local, _, domain = email.rpartition("@")
    tld = domain.rsplit(".", 1)[-1]
    if tld in _BAD_TLDS or domain in _JUNK_EMAIL_DOMAINS or local in _JUNK_EMAIL_LOCALS:
        return False
    return bool(local) and ".." not in email and len(email) <= 120


def _json_ld_nodes(soup: BeautifulSoup) -> list[dict]:
    nodes: list[dict] = []

    def walk(obj):
        if isinstance(obj, list):
            for o in obj:
                walk(o)
        elif isinstance(obj, dict):
            nodes.append(obj)
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    walk(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or tag.get_text() or ""))
        except (ValueError, TypeError):
            continue
    return nodes


def parse_page(html: str, base_url: str) -> dict:
    """Extract publicly listed emails, phones, contact-page links and a contact person."""
    soup = BeautifulSoup(html, "html.parser")
    emails: list[str] = []
    phones: list[str] = []
    person = None

    def add_email(raw: str):
        raw = unquote(raw.split("?")[0]).strip().lower()
        if _valid_email(raw) and raw not in emails:
            emails.append(raw)

    def add_phone(raw: str):
        raw = unquote(raw).strip()
        digits = _digits(raw)
        if 7 <= len(digits) <= 15 and raw not in phones:
            phones.append(re.sub(r"\s+", " ", raw))

    for node in _json_ld_nodes(soup):
        if isinstance(node.get("email"), str):
            add_email(node["email"].replace("mailto:", ""))
        if isinstance(node.get("telephone"), str):
            add_phone(node["telephone"])
        for key in ("founder", "employee", "member", "contactPoint"):
            val = node.get(key)
            for item in val if isinstance(val, list) else [val]:
                if isinstance(item, dict) and isinstance(item.get("name"), str) and not person:
                    cand = item["name"].strip()
                    if 3 <= len(cand) <= 60 and not re.search(r"\d|@|customer|support|sales", cand, re.I):
                        person = cand

    links: list[str] = []
    base_domain = normalize_domain(base_url)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            add_email(href[7:])
        elif low.startswith("tel:"):
            add_phone(href[4:])
        elif not low.startswith(("javascript:", "#")):
            if "contact" in low or "contact" in a.get_text(" ").lower():
                full = urldefrag(urljoin(base_url, href))[0]
                if normalize_domain(full) == base_domain and full not in links:
                    links.append(full)

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for match in EMAIL_RE.findall(soup.get_text(" ")):
        add_email(match)

    return {"emails": emails, "phones": phones, "contact_links": links[:2], "contact_person": person}


def _mx_exists(domain: str) -> bool | None:
    """True/False if we could look it up, None if the optional dnspython package is missing."""
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        return None
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return len(answers) > 0
    except Exception:
        return False


class WebsiteEnricher:
    def __init__(self, settings, allow_private: bool = False):
        self.settings = settings
        self.allow_private = allow_private  # tests only
        self._robots: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()
        self.headers = {"User-Agent": settings.USER_AGENT, "Accept": "text/html,application/xhtml+xml"}

    # --- low-level fetching -------------------------------------------------------
    def _safe_get(self, url: str, max_redirects: int = 3) -> requests.Response | None:
        current = url
        for _ in range(max_redirects + 1):
            if not self.allow_private and not is_public_url(current):
                return None
            try:
                resp = requests.get(current, headers=self.headers, timeout=self.settings.REQUEST_TIMEOUT,
                                    allow_redirects=False, stream=True)
            except requests.RequestException:
                return None
            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                resp.close()
                if not loc:
                    return None
                current = urljoin(current, loc)
                continue
            return resp
        return None

    def _read_text(self, resp: requests.Response) -> str | None:
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype.lower() and "text" not in ctype.lower():
            resp.close()
            return None
        chunks, size = [], 0
        for chunk in resp.iter_content(16384):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_PAGE_BYTES:
                break
        resp.close()
        raw = b"".join(chunks)
        return raw.decode(resp.encoding or "utf-8", errors="replace")

    def _allowed(self, url: str) -> bool:
        if not self.settings.RESPECT_ROBOTS_TXT:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            cached = origin in self._robots
        if not cached:
            parser: RobotFileParser | None
            resp = self._safe_get(origin + "/robots.txt")
            if resp is None:
                parser = None  # unreachable -> be conservative
            elif resp.status_code == 200:
                text = self._read_text(resp) or ""
                parser = RobotFileParser()
                parser.parse(text.splitlines())
            elif 400 <= resp.status_code < 500:
                resp.close()
                parser = RobotFileParser()
                parser.parse([])  # no robots.txt -> everything allowed
            else:
                resp.close()
                parser = None
            with self._lock:
                self._robots[origin] = parser
        parser = self._robots[origin]
        return bool(parser and parser.can_fetch(self.settings.USER_AGENT, url))

    def _fetch_html(self, url: str) -> str | None:
        if not self._allowed(url):
            return None
        resp = self._safe_get(url)
        if resp is None:
            return None
        if resp.status_code != 200:  # includes 401/403/429: never try to get around them
            resp.close()
            return None
        text = self._read_text(resp)
        if text is None:
            return None
        head = text[:4000].lower()
        if any(hint in head for hint in _CAPTCHA_HINTS):
            return None
        return text

    # --- enrichment -----------------------------------------------------------------
    def enrich(self, lead: dict) -> dict:
        website = lead.get("website")
        if is_missing(website):
            return lead
        base = ensure_scheme(website)
        site_domain = normalize_domain(base)
        if not site_domain or is_aggregator(site_domain):
            return lead

        pages: list[tuple[str, dict]] = []
        html = self._fetch_html(base)
        if html is None:
            return lead
        lead["_site_checked"] = True
        pages.append((base, parse_page(html, base)))

        need_more = lambda: (not any(p["emails"] for _, p in pages)) or is_missing(lead.get("phone")) \
            and not any(p["phones"] for _, p in pages)
        if need_more():
            candidates = list(pages[0][1]["contact_links"])
            if not candidates:
                candidates = [urljoin(base, "/contact"), urljoin(base, "/contact-us")]
            for url in candidates[:2]:
                if not need_more():
                    break
                sub = self._fetch_html(url)
                if sub is not None:
                    pages.append((url, parse_page(sub, url)))

        # Choose values - prefer emails on the company's own domain.
        found_email, email_page = None, None
        all_emails = [(e, u) for u, p in pages for e in p["emails"]]
        own = [(e, u) for e, u in all_emails if self._same_site(e.rsplit("@", 1)[1], site_domain)]
        if own or all_emails:
            found_email, email_page = (own or all_emails)[0]

        used_pages: list[str] = []
        if found_email:
            same_other = [e for e, _ in (own or all_emails)][:3]
            if is_missing(lead.get("email")):
                lead["email"] = "; ".join(same_other)
                status = "Published on official website (mailbox not verified)"
                if self.settings.CHECK_EMAIL_DOMAIN_MX:
                    mx = _mx_exists(found_email.rsplit("@", 1)[1])
                    if mx is True:
                        status = "Published on official website; domain has mail server (MX) - mailbox not verified"
                    elif mx is False:
                        status = "Published on official website; WARNING: domain has no mail server (MX)"
                lead["email_status"] = status
                used_pages.append(email_page)
            elif found_email in lead["email"].lower():
                lead["email_status"] = "Also published on official website (mailbox not verified)"
                used_pages.append(email_page)

        if is_missing(lead.get("phone")):
            for url, parsed in pages:
                if parsed["phones"]:
                    lead["phone"] = parsed["phones"][0]
                    used_pages.append(url)
                    break
        if is_missing(lead.get("contact_person")):
            for url, parsed in pages:
                if parsed["contact_person"]:
                    lead["contact_person"] = parsed["contact_person"]
                    used_pages.append(url)
                    break

        if used_pages:
            lead["source_url"] = join_unique(lead.get("source_url", NOT_FOUND), " | ".join(dict.fromkeys(used_pages)))
            lead["source_type"] = join_unique(lead.get("source_type", NOT_FOUND), "Official website", sep=" + ")
        return lead

    @staticmethod
    def _same_site(email_domain: str, site_domain: str) -> bool:
        a, b = email_domain.lower(), site_domain.lower()
        return a == b or a.endswith("." + b) or b.endswith("." + a)
