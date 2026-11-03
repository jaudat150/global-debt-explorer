from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.relationship_map import (
    build_relationship_map, country_locations, curved_connection, extract_map_action, flag_emoji,
)


def feature(iso3: str, name: str, min_lon: float, min_lat: float, max_lon: float, max_lat: float):
    return {
        "type": "Feature",
        "properties": {"ADM0_A3": iso3, "ADMIN": name},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
                [min_lon, max_lat], [min_lon, min_lat],
            ]],
        },
    }


class RelationshipMapTests(unittest.TestCase):
    def setUp(self):
        self.boundaries = {
            "type": "FeatureCollection",
            "features": [
                feature("AGO", "Angola", 11, -18, 24, -5),
                feature("CHN", "China", 73, 18, 134, 53),
                feature("JPN", "Japan", 129, 31, 146, 46),
                feature("LBN", "Lebanon", 35, 33, 36.7, 34.7),
            ],
        }

    def test_country_locations_returns_representative_points(self):
        locations = country_locations(self.boundaries)
        self.assertEqual(set(locations), {"AGO", "CHN", "JPN", "LBN"})
        self.assertTrue(-18 <= locations["AGO"][0] <= -5)
        self.assertTrue(11 <= locations["AGO"][1] <= 24)

    def test_curve_contains_endpoints_and_intermediate_points(self):
        curve = curved_connection((-8, 18), (35, 139))
        self.assertEqual(len(curve), 43)
        self.assertAlmostEqual(curve[0][0], -8)
        self.assertAlmostEqual(curve[0][1], 18)
        self.assertAlmostEqual(curve[-1][0], 35)
        self.assertAlmostEqual(curve[-1][1], 139)

    def test_flag_emoji_is_returned_for_iso3(self):
        self.assertEqual(len(flag_emoji("DEU")), 2)
        self.assertEqual(flag_emoji("NOT"), "🌐")


    def test_selected_creditor_outside_top_connections_is_still_drawn(self):
        self.boundaries["features"].append(feature("FRA", "France", -5, 42, 8, 51))
        creditors = [
            {"creditor_id": "730", "creditor_iso3": "CHN", "creditor_name": "China", "amount_usd": 700, "share_pct": 70},
            {"creditor_id": "701", "creditor_iso3": "JPN", "creditor_name": "Japan", "amount_usd": 200, "share_pct": 20},
            {"creditor_id": "250", "creditor_iso3": "FRA", "creditor_name": "France", "amount_usd": 100, "share_pct": 10},
        ]
        map_obj, stats = build_relationship_map(
            boundary_geojson=self.boundaries,
            debtor_rows=[{"debtor_iso3": "AGO", "debtor_name": "Angola"}],
            creditor_rows=creditors,
            debtor_iso3="AGO",
            debtor_name="Angola",
            selected_creditor_id="250",
            total_amount_usd=1000,
            year=2024,
            max_connections=1,
        )
        rendered = map_obj.get_root().render()
        self.assertIn("CREDITOR|250", rendered)
        self.assertEqual(stats["geographic_creditor_connections"], 1)

    def test_builds_relationship_map_with_curves_and_summary_card(self):
        creditors = [
            {"creditor_id": "730", "creditor_iso3": "CHN", "creditor_name": "China", "amount_usd": 700_000_000, "share_pct": 70.0},
            {"creditor_id": "701", "creditor_iso3": "JPN", "creditor_name": "Japan", "amount_usd": 300_000_000, "share_pct": 30.0},
        ]
        map_obj, stats = build_relationship_map(
            boundary_geojson=self.boundaries,
            debtor_rows=[{"debtor_iso3": "AGO", "debtor_name": "Angola", "amount_usd": 1_000_000_000}],
            creditor_rows=creditors,
            debtor_iso3="AGO",
            debtor_name="Angola",
            selected_creditor_id="730",
            total_amount_usd=1_000_000_000,
            year=2024,
        )
        rendered = map_obj.get_root().render()
        self.assertIn("BILATERAL DEBT STOCK", rendered)
        self.assertIn("Angola", rendered)
        self.assertIn("China", rendered)
        self.assertIn("dashArray", rendered)
        self.assertEqual(stats["reporting_debtors_on_map"], 1)
        self.assertEqual(stats["geographic_creditor_connections"], 2)
        self.assertEqual(stats["creditor_areas_total"], 2)

    def test_map_country_popup_uses_full_name_and_relationship_details_not_iso_label(self):
        creditors = [
            {"creditor_id": "250", "creditor_iso3": "LBN", "creditor_name": "Lebanon", "amount_usd": 250_000_000},
            {"creditor_id": "730", "creditor_iso3": "CHN", "creditor_name": "China", "amount_usd": 750_000_000},
        ]
        map_obj, _ = build_relationship_map(
            boundary_geojson=self.boundaries,
            debtor_rows=[{"debtor_iso3": "AGO", "debtor_name": "Angola", "amount_usd": 1_000_000_000}],
            creditor_rows=creditors,
            creditor_totals_rows=[{"creditor_id": "250", "creditor_iso3": "LBN", "creditor_name": "Lebanon", "amount_usd": 250_000_000}],
            debtor_iso3="AGO",
            debtor_name="Angola",
            selected_creditor_id="250",
            total_amount_usd=1_000_000_000,
            year=2024,
        )
        rendered = map_obj.get_root().render()
        self.assertIn("Lebanon", rendered)
        self.assertIn("Angola → Lebanon", rendered)
        self.assertNotIn("ISO-3</th>", rendered)
        self.assertIn("GDE_ACTION|CREDITOR|250", rendered)
        geojson_layer = next(child for child in map_obj._children.values() if getattr(child, "layer_name", "") == "Country information")
        lebanon_properties = next(feature["properties"] for feature in geojson_layer.data["features"] if feature["properties"].get("_gde_iso3") == "LBN")
        self.assertIn("Selected debtor → this creditor", lebanon_properties["_gde_popup_html"])
        self.assertIn("Angola → Lebanon", lebanon_properties["_gde_popup_html"])

    def test_map_action_token_is_readable_for_streamlit_and_supports_legacy(self):
        self.assertEqual(extract_map_action("<span>GDE_ACTION|DEBTOR|AGO</span>"), ("DEBTOR", "AGO"))
        self.assertEqual(extract_map_action("CREDITOR|701"), ("CREDITOR", "701"))
        self.assertEqual(extract_map_action("<span>GDE_ACTION|COUNTRY|CAN</span>"), ("COUNTRY", "CAN"))
        self.assertIsNone(extract_map_action("ISO-3 LBN"))

    def test_relationship_tooltip_names_both_countries_and_amount(self):
        creditors = [{"creditor_id": "730", "creditor_iso3": "CHN", "creditor_name": "China", "amount_usd": 700_000_000}]
        map_obj, _ = build_relationship_map(
            boundary_geojson=self.boundaries,
            debtor_rows=[{"debtor_iso3": "AGO", "debtor_name": "Angola", "amount_usd": 700_000_000}],
            creditor_rows=creditors,
            debtor_iso3="AGO",
            debtor_name="Angola",
            selected_creditor_id="730",
            total_amount_usd=700_000_000,
            year=2024,
        )
        rendered = map_obj.get_root().render()
        self.assertIn("Angola → China", rendered)
        self.assertIn("Angola owes China", rendered)
        self.assertIn("$700.0M", rendered)
        self.assertIn("Share of debtor&#x27;s reported total", rendered)

    def test_default_basemap_uses_keyless_openstreetmap_with_attribution(self):
        creditors = [
            {"creditor_id": "730", "creditor_iso3": "CHN", "creditor_name": "China", "amount_usd": 700},
        ]
        with patch.dict(os.environ, {"GDE_MAP_BASEMAP": "openstreetmap"}, clear=False):
            with patch.dict(os.environ, {"CARTO_BASEMAP_API_KEY": ""}, clear=False):
                map_obj, _ = build_relationship_map(
                    boundary_geojson=self.boundaries,
                    debtor_rows=[{"debtor_iso3": "AGO", "debtor_name": "Angola"}],
                    creditor_rows=creditors,
                    debtor_iso3="AGO",
                    debtor_name="Angola",
                    selected_creditor_id="730",
                    total_amount_usd=700,
                    year=2024,
                )
        rendered = map_obj.get_root().render()
        self.assertIn("https://tile.openstreetmap.org/{z}/{x}/{y}.png", rendered)
        self.assertIn("OpenStreetMap contributors", rendered)
        self.assertNotIn("basemaps.cartocdn.com", rendered)


    def test_national_profile_is_visible_for_countries_without_ids_bilateral_rows(self):
        self.boundaries["features"].append(feature("CAN", "Canada", -141, 42, -52, 83))
        profile = {
            "iso3": "CAN", "country_name": "Canada", "value": 109.0,
            "display_value": "109.0% of GDP", "metric_type": "general_government_debt_pct_gdp",
            "metric_label": "General government gross debt (% of GDP)", "source_label": "IMF · DataMapper",
            "source_date_label": "Retrieved 2026-10-31 UTC",
            "fallback_tier": 2, "observation_year": 2024, "data_available": True,
        }
        map_obj, stats = build_relationship_map(
            boundary_geojson=self.boundaries,
            debtor_rows=[{"debtor_iso3": "AGO", "debtor_name": "Angola", "amount_usd": 0}],
            creditor_rows=[],
            debtor_iso3="AGO",
            debtor_name="Angola",
            selected_creditor_id=None,
            total_amount_usd=0,
            year=2024,
            country_profiles={"CAN": profile},
        )
        rendered = map_obj.get_root().render()
        self.assertIn("Canada", rendered)
        self.assertIn("109.0% of GDP", rendered)
        self.assertIn("General government gross debt (% of GDP)", rendered)
        self.assertIn("IMF · DataMapper", rendered)
        self.assertIn("Retrieved 2026-10-31 UTC", rendered)
        self.assertIn("GDE_ACTION|COUNTRY|CAN", rendered)
        self.assertEqual(stats["country_profile_markers"], 1)


    def test_country_without_any_verified_profile_data_remains_clickable_and_never_becomes_zero(self):
        self.boundaries["features"].append(feature("CAN", "Canada", -141, 42, -52, 83))
        unavailable = {
            "iso3": "CAN", "country_name": "Canada", "value": None,
            "display_value": "No verified data", "metric_type": "unavailable",
            "metric_label": "No verified national debt indicator available",
            "source_label": "No source available", "data_available": False,
            "coordinates": None, "map_available": False,
        }
        map_obj, stats = build_relationship_map(
            boundary_geojson=self.boundaries,
            debtor_rows=[], creditor_rows=[], debtor_iso3="AGO", debtor_name="Angola",
            selected_creditor_id=None, total_amount_usd=0, year=2024,
            country_profiles={"CAN": unavailable},
        )
        rendered = map_obj.get_root().render()
        self.assertIn("Canada", rendered)
        self.assertIn("No verified data", rendered)
        self.assertIn("Not available", rendered)
        self.assertIn("not zero", rendered)
        self.assertIn("GDE_ACTION|COUNTRY|CAN", rendered)
        self.assertEqual(stats["country_profiles_available"], 0)
        self.assertEqual(stats["country_profiles_without_geometry"], 0)

    def test_country_without_bilateral_rows_gets_profile_summary_card(self):
        profile = {
            "iso3": "AGO", "country_name": "Angola", "value": 25.0,
            "display_value": "25.0% of GDP", "metric_type": "general_government_debt_pct_gdp",
            "metric_label": "General government gross debt (% of GDP)", "source_label": "IMF DataMapper",
            "fallback_tier": 2, "observation_year": 2024, "data_available": True,
        }
        map_obj, _ = build_relationship_map(
            boundary_geojson=self.boundaries,
            debtor_rows=[], creditor_rows=[], debtor_iso3="AGO", debtor_name="Angola",
            selected_creditor_id=None, total_amount_usd=0, year=2024,
            country_profiles={"AGO": profile},
        )
        rendered = map_obj.get_root().render()
        self.assertIn("COUNTRY DEBT PROFILE", rendered)
        self.assertIn("25.0% of GDP", rendered)
        self.assertIn("IMF DataMapper", rendered)

    def test_carto_basemap_is_opt_in_and_uses_user_key(self):
        creditors = [
            {"creditor_id": "730", "creditor_iso3": "CHN", "creditor_name": "China", "amount_usd": 700},
        ]
        with patch.dict(os.environ, {"GDE_MAP_BASEMAP": "carto", "CARTO_BASEMAP_API_KEY": "test-key"}, clear=False):
            map_obj, _ = build_relationship_map(
                boundary_geojson=self.boundaries,
                debtor_rows=[{"debtor_iso3": "AGO", "debtor_name": "Angola"}],
                creditor_rows=creditors,
                debtor_iso3="AGO",
                debtor_name="Angola",
                selected_creditor_id="730",
                total_amount_usd=700,
                year=2024,
            )
        rendered = map_obj.get_root().render()
        self.assertIn("basemaps.cartocdn.com/light_all", rendered)
        self.assertIn("key=test-key", rendered)



class ResilienceMapTests(unittest.TestCase):
    def _profile(self, iso3, name, display, tier, available=True):
        return {"iso3": iso3, "country_name": name, "display_value": display, "fallback_tier": tier,
                "tier_label": f"Tier {tier}" if tier else "No verified source", "data_available": available,
                "metric_label": "metric", "source_label": "source", "metric_short": "short"}

    def test_natural_earth_codes_are_mapped_to_iso_codes(self):
        from src.relationship_map import iso3_from_properties

        self.assertEqual(iso3_from_properties({"ADM0_A3": "PSX", "ISO_A3": "PSE"}), "PSE")
        self.assertEqual(iso3_from_properties({"ADM0_A3": "SDS"}), "SSD")
        self.assertEqual(iso3_from_properties({"ADM0_A3": "KOS", "ISO_A3": "-99"}), "XKX")

    def test_palestine_polygon_shows_its_fallback_profile(self):
        boundaries = {"type": "FeatureCollection", "features": [
            feature("AGO", "Angola", 11, -18, 24, -5), feature("PSX", "Palestine", 34.9, 31.3, 35.5, 32.5)]}
        profile = self._profile("PSE", "West Bank and Gaza", "US$4.8B (estimate)", 3)
        map_obj, _ = build_relationship_map(
            boundary_geojson=boundaries, debtor_rows=[], creditor_rows=[], debtor_iso3="AGO", debtor_name="Angola",
            selected_creditor_id=None, total_amount_usd=0, year=2024, country_profiles={"PSE": profile})
        rendered = map_obj.get_root().render()
        self.assertIn("US$4.8B (estimate)", rendered)
        self.assertIn("GDE_ACTION|COUNTRY|PSE", rendered)

    def test_small_state_without_polygon_still_gets_a_marker(self):
        boundaries = {"type": "FeatureCollection", "features": [feature("AGO", "Angola", 11, -18, 24, -5)]}
        profiles = {"SGP": self._profile("SGP", "Singapore", "174.0% of GDP", 2),
                    "PRK": self._profile("PRK", "North Korea", "No verified data", None, available=False)}
        map_obj, stats = build_relationship_map(
            boundary_geojson=boundaries, debtor_rows=[], creditor_rows=[], debtor_iso3="AGO", debtor_name="Angola",
            selected_creditor_id=None, total_amount_usd=0, year=2024, country_profiles=profiles)
        rendered = map_obj.get_root().render()
        self.assertIn("Singapore · 174.0% of GDP", rendered)
        self.assertIn("North Korea · No verified data (not zero)", rendered)
        self.assertEqual((stats["country_profile_markers"], stats["unavailable_profile_markers"]), (1, 1))
        self.assertEqual(stats["country_profiles_without_geometry"], 0)

    def test_relationship_card_shows_national_profile_next_to_bilateral_total(self):
        boundaries = {"type": "FeatureCollection", "features": [
            feature("SYR", "Syria", 36, 32, 42, 37), feature("RUS", "Russia", 40, 50, 100, 70)]}
        creditors = [{"creditor_id": "643", "creditor_iso3": "RUS", "creditor_name": "Russian Federation", "amount_usd": 525e6}]
        profile = self._profile("SYR", "Syria", "US$20–23B (estimated range)", 3)
        map_obj, _ = build_relationship_map(
            boundary_geojson=boundaries, debtor_rows=[{"debtor_iso3": "SYR", "debtor_name": "Syria", "amount_usd": 2.01e9}],
            creditor_rows=creditors, debtor_iso3="SYR", debtor_name="Syria", selected_creditor_id="643",
            total_amount_usd=2.01e9, year=2024, country_profiles={"SYR": profile})
        rendered = map_obj.get_root().render()
        self.assertIn("Bilateral IDS total (not total debt)", rendered)
        self.assertIn("National debt profile", rendered)
        self.assertIn("US$20\\u201323B (estimated range)", rendered)  # card HTML is JSON-encoded


if __name__ == "__main__":
    unittest.main()
