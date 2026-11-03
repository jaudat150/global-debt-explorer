"""Tests for creditor-slice GeoJSON generation."""

import unittest

from shapely.geometry import mapping, shape

from src.map_geometry import build_creditor_map_geojson


class MapGeometryTests(unittest.TestCase):
    def test_creates_clipped_country_slices_with_share_metadata(self):
        boundaries = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"ADM0_A3": "PAK", "NAME": "Pakistan"},
                    "geometry": mapping(
                        shape({
                            "type": "Polygon",
                            "coordinates": [[[60.0, 23.0], [77.0, 23.0], [77.0, 37.0],
                                            [60.0, 37.0], [60.0, 23.0]]]
                        })
                    ),
                }
            ],
        }
        rows = [
            {
                "debtor_iso3": "PAK", "debtor_name": "Pakistan",
                "creditor_id": "CHN", "creditor_iso3": "CHN",
                "creditor_name": "China", "amount_usd": 75.0,
            },
            {
                "debtor_iso3": "PAK", "debtor_name": "Pakistan",
                "creditor_id": "USA", "creditor_iso3": "USA",
                "creditor_name": "United States", "amount_usd": 25.0,
            },
        ]
        result = build_creditor_map_geojson(rows, boundaries, max_map_slices=7)
        self.assertEqual(result["type"], "FeatureCollection")
        self.assertEqual(len(result["features"]), 2)
        shares = sorted(feature["properties"]["share_pct"] for feature in result["features"])
        self.assertEqual(shares, [25.0, 75.0])
        for feature in result["features"]:
            self.assertTrue(feature["geometry"]["type"] in {"Polygon", "MultiPolygon"})
            self.assertEqual(feature["properties"]["debtor_iso3"], "PAK")

    def test_groups_long_tail_as_other(self):
        boundaries = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"ADM0_A3": "PAK"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[60, 23], [77, 23], [77, 37], [60, 37], [60, 23]]],
                    },
                }
            ],
        }
        rows = [
            {
                "debtor_iso3": "PAK", "debtor_name": "Pakistan", "creditor_id": f"C{i}",
                "creditor_iso3": "", "creditor_name": f"Creditor {i}", "amount_usd": float(10 - i),
            }
            for i in range(9)
        ]
        result = build_creditor_map_geojson(rows, boundaries, max_map_slices=4)
        self.assertEqual(len(result["features"]), 4)
        self.assertTrue(any("Other creditors" in f["properties"]["creditor_name"] for f in result["features"]))


if __name__ == "__main__":
    unittest.main()
