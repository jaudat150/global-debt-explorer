from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import fetcher, processor

_SNAPSHOT_DIR = tempfile.TemporaryDirectory()


def setUpModule():
    # Never let tests write mock data into the repository's data/ folder.
    os.environ["GDE_PROFILE_SNAPSHOT"] = str(Path(_SNAPSHOT_DIR.name) / "snapshot.json")


def tearDownModule():
    os.environ.pop("GDE_PROFILE_SNAPSHOT", None)
    _SNAPSHOT_DIR.cleanup()


class CountryProfileFetcherTests(unittest.TestCase):
    def test_world_bank_parser_uses_latest_non_null_observation(self):
        payload = [
            {"page": 1, "pages": 1, "total": 4},
            [
                {"countryiso3code": "CAN", "country": {"value": "Canada"}, "date": "2024", "value": None},
                {"countryiso3code": "CAN", "country": {"value": "Canada"}, "date": "2022", "value": "1,250,000"},
                {"countryiso3code": "CAN", "country": {"value": "Canada"}, "date": "2023", "value": 1_500_000},
                {"countryiso3code": "CAN", "country": {"value": "Canada"}, "date": "2021", "value": ".."},
            ],
        ]
        parsed = fetcher.parse_world_bank_country_indicator_payload(payload)
        self.assertEqual(parsed["CAN"]["observation_year"], 2023)
        self.assertEqual(parsed["CAN"]["value"], 1_500_000)
        self.assertEqual(parsed["CAN"]["metric_type"], "external_debt_usd_long_term")

    def test_world_bank_parser_can_filter_requested_iso3(self):
        payload = [
            {"page": 1, "pages": 1},
            [
                {"countryiso3code": "CAN", "country": {"value": "Canada"}, "date": "2024", "value": 100},
                {"countryiso3code": "SAU", "country": {"value": "Saudi Arabia"}, "date": "2024", "value": 200},
            ],
        ]
        parsed = fetcher.parse_world_bank_country_indicator_payload(payload, requested_iso3="SAU")
        self.assertEqual(set(parsed), {"SAU"})

    def test_imf_parser_uses_latest_non_null_value(self):
        payload = {
            "values": {
                "GG_DEBT_GDP": {
                    "SAU": {"2022": 23.9, "2023": None, "2024": 28.3, "2025": ".."}
                }
            }
        }
        parsed = fetcher.parse_imf_gg_debt_gdp_payload(payload, "SAU")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["observation_year"], 2024)
        self.assertEqual(parsed["value"], 28.3)
        self.assertEqual(parsed["unit"], "percent_of_gdp")

    def test_imf_parser_returns_none_for_missing_country_or_null_series(self):
        payload = {"values": {"GG_DEBT_GDP": {"CAN": {"2024": None}}}}
        self.assertIsNone(fetcher.parse_imf_gg_debt_gdp_payload(payload, "CAN"))
        self.assertIsNone(fetcher.parse_imf_gg_debt_gdp_payload(payload, "SAU"))


    @patch("src.fetcher._country_profile_get_json", side_effect=RuntimeError("network unavailable"))
    def test_world_bank_network_error_returns_none_for_fallback(self, _request_mock):
        self.assertIsNone(fetcher.fetch_world_bank_country_indicator("CAN"))

    def test_world_bank_parser_skips_invalid_and_negative_values(self):
        payload = [
            {"page": 1, "pages": 1},
            [
                {"countryiso3code": "CAN", "country": {"value": "Canada"}, "date": "2024", "value": -5},
                {"countryiso3code": "CAN", "country": {"value": "Canada"}, "date": "2023", "value": ".."},
                {"countryiso3code": "CAN", "country": {"value": "Canada"}, "date": "2022", "value": 12.5},
            ],
        ]
        parsed = fetcher.parse_world_bank_country_indicator_payload(payload)
        self.assertEqual(parsed["CAN"]["observation_year"], 2022)
        self.assertEqual(parsed["CAN"]["value"], 12.5)

    @patch("src.fetcher._country_profile_get_json")
    def test_single_country_lookup_uses_required_world_bank_v2_route(self, request_mock):
        request_mock.return_value = [
            {"page": 1, "pages": 1},
            [{"countryiso3code": "CAN", "country": {"value": "Canada"}, "date": "2024", "value": 123.0}],
        ]
        result = fetcher.fetch_world_bank_country_indicator("CAN")
        self.assertEqual(result["value"], 123.0)
        called_url = request_mock.call_args.args[0]
        self.assertIn("/country/CAN/indicator/DT.DOD.DLXF.CD", called_url)
        self.assertEqual(result["source_url"], called_url + "?format=json")


class ResilienceFallbackProcessorTests(unittest.TestCase):
    def setUp(self):
        processor.clear_country_debt_profile_cache()
        Path(os.environ["GDE_PROFILE_SNAPSHOT"]).unlink(missing_ok=True)

    def tearDown(self):
        processor.clear_country_debt_profile_cache()

    @patch("src.fetcher.fetch_imf_gg_debt_gdp_payload")
    @patch("src.fetcher.fetch_world_bank_country_indicators_bulk")
    def test_three_tier_priority_and_complete_iso3_contract(self, wb_mock, imf_mock):
        wb_mock.return_value = {
            "SYR": {
                "iso3": "SYR", "country_name": "Syrian Arab Republic", "value": 3_409_600_000,
                "observation_year": 2024, "metric_type": "external_debt_usd_long_term",
                "metric_label": fetcher.WORLD_BANK_COUNTRY_INDICATOR_LABEL, "unit": "USD",
                "source_label": "World Bank · International Debt Statistics", "data_source": "World Bank API",
                "indicator_code": fetcher.WORLD_BANK_COUNTRY_INDICATOR,
                "source_url": "https://api.worldbank.org/test", "fallback_tier": 1,
                "is_estimate": False, "definition_note": "WB long-term external debt.",
            }
        }
        imf_mock.return_value = {
            "values": {
                "GG_DEBT_GDP": {
                    "CAN": {"2023": 105.0, "2024": 109.0},
                    "SAU": {"2024": 29.0, "2025": 30.5},
                }
            }
        }
        profiles = processor.get_country_debt_profiles(("SYR", "CAN", "SAU", "PSE", "ZZZ"))

        self.assertEqual(set(profiles), {"SYR", "CAN", "SAU", "PSE", "ZZZ"})
        self.assertEqual(profiles["SYR"]["fallback_tier"], 1)  # World Bank wins over fallback.
        self.assertEqual(profiles["CAN"]["fallback_tier"], 2)
        self.assertEqual(profiles["CAN"]["metric_type"], "general_government_debt_pct_gdp")
        self.assertEqual(profiles["CAN"]["display_value"], "109.0% of GDP")
        self.assertEqual(profiles["SAU"]["observation_year"], 2025)
        self.assertEqual(profiles["PSE"]["fallback_tier"], 3)
        self.assertEqual(profiles["PSE"]["metric_type"], "estimated_public_debt_usd")
        self.assertFalse(profiles["ZZZ"]["data_available"])
        self.assertEqual(profiles["ZZZ"]["display_value"], "No verified data")
        for profile in profiles.values():
            for field in ("iso3", "country_name", "display_value", "metric_type", "metric_label", "source_label", "data_source", "fallback_tier", "observation_year", "data_available", "coordinates", "map_available"):
                self.assertIn(field, profile)
        wb_mock.assert_called_once()
        requested = [call.kwargs.get("indicator") for call in imf_mock.call_args_list]
        self.assertEqual(requested, ["GGXWDG_NGDP", "GG_DEBT_GDP"])  # WEO first, then Global Debt Database

    @patch("src.fetcher.fetch_imf_gg_debt_gdp_payload", return_value=None)
    @patch("src.fetcher.fetch_world_bank_country_indicators_bulk", return_value={})
    def test_local_fallback_is_used_when_both_live_sources_are_missing(self, _wb_mock, _imf_mock):
        profile = processor.get_country_debt_profiles(("PSE",))["PSE"]
        self.assertEqual(profile["fallback_tier"], 3)
        self.assertTrue(profile["is_estimate"])
        self.assertIn("estimate", profile["display_value"].casefold())
        self.assertTrue(profile["source_url"].startswith("https://"))

    @patch("src.fetcher.fetch_imf_gg_debt_gdp_payload", return_value=None)
    @patch("src.fetcher.fetch_world_bank_country_indicators_bulk", return_value={})
    def test_every_code_returns_explicit_unavailable_profile_if_no_tier_has_data(self, _wb_mock, _imf_mock):
        profile = processor.get_country_debt_profiles(("AAA",))["AAA"]
        self.assertFalse(profile["data_available"])
        self.assertIsNone(profile["value"])
        self.assertEqual(profile["metric_type"], "unavailable")
        self.assertEqual(profile["source_label"], "No source available")
        self.assertEqual(profile["data_source"], "Unavailable")
        self.assertEqual(profile["display_value"], "No verified data")
        self.assertFalse(profile["map_available"])
        self.assertIsNone(profile["coordinates"])


    @patch("src.fetcher.fetch_imf_gg_debt_gdp_payload")
    @patch("src.fetcher.fetch_world_bank_country_indicator", return_value=None)
    def test_single_country_falls_through_world_bank_to_imf(self, _wb_mock, imf_mock):
        imf_mock.return_value = {"values": {"GG_DEBT_GDP": {"CAN": {"2024": 109.0}}}}
        profile = processor.get_country_debt_profile("CAN")
        self.assertEqual(profile["fallback_tier"], 2)
        self.assertEqual(profile["metric_type"], "general_government_debt_pct_gdp")
        self.assertEqual(profile["display_value"], "109.0% of GDP")
        self.assertEqual(profile["source_label"], "IMF · DataMapper · General government debt (% of GDP)")

    @patch("src.fetcher.fetch_imf_gg_debt_gdp_payload", return_value=None)
    @patch("src.fetcher.fetch_world_bank_country_indicator", return_value=None)
    def test_single_country_uses_local_fallback_when_both_live_tiers_missing(self, _wb_mock, _imf_mock):
        profile = processor.get_country_debt_profile("PSE")
        self.assertEqual(profile["fallback_tier"], 3)
        self.assertTrue(profile["is_estimate"])
        self.assertEqual(profile["source_timestamp"], "2026-05-19")
        self.assertIn("different debt perimeter", profile["definition_note"].casefold())

    def test_country_name_override_for_palestine_iso3(self):
        self.assertEqual(processor._country_display_name("PSE"), "West Bank and Gaza")


class LiveClientEdgeCaseTests(unittest.TestCase):
    def test_imf_parser_ignores_weo_projection_years(self):
        payload = {"values": {"GG_DEBT_GDP": {"CAN": {"2024": 110.0, "2025": 111.0, "2030": 95.0}}}}
        parsed = fetcher.parse_imf_gg_debt_gdp_payload(payload, "CAN", max_year=2025)
        self.assertEqual(parsed["observation_year"], 2025)
        self.assertEqual(parsed["value"], 111.0)

    def test_imf_parser_translates_iso_codes_to_imf_aliases(self):
        payload = {"values": {"GG_DEBT_GDP": {"WBG": {"2024": 30.0}, "UVK": {"2024": 17.0}}}}
        self.assertEqual(fetcher.parse_imf_gg_debt_gdp_payload(payload, "PSE", max_year=2026)["value"], 30.0)
        self.assertEqual(fetcher.parse_imf_gg_debt_gdp_payload(payload, "XKX", max_year=2026)["value"], 17.0)

    def test_world_bank_error_envelope_is_treated_as_missing_data(self):
        payload = [{"message": [{"id": "120", "key": "Invalid value", "value": "The provided parameter value is not valid"}]}]
        self.assertEqual(fetcher.parse_world_bank_country_indicator_payload(payload), {})

    def test_zero_values_are_not_usable_observations(self):
        payload = [{"page": 1, "pages": 1}, [{"countryiso3code": "SAU", "date": "2024", "value": 0}]]
        self.assertEqual(fetcher.parse_world_bank_country_indicator_payload(payload), {})

    @patch("src.fetcher._country_profile_get_json", side_effect=fetcher.LiveSourceError("down"))
    def test_strict_mode_raises_so_outages_are_not_cached(self, _mock):
        with self.assertRaises(fetcher.LiveSourceError):
            fetcher.fetch_world_bank_country_indicator("CAN", raise_on_error=True)
        with self.assertRaises(fetcher.LiveSourceError):
            fetcher.fetch_imf_gg_debt_gdp_payload(raise_on_error=True)

    @patch("src.fetcher.fetch_world_bank_country_indicator", side_effect=fetcher.LiveSourceError("down"))
    def test_per_country_circuit_breaker_stops_requests_during_outage(self, single_mock):
        codes = [f"A{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(60)]
        with self.assertRaises(fetcher.LiveSourceError):
            fetcher.fetch_world_bank_country_indicators_individually(codes, workers=1, breaker_threshold=5)
        self.assertEqual(single_mock.call_count, 5)

    @patch("src.fetcher.fetch_world_bank_country_indicators_individually", return_value={"CAN": {"value": 1}})
    @patch("src.fetcher.fetch_world_bank_country_indicators_bulk", side_effect=fetcher.LiveSourceError("413"))
    def test_bulk_failure_falls_back_to_exact_per_country_route(self, _bulk, individual):
        self.assertEqual(fetcher.fetch_world_bank_external_debt(["CAN"]), {"CAN": {"value": 1}})
        individual.assert_called_once()


def _wb(iso3, value, year):
    return fetcher._world_bank_observation(iso3, iso3, year, value)


class PureResolverTests(unittest.TestCase):
    IMF = {"values": {"GG_DEBT_GDP": {"CAN": {"2024": 110.0}, "SAU": {"2025": 30.5}}}}

    def resolve(self, iso3, **overrides):
        kwargs = dict(world_bank=None, world_bank_status="ok", imf_payload=self.IMF, imf_status="ok",
                      snapshot={}, retrieved_at="2026-11-02T00:00:00+00:00", current_year=2026)
        kwargs.update(overrides)
        return processor.resolve_country_profile(iso3, **kwargs)

    def test_tier1_wins_and_attaches_curated_alternative(self):
        profile = self.resolve("SYR", world_bank=_wb("SYR", 4.1e9, 2024))
        self.assertEqual(profile["fallback_tier"], 1)
        self.assertEqual(profile["display_value"], "US$4.10B")
        self.assertEqual(profile["alternative_estimate"]["display_value"], "US$20–23B (estimated range)")

    def test_canada_null_in_world_bank_uses_imf(self):
        profile = self.resolve("CAN")
        self.assertEqual((profile["fallback_tier"], profile["display_value"]), (2, "110.0% of GDP"))
        self.assertIn("Tier 1 World Bank: no non-null observation", profile["resolution_trail"])

    def test_stale_tier1_observation_falls_through(self):
        profile = self.resolve("CAN", world_bank=_wb("CAN", 5e9, 2009), max_age_years=10)
        self.assertEqual(profile["fallback_tier"], 2)
        self.assertTrue(any("older than 10 years" in step for step in profile["resolution_trail"]))

    def test_both_apis_down_uses_curated_estimate_for_palestine(self):
        profile = self.resolve("PSE", world_bank_status="unreachable", imf_payload=None, imf_status="unreachable")
        self.assertEqual(profile["fallback_tier"], 3)
        self.assertEqual(profile["data_source"], "Local fallback database")
        self.assertIsNotNone(profile["coordinates"])

    def test_both_apis_down_uses_last_known_snapshot(self):
        snapshot = {"world_bank": {"KEN": {**_wb("KEN", 4.0e10, 2024), "retrieved_at_utc": "2026-10-01T00:00:00+00:00"}}}
        profile = self.resolve("KEN", world_bank_status="unreachable", imf_payload=None, imf_status="unreachable", snapshot=snapshot)
        self.assertEqual(profile["fallback_tier"], 3)
        self.assertTrue(profile["is_stale_snapshot"])
        self.assertIn("2026-10-01", profile["source_date_label"])

    def test_snapshot_is_ignored_when_source_answered_with_no_data(self):
        snapshot = {"world_bank": {"KEN": _wb("KEN", 4.0e10, 2024)}}
        profile = self.resolve("KEN", imf_payload={"values": {"GG_DEBT_GDP": {}}}, snapshot=snapshot)
        self.assertFalse(profile["data_available"])

    def test_prefer_over_live_pins_the_curated_entry(self):
        db = {"SYR": {**processor.LOCAL_FALLBACK_DB["SYR"], "prefer_over_live": True}}
        profile = self.resolve("SYR", world_bank=_wb("SYR", 4.1e9, 2024), local_db=db)
        self.assertEqual(profile["fallback_tier"], 3)

    def test_every_real_country_resolves_with_complete_contract_and_coordinates(self):
        import pycountry

        codes = [c.alpha_3 for c in pycountry.countries] + ["XKX"]
        for code in codes:
            profile = self.resolve(code, world_bank_status="unreachable", imf_payload=None, imf_status="unreachable")
            for field in ("display_value", "metric_type", "data_source", "tier_label", "country_name"):
                self.assertTrue(profile[field], f"{code} missing {field}")
            self.assertIsNotNone(profile["coordinates"], f"{code} has no coordinates")
            self.assertTrue(profile["map_available"])


class WeoTier2Tests(unittest.TestCase):
    """Saudi Arabia regression: present in the WEO series, absent from the Global Debt Database."""

    def setUp(self):
        processor.clear_country_debt_profile_cache()
        Path(os.environ["GDE_PROFILE_SNAPSHOT"]).unlink(missing_ok=True)

    def tearDown(self):
        processor.clear_country_debt_profile_cache()

    def test_weo_series_fills_countries_missing_from_global_debt_database(self):
        payloads = {
            "GGXWDG_NGDP": {"values": {"GGXWDG_NGDP": {"SAU": {"2025": 32.0, "2026": 35.4}, "CAN": {"2026": 108.0}}}},
            "GG_DEBT_GDP": {"values": {"GG_DEBT_GDP": {"CAN": {"2024": 110.8}}}},
        }
        with patch("src.fetcher.fetch_world_bank_external_debt", return_value={}), \
             patch("src.fetcher.fetch_imf_gg_debt_gdp_payload",
                   side_effect=lambda indicator="GG_DEBT_GDP", **_: payloads[indicator]):
            profiles = processor.get_country_debt_profiles(("SAU", "CAN"))
        self.assertEqual(profiles["SAU"]["fallback_tier"], 2)
        self.assertEqual(profiles["SAU"]["display_value"], "35.4% of GDP")
        self.assertIn("World Economic Outlook", profiles["SAU"]["source_label"])
        self.assertEqual(profiles["CAN"]["observation_year"], 2026)  # WEO preferred over the older GDD value

    def test_resolver_falls_back_to_gdd_when_weo_value_is_stale(self):
        payloads = {
            "GGXWDG_NGDP": {"values": {"GGXWDG_NGDP": {"XYZ": {"2005": 50.0}}}},
            "GG_DEBT_GDP": {"values": {"GG_DEBT_GDP": {"XYZ": {"2024": 61.0}}}},
        }
        profile = processor.resolve_country_profile("XYZ", world_bank=None, world_bank_status="ok",
                                                    imf_payload=payloads, imf_status="ok", current_year=2026)
        self.assertEqual((profile["value"], profile["observation_year"]), (61.0, 2024))


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        processor.clear_country_debt_profile_cache()
        self.snapshot_path = Path(os.environ["GDE_PROFILE_SNAPSHOT"])
        self.snapshot_path.unlink(missing_ok=True)

    def tearDown(self):
        processor.clear_country_debt_profile_cache()

    @patch("src.fetcher.fetch_imf_gg_debt_gdp_payload", side_effect=fetcher.LiveSourceError("down"))
    @patch("src.fetcher.fetch_world_bank_external_debt", side_effect=fetcher.LiveSourceError("down"))
    def test_outage_is_backed_off_not_retried_every_rerun(self, wb, imf):
        processor.get_country_debt_profiles(("CAN",))
        processor.get_country_debt_profiles(("CAN",))
        self.assertEqual((wb.call_count, imf.call_count), (1, 1))
        processor.clear_country_debt_profile_cache()  # the Refresh button resets the back-off
        processor.get_country_debt_profiles(("CAN",))
        self.assertEqual(wb.call_count, 2)

    @patch("src.fetcher.fetch_imf_gg_debt_gdp_payload", return_value={"values": {"GG_DEBT_GDP": {"CAN": {"2024": 110.0}}}})
    @patch("src.fetcher.fetch_world_bank_external_debt", return_value={"KEN": _wb("KEN", 4.0e10, 2024)})
    def test_successful_live_calls_are_cached_and_snapshotted(self, wb, _imf):
        first = processor.get_country_debt_profiles(("CAN", "KEN"))
        processor.get_country_debt_profiles(("CAN", "KEN"))
        self.assertEqual(wb.call_count, 1)
        self.assertEqual((first["KEN"]["fallback_tier"], first["CAN"]["fallback_tier"]), (1, 2))
        snapshot = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        self.assertIn("KEN", snapshot["sources"]["world_bank"])
        self.assertIn("CAN", snapshot["sources"]["imf"])

    @patch("src.fetcher.fetch_imf_gg_debt_gdp_payload", side_effect=fetcher.LiveSourceError("down"))
    @patch("src.fetcher.fetch_world_bank_external_debt", side_effect=fetcher.LiveSourceError("down"))
    def test_snapshot_from_previous_run_survives_outage(self, _wb_mock, _imf_mock):
        processor.write_profile_snapshot("world_bank", {"KEN": _wb("KEN", 4.0e10, 2024)})
        profile = processor.get_country_debt_profiles(("KEN",))["KEN"]
        self.assertEqual(profile["fallback_tier"], 3)
        self.assertEqual(profile["value"], 4.0e10)


if __name__ == "__main__":
    unittest.main()
