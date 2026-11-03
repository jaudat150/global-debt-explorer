"""Unit tests for normalization and debt aggregation."""

import tempfile
import unittest
from pathlib import Path

from src.processor import (
    aggregate_debt,
    clean_records,
    country_creditor_breakdown,
    creditor_totals,
    format_usd,
    read_cache,
    write_cache,
)


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "year": 2023, "debtor_iso3": "PAK", "debtor_name": "Pakistan",
                "creditor_id": "CHN", "creditor_iso3": "CHN", "creditor_name": "China",
                "amount_usd": 2_000_000_000,
            },
            {
                "year": 2023, "debtor_iso3": "PAK", "debtor_name": "Pakistan",
                "creditor_id": "USA", "creditor_iso3": "USA", "creditor_name": "United States",
                "amount_usd": 500_000_000,
            },
            {
                "year": 2023, "debtor_iso3": "EGY", "debtor_name": "Egypt",
                "creditor_id": "CHN", "creditor_iso3": "CHN", "creditor_name": "China",
                "amount_usd": 250_000_000,
            },
            {"year": "not-a-year", "debtor_iso3": "BAD", "creditor_id": "CHN", "amount_usd": 10},
            {"year": 2023, "debtor_iso3": "ARG", "creditor_id": "CHN", "amount_usd": -3},
        ]

    def test_clean_records_drops_invalid_rows(self):
        frame = clean_records(self.records)
        self.assertEqual(len(frame), 3)
        self.assertIn("creditor_id", frame.columns)

    def test_aggregation_and_creditor_totals(self):
        frame = clean_records(self.records)
        debtors = aggregate_debt(frame, 2023)
        self.assertEqual(debtors.iloc[0]["debtor_iso3"], "PAK")
        self.assertEqual(debtors.iloc[0]["amount_usd"], 2_500_000_000)
        creditors = creditor_totals(frame, 2023)
        self.assertEqual(creditors.iloc[0]["creditor_iso3"], "CHN")
        self.assertEqual(creditors.iloc[0]["amount_usd"], 2_250_000_000)

    def test_country_breakdown_sums_to_100_percent(self):
        frame = clean_records(self.records)
        breakdown = country_creditor_breakdown(frame, 2023, "PAK")
        self.assertAlmostEqual(float(breakdown["share_pct"].sum()), 100.0)
        self.assertEqual(breakdown.iloc[0]["creditor_name"], "China")

    def test_format_usd(self):
        self.assertEqual(format_usd(1_250_000_000), "$1.25B")
        self.assertEqual(format_usd(2_500_000), "$2.5M")
        self.assertEqual(format_usd(500), "$500")

    def test_cache_round_trip(self):
        records = clean_records(self.records)
        import pandas as pd
        payload = {"metadata": {"status": "success"}, "records": records.to_dict(orient="records")}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            write_cache(payload, path)
            loaded = read_cache(path)
        self.assertEqual(loaded["metadata"]["status"], "success")
        self.assertEqual(len(loaded["records"]), 3)


if __name__ == "__main__":
    unittest.main()
