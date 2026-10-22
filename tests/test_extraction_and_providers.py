import http.server
import threading
import unittest
from unittest import mock

from config import Settings
from services import http_utils
from services.business_directory import GooglePlacesProvider
from services.lead_extractor import (WebsiteEnricher, extract_companies_from_results, parse_page)
from services.models import NOT_FOUND, SearchSpec, new_lead
from services.url_utils import is_public_url, normalize_domain
from services.web_search import TavilyProvider

SPEC = SearchSpec(industry="real estate companies", location="Toronto, Ontario, Canada", count=10)

HTML = """<html><head>
<script type="application/ld+json">{"@type":"RealEstateAgent","name":"Acme","telephone":"+1 416-555-0100",
"founder":{"@type":"Person","name":"Jane Doe"}}</script></head><body>
<a href="mailto:Info@Acme.ca?subject=Hi">Email us</a><a href="tel:+14165550100">Call</a>
<a href="/contact-us">Contact</a><a href="https://other.com/contact">Other</a>
<p>Sales: sales@acme.ca  logo@2x.png  you@example.com</p></body></html>"""


class ParsePageTests(unittest.TestCase):
    def test_parse_page(self):
        out = parse_page(HTML, "https://acme.ca/")
        self.assertEqual(out["emails"], ["info@acme.ca", "sales@acme.ca"])
        self.assertIn("+1 416-555-0100", out["phones"])
        self.assertEqual(out["contact_links"], ["https://acme.ca/contact-us"])
        self.assertEqual(out["contact_person"], "Jane Doe")

    def test_no_guessing_on_empty_page(self):
        out = parse_page("<html><body>Hello</body></html>", "https://x.com")
        self.assertEqual((out["emails"], out["phones"]), ([], []))


class ResultExtractionTests(unittest.TestCase):
    RESULTS = [
        {"url": "https://www.acmerealty.ca/", "title": "Acme Realty | Toronto Homes", "content": "x"},
        {"url": "https://www.yelp.ca/biz/foo", "title": "Foo Realty - Yelp", "content": "x"},
        {"url": "https://blog.com/a", "title": "10 Best Real Estate Companies in Toronto", "content": "x"},
        {"url": "https://brightco.com/contact", "title": "Contact Us - Bright Co", "content": "x"},
    ]

    def test_heuristic_skips_directories_and_listicles(self):
        leads = extract_companies_from_results(self.RESULTS, SPEC, None)
        self.assertEqual([l["company_name"] for l in leads], ["Acme Realty", "Bright Co"])
        self.assertEqual(leads[0]["website"], "https://www.acmerealty.ca/")
        self.assertEqual(leads[0]["email"], NOT_FOUND)

    def test_llm_output_is_grounded_in_source_text(self):
        class LLM:
            available = True

            def json_chat(self, *a, **k):
                return {"companies": [
                    {"name": "Maple Homes", "website": "https://maplehomes.ca", "phone": "416-555-0188",
                     "email": "hello@maplehomes.ca", "address": None, "result_index": 0},
                    {"name": "Ghost Realty", "website": None, "phone": None, "email": None,
                     "address": None, "result_index": 0},
                    {"name": "Birch Realty", "website": "https://birch.ca", "phone": "999-999-9999",
                     "email": "made.up@birch.ca", "address": None, "result_index": 0}]}

        results = [{"url": "https://blog.com/x", "title": "Toronto agencies",
                    "content": "Maple Homes (maplehomes.ca) call 416-555-0188 or hello@maplehomes.ca. Birch Realty"}]
        leads = extract_companies_from_results(results, SPEC, LLM())
        names = {l["company_name"]: l for l in leads}
        self.assertNotIn("Ghost Realty", names)  # not in the text -> dropped
        self.assertEqual(names["Maple Homes"]["email"], "hello@maplehomes.ca")
        self.assertEqual(names["Birch Realty"]["email"], NOT_FOUND)  # invented -> rejected
        self.assertEqual(names["Birch Realty"]["phone"], NOT_FOUND)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
            self.wfile.write(b"User-agent: *\nDisallow: /private\n")
        elif self.path == "/":
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b'<a href="/contact">Contact</a><a href="/private">x</a>')
        elif self.path == "/contact":
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b'<a href="mailto:office@local.test">m</a><a href="tel:+1 416 555 0123">t</a>')
        else:
            self.send_response(403); self.end_headers()


class EnricherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def settings(self, **kw):
        return Settings(CHECK_EMAIL_DOMAIN_MX=False, REQUEST_TIMEOUT=5, **kw)

    def test_enrich_follows_contact_page(self):
        lead = new_lead(company_name="Local", website=f"http://127.0.0.1:{self.port}/")
        WebsiteEnricher(self.settings(), allow_private=True).enrich(lead)
        self.assertEqual(lead["email"], "office@local.test")
        self.assertEqual(lead["phone"], "+1 416 555 0123")
        self.assertTrue(lead["_site_checked"])
        self.assertIn("Official website", lead["source_type"])
        self.assertIn("/contact", lead["source_url"])
        self.assertIn("not verified", lead["email_status"])

    def test_private_hosts_blocked_by_default(self):
        lead = new_lead(company_name="Local", website=f"http://127.0.0.1:{self.port}/")
        WebsiteEnricher(self.settings()).enrich(lead)
        self.assertEqual(lead["email"], NOT_FOUND)
        self.assertNotIn("_site_checked", lead)

    def test_robots_disallow_is_respected(self):
        enr = WebsiteEnricher(self.settings(), allow_private=True)
        self.assertFalse(enr._allowed(f"http://127.0.0.1:{self.port}/private"))
        self.assertTrue(enr._allowed(f"http://127.0.0.1:{self.port}/contact"))

    def test_existing_phone_not_overwritten(self):
        lead = new_lead(company_name="Local", website=f"http://127.0.0.1:{self.port}/", phone="+1 000")
        WebsiteEnricher(self.settings(), allow_private=True).enrich(lead)
        self.assertEqual(lead["phone"], "+1 000")


class UrlTests(unittest.TestCase):
    def test_domain_and_ssrf(self):
        self.assertEqual(normalize_domain("HTTPS://www.Example.com:8080/x"), "example.com")
        self.assertFalse(is_public_url("http://127.0.0.1/"))
        self.assertFalse(is_public_url("http://localhost:5000/"))
        self.assertFalse(is_public_url("file:///etc/passwd"))


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers, self.text = status, body, headers or {}, str(body)

    def json(self):
        return self._body


class ProviderTests(unittest.TestCase):
    PLACE = {"id": "p1", "displayName": {"text": "Acme Realty"}, "formattedAddress": "1 King St W, Toronto, ON",
             "addressComponents": [{"longText": "Toronto", "types": ["locality"]},
                                   {"longText": "Ontario", "types": ["administrative_area_level_1"]},
                                   {"longText": "Canada", "types": ["country"]}],
             "internationalPhoneNumber": "+1 416-555-0100", "websiteUri": "https://acme.ca",
             "googleMapsUri": "https://maps.google.com/?cid=1",
             "primaryTypeDisplayName": {"text": "Real estate agency"}}

    def test_google_places_parsing_and_paging(self):
        pages = [FakeResponse(200, {"places": [self.PLACE, {**self.PLACE, "id": "p2", "businessStatus": "CLOSED_PERMANENTLY"}],
                                    "nextPageToken": "t"}),
                 FakeResponse(200, {"places": [self.PLACE]})]  # duplicate id -> skipped
        pages += [FakeResponse(200, {"places": []})] * 10  # later query variants return nothing
        with mock.patch("requests.request", side_effect=pages) as req:
            prov = GooglePlacesProvider(Settings(GOOGLE_PLACES_API_KEY="k"))
            leads = prov.search(SPEC, 50)
        self.assertEqual(len(leads), 1)
        lead = leads[0]
        self.assertEqual((lead["city"], lead["region"], lead["country"]), ("Toronto", "Ontario", "Canada"))
        self.assertEqual(lead["phone"], "+1 416-555-0100")
        self.assertEqual(req.call_args_list[0].kwargs["headers"]["X-Goog-Api-Key"], "k")
        self.assertEqual(req.call_args_list[1].kwargs["json"]["pageToken"], "t")

    def test_google_auth_error_is_reported_not_raised(self):
        err = FakeResponse(403, {"error": {"message": "API key not valid"}})
        with mock.patch("requests.request", return_value=err):
            prov = GooglePlacesProvider(Settings(GOOGLE_PLACES_API_KEY="bad"))
            self.assertEqual(prov.search(SPEC, 10), [])
        self.assertIn("API key not valid", prov.warnings[0])

    def test_rate_limit_retries_then_warns(self):
        limited = FakeResponse(429, {"error": {"message": "slow down"}}, {"Retry-After": "0"})
        with mock.patch("requests.request", return_value=limited) as req, \
                mock.patch.object(http_utils.time, "sleep"):
            prov = TavilyProvider(Settings(TAVILY_API_KEY="k"))
            self.assertEqual(prov.search(SPEC, 10), [])
        self.assertEqual(req.call_count, 3)
        self.assertIn("rate limit", prov.warnings[0])

    def test_tavily_results_become_leads(self):
        body = {"results": [{"url": "https://acmerealty.ca/", "title": "Acme Realty | Homes", "content": ""}]}
        with mock.patch("requests.request", return_value=FakeResponse(200, body)):
            leads = TavilyProvider(Settings(TAVILY_API_KEY="k")).search(SPEC, 1)
        self.assertEqual(leads[0]["company_name"], "Acme Realty")
        self.assertEqual(leads[0]["source_type"], "Web search (Tavily)")


if __name__ == "__main__":
    unittest.main()
