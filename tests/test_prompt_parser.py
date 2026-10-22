import unittest

from services.llm import LLMError
from services.prompt_parser import parse_prompt


class FakeLLM:
    available = True

    def __init__(self, result=None, error=False):
        self.result, self.error = result, error

    def json_chat(self, system, user, max_tokens=0):
        if self.error:
            raise LLMError("boom")
        return self.result


class PromptParserTests(unittest.TestCase):
    def test_full_example_prompt(self):
        spec = parse_prompt("Find 100 real estate companies in Toronto, Ontario, Canada, including company "
                            "names, phone numbers, business email addresses, websites, and addresses.")
        self.assertEqual(spec.count, 100)
        self.assertEqual(spec.industry, "real estate companies")
        self.assertEqual(spec.location, "Toronto, Ontario, Canada")
        self.assertEqual(spec.filters, [])

    def test_short_prompt(self):
        spec = parse_prompt("Find 200 real estate agencies in Toronto, Canada.")
        self.assertEqual((spec.count, spec.industry, spec.location),
                         (200, "real estate agencies", "Toronto, Canada"))

    def test_default_count_and_thousands_separator(self):
        self.assertEqual(parse_prompt("Find plumbers in Austin, Texas", default_count=30).count, 30)
        self.assertEqual(parse_prompt("Get 1,000 dentists in Leeds", max_leads=2000).count, 1000)

    def test_count_is_capped(self):
        self.assertEqual(parse_prompt("Find 9000 cafes in Paris", max_leads=500).count, 500)

    def test_filter_clause_is_kept(self):
        spec = parse_prompt("Find 20 law firms in Chicago that specialize in immigration law")
        self.assertEqual(spec.location, "Chicago")
        self.assertEqual(len(spec.filters), 1)
        self.assertIn("immigration", spec.filters[0])

    def test_form_overrides_win(self):
        spec = parse_prompt("Find 10 cafes in Paris",
                            overrides={"industry": "bakeries", "location": "Lyon, France", "count": 40})
        self.assertEqual((spec.industry, spec.location, spec.count), ("bakeries", "Lyon, France", 40))

    def test_fields_only_no_prompt(self):
        spec = parse_prompt("", overrides={"industry": "architects", "location": "Berlin", "count": 5})
        self.assertEqual((spec.industry, spec.location, spec.count), ("architects", "Berlin", 5))

    def test_unparseable_raises(self):
        with self.assertRaises(ValueError):
            parse_prompt("hello there")

    def test_llm_result_is_used(self):
        llm = FakeLLM({"industry": "solar installers", "location": "Phoenix, Arizona, USA",
                       "count": 75, "filters": ["residential"]})
        spec = parse_prompt("I need about seventy five residential solar installers around Phoenix", llm=llm)
        self.assertEqual((spec.industry, spec.location, spec.count, spec.parser),
                         ("solar installers", "Phoenix, Arizona, USA", 75, "llm"))
        self.assertEqual(spec.filters, ["residential"])

    def test_llm_failure_falls_back_to_rules(self):
        spec = parse_prompt("Find 15 florists in Dublin", llm=FakeLLM(error=True))
        self.assertEqual((spec.count, spec.industry, spec.location, spec.parser),
                         (15, "florists", "Dublin", "rules"))

    def test_llm_garbage_count_ignored(self):
        llm = FakeLLM({"industry": "florists", "location": "Dublin", "count": "lots", "filters": "x"})
        spec = parse_prompt("Find 15 florists in Dublin", llm=llm)
        self.assertEqual(spec.count, 15)


if __name__ == "__main__":
    unittest.main()
