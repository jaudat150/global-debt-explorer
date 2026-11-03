from __future__ import annotations

import io
import json
import unittest
import zipfile

import pandas as pd

from src import exports
from src.catalog import INDICATOR_BY_ID, INDICATORS
from src.indicator_data import resolve_layer
from src.spending import ITEM_BY_ID, REQUIRED_LAYERS, compute_line, country_spending, spending_matrix


def layer(indicator_id: str, series_by_code: dict, current_year: int = 2026):
    indicator = INDICATOR_BY_ID[indicator_id]
    downloads = {s.code: series_by_code.get(s.code, {}) for s in indicator.sources}
    return resolve_layer(indicator, downloads, status={code: "ok" for code in downloads}, current_year=current_year)


def usa_layers() -> dict:
    layers = {iid: layer(iid, {}) for iid in REQUIRED_LAYERS}
    layers.update({
        "gdp_usd": layer("gdp_usd", {"NGDPD": {"USA": {2024: 29.0e12 / 1e9 * 1e9}}, "NY.GDP.MKTP.CD": {"USA": {2023: 27.7e12}}}),
        "population": layer("population", {"SP.POP.TOTL": {"USA": {2023: 335e6, 2024: 340e6}}}),
        "military_usd": layer("military_usd", {"MS.MIL.XPND.CD": {"USA": {2024: 997e9}}}),
        "health_gov_gdp": layer("health_gov_gdp", {"SH.XPD.GHED.GD.ZS": {"USA": {2023: 8.0}}}),
        "education_gdp": layer("education_gdp", {"SE.XPD.TOTL.GD.ZS": {"USA": {2021: 5.4}}}),  # no GDP for 2021
        "wages_pct_expense": layer("wages_pct_expense", {"GC.XPN.COMP.ZS": {"USA": {2023: 10.0}}}),
        "central_gov_expense_gdp": layer("central_gov_expense_gdp", {"GC.XPN.TOTL.GD.ZS": {"USA": {2023: 25.0}}}),
    })
    return layers


class SpendingTests(unittest.TestCase):
    def test_direct_usd_line_uses_source_amount_and_same_year_gdp_share(self):
        line = compute_line(ITEM_BY_ID["military"], "USA", usa_layers(), current_year=2026)
        self.assertEqual((line.usd, line.year), (997e9, 2024))
        self.assertAlmostEqual(line.pct_gdp, 997e9 / 29.0e12 * 100)
        self.assertAlmostEqual(line.per_capita, 997e9 / 340e6)
        self.assertIn("MS.MIL.XPND.CD", line.sources)

    def test_derived_line_multiplies_share_by_gdp_of_the_same_year(self):
        line = compute_line(ITEM_BY_ID["health"], "USA", usa_layers(), current_year=2026)
        self.assertEqual(line.year, 2023)
        self.assertAlmostEqual(line.usd, 0.08 * 27.7e12)
        self.assertIn("NY.GDP.MKTP.CD", line.sources)  # the 2023 GDP came from the World Bank series

    def test_two_step_chain_for_central_government_lines(self):
        line = compute_line(ITEM_BY_ID["wages"], "USA", usa_layers(), current_year=2026)
        self.assertAlmostEqual(line.usd, 0.10 * 0.25 * 27.7e12)

    def test_no_common_year_means_missing_not_estimated(self):
        self.assertIsNone(compute_line(ITEM_BY_ID["education"], "USA", usa_layers(), current_year=2026))

    def test_reported_only_excludes_current_year(self):
        layers = usa_layers()
        layers["military_usd"] = layer("military_usd", {"MS.MIL.XPND.CD": {"USA": {2025: 1.0e12, 2026: 1.1e12}}})
        line = compute_line(ITEM_BY_ID["military"], "USA", layers, current_year=2026, include_estimates=False)
        self.assertEqual(line.year, 2025)

    def test_country_spending_sorts_and_lists_missing(self):
        lines, missing, total = country_spending("USA", usa_layers(), current_year=2026)
        self.assertEqual(lines[0].item.id, "health")  # 8% of US$27.7T > US$997B
        self.assertEqual([l.usd for l in lines], sorted((l.usd for l in lines), reverse=True))
        self.assertIn("education", {item.id for item in missing})
        self.assertIsNone(total)

    def test_matrix_has_provenance_columns(self):
        frame = spending_matrix(["USA"], usa_layers(), {"USA": "United States"}, current_year=2026)
        self.assertTrue({"usd", "pct_gdp", "usd_per_person", "year", "method", "sources"} <= set(frame.columns))
        self.assertEqual(frame.loc[frame.item_id == "military", "country"].iloc[0], "United States")


class ExportTests(unittest.TestCase):
    def test_catalog_document_lists_every_indicator_with_api_urls(self):
        document = exports.catalog_document()
        self.assertEqual(len(document["indicators"]), len(INDICATORS))
        for item in document["indicators"]:
            self.assertTrue(all(s["api_url"].startswith("https://") for s in item["sources"]))

    def test_countries_frame_has_coordinates_for_palestine_and_kosovo(self):
        frame = exports.countries_frame().set_index("iso3")
        self.assertEqual(frame.loc["XKX", "iso2"], "XK")
        self.assertFalse(pd.isna(frame.loc["PSE", "latitude"]))

    def test_timeseries_keeps_one_row_per_source(self):
        gdp = layer("gdp_growth", {"NGDP_RPCH": {"KEN": {2024: 4.6}}, "NY.GDP.MKTP.KD.ZG": {"KEN": {2024: 4.7}}})
        frame = exports.timeseries_frame({"gdp_growth": gdp})
        self.assertEqual(sorted(frame["series_code"]), ["NGDP_RPCH", "NY.GDP.MKTP.KD.ZG"])

    def test_bundle_contains_every_file_and_valid_datapackage(self):
        frames = {
            "countries": exports.countries_frame().head(3),
            "latest_values": exports.latest_values_frame({"gdp_growth": layer("gdp_growth", {"NGDP_RPCH": {"KEN": {2024: 4.6}}})}),
            "timeseries": exports.timeseries_frame({}),
            "spending_usd": spending_matrix(["USA"], usa_layers(), current_year=2026),
            "national_debt_profiles": pd.DataFrame(columns=exports.resource_columns()["national_debt_profiles"]),
            "bilateral_debt": pd.DataFrame({"year": [2024], "debtor_iso3": ["KEN"], "amount_usd": [5.4e9]}),
        }
        archive = zipfile.ZipFile(io.BytesIO(exports.build_bundle(frames)))
        names = set(archive.namelist())
        self.assertTrue({"datapackage.json", "catalog.json", "README.md", "latest_values.csv", "timeseries.csv.gz"} <= names)
        descriptor = json.loads(archive.read("datapackage.json"))
        resource = next(r for r in descriptor["resources"] if r["name"] == "latest_values")
        fields = {f["name"]: f for f in resource["schema"]["fields"]}
        self.assertEqual(fields["value"]["type"], "number")
        self.assertTrue(fields["iso3"]["description"])
        self.assertEqual(next(r for r in descriptor["resources"] if r["name"] == "timeseries")["compression"], "gz")

    def test_resource_columns_cover_every_export(self):
        self.assertEqual(set(exports.resource_columns()), set(exports.RESOURCE_INFO))


if __name__ == "__main__":
    unittest.main()


class SpendingSignTests(unittest.TestCase):
    def test_negative_source_values_are_never_shown_as_spending(self):
        layers = usa_layers()
        layers["interest_gdp"] = layer("interest_gdp", {"ie": {"USA": {2023: -0.4}}})
        layers["military_usd"] = layer("military_usd", {"MS.MIL.XPND.CD": {"USA": {2024: -1.0}}})
        self.assertIsNone(compute_line(ITEM_BY_ID["interest"], "USA", layers, current_year=2026))
        line = compute_line(ITEM_BY_ID["military"], "USA", layers, current_year=2026)
        self.assertTrue(line is None or line.usd >= 0)
