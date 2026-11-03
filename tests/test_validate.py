from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src import validate


def _write(directory: Path, latest_rows: list[dict], ts_rows: list[dict], spending_rows: list[dict] | None = None) -> None:
    pd.DataFrame(latest_rows).to_csv(directory / "latest_values.csv", index=False)
    pd.DataFrame(ts_rows).to_csv(directory / "timeseries.csv.gz", index=False, compression="gzip")
    if spending_rows is not None:
        pd.DataFrame(spending_rows).to_csv(directory / "spending_usd.csv", index=False)


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reference_pass_and_fail(self):
        latest = [
            {"iso3": "USA", "indicator_id": "military_usd", "value": 997e9, "year": 2024},
            {"iso3": "JPN", "indicator_id": "gov_debt_gdp", "value": 65.9, "year": 2026},  # wrong: should be ~235
        ]
        ts = [{"iso3": "USA", "series_code": "MS.MIL.XPND.CD", "year": 2024}]
        _write(self.dir, latest, ts)
        findings, _ = validate.run(self.dir, current_year=2026)
        messages = {f.message.split(" =")[0]: f.status for f in findings if f.area == "reference" and "=" in f.message}
        self.assertEqual(messages["USA military_usd"], validate.PASS)
        self.assertEqual(messages["JPN gov_debt_gdp"], validate.FAIL)

    def test_empty_series_and_future_years_fail(self):
        latest = [{"iso3": "USA", "indicator_id": "gdp_growth", "value": 2.0, "year": 2031}]
        _write(self.dir, latest, [{"iso3": "USA", "series_code": "NGDP_RPCH", "year": 2031}])
        findings, coverage = validate.run(self.dir, current_year=2026)
        self.assertTrue(any(f.status == validate.FAIL and "dated after 2026" in f.message for f in findings))
        self.assertEqual(coverage.set_index("series").loc["MS.MIL.XPND.CD", "status"], validate.FAIL)

    def test_spending_military_must_match_sipri_series(self):
        latest = [{"iso3": "USA", "indicator_id": "military_usd", "value": 997e9, "year": 2024}]
        spending = [{"iso3": "USA", "item_id": "military", "usd": 900e9, "pct_gdp": 3.0,
                     "method": "Reported in US$ by the source"}]
        _write(self.dir, latest, [{"iso3": "USA", "series_code": "MS.MIL.XPND.CD", "year": 2024}], spending)
        findings, _ = validate.run(self.dir, current_year=2026)
        self.assertTrue(any(f.status == validate.FAIL and "match SIPRI" in f.message for f in findings))

    def test_missing_exports_gives_clear_message(self):
        self.assertEqual(validate.main(["--dir", str(self.dir / "nope")]), 2)


if __name__ == "__main__":
    unittest.main()


class SpendingSignTests(unittest.TestCase):
    def test_negative_lines_fail_and_zero_lines_warn_with_country_names(self):
        import tempfile as _t
        with _t.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            spending = [
                {"iso3": "CRI", "item_id": "military", "usd": 0.0, "pct_gdp": 0.0, "year": 2024, "method": "x", "sources": "SIPRI"},
                {"iso3": "FRA", "item_id": "health", "usd": 0.0, "pct_gdp": 0.0, "year": 2024, "method": "x", "sources": "WHO"},
                {"iso3": "XYZ", "item_id": "interest", "usd": -5e6, "pct_gdp": -0.1, "year": 2024, "method": "x", "sources": "IMF ie"},
            ]
            _write(directory, [{"iso3": "USA", "indicator_id": "gdp_growth", "value": 2.0, "year": 2024}],
                   [{"iso3": "USA", "series_code": "NGDP_RPCH", "year": 2024}], spending)
            findings, _ = validate.run(directory, current_year=2026)
            negative = next(f for f in findings if "negative spending" in f.message)
            zero = next(f for f in findings if "exactly 0" in f.message)
            self.assertEqual((negative.status, zero.status), (validate.FAIL, validate.WARN))
            self.assertIn("XYZ interest", negative.message)
            self.assertIn("FRA health", zero.message)          # unexpected zero -> warning
            known = next(f for f in findings if f.message.startswith("Known zeros"))
            self.assertEqual(known.status, validate.PASS)         # Costa Rica has no army -> expected
            self.assertIn("CRI military", known.message)
