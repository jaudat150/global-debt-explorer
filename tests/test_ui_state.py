from __future__ import annotations

import unittest

from src.ui_state import apply_pending_creditor_selection, apply_pending_debtor_selection


class WidgetStateHandoffTests(unittest.TestCase):
    def test_map_debtor_selection_is_applied_before_widget_creation(self):
        state = {"gde_debtor": "AGO", "_gde_pending_debtor": "PAK"}
        apply_pending_debtor_selection(state, ["AGO", "PAK"])
        self.assertEqual(state["gde_debtor"], "PAK")
        self.assertTrue(state["_gde_reset_creditor"])
        self.assertNotIn("_gde_pending_debtor", state)

    def test_pending_creditor_is_applied_before_widget_creation(self):
        state = {"gde_creditor": "730", "_gde_pending_creditor": "701"}
        apply_pending_creditor_selection(state, ["701", "730"])
        self.assertEqual(state["gde_creditor"], "701")
        self.assertNotIn("_gde_pending_creditor", state)

    def test_debtor_change_resets_invalid_creditor_to_first_available(self):
        state = {
            "gde_creditor": "730",
            "_gde_pending_creditor": None,
            "_gde_reset_creditor": True,
        }
        apply_pending_creditor_selection(state, ["701", "250"])
        self.assertEqual(state["gde_creditor"], "701")
        self.assertNotIn("_gde_reset_creditor", state)

    def test_empty_creditor_options_produce_empty_widget_value(self):
        state = {"gde_creditor": "730"}
        apply_pending_creditor_selection(state, [])
        self.assertEqual(state["gde_creditor"], "")

    def test_invalid_debtor_is_replaced_with_available_debtor(self):
        state = {"gde_debtor": "OLD"}
        apply_pending_debtor_selection(state, ["AGO", "PAK"])
        self.assertEqual(state["gde_debtor"], "AGO")


if __name__ == "__main__":
    unittest.main()
