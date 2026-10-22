"""URL helpers: domain normalisation, aggregator detection and SSRF protection."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Directories / social / media sites. A result on one of these is a *source*, never a company website.
AGGREGATOR_DOMAINS = {
    "yelp.com", "yelp.ca", "yellowpages.com", "yellowpages.ca", "linkedin.com", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "youtube.com", "tiktok.com", "pinterest.com",
    "wikipedia.org", "tripadvisor.com", "tripadvisor.ca", "mapquest.com", "bbb.org", "clutch.co",
    "glassdoor.com", "glassdoor.ca", "indeed.com", "ca.indeed.com", "zoominfo.com", "crunchbase.com",
    "reddit.com", "quora.com", "manta.com", "houzz.com", "zillow.com", "trulia.com", "realtor.com",
    "realtor.ca", "google.com", "google.ca", "bing.com", "yahoo.com", "medium.com", "forbes.com",
    "expertise.com", "threebestrated.com", "threebestrated.ca", "goodfirms.co", "designrush.com",
    "apple.com", "foursquare.com", "dnb.com", "opencorporates.com", "canada411.ca", "cylex.ca",
    "cylex.com", "hotfrog.ca", "hotfrog.com", "superpages.com", "angi.com", "thumbtack.com",
    "g2.com", "capterra.com", "trustpilot.com", "wikidata.org", "eventbrite.com", "meetup.com",
}


def ensure_scheme(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    return url


def normalize_domain(url: str) -> str:
    """'https://www.Example.com:443/path' -> 'example.com'. Returns '' when there is no host."""
    parsed = urlparse(ensure_scheme(url))
    host = (parsed.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def is_aggregator(domain: str) -> bool:
    domain = (domain or "").lower()
    return any(domain == d or domain.endswith("." + d) for d in AGGREGATOR_DOMAINS)


def root_url(url: str) -> str:
    parsed = urlparse(ensure_scheme(url))
    if not parsed.hostname:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/"


def is_public_url(url: str) -> bool:
    """True only for http(s) URLs whose host resolves exclusively to public IP addresses.

    Prevents the enrichment crawler from being pointed at localhost / private networks.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        try:
            if not ipaddress.ip_address(info[4][0]).is_global:
                return False
        except ValueError:
            return False
    return bool(infos)
