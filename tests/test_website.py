from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from src import website
from tests.test_library import build_fixture


class WebsiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.exports = build_fixture(root / "exports")
        cls.site = root / "site"
        cls.stats = website.build(cls.site, base_url="https://example.org/atlas/", app_url="https://app.example.org",
                                  export_dir=cls.exports, verification="abc123")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def page(self, path: str) -> str:
        return (self.site / path / "index.html").read_text(encoding="utf-8")

    def test_one_page_per_country_and_indicator(self):
        self.assertTrue((self.site / "country" / "kenya" / "index.html").exists())
        self.assertTrue((self.site / "country" / "united-states" / "index.html").exists())
        self.assertTrue((self.site / "indicator" / "gov-debt-gdp" / "index.html").exists())
        self.assertEqual(self.stats["indicators"], len(website.INDICATORS))

    def test_country_page_has_real_numbers_in_title_description_and_faq(self):
        html = self.page("country/kenya")
        self.assertIn("<title>Kenya Debt, Government Spending &amp; Who It Owes", html)
        description = re.search(r'name="description" content="(.*?)"', html).group(1)
        self.assertIn("67.0% of GDP", description)
        self.assertIn("China", description)
        self.assertIn('<link rel="canonical" href="https://example.org/atlas/country/kenya/">', html)
        self.assertIn("Who does Kenya owe money to?", html)
        self.assertIn("https://app.example.org/?view=country&amp;country=KEN", html)

    def test_every_jsonld_block_is_valid_and_titles_are_unique(self):
        titles = set()
        pages = list(self.site.rglob("index.html"))
        for path in pages:
            html = path.read_text(encoding="utf-8")
            for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
                json.loads(block)
            titles.add(re.search(r"<title>(.*?)</title>", html).group(1))
        self.assertEqual(len(titles), len(pages))

    def test_dataset_schema_on_country_page(self):
        html = self.page("country/kenya")
        blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
        dataset = next(b for b in blocks if b["@type"] == "Dataset")
        self.assertEqual(dataset["spatialCoverage"]["name"], "Kenya")
        self.assertTrue(dataset["distribution"][0]["contentUrl"].endswith(".csv"))
        crumbs = next(b for b in blocks if b["@type"] == "BreadcrumbList")
        self.assertEqual(crumbs["itemListElement"][1]["item"], "https://example.org/atlas/country/")

    def test_sitemap_robots_verification_and_relative_assets(self):
        sitemap = (self.site / "sitemap.xml").read_text()
        self.assertIn("<loc>https://example.org/atlas/country/kenya/</loc>", sitemap)
        self.assertIn("Sitemap: https://example.org/atlas/sitemap.xml", (self.site / "robots.txt").read_text())
        self.assertIn('content="abc123"', self.page(""))
        self.assertIn('href="../../style.css"', self.page("country/kenya"))

    def test_slugify_and_common_names(self):
        self.assertEqual(website.slugify("Côte d'Ivoire"), "cote-divoire")
        self.assertEqual(website.COMMON_NAMES["KOR"], "South Korea")

    def test_support_page_is_linked_and_shows_coming_soon_without_options(self):
        html = self.page("support")
        self.assertIn("Donation options are coming soon", html)
        self.assertIn('href="../../support/">❤ Support</a>', self.page("country/kenya"))
        self.assertIn("support/</loc>", (self.site / "sitemap.xml").read_text(encoding="utf-8"))

    def test_support_page_lists_patreon_and_crypto(self):
        funding = website.Funding.from_values(
            "https://www.patreon.com/example", "USDT (TRC-20) | TExampleAddress123\nnot a valid line\n")
        self.assertEqual(funding.crypto, [("USDT (TRC-20)", "TExampleAddress123")])
        html = website.support_page(funding).body
        self.assertIn('href="https://www.patreon.com/example"', html)
        self.assertIn("<code>TExampleAddress123</code>", html)
        self.assertNotIn("coming soon", html)
        self.assertIsNone(website.Funding.from_values("javascript:alert(1)", None).patreon_url)


    def test_python_library_page_and_footer_links(self):
        html = self.page("python")
        self.assertIn("pip install debt-atlas", html)
        self.assertIn("da.who_owes(", html)
        self.assertIn('"@type": "SoftwareApplication"', html)
        country = self.page("country/kenya")
        self.assertIn('href="../../python/">Python</a>', country)
        self.assertIn("Want to help us with future research and open-source projects?", country)
        self.assertIn('href="../../support/">❤ Donate</a>', country)

if __name__ == "__main__":
    unittest.main()
