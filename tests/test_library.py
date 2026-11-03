from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

import debt_atlas as da
from debt_atlas import Atlas, AtlasError
from debt_atlas.cli import main as cli_main
from src.exports import catalog_document


def build_fixture(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"iso3": "KEN", "iso2": "KE", "name": "Kenya", "latitude": 0.5, "longitude": 37.9},
        {"iso3": "USA", "iso2": "US", "name": "United States", "latitude": 39.0, "longitude": -98.0},
        {"iso3": "CHN", "iso2": "CN", "name": "China", "latitude": 35.0, "longitude": 103.0},
        {"iso3": "JPN", "iso2": "JP", "name": "Japan", "latitude": 36.0, "longitude": 138.0},
        {"iso3": "PAK", "iso2": "PK", "name": "Pakistan", "latitude": 30.0, "longitude": 69.0},
    ]).to_csv(folder / "countries.csv", index=False)
    pd.DataFrame([
        {"iso3": "JPN", "indicator_id": "gov_debt_gdp", "indicator": "Gov debt", "value": 199.4, "unit": "percent_of_gdp",
         "year": 2026, "is_estimate": True, "source": "IMF WEO", "series_code": "GGXWDG_NGDP", "series_url": "x",
         "from_snapshot": False, "retrieved_at_utc": "2026-11-02"},
        {"iso3": "USA", "indicator_id": "gov_debt_gdp", "indicator": "Gov debt", "value": 126.8, "unit": "percent_of_gdp",
         "year": 2026, "is_estimate": True, "source": "IMF WEO", "series_code": "GGXWDG_NGDP", "series_url": "x",
         "from_snapshot": False, "retrieved_at_utc": "2026-11-02"},
        {"iso3": "KEN", "indicator_id": "gov_debt_gdp", "indicator": "Gov debt", "value": 67.0, "unit": "percent_of_gdp",
         "year": 2026, "is_estimate": True, "source": "IMF WEO", "series_code": "GGXWDG_NGDP", "series_url": "x",
         "from_snapshot": False, "retrieved_at_utc": "2026-11-02"},
        {"iso3": "USA", "indicator_id": "military_usd", "indicator": "Military", "value": 954e9, "unit": "usd",
         "year": 2025, "is_estimate": False, "source": "SIPRI", "series_code": "MS.MIL.XPND.CD", "series_url": "x",
         "from_snapshot": False, "retrieved_at_utc": "2026-11-02"},
    ]).to_csv(folder / "latest_values.csv", index=False)
    pd.DataFrame([
        {"iso3": "JPN", "indicator_id": "gov_debt_gdp", "year": y, "value": v, "unit": "percent_of_gdp",
         "provider": "imf", "series_code": code, "source": "IMF", "from_snapshot": False}
        for code, values in (("GGXWDG_NGDP", {2024: 210.0, 2025: 205.0, 2026: 199.4}), ("GG_DEBT_GDP", {2024: 236.0}))
        for y, v in values.items()
    ]).to_csv(folder / "timeseries.csv.gz", index=False, compression="gzip")
    pd.DataFrame([
        {"iso3": "USA", "country": "United States", "item_id": "military", "item": "Military & defence", "scope": "Government",
         "usd": 954e9, "pct_gdp": 3.1, "usd_per_person": 2800.0, "year": 2025, "method": "Reported", "sources": "SIPRI"},
        {"iso3": "USA", "country": "United States", "item_id": "health", "item": "Health care", "scope": "General government",
         "usd": 2.4e12, "pct_gdp": 8.5, "usd_per_person": 7000.0, "year": 2024, "method": "Share × GDP", "sources": "WHO"},
    ]).to_csv(folder / "spending_usd.csv", index=False)
    pd.DataFrame([{"iso3": "KEN", "country": "Kenya", "value": 4.1e10, "display_value": "US$41B", "metric_type": "x",
                   "metric_label": "Long-term external debt", "unit": "USD", "observation_year": 2024, "fallback_tier": 1,
                   "source_label": "World Bank", "source_url": "x", "source_date_label": "x", "is_estimate_profile": False,
                   "data_available": True, "definition_note": ""}]).to_csv(folder / "national_debt_profiles.csv", index=False)
    pd.DataFrame([
        {"year": 2024, "debtor_iso3": "KEN", "debtor_name": "Kenya", "creditor_id": "156", "creditor_iso3": "CHN", "creditor_name": "China", "amount_usd": 5.4e9},
        {"year": 2024, "debtor_iso3": "KEN", "debtor_name": "Kenya", "creditor_id": "392", "creditor_iso3": "JPN", "creditor_name": "Japan", "amount_usd": 1.2e9},
        {"year": 2023, "debtor_iso3": "KEN", "debtor_name": "Kenya", "creditor_id": "156", "creditor_iso3": "CHN", "creditor_name": "China", "amount_usd": 6.0e9},
        {"year": 2024, "debtor_iso3": "PAK", "debtor_name": "Pakistan", "creditor_id": "156", "creditor_iso3": "CHN", "creditor_name": "China", "amount_usd": 22.4e9},
    ]).to_csv(folder / "bilateral_debt.csv.gz", index=False, compression="gzip")
    (folder / "catalog.json").write_text(json.dumps(catalog_document()), encoding="utf-8")
    (folder / "datapackage.json").write_text("{}", encoding="utf-8")
    return folder


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.folder = build_fixture(Path(cls._tmp.name))
        cls.atlas = Atlas(cls.folder, cache_dir=None)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_country_names_iso2_and_iso3_all_resolve(self):
        for text in ("Kenya", "kenya", "KE", "ken", "KEN"):
            self.assertEqual(self.atlas.resolve_country(text), "KEN")
        with self.assertRaises(AtlasError):
            self.atlas.resolve_country("Atlantis")

    def test_latest_and_rank(self):
        frame = self.atlas.latest("gov_debt_gdp", countries=["Japan", "US"])
        self.assertEqual(list(frame["iso3"]), ["JPN", "USA"])
        self.assertIn("country", frame.columns)
        self.assertEqual(self.atlas.rank("gov_debt_gdp", n=1)["iso3"].iloc[0], "JPN")
        self.assertEqual(self.atlas.rank("gov_debt_gdp", n=1, lowest=True)["iso3"].iloc[0], "KEN")

    def test_unknown_indicator_gives_helpful_error(self):
        with self.assertRaises(AtlasError) as ctx:
            self.atlas.latest("gov_debt")
        self.assertIn("gov_debt_gdp", str(ctx.exception))

    def test_series_keeps_sources_separate_and_wide_prefers_first_series(self):
        series = self.atlas.series("gov_debt_gdp", "Japan")
        self.assertEqual(set(series["series_code"]), {"GGXWDG_NGDP", "GG_DEBT_GDP"})
        wide = self.atlas.wide("gov_debt_gdp", "Japan")
        self.assertEqual(wide.loc[2024, "JPN"], 210.0)  # WEO listed first in the catalogue

    def test_spending_and_profile(self):
        spending = self.atlas.spending("United States")
        self.assertEqual(spending["item_id"].iloc[0], "health")
        self.assertEqual(self.atlas.profile("Kenya")["fallback_tier"], 1)

    def test_who_owes_and_owed_by(self):
        lenders = self.atlas.who_owes("Kenya")
        self.assertEqual(list(lenders["lender"]), ["China", "Japan"])
        self.assertEqual(int(lenders["year"].iloc[0]), 2024)
        self.assertAlmostEqual(lenders["share_pct"].sum(), 100.0)
        borrowers = self.atlas.owed_by("China")
        self.assertEqual(list(borrowers["borrower_iso3"]), ["PAK", "KEN"])
        self.assertEqual(self.atlas.who_owes("Kenya", year=2023)["amount_usd"].iloc[0], 6.0e9)

    def test_compare_table(self):
        table = self.atlas.compare(["Japan", "Kenya"], ["gov_debt_gdp"])
        self.assertEqual(list(table.index), ["Japan (JPN)", "Kenya (KEN)"])

    def test_indicators_catalogue_and_download_all(self):
        self.assertGreaterEqual(len(self.atlas.indicators()), 30)
        self.assertTrue((self.atlas.indicators("spending")["category"] == "spending").all())
        with tempfile.TemporaryDirectory() as out:
            target = self.atlas.download_all(out)
            self.assertTrue((target / "latest_values.csv").exists())

    def test_module_level_functions_use_default_client(self):
        da._default = Atlas(self.folder, cache_dir=None)
        try:
            self.assertEqual(da.latest("military_usd")["iso3"].iloc[0], "USA")
            self.assertEqual(da.who_owes.__name__, "who_owes")
        finally:
            da._default = None

    def test_cli(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli_main(["--source", str(self.folder), "--csv", "who-owes", "Kenya"])
        self.assertEqual(code, 0)
        self.assertIn("China", buffer.getvalue())

    def test_remote_failure_falls_back_to_cache(self):
        with tempfile.TemporaryDirectory() as cache:
            cached = Path(cache) / "countries.csv"
            cached.write_bytes((self.folder / "countries.csv").read_bytes())
            atlas = Atlas("https://invalid.invalid/exports", cache_dir=cache, refresh=True, timeout=2)
            self.assertIn("KEN", set(atlas.countries()["iso3"]))


if __name__ == "__main__":
    unittest.main()
