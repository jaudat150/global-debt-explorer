from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import catalog, indicator_data as data
from src.atlas_map import NO_DATA, build_atlas_map, classify
from src.catalog import INDICATOR_BY_ID, INDICATORS, SOURCE_DIRECTORY, SourceRef

_TMP = tempfile.TemporaryDirectory()


def setUpModule():
    os.environ["GDE_INDICATOR_SNAPSHOT"] = str(Path(_TMP.name) / "indicator_snapshot.json")


def tearDownModule():
    os.environ.pop("GDE_INDICATOR_SNAPSHOT", None)
    _TMP.cleanup()


class CatalogTests(unittest.TestCase):
    def test_ids_are_unique_and_categories_and_directory_keys_exist(self):
        ids = [indicator.id for indicator in INDICATORS]
        self.assertEqual(len(ids), len(set(ids)))
        for indicator in INDICATORS:
            self.assertIn(indicator.category, catalog.CATEGORY_BY_ID, indicator.id)
            self.assertTrue(indicator.sources, indicator.id)
            self.assertTrue(indicator.directory, f"{indicator.id} needs at least one 'find newer data' source")
            for key in indicator.directory:
                self.assertIn(key, SOURCE_DIRECTORY, f"{indicator.id} -> {key}")
        for category in catalog.CATEGORIES:
            self.assertTrue(catalog.indicators_in(category.id), category.id)

    def test_every_external_link_is_https(self):
        for source in SOURCE_DIRECTORY.values():
            self.assertTrue(source.url.startswith("https://"), source.key)
        for label, url in catalog.country_links("SYR", "SY"):
            self.assertTrue(url.startswith("https://"), label)

    def test_spending_lines_are_percent_of_gdp(self):
        self.assertGreaterEqual(len(catalog.SPENDING_LINES), 4)
        for indicator in catalog.SPENDING_LINES:
            self.assertEqual(indicator.unit, "percent_of_gdp")

    def test_generated_docs_mention_every_indicator(self):
        markdown = catalog.markdown_catalog()
        for indicator in INDICATORS:
            self.assertIn(f"`{indicator.id}`", markdown)


class ParsingTests(unittest.TestCase):
    def test_world_bank_parser_keeps_negative_values_and_drops_nulls(self):
        payload = [{"page": 1, "pages": 1}, [
            {"countryiso3code": "CAN", "date": "2024", "value": -1.5},
            {"countryiso3code": "CAN", "date": "2023", "value": None},
            {"countryiso3code": "", "date": "2024", "value": 3},
        ]]
        self.assertEqual(data.parse_world_bank_series(payload), {"CAN": {2024: -1.5}})

    def test_world_bank_error_envelope_is_empty(self):
        self.assertEqual(data.parse_world_bank_series([{"message": [{"id": "120"}]}]), {})

    def test_imf_parser_maps_aliases_and_applies_multiplier(self):
        payload = {"values": {"NGDPD": {"WBG": {"2024": 19.1}, "USA": {"2024": 29000.0, "2025": None}}}}
        parsed = data.parse_imf_series(payload, "NGDPD", 1e9)
        self.assertAlmostEqual(parsed["PSE"][2024], 19.1e9)
        self.assertEqual(set(parsed["USA"]), {2024})

    def test_latest_valid_drops_projections_and_optionally_current_year_estimates(self):
        years = {2024: 1.0, 2026: 2.0, 2030: 9.0}
        self.assertEqual(data.latest_valid(years, current_year=2026, include_estimates=True, provider="imf"), (2026, 2.0))
        self.assertEqual(data.latest_valid(years, current_year=2026, include_estimates=False, provider="imf"), (2024, 1.0))
        self.assertEqual(data.latest_valid(years, current_year=2026, include_estimates=False, provider="worldbank"), (2026, 2.0))


class ResolveLayerTests(unittest.TestCase):
    indicator = INDICATOR_BY_ID["gdp_growth"]  # IMF NGDP_RPCH first, World Bank second

    def test_freshest_value_wins_and_ties_go_to_first_source(self):
        downloads = {
            "NGDP_RPCH": {"KEN": {2025: 4.8}, "LBN": {2019: -6.9}},
            "NY.GDP.MKTP.KD.ZG": {"KEN": {2025: 4.5}, "LBN": {2024: -7.5}, "WLD": {2024: 2.8}},
        }
        layer = data.resolve_layer(self.indicator, downloads, status={"NGDP_RPCH": "ok", "NY.GDP.MKTP.KD.ZG": "ok"},
                                   countries=["KEN", "LBN"], current_year=2026)
        self.assertEqual(layer.values["KEN"].source.code, "NGDP_RPCH")
        self.assertEqual((layer.values["LBN"].year, layer.values["LBN"].source.code), (2024, "NY.GDP.MKTP.KD.ZG"))
        self.assertNotIn("WLD", layer.values)  # aggregates are filtered out

    def test_current_year_imf_values_are_flagged_as_estimates(self):
        layer = data.resolve_layer(self.indicator, {"NGDP_RPCH": {"KEN": {2026: 5.0}}, "NY.GDP.MKTP.KD.ZG": {}},
                                   status={"NGDP_RPCH": "ok", "NY.GDP.MKTP.KD.ZG": "ok"}, current_year=2026)
        self.assertTrue(layer.values["KEN"].is_estimate)

    def test_snapshot_is_used_only_for_unreachable_sources(self):
        snapshot = {"NGDP_RPCH": {"retrieved_at_utc": "2026-10-01T00:00:00+00:00", "series": {"KEN": {"2025": 4.8}}}}
        layer = data.resolve_layer(self.indicator, {"NGDP_RPCH": None, "NY.GDP.MKTP.KD.ZG": None},
                                   status={"NGDP_RPCH": "unreachable", "NY.GDP.MKTP.KD.ZG": "unreachable"},
                                   snapshot=snapshot, current_year=2026)
        self.assertTrue(layer.values["KEN"].from_snapshot)
        self.assertIn("2026-10-01", layer.values["KEN"].source_label)
        self.assertTrue(layer.all_unreachable)

    def test_rows_carry_full_provenance(self):
        layer = data.resolve_layer(self.indicator, {"NGDP_RPCH": {"KEN": {2025: 4.8}}, "NY.GDP.MKTP.KD.ZG": {}},
                                   status={"NGDP_RPCH": "ok", "NY.GDP.MKTP.KD.ZG": "ok"}, current_year=2026)
        row = layer.rows()[0]
        for key in ("iso3", "value", "year", "source", "series_code", "series_url", "is_estimate", "retrieved_at_utc"):
            self.assertIn(key, row)


class FetchSourceTests(unittest.TestCase):
    def setUp(self):
        data.clear_indicator_cache()

    def tearDown(self):
        data.clear_indicator_cache()

    def test_transport_failure_backs_off_the_whole_provider(self):
        with patch("src.indicator_data.download_imf", side_effect=data.IndicatorSourceError("down")) as download:
            self.assertEqual(data.fetch_source(SourceRef("imf", "AAA", "x"))[1], data.UNREACHABLE)
            self.assertEqual(data.fetch_source(SourceRef("imf", "BBB", "x"))[1], data.UNREACHABLE)
            self.assertEqual(download.call_count, 1)

    def test_unknown_series_does_not_take_provider_down(self):
        with patch("src.indicator_data.download_imf", side_effect=data.UnknownSeriesError("bad code")) as download:
            data.fetch_source(SourceRef("imf", "AAA", "x"))
            data.fetch_source(SourceRef("imf", "BBB", "x"))
            self.assertEqual(download.call_count, 2)

    def test_successful_download_is_cached_and_snapshotted(self):
        with patch("src.indicator_data.download_world_bank", return_value={"KEN": {2024: 1.0}}) as download:
            data.fetch_source(SourceRef("worldbank", "X.Y.Z", "x"))
            data.fetch_source(SourceRef("worldbank", "X.Y.Z", "x"))
            self.assertEqual(download.call_count, 1)
        self.assertIn("X.Y.Z", data.read_indicator_snapshot())

    def test_load_layer_never_raises_when_everything_is_down(self):
        with patch("src.indicator_data.download_imf", side_effect=data.IndicatorSourceError("down")), \
             patch("src.indicator_data.download_world_bank", side_effect=data.IndicatorSourceError("down")):
            layer = data.load_layer("gdp_growth", countries=["KEN"])
        self.assertEqual(layer.coverage, 0)
        self.assertTrue(layer.notes)


class FormattingAndMapTests(unittest.TestCase):
    def test_format_value_units(self):
        self.assertEqual(data.format_value(2.5e12, "usd"), "US$2.50T")
        self.assertEqual(data.format_value(73496.4, "usd_per_capita"), "US$73,496")
        self.assertEqual(data.format_value(-3.25, "percent_of_gdp"), "-3.2% of GDP")
        self.assertEqual(data.format_value(99.2e6, "people"), "99.2M")
        self.assertEqual(data.format_value(None, "percent"), "No data")

    def test_sequential_classes_are_ordered_and_missing_is_no_data(self):
        classifier = classify(INDICATOR_BY_ID["military_gdp"], [0.5, 1, 1.5, 2, 3, 4, 6, 9, 12])
        self.assertEqual(list(classifier.edges), sorted(classifier.edges))
        self.assertEqual(classifier.color(None), NO_DATA)
        self.assertNotEqual(classifier.color(0.1), classifier.color(50))

    def test_diverging_classes_are_symmetric_around_center(self):
        classifier = classify(INDICATOR_BY_ID["fiscal_balance_gdp"], [-9, -5, -2, 0, 1, 3, 6])
        edges = classifier.edges
        self.assertAlmostEqual(edges[0], -edges[-1])
        self.assertEqual(classifier.color(0.0), "#f0efec")

    def test_map_renders_values_missing_countries_and_small_states(self):
        indicator = INDICATOR_BY_ID["gov_debt_gdp"]
        layer = data.resolve_layer(indicator, {"GGXWDG_NGDP": {"AGO": {2025: 61.0}, "SGP": {2025: 175.0}}, "GG_DEBT_GDP": {}},
                                   status={"GGXWDG_NGDP": "ok", "GG_DEBT_GDP": "ok"}, current_year=2026)
        boundaries = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"ADM0_A3": code, "ADMIN": name},
             "geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + 5, y], [x + 5, y + 5], [x, y + 5], [x, y]]]}}
            for code, name, x, y in (("AGO", "Angola", 12, -15), ("CAN", "Canada", -110, 50))]}
        map_obj, _ = build_atlas_map(boundary_geojson=boundaries, layer=layer,
                                     name_by_code={"AGO": "Angola", "CAN": "Canada", "SGP": "Singapore"}, selected_iso3="AGO")
        rendered = map_obj.get_root().render()
        self.assertIn("61.0% of GDP", rendered)
        self.assertIn("No data (not zero)", rendered)
        self.assertIn("Singapore", rendered)  # no polygon, still drawn as a point
        self.assertIn("GDE_ACTION|COUNTRY|AGO", rendered)

    def test_country_index_excludes_uninhabited_and_includes_palestine_kosovo(self):
        from src.atlas_view import build_country_index

        index = build_country_index({"type": "FeatureCollection", "features": []})
        self.assertNotIn("ATA", index.codes)
        self.assertIn("PSE", index.codes)
        self.assertIn("XKX", index.codes)


if __name__ == "__main__":
    unittest.main()
