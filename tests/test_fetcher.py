"""Tests for the World Bank IDS four-dimensional API parser."""

import unittest

from unittest.mock import patch

from src.fetcher import (
    _chunk_counterpart_codes,
    _dimension_items_from_payload_for_test,
    _parse_api_observations,
    _source_object,
    WorldBankAPIError,
    fetch_debtor_via_api,
)


class FetcherTests(unittest.TestCase):
    def test_source_object_accepts_list_and_dict_envelopes(self):
        source = {"id": 6, "data": []}
        self.assertEqual(_source_object({"source": source}), source)
        self.assertEqual(_source_object({"source": [source]}), source)

    def test_dimension_metadata_extracts_codes_and_names(self):
        payload = {
            "source": [{
                "id": 6,
                "concept": [{
                    "variable": [
                        {"id": "AGO", "value": "Angola"},
                        {"id": "PAK", "value": "Pakistan"},
                    ]
                }],
            }]
        }
        self.assertEqual(
            _dimension_items_from_payload_for_test(payload),
            [{"id": "AGO", "name": "Angola"}, {"id": "PAK", "name": "Pakistan"}],
        )

    def test_parse_all_counterpart_area_observations(self):
        rows = [
            {
                "value": 1_250_000_000,
                "variable": [
                    {"id": "701", "value": "Japan"},
                    {"id": "2023", "value": "2023"},
                ],
            },
            {
                "value": 500_000_000,
                "variable": [
                    {"id": "001", "value": "Austria"},
                    {"id": "2023", "value": "2023"},
                ],
            },
            {
                "value": 1_750_000_000,
                "variable": [
                    {"id": "WLD", "value": "World"},
                    {"id": "2023", "value": "2023"},
                ],
            },
            {
                "value": None,
                "variable": [
                    {"id": "701", "value": "Japan"},
                    {"id": "2023", "value": "2023"},
                ],
            },
        ]
        parsed = _parse_api_observations(
            rows,
            debtor_code="AGO",
            debtor_name="Angola",
            counterpart_lookup={"701": "Japan", "001": "Austria"},
        )
        self.assertEqual(len(parsed), 2)
        self.assertEqual({row["creditor_id"] for row in parsed}, {"701", "001"})
        self.assertEqual({row["year"] for row in parsed}, {2023})
        self.assertEqual(sum(row["amount_usd"] for row in parsed), 1_750_000_000)
        self.assertTrue(all(row["debtor_iso3"] == "AGO" for row in parsed))

    def test_parse_real_ids_response_with_concept_names(self):
        rows = [{
            "variable": [
                {"concept": "Counterpart-Area", "id": "701", "value": "Japan"},
                {"concept": "Time", "id": "YR2024", "value": "2024"},
            ],
            "value": 265015376.9,
        }]
        parsed = _parse_api_observations(
            rows,
            debtor_code="AGO",
            debtor_name="Angola",
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["creditor_id"], "701")
        self.assertEqual(parsed[0]["creditor_name"], "Japan")
        self.assertEqual(parsed[0]["year"], 2024)
        self.assertAlmostEqual(parsed[0]["amount_usd"], 265015376.9)

    def test_counterpart_codes_are_chunked_without_exceeding_path_limit(self):
        codes = [f"C{i:03d}" for i in range(30)]
        chunks = _chunk_counterpart_codes(codes, max_segment_chars=19)
        self.assertGreater(len(chunks), 1)
        self.assertEqual([code for chunk in chunks for code in chunk], codes)
        self.assertTrue(all(len(";".join(chunk)) <= 19 for chunk in chunks))

    def test_counterpart_batches_are_capped_at_50_codes(self):
        codes = [f"{i:03d}" for i in range(121)]
        chunks = _chunk_counterpart_codes(codes)
        self.assertEqual([len(chunk) for chunk in chunks], [50, 50, 21])
        self.assertEqual([code for chunk in chunks for code in chunk], codes)

    def test_fetch_recursively_splits_batch_after_http_400(self):
        payload_for = lambda code: {
            "source": [{
                "pages": 1,
                "data": [{
                    "variable": [
                        {"concept": "Counterpart-Area", "id": code, "value": {"701": "Japan", "730": "China", "740": "Germany"}[code]},
                        {"concept": "Time", "id": "YR2024", "value": "2024"},
                    ],
                    "value": 100.0,
                }],
            }]
        }

        def fake_get_json(url, *, params=None, **kwargs):
            segment = url.split("/counterpart-area/", 1)[1].split("/time/", 1)[0]
            codes = segment.split(";")
            if len(codes) > 1:
                raise WorldBankAPIError(400, url, "test batch limit")
            return payload_for(codes[0])

        with patch("src.fetcher._get_json", side_effect=fake_get_json) as get_json:
            rows = fetch_debtor_via_api(
                "AGO", "Angola",
                {"701": "Japan", "730": "China", "740": "Germany"},
                ["701", "730", "740"], start_year=2000, end_year=2026,
            )
        self.assertEqual({row["creditor_id"] for row in rows}, {"701", "730", "740"})
        self.assertEqual(len(rows), 3)
        self.assertEqual(get_json.call_count, 5)

    def test_fetch_uses_semicolon_delimited_counterpart_codes(self):
        payload = {
            "source": [{
                "pages": 1,
                "data": [{
                    "variable": [
                        {"concept": "Counterpart-Area", "id": "701", "value": "Japan"},
                        {"concept": "Time", "id": "YR2024", "value": "2024"},
                    ],
                    "value": 265015376.9,
                }],
            }]
        }
        with patch("src.fetcher._get_json", return_value=payload) as get_json:
            rows = fetch_debtor_via_api(
                "AGO", "Angola", {"701": "Japan", "730": "China"}, ["701", "730"],
                start_year=2000, end_year=2026,
            )
        self.assertEqual(len(rows), 1)
        requested_url = get_json.call_args.args[0]
        self.assertIn("/counterpart-area/701;730/time/all", requested_url)
        self.assertNotIn("2000:2026", requested_url)

    def test_unqueryable_single_counterpart_is_skipped_after_split(self):
        def fake_get_json(url, *, params=None, **kwargs):
            segment = url.split("/counterpart-area/", 1)[1].split("/time/", 1)[0]
            codes = segment.split(";")
            if len(codes) > 1:
                raise WorldBankAPIError(404, url, "legacy route cannot resolve batch")
            if codes[0] == "999":
                raise WorldBankAPIError(404, url, "counterpart area not queryable")
            return {"pages": 1, "source": [{"data": [{
                "variable": [
                    {"concept": "Counterpart-Area", "id": "701", "value": "Japan"},
                    {"concept": "Time", "id": "YR2024", "value": "2024"},
                ], "value": 123.0,
            }]}]}
        with patch("src.fetcher._get_json", side_effect=fake_get_json):
            rows = fetch_debtor_via_api(
                "AGO", "Angola", {"701": "Japan", "999": "Unknown"}, ["701", "999"],
                start_year=2000, end_year=2026,
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["creditor_id"], "701")

    def test_fetch_uses_time_all_and_filters_years_locally(self):
        payload = {"pages": 1, "source": [{
            "data": [
                {"variable": [
                    {"concept": "Counterpart-Area", "id": "701", "value": "Japan"},
                    {"concept": "Time", "id": "YR1999", "value": "1999"},
                ], "value": 10.0},
                {"variable": [
                    {"concept": "Counterpart-Area", "id": "701", "value": "Japan"},
                    {"concept": "Time", "id": "YR2000", "value": "2000"},
                ], "value": 20.0},
                {"variable": [
                    {"concept": "Counterpart-Area", "id": "701", "value": "Japan"},
                    {"concept": "Time", "id": "YR2024", "value": "2024"},
                ], "value": 30.0},
                {"variable": [
                    {"concept": "Counterpart-Area", "id": "701", "value": "Japan"},
                    {"concept": "Time", "id": "YR2027", "value": "2027"},
                ], "value": 40.0},
            ]
        }]}
        with patch("src.fetcher._get_json", return_value=payload) as get_json:
            rows = fetch_debtor_via_api(
                "AGO", "Angola", {"701": "Japan"}, ["701"],
                start_year=2000, end_year=2026,
            )
        self.assertEqual([row["year"] for row in rows], [2000, 2024])
        self.assertIn("/time/all", get_json.call_args.args[0])

    def test_fetch_recursively_splits_batch_after_http_404(self):
        names = {"701": "Japan", "730": "China", "740": "Germany"}
        def payload_for(code):
            return {"pages": 1, "source": [{"data": [{
                "variable": [
                    {"concept": "Counterpart-Area", "id": code, "value": names[code]},
                    {"concept": "Time", "id": "YR2024", "value": "2024"},
                ], "value": 100.0,
            }]}]}
        def fake_get_json(url, *, params=None, **kwargs):
            segment = url.split("/counterpart-area/", 1)[1].split("/time/", 1)[0]
            codes = segment.split(";")
            if len(codes) > 1:
                raise WorldBankAPIError(404, url, "test legacy router batch limit")
            return payload_for(codes[0])
        with patch("src.fetcher._get_json", side_effect=fake_get_json):
            rows = fetch_debtor_via_api(
                "AGO", "Angola", names, ["701", "730", "740"],
                start_year=2000, end_year=2026,
            )
        self.assertEqual({row["creditor_id"] for row in rows}, set(names))
        self.assertEqual(len(rows), 3)

    def test_fetch_follows_api_pagination(self):
        def response_for_page(url, *, params=None, **kwargs):
            year = 2023 if params["page"] == 1 else 2024
            amount = 100.0 if params["page"] == 1 else 200.0
            return {"source": [{
                "pages": 2,
                "data": [{
                    "variable": [
                        {"concept": "Counterpart-Area", "id": "701", "value": "Japan"},
                        {"concept": "Time", "id": f"YR{year}", "value": str(year)},
                    ],
                    "value": amount,
                }],
            }]}

        with patch("src.fetcher._get_json", side_effect=response_for_page) as get_json:
            rows = fetch_debtor_via_api(
                "AGO", "Angola", {"701": "Japan"}, ["701"],
                start_year=2000, end_year=2026,
            )
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["year"] for row in rows}, {2023, 2024})
        self.assertEqual(get_json.call_count, 2)
        self.assertEqual(get_json.call_args_list[0].kwargs["params"]["page"], 1)
        self.assertEqual(get_json.call_args_list[1].kwargs["params"]["page"], 2)

    def test_parse_supports_top_level_year_and_counterpart_fields(self):
        rows = [{
            "value": "1000000",
            "date": "2022",
            "counterpart-area-id": "701",
        }]
        parsed = _parse_api_observations(
            rows,
            debtor_code="AGO",
            debtor_name="Angola",
            counterpart_lookup={"701": "Japan"},
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["creditor_name"], "Japan")
        self.assertEqual(parsed[0]["year"], 2022)

    def test_non_numeric_and_missing_creditor_rows_are_skipped(self):
        rows = [
            {"value": "..", "variable": [{"id": "701", "value": "Japan"}, {"id": "2023", "value": "2023"}]},
            {"value": 100, "variable": [{"id": "2023", "value": "2023"}]},
        ]
        parsed = _parse_api_observations(rows, debtor_code="AGO", debtor_name="Angola")
        self.assertEqual(parsed, [])


if __name__ == "__main__":
    unittest.main()
