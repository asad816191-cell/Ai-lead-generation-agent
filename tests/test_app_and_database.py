import os
import tempfile
import time
import unittest

from app import create_app
from config import Settings
from database import Database
from services.base import LeadProvider
from services.models import NOT_FOUND, new_lead


class FakeProvider(LeadProvider):
    name = "Fake"

    def is_configured(self):
        return True

    def search(self, spec, target, progress=None):
        out = []
        for i in range(8):
            lead = new_lead(company_name=f"Company {i % 6}", industry=spec.industry,
                            website=f"https://company{i % 6}.example/", phone=f"+1 416-555-01{i:02d}",
                            source_url=f"https://src.example/{i}", source_type="Fake")
            lead["_google"] = True
            out.append(lead)
        return out


class FakeEnricher:
    def enrich(self, lead):
        lead["_site_checked"] = True
        if lead["company_name"] == "Company 1":
            lead["email"] = "hello@company1.example"
            lead["email_status"] = "Published on official website (mailbox not verified)"
        return lead


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(DATABASE_PATH=os.path.join(self.tmp.name, "t.db"))
        self.app = create_app(self.settings, provider_factory=lambda s, l: [FakeProvider()],
                              enricher=FakeEnricher())
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["lead_executor"].shutdown(wait=True)
        self.tmp.cleanup()

    def wait(self, sid):
        for _ in range(100):
            data = self.client.get(f"/api/search/{sid}").get_json()
            if data["search"]["status"] in ("completed", "failed"):
                return data
            time.sleep(0.1)
        self.fail("search did not finish")

    def test_full_flow_history_and_exports(self):
        r = self.client.post("/api/search", json={"prompt": "Find 5 real estate agencies in Toronto, Canada"})
        self.assertEqual(r.status_code, 202)
        info = r.get_json()
        self.assertEqual((info["count"], info["industry"], info["location"]),
                         (5, "real estate agencies", "Toronto, Canada"))
        data = self.wait(info["id"])
        self.assertEqual(data["search"]["status"], "completed", data["search"]["message"])
        leads = data["leads"]
        self.assertEqual(len(leads), 5)  # 8 raw -> 6 unique -> capped to requested 5
        self.assertEqual(len({l["company_name"] for l in leads}), 5)
        with_email = [l for l in leads if l["email"] != NOT_FOUND]
        self.assertEqual([l["email"] for l in with_email], ["hello@company1.example"])
        self.assertTrue(all("Cross-checked" in l["verification_status"] for l in leads))
        self.assertTrue(any("src.example/1" in l["source_url"] and "src.example/7" in l["source_url"]
                            for l in leads if l["company_name"] == "Company 1"))

        # history + export of a saved search
        listing = self.client.get("/api/searches").get_json()["searches"]
        self.assertEqual(listing[0]["id"], info["id"])
        self.assertIn(b"real estate agencies", self.client.get("/history").data)
        csv = self.client.get(f"/api/search/{info['id']}/export/csv")
        self.assertEqual(csv.status_code, 200)
        self.assertIn("attachment", csv.headers["Content-Disposition"])
        xlsx = self.client.get(f"/api/search/{info['id']}/export/xlsx")
        self.assertEqual(xlsx.data[:2], b"PK")
        self.assertEqual(self.client.get(f"/api/search/{info['id']}/export/pdf").status_code, 404)

        self.assertEqual(self.client.delete(f"/api/search/{info['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/search/{info['id']}").status_code, 404)

    def test_validation_errors(self):
        post = lambda body: self.client.post("/api/search", json=body)
        self.assertEqual(post({}).status_code, 400)
        self.assertEqual(post({"prompt": "Find 5 cafes in Rome", "count": "abc"}).status_code, 400)
        self.assertEqual(post({"prompt": "Find 5 cafes in Rome", "count": 100000}).status_code, 400)
        self.assertEqual(post({"prompt": "x" * 2000}).status_code, 400)
        self.assertEqual(post({"prompt": "hello there friend"}).status_code, 400)
        self.assertEqual(self.client.post("/api/search", data="x").status_code, 415)
        self.assertEqual(post({"industry": "cafes", "location": "Rome", "count": 3}).status_code, 202)

    def test_no_provider_configured_fails_gracefully(self):
        app = create_app(Settings(DATABASE_PATH=os.path.join(self.tmp.name, "n.db")))
        c = app.test_client()
        sid = c.post("/api/search", json={"prompt": "Find 5 cafes in Rome"}).get_json()["id"]
        for _ in range(50):
            s = c.get(f"/api/search/{sid}").get_json()["search"]
            if s["status"] != "queued" and s["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(s["status"], "failed")
        self.assertIn("API_KEY", s["message"])
        app.extensions["lead_executor"].shutdown(wait=True)

    def test_status_endpoint_never_leaks_keys(self):
        app = create_app(Settings(DATABASE_PATH=os.path.join(self.tmp.name, "k.db"), TAVILY_API_KEY="SECRET123"))
        body = app.test_client().get("/api/status").get_data(as_text=True)
        self.assertNotIn("SECRET123", body)
        self.assertIn('"tavily":true', body.replace(" ", ""))
        app.extensions["lead_executor"].shutdown(wait=True)

    def test_security_headers(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertNotIn(b"API_KEY", r.data)


class DatabaseTests(unittest.TestCase):
    def test_roundtrip_and_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "d.db"))
            sid = db.create_search("p", "ind", "loc", 5, ["f"], True)
            db.save_leads(sid, [new_lead(company_name="A"), new_lead(company_name="B", phone="1")])
            self.assertEqual([l["company_name"] for l in db.get_leads(sid)], ["A", "B"])
            db.mark_interrupted()
            self.assertEqual(db.get_search(sid)["status"], "failed")
            with self.assertRaises(ValueError):
                db.update_search(sid, bogus=1)
            self.assertTrue(db.delete_search(sid))
            self.assertEqual(db.get_leads(sid), [])


if __name__ == "__main__":
    unittest.main()
