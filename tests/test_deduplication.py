import unittest

from services.deduplication import (assign_verification_status, deduplicate, normalize_name)
from services.models import NOT_FOUND, new_lead


class DedupTests(unittest.TestCase):
    def test_normalize_name(self):
        self.assertEqual(normalize_name("The Smith & Sons Realty, Inc."), "smith and sons realty")
        self.assertEqual(normalize_name(NOT_FOUND), "")

    def test_merge_by_domain_fills_gaps_and_keeps_sources(self):
        a = new_lead(company_name="Acme Realty Inc.", website="https://www.acme.ca/", phone="+1 416-555-0100",
                     source_url="https://maps.google.com/?cid=1", source_type="Google Places API")
        b = new_lead(company_name="ACME Realty", website="http://acme.ca/about", email="info@acme.ca",
                     source_url="https://acme.ca/contact", source_type="Web search (Tavily)")
        out = deduplicate([a, b])
        self.assertEqual(len(out), 1)
        lead = out[0]
        self.assertEqual(lead["phone"], "+1 416-555-0100")
        self.assertEqual(lead["email"], "info@acme.ca")
        self.assertIn("maps.google.com", lead["source_url"])
        self.assertIn("acme.ca/contact", lead["source_url"])
        self.assertIn("Google Places API", lead["source_type"])
        self.assertIn("Tavily", lead["source_type"])

    def test_merge_by_name_when_no_website(self):
        out = deduplicate([new_lead(company_name="Bright Homes Ltd"),
                           new_lead(company_name="bright homes", phone="+1 647-555-0101")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["phone"], "+1 647-555-0101")

    def test_different_companies_stay_separate(self):
        out = deduplicate([new_lead(company_name="Alpha Realty", website="https://alpha.com"),
                           new_lead(company_name="Beta Realty", website="https://beta.com")])
        self.assertEqual(len(out), 2)

    def test_aggregator_domain_is_not_a_merge_key(self):
        out = deduplicate([new_lead(company_name="Alpha Realty", website="https://facebook.com/alpha"),
                           new_lead(company_name="Beta Realty", website="https://facebook.com/beta")])
        self.assertEqual(len(out), 2)

    def test_bridging_lead_merges_two_groups(self):
        a = new_lead(company_name="Gamma Group", phone="111")
        b = new_lead(company_name="Gamma Holdings", website="https://gamma.io")
        c = new_lead(company_name="Gamma Group", website="https://gamma.io")
        self.assertEqual(len(deduplicate([a, b, c])), 1)

    def test_missing_name_is_dropped(self):
        self.assertEqual(deduplicate([new_lead(website="https://x.com")]), [])

    def test_email_is_never_invented(self):
        out = deduplicate([new_lead(company_name="NoMail Co", website="https://nomail.com")])
        self.assertEqual(out[0]["email"], NOT_FOUND)
        assign_verification_status(out[0])
        self.assertEqual(out[0]["email"], NOT_FOUND)
        self.assertEqual(out[0]["email_status"], NOT_FOUND)

    def test_verification_labels(self):
        g = assign_verification_status({**new_lead(company_name="A"), "_google": True})
        self.assertIn("Google Places", g["verification_status"])
        both = assign_verification_status({**new_lead(company_name="A"), "_google": True, "_site_checked": True})
        self.assertIn("Cross-checked", both["verification_status"])
        web = assign_verification_status(new_lead(company_name="A", email="a@b.com"))
        self.assertIn("Unverified", web["verification_status"])
        self.assertIn("not verified", web["email_status"].lower())

    def test_does_not_mutate_inputs(self):
        a = new_lead(company_name="Zed Co", website="https://zed.com")
        b = new_lead(company_name="Zed Co", phone="555")
        deduplicate([a, b])
        self.assertEqual(a["phone"], NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
