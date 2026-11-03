from __future__ import annotations

import unittest

import folium
import pandas as pd

import itertools

from src.debt_flows import LENT_DASH, add_flow_arrows, country_flows, flow_key, flows_table
from src.map_geometry import assign_distinct_colors, color_distance
from src.relationship_map import PillPlacer, curved_connection, screen_angle, short_money


def _frame() -> pd.DataFrame:
    rows = [
        # year, debtor, debtor name, creditor id, creditor iso3, creditor name, amount
        (2023, "KEN", "Kenya", "156", "CHN", "China", 6.0e9),
        (2024, "KEN", "Kenya", "156", "CHN", "China", 5.4e9),
        (2024, "KEN", "Kenya", "392", "JPN", "Japan", 1.2e9),
        (2024, "KEN", "Kenya", "994", "", "Multiple Lenders", 0.4e9),
        (2024, "PAK", "Pakistan", "156", "CHN", "China", 22.4e9),
    ]
    return pd.DataFrame(rows, columns=["year", "debtor_iso3", "debtor_name", "creditor_id", "creditor_iso3", "creditor_name", "amount_usd"])


class CountryFlowsTests(unittest.TestCase):
    def test_borrower_sees_lenders_sorted_with_shares_for_latest_year(self):
        flows = country_flows(_frame(), "KEN")
        self.assertEqual(flows.year, 2024)
        self.assertEqual([f.lender_name for f in flows.borrowed], ["China", "Japan", "Multiple Lenders"])
        self.assertAlmostEqual(flows.borrowed_total, 7.0e9)
        self.assertAlmostEqual(flows.borrowed[0].share_pct, 5.4 / 7.0 * 100)
        self.assertEqual(flows.lent, [])

    def test_lender_sees_who_owes_it(self):
        flows = country_flows(_frame(), "CHN", names={"PAK": "Pakistan", "KEN": "Kenya"})
        self.assertEqual([(f.borrower_iso3, f.amount_usd) for f in flows.lent], [("PAK", 22.4e9), ("KEN", 5.4e9)])
        self.assertEqual(flows.borrowed, [])

    def test_explicit_year_and_unknown_country(self):
        self.assertEqual(country_flows(_frame(), "KEN", year=2023).borrowed[0].amount_usd, 6.0e9)
        self.assertTrue(country_flows(_frame(), "USA").empty)

    def test_table_is_readable(self):
        table = flows_table(country_flows(_frame(), "KEN").borrowed, counterpart="Lender")
        self.assertEqual(list(table.columns), ["Lender", "Debt outstanding", "Share", "Year"])
        self.assertEqual(table.iloc[0]["Debt outstanding"], "US$5.40B")


class ArrowTests(unittest.TestCase):
    locations = {"KEN": (0.5, 37.9), "CHN": (35.0, 103.0), "JPN": (36.0, 138.0), "PAK": (30.0, 69.0)}

    def test_arrows_point_lender_to_borrower_with_amount_labels_and_legend(self):
        map_obj = folium.Map()
        drawn = add_flow_arrows(map_obj, country_flows(_frame(), "KEN"), self.locations, selected_name="Kenya")
        self.assertEqual(drawn, {"borrowed": 2, "lent": 0})  # "Multiple Lenders" has no location
        html = map_obj.get_root().render()
        self.assertIn("Kenya owes China US$5.40B (2024)", html)
        self.assertIn("LENT US$5.4B", html)
        self.assertIn("gde-arrowhead", html)
        expected = assign_distinct_colors(["156", "392"])   # same assignment as the Who-owes-whom map
        self.assertIn(expected["156"], html)
        self.assertIn(expected["392"], html)
        self.assertNotEqual(expected["156"], expected["392"])
        self.assertIn("Kenya borrowed from", html)
        self.assertIn("Each colour is one country", html)

    def test_lender_view_draws_lent_arrows(self):
        map_obj = folium.Map()
        drawn = add_flow_arrows(map_obj, country_flows(_frame(), "CHN"), self.locations, selected_name="China")
        self.assertEqual(drawn["lent"], 2)
        html = map_obj.get_root().render()
        self.assertIn(assign_distinct_colors(["PAK", "KEN"])["PAK"], html)
        self.assertIn(f'"dashArray": "{LENT_DASH}"', html)

    def test_colour_keys_match_the_who_owes_whom_map(self):
        kenya, china = country_flows(_frame(), "KEN"), country_flows(_frame(), "CHN")
        self.assertEqual(flow_key(kenya.borrowed[0], "borrowed"), "156")   # IDS creditor code, as in the debt map
        self.assertEqual(flow_key(china.lent[0], "lent"), "PAK")

    def test_colours_on_one_map_are_clearly_distinct(self):
        for trial in range(200):
            colors = list(assign_distinct_colors([f"c{trial}-{i}" for i in range(6)]).values())
            self.assertEqual(len(set(colors)), 6)
            self.assertGreaterEqual(min(color_distance(a, b) for a, b in itertools.combinations(colors, 2)), 15)

    def test_assignment_is_stable_for_the_same_key(self):
        self.assertEqual(assign_distinct_colors(["156"])["156"], assign_distinct_colors(["156", "392"])["156"])

    def test_screen_angle_uses_screen_orientation(self):
        self.assertAlmostEqual(screen_angle((0, 0), (0, 10)), 0.0)          # east
        self.assertAlmostEqual(screen_angle((0, 0), (10, 0)), -90.0)        # north is up on screen
        self.assertAlmostEqual(abs(screen_angle((0, 10), (0, 0))), 180.0)   # west

    def test_curves_never_cross_the_antimeridian(self):
        curve = curved_connection((56.0, -106.0), (35.0, 103.0))  # Canada -> China
        longitudes = [lon for _lat, lon in curve]
        self.assertTrue(all(-180 <= lon <= 180 for lon in longitudes))
        self.assertTrue(all(abs(b - a) < 30 for a, b in zip(longitudes, longitudes[1:])))

    def test_pill_placer_refuses_overlapping_labels(self):
        pills = PillPlacer()
        self.assertTrue(pills.place((48.0, 2.0)))      # Paris
        self.assertFalse(pills.place((51.0, 10.0)))    # Germany, too close
        self.assertTrue(pills.place((-34.0, -64.0)))   # Argentina
        self.assertTrue(pills.place((51.0, 10.0), force=True))

    def test_short_money(self):
        self.assertEqual(short_money(742.25e6), "US$742M")
        self.assertEqual(short_money(5.37e9), "US$5.4B")
        self.assertEqual(short_money(22.38e9), "US$22B")


if __name__ == "__main__":
    unittest.main()


class LegendPlacementTests(unittest.TestCase):
    def test_legend_position_can_avoid_the_relationship_card(self):
        map_obj = folium.Map()
        add_flow_arrows(map_obj, country_flows(_frame(), "CHN"), ArrowTests.locations, legend_position="bottomleft")
        self.assertIn('position: "bottomleft"', map_obj.get_root().render())
