from __future__ import annotations

import unittest

from src import share


class FakeParams(dict):
    def from_dict(self, mapping):
        self.clear()
        self.update(mapping)


COUNTRIES = {"KEN", "USA", "CHN", "SYR"}
ITEMS = {"military", "health"}


class ShareTests(unittest.TestCase):
    def test_parse_full_link(self):
        updates = share.parse_query({"view": "atlas", "indicator": "military_gdp", "country": "ken"},
                                    countries=COUNTRIES, spending_items=ITEMS)
        self.assertEqual(updates[share.VIEW_KEY], "atlas")
        self.assertEqual(updates[share.COUNTRY_KEY], "KEN")
        self.assertEqual(updates[share.CATEGORY_KEY], "spending")       # derived from the indicator
        self.assertEqual(updates[share.indicator_key("spending")], "military_gdp")
        self.assertEqual(updates[share.PENDING_DEBTOR_KEY], "KEN")

    def test_invalid_values_are_ignored(self):
        updates = share.parse_query({"view": "hack", "country": "ZZZ", "indicator": "nope", "item": "x"},
                                    countries=COUNTRIES, spending_items=ITEMS)
        self.assertEqual(updates, {})

    def test_build_query_per_view(self):
        state = {share.VIEW_KEY: "atlas", share.COUNTRY_KEY: "KEN", share.CATEGORY_KEY: "debt",
                 share.indicator_key("debt"): "gov_debt_gdp"}
        self.assertEqual(share.build_query(state),
                         {"view": "atlas", "country": "KEN", "category": "debt", "indicator": "gov_debt_gdp"})
        self.assertEqual(share.build_query({share.VIEW_KEY: "debt", share.COUNTRY_KEY: "KEN", "gde_debtor": "CHN"}),
                         {"view": "debt", "country": "CHN"})
        self.assertEqual(share.build_query({share.VIEW_KEY: "spending", share.COUNTRY_KEY: "USA",
                                            share.SPENDING_ITEM_KEY: "health"}),
                         {"view": "spending", "country": "USA", "item": "health"})
        self.assertEqual(share.build_query({share.VIEW_KEY: "developers", share.COUNTRY_KEY: "USA"}), {"view": "developers"})

    def test_round_trip(self):
        state = {share.VIEW_KEY: "spending", share.COUNTRY_KEY: "USA", share.SPENDING_ITEM_KEY: "military"}
        parsed = share.parse_query(share.build_query(state), countries=COUNTRIES, spending_items=ITEMS)
        for key, value in state.items():
            self.assertEqual(parsed[key], value)

    def test_applied_once_per_session(self):
        state: dict = {}
        share.apply_query_once(state, {"country": "KEN"}, countries=COUNTRIES, spending_items=ITEMS)
        state[share.COUNTRY_KEY] = "USA"  # user picks another country
        share.apply_query_once(state, {"country": "KEN"}, countries=COUNTRIES, spending_items=ITEMS)
        self.assertEqual(state[share.COUNTRY_KEY], "USA")

    def test_sync_writes_only_when_changed_and_share_url(self):
        params = FakeParams(view="atlas")
        wanted = share.sync_query(params, {share.VIEW_KEY: "debt", share.COUNTRY_KEY: "KEN"})
        self.assertEqual(dict(params), wanted)
        self.assertEqual(share.share_url("http://localhost:8501/?old=1", {"view": "debt", "country": "KEN"}),
                         "http://localhost:8501/?view=debt&country=KEN")
        self.assertEqual(share.share_url(None, {"view": "atlas"}), "?view=atlas")


if __name__ == "__main__":
    unittest.main()
