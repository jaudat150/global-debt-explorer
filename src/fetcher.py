"""Fetch debtor-by-creditor observations from the World Bank IDS API.

The IDS database has four dimensions: debtor country, indicator, year, and
creditor (``counterpart-area``). The World Bank API accepts semicolon-delimited
values for a dimension. This module uses that documented multi-value syntax to
request many creditor areas together for each debtor, with pagination and URL-
length-aware batches. It does not use ``counterpart-area/all`` as a wildcard.

The second half of the module holds the live clients for the national debt
profile layer (Tier 1: World Bank V2 ``DT.DOD.DLXF.CD``; Tier 2: IMF DataMapper
``GG_DEBT_GDP``). Tier resolution, caching and the local fallback live in
:mod:`src.processor`.

Run from the repository root:
    python -m src.fetcher

Useful smoke test:
    python -m src.fetcher --debtor AGO
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from src.processor import clean_records, frame_to_records, read_cache, utc_now_iso, write_cache

LOGGER = logging.getLogger("global_debt_explorer.fetcher")

IDS_SOURCE_ID = 6
INDICATOR_CODE = "DT.DOD.BLAT.CD"
INDICATOR_NAME = "PPG, bilateral (DOD, current US$)"
API_ROOT = f"https://api.worldbank.org/v2/sources/{IDS_SOURCE_ID}"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "bilateral_debt_cache.json"
DEFAULT_TIMEOUT = (20.0, 90.0)
DEFAULT_PER_PAGE = 10_000
DEFAULT_START_YEAR = 2000
MAX_PATH_SEGMENT_CHARS = 1_400
# Keep requests bounded. The API has accepted a semicolon-delimited list of
# counterpart-area IDs, but some larger requests may be rejected by its legacy
# routing layer; those batches are recursively split on HTTP 400/404.
MAX_COUNTERPART_CODES_PER_BATCH = 50
DEFAULT_WORKERS = 4
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
AGGREGATE_COUNTERPART_NAMES = {
    "world", "all", "all countries", "all creditors", "all counterpart areas",
    "total", "total creditors", "total bilateral", "bilateral",
}

CREDITOR_NAME_ALIASES = {
    "china, p.r.": "CHN",
    "china, pr": "CHN",
    "china": "CHN",
    "united states": "USA",
    "united states of america": "USA",
    "korea, rep.": "KOR",
    "korea, rep. of": "KOR",
    "korea, dem. people's rep.": "PRK",
    "iran, islamic rep.": "IRN",
    "egypt, arab rep.": "EGY",
    "venezuela, rb": "VEN",
    "russian federation": "RUS",
    "syrian arab republic": "SYR",
    "lao pdr": "LAO",
    "kyrgyz republic": "KGZ",
    "slovak republic": "SVK",
    "czech republic": "CZE",
    "congo, dem. rep.": "COD",
    "congo, rep.": "COG",
    "gambia, the": "GMB",
    "bahamas, the": "BHS",
    "cote d'ivoire": "CIV",
    "côte d'ivoire": "CIV",
    "west bank and gaza": "PSE",
    "kosovo": "XKX",
    "hong kong sar, china": "HKG",
    "macao sar, china": "MAC",
    "germany, fed. rep. of": "DEU",
    "germany, fed.rep. of": "DEU",
    "turkey": "TUR",
    "türkiye": "TUR",
}

_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_MIN_REQUEST_INTERVAL = 0.12


def _name_to_iso3(name: str) -> str:
    """Map an exact creditor-area label to ISO-3; leave institutions unmapped."""
    cleaned = str(name or "").strip()
    if not cleaned:
        return ""
    alias = CREDITOR_NAME_ALIASES.get(cleaned.casefold())
    if alias:
        return alias

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.casefold())

    target = normalize(cleaned)
    try:
        import pycountry

        for country in pycountry.countries:
            possible_names = (
                getattr(country, "name", ""),
                getattr(country, "official_name", ""),
                getattr(country, "common_name", ""),
            )
            if target in {normalize(value) for value in possible_names if value}:
                return country.alpha_3
    except ImportError:
        LOGGER.warning("pycountry is unavailable; creditor ISO-3 mapping will be limited")
    return ""


class WorldBankAPIError(RuntimeError):
    """Non-retryable HTTP error returned by the World Bank API."""

    def __init__(self, status_code: int, url: str, body: str = "") -> None:
        self.status_code = int(status_code)
        self.url = url
        self.body = (body or "").strip()[:800]
        detail = f"World Bank API HTTP {self.status_code} for {self.url}"
        if self.body:
            detail += f"; response: {self.body}"
        super().__init__(detail)


def _source_object(payload: Any) -> dict[str, Any]:
    """Return the IDS source object from its JSON envelope."""
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected World Bank API response type: {type(payload).__name__}")
    source = payload.get("source")
    if isinstance(source, list):
        source = source[0] if source else None
    if not isinstance(source, dict):
        detail = payload.get("message") or payload.get("messages") or payload
        raise RuntimeError(f"World Bank IDS API returned no source object: {detail}")
    return source


def _throttle() -> None:
    """Apply a small process-wide pause between requests, including worker threads."""
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        now = time.monotonic()
        delay = _MIN_REQUEST_INTERVAL - (now - _LAST_REQUEST_AT)
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST_AT = time.monotonic()


def _get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = 4,
) -> dict[str, Any]:
    """GET JSON with bounded retry/backoff for transient API/network failures."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "GlobalBilateralDebtExplorer/2.1 (open-data research project)",
    }
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        _throttle()
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt < retries - 1:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait = min(60.0, max(1.0, float(retry_after))) if retry_after else min(30.0, 1.5 ** attempt)
                    except ValueError:
                        wait = min(30.0, 1.5 ** attempt)
                    LOGGER.warning(
                        "World Bank API returned HTTP %s; retrying in %.1fs (%s/%s)",
                        response.status_code, wait, attempt + 1, retries,
                    )
                    time.sleep(wait)
                    continue
            if response.status_code >= 400:
                raise WorldBankAPIError(
                    response.status_code, response.url, response.text
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("World Bank API returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise RuntimeError("World Bank API returned an unexpected JSON document")
            if payload.get("message") and not payload.get("source"):
                raise RuntimeError(f"World Bank API error: {payload['message']}")
            return payload
        except WorldBankAPIError:
            # Preserve the status code so a caller can adaptively split a
            # rejected multi-creditor request instead of losing this detail.
            raise
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            retryable = isinstance(exc, requests.RequestException)
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                retryable = exc.response.status_code in RETRYABLE_STATUS_CODES
            if not retryable or attempt >= retries - 1:
                break
            wait = min(30.0, 1.5 ** attempt)
            LOGGER.warning("World Bank API request failed (%s); retrying in %.1fs", exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"World Bank API request failed after {retries} attempts: {last_error}")


def _dimension_items_from_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Extract ``id``/``value`` dimension entries from an IDS source envelope."""
    source = _source_object(payload)
    concepts = source.get("concept", [])
    if isinstance(concepts, dict):
        concepts = [concepts]

    items: list[dict[str, str]] = []
    for concept in concepts if isinstance(concepts, list) else []:
        if not isinstance(concept, dict):
            continue
        variables = concept.get("variable", [])
        if isinstance(variables, dict):
            variables = [variables]
        for variable in variables if isinstance(variables, list) else []:
            if not isinstance(variable, dict):
                continue
            code = str(variable.get("id") or "").strip()
            name = str(variable.get("value") or "").strip()
            if code and name:
                items.append({"id": code, "name": name})

    # Some source responses nest the variable list a level differently.
    if not items:
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                if value.get("id") is not None and value.get("value") is not None:
                    code, name = str(value["id"]).strip(), str(value["value"]).strip()
                    if code and name:
                        items.append({"id": code, "name": name})
                for nested in value.values():
                    walk(nested)
            elif isinstance(value, list):
                for nested in value:
                    walk(nested)
        walk(source.get("concept", source))

    deduped: dict[str, dict[str, str]] = {}
    for item in items:
        deduped[item["id"]] = item
    return list(deduped.values())


def _dimension_items_from_payload_for_test(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Backward-compatible test hook for validating metadata parsing."""
    return _dimension_items_from_payload(payload)


def _dimension_items(dimension: str, *, retries: int = 4) -> list[dict[str, str]]:
    """Load IDS debtor or counterpart-area codes from source metadata."""
    if dimension not in {"country", "counterpart-area"}:
        raise ValueError(f"Unsupported IDS dimension: {dimension}")
    url = f"{API_ROOT}/{dimension}"
    payload = _get_json(url, params={"format": "json", "per_page": 500}, retries=retries)
    result = _dimension_items_from_payload(payload)
    if not result:
        raise RuntimeError(f"World Bank returned no {dimension} dimension codes")
    return result


def _as_variable_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _year_from_variable(variable: dict[str, Any]) -> int | None:
    """Return a year if either dimension ID or label encodes one."""
    for candidate in (variable.get("id"), variable.get("value"), variable.get("name")):
        if candidate is None:
            continue
        match = re.fullmatch(r"\s*((?:19|20)\d{2})\s*", str(candidate))
        if match:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
    return None


def _parse_api_observations(
    rows: list[dict[str, Any]],
    *,
    debtor_code: str,
    debtor_name: str,
    counterpart_lookup: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Parse the World Bank response into normalized debtor-creditor rows.

    For the IDS endpoint, the response's ``variable`` array contains the
    remaining dimensions, normally ``Counterpart-Area`` and ``Time``. We
    identify those dimensions by their concept names where available, with a
    year-value fallback for older response variants.
    """
    counterpart_lookup = counterpart_lookup or {}
    records: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        raw_amount = item.get("value")
        if raw_amount is None or str(raw_amount).strip().casefold() in {"", "..", "nan", "null", "na"}:
            continue
        try:
            amount = float(str(raw_amount).replace(",", "").strip())
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(amount) or amount < 0:
            continue

        variables = _as_variable_list(item.get("variable") or item.get("variables"))
        year: int | None = None
        year_index: int | None = None
        creditor_variable: dict[str, Any] | None = None

        for index, variable in enumerate(variables):
            concept = str(variable.get("concept") or "").strip().casefold()
            if concept in {"time", "year", "date"}:
                year = _year_from_variable(variable)
                year_index = index
            elif concept in {"counterpart-area", "counterpart area", "counterpart_area"}:
                creditor_variable = variable

        # Some IDS responses omit concept labels from individual variables.
        if year is None:
            for index, variable in enumerate(variables):
                candidate_year = _year_from_variable(variable)
                if candidate_year is not None:
                    year = candidate_year
                    year_index = index
                    break
        if creditor_variable is None:
            for index, variable in enumerate(variables):
                if index != year_index:
                    creditor_variable = variable
                    break

        if year is None:
            raw_date = item.get("date") or item.get("time") or item.get("year")
            match = re.search(r"(?:19|20)\d{2}", str(raw_date or ""))
            if match:
                year = int(match.group(0))
        if year is None:
            continue

        creditor_id = ""
        creditor_name = ""
        if creditor_variable is not None:
            creditor_id = str(creditor_variable.get("id") or "").strip()
            creditor_name = str(
                creditor_variable.get("value") or creditor_variable.get("name") or ""
            ).strip()
        if not creditor_id:
            creditor_id = str(item.get("counterpart-area-id") or item.get("counterpartAreaId") or "").strip()
        if not creditor_name:
            creditor_name = str(item.get("counterpart-area") or item.get("counterpartArea") or "").strip()
        if creditor_id and not creditor_name:
            creditor_name = counterpart_lookup.get(creditor_id, creditor_id)
        if creditor_name and not creditor_id:
            creditor_id = creditor_name
        if not creditor_id or not creditor_name:
            continue

        normalized_name = creditor_name.casefold().strip()
        if creditor_id.casefold() in {"wld", "world", "all"} or normalized_name in AGGREGATE_COUNTERPART_NAMES:
            continue

        records.append({
            "year": year,
            "debtor_iso3": debtor_code.upper(),
            "debtor_name": debtor_name,
            "creditor_id": creditor_id,
            "creditor_iso3": _name_to_iso3(creditor_name),
            "creditor_name": creditor_name,
            "amount_usd": amount,
        })
    return records


def _chunk_counterpart_codes(
    counterpart_codes: list[str],
    *,
    max_segment_chars: int = MAX_PATH_SEGMENT_CHARS,
    max_codes_per_batch: int = MAX_COUNTERPART_CODES_PER_BATCH,
) -> list[list[str]]:
    """Split creditor codes by both path length and number of values.

    The World Bank documents a 1,500-character path-segment ceiling. In
    practice, the IDS counterpart-area endpoint can reject a semicolon list
    containing hundreds of values even when it fits under that ceiling. A
    conservative 50-code batch avoids that failure mode.
    """
    if max_segment_chars < 1:
        raise ValueError("max_segment_chars must be positive")
    if max_codes_per_batch < 1:
        raise ValueError("max_codes_per_batch must be positive")

    chunks: list[list[str]] = []
    current: list[str] = []
    current_length = 0
    for raw_code in counterpart_codes:
        code = str(raw_code).strip()
        if not code:
            continue
        if ";" in code or "/" in code:
            raise ValueError(f"Invalid counterpart-area code for URL path: {code!r}")
        added_length = len(code) + (1 if current else 0)
        if len(code) > max_segment_chars:
            raise ValueError(f"Counterpart-area code exceeds URL segment limit: {code!r}")
        if current and (
            current_length + added_length > max_segment_chars
            or len(current) >= max_codes_per_batch
        ):
            chunks.append(current)
            current = [code]
            current_length = len(code)
        else:
            current.append(code)
            current_length += added_length
    if current:
        chunks.append(current)
    return chunks


def _fetch_counterpart_batch(
    *,
    debtor_code: str,
    debtor_name: str,
    counterpart_lookup: dict[str, str],
    code_batch: list[str],
    start_year: int,
    end_year: int,
    per_page: int,
    timeout: tuple[float, float],
    retries: int,
) -> list[dict[str, Any]]:
    """Fetch one creditor batch; recursively split it if the API rejects it.

    Adaptive splitting protects the pipeline from undocumented endpoint
    limits and from a problematic metadata item. A 400 on a one-code request
    is raised with the World Bank response body for diagnosis.
    """
    counterpart_segment = ";".join(code_batch)
    # IDS Time dimension IDs are YR1970, YR1971, ... . A numeric path range
    # such as time/2000:2026 is not a valid dimension-value range for this
    # source and can be rewritten by the API to a legacy service URL (404).
    # Use the known-good time/all route, then filter years locally below.
    url = (
        f"{API_ROOT}/country/{debtor_code.upper()}/series/{INDICATOR_CODE}"
        f"/counterpart-area/{counterpart_segment}/time/all"
    )
    try:
        records: list[dict[str, Any]] = []
        page = 1
        total_pages = 1
        while page <= total_pages:
            payload = _get_json(
                url,
                params={"format": "json", "per_page": per_page, "page": page},
                timeout=timeout,
                retries=retries,
            )
            source = _source_object(payload)
            data = source.get("data", [])
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                raise RuntimeError(
                    f"Unexpected IDS data payload for debtor {debtor_code}: {type(data).__name__}"
                )
            # The World Bank JSON envelope normally stores pagination at the
            # top level, not inside source. Accept either shape for resilience.
            try:
                total_pages = int(payload.get("pages") or source.get("pages") or 1)
            except (TypeError, ValueError):
                total_pages = 1
            total_pages = max(1, total_pages)
            parsed_page = _parse_api_observations(
                [row for row in data if isinstance(row, dict)],
                debtor_code=debtor_code,
                debtor_name=debtor_name,
                counterpart_lookup=counterpart_lookup,
            )
            records.extend(
                row for row in parsed_page
                if int(start_year) <= int(row["year"]) <= int(end_year)
            )
            page += 1
        return records
    except WorldBankAPIError as exc:
        if exc.status_code not in {400, 404}:
            raise
        if len(code_batch) == 1:
            # Some counterpart-area metadata values are not exposed by the
            # data route for this indicator. Keep other creditor observations
            # and report the exact omitted dimension value in the log.
            LOGGER.warning(
                "%s: skipping non-queryable counterpart-area %s (%s), HTTP %s",
                debtor_code, code_batch[0], counterpart_lookup.get(code_batch[0], "unknown"),
                exc.status_code,
            )
            return []
        midpoint = len(code_batch) // 2
        left, right = code_batch[:midpoint], code_batch[midpoint:]
        LOGGER.warning(
            "%s: API rejected a %s-creditor batch (HTTP %s); retrying as %s + %s creditor batches",
            debtor_code, len(code_batch), exc.status_code, len(left), len(right),
        )
        return (
            _fetch_counterpart_batch(
                debtor_code=debtor_code, debtor_name=debtor_name,
                counterpart_lookup=counterpart_lookup, code_batch=left,
                start_year=start_year, end_year=end_year, per_page=per_page,
                timeout=timeout, retries=retries,
            )
            + _fetch_counterpart_batch(
                debtor_code=debtor_code, debtor_name=debtor_name,
                counterpart_lookup=counterpart_lookup, code_batch=right,
                start_year=start_year, end_year=end_year, per_page=per_page,
                timeout=timeout, retries=retries,
            )
        )


def fetch_debtor_via_api(
    debtor_code: str,
    debtor_name: str,
    counterpart_lookup: dict[str, str],
    counterpart_codes: list[str],
    *,
    per_page: int = DEFAULT_PER_PAGE,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int | None = None,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = 4,
) -> list[dict[str, Any]]:
    """Fetch listed creditor areas for one debtor using bounded semicolon batches.

    The default observation window starts in 2000 and extends through the
    current UTC year. Set ``start_year=1960`` to request more history.

    ``counterpart-area/all`` is not treated as an all-creditors wildcard. The
    selected codes are joined with semicolons. Requests are limited to 50
    counterpart codes and split further if the API returns HTTP 400 or 404.
    IDS years use YR-prefixed dimension IDs, so the API is queried with
    ``time/all`` and the requested year window is filtered locally.
    """
    if not counterpart_codes:
        raise ValueError("No counterpart-area codes were supplied")
    resolved_end_year = int(end_year or datetime.now(timezone.utc).year)
    if int(start_year) > resolved_end_year:
        raise ValueError("start_year cannot be greater than end_year")
    code_batches = _chunk_counterpart_codes(counterpart_codes)
    all_records: list[dict[str, Any]] = []

    for batch_number, code_batch in enumerate(code_batches, start=1):
        batch_records = _fetch_counterpart_batch(
            debtor_code=debtor_code,
            debtor_name=debtor_name,
            counterpart_lookup=counterpart_lookup,
            code_batch=code_batch,
            start_year=int(start_year),
            end_year=resolved_end_year,
            per_page=int(per_page),
            timeout=timeout,
            retries=retries,
        )
        all_records.extend(batch_records)
        LOGGER.debug(
            "%s: completed counterpart batch %s/%s (%s areas; %s non-null rows)",
            debtor_code, batch_number, len(code_batches), len(code_batch), len(batch_records),
        )

    return all_records


def _valid_debtor_codes(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep real ISO-3 debtor countries, excluding World Bank aggregates."""
    try:
        import pycountry
        known = {country.alpha_3 for country in pycountry.countries}
        known.update({"XKX", "PSE", "TWN", "HKG", "MAC"})
    except ImportError:
        known = set()
    result = []
    for item in items:
        code = item["id"].upper()
        if len(code) == 3 and code.isalpha() and (not known or code in known):
            result.append({"id": code, "name": item["name"]})
    return result


def download_and_parse(
    *,
    debtor: str | None = None,
    max_debtors: int | None = None,
    workers: int = DEFAULT_WORKERS,
    per_page: int = DEFAULT_PER_PAGE,
    start_year: int = DEFAULT_START_YEAR,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = 4,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Download the IDS debtor/creditor matrix using multi-code API requests.

    Returns (records, failed_debtor_codes, debtor_count). A single debtor failure
    does not discard successful observations; callers mark the cache partial.
    """
    current_year = datetime.now(timezone.utc).year
    if not 1900 <= int(start_year) <= current_year:
        raise ValueError(f"--start-year must be between 1900 and {current_year}")
    if int(per_page) < 1:
        raise ValueError("--per-page must be at least 1")

    debtors = _valid_debtor_codes(_dimension_items("country", retries=retries))
    counterpart_areas = _dimension_items("counterpart-area", retries=retries)
    counterpart_areas = [
        item for item in counterpart_areas
        if item["id"].strip().casefold() not in {"wld", "all"}
        and item["name"].strip().casefold() not in AGGREGATE_COUNTERPART_NAMES
    ]
    counterpart_lookup = {item["id"]: item["name"] for item in counterpart_areas}
    counterpart_codes = [item["id"] for item in counterpart_areas]
    code_batches = _chunk_counterpart_codes(counterpart_codes)

    if debtor:
        requested = debtor.strip().upper()
        debtors = [item for item in debtors if item["id"] == requested]
        if not debtors:
            raise ValueError(f"Debtor {requested!r} was not found in the IDS country dimension")
    if max_debtors is not None:
        if max_debtors < 1:
            raise ValueError("--max-debtors must be at least 1")
        debtors = debtors[:max_debtors]

    LOGGER.info(
        "Fetching IDS bilateral debt for %s debtors and %s counterpart areas "
        "(%s creditor-code batch(es) per debtor; max %s creditors per batch; %s workers; page size %s)",
        len(debtors), len(counterpart_areas), len(code_batches),
        MAX_COUNTERPART_CODES_PER_BATCH, max(1, workers), per_page,
    )
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    completed = 0
    max_workers = max(1, min(int(workers), 8))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                fetch_debtor_via_api,
                item["id"],
                item["name"],
                counterpart_lookup,
                counterpart_codes,
                per_page=per_page,
                start_year=start_year,
                timeout=timeout,
                retries=retries,
            ): item
            for item in debtors
        }
        for future in as_completed(futures):
            debtor_item = futures[future]
            code = debtor_item["id"]
            completed += 1
            try:
                debtor_records = future.result()
                records.extend(debtor_records)
                LOGGER.info(
                    "[%s/%s] %s: %s non-null bilateral observations",
                    completed, len(debtors), code, len(debtor_records),
                )
            except Exception as exc:
                failures.append(code)
                LOGGER.warning("[%s/%s] %s failed: %s", completed, len(debtors), code, exc)

    if not records:
        failed_summary = ", ".join(failures[:20]) if failures else "none"
        raise RuntimeError(
            "The IDS API returned no usable country-level creditor observations. "
            "Check that the selected debtor-creditor query is valid and that the response "
            f"contains non-null values. Failed debtor requests: {failed_summary}."
        )
    return records, failures, len(debtors)


def update_cache(
    cache_path: Path = DEFAULT_CACHE_PATH,
    *,
    debtor: str | None = None,
    max_debtors: int | None = None,
    workers: int = DEFAULT_WORKERS,
    per_page: int = DEFAULT_PER_PAGE,
    start_year: int = DEFAULT_START_YEAR,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
    retries: int = 4,
) -> dict[str, Any]:
    """Refresh cache atomically; preserve the last good cache on total failure."""
    try:
        current_year = datetime.now(timezone.utc).year
        if not 1900 <= int(start_year) <= current_year:
            raise ValueError(f"start_year must be between 1900 and {current_year}")
        if int(per_page) < 1:
            raise ValueError("per_page must be at least 1")
        records, failed_debtors, requested_debtors = download_and_parse(
            debtor=debtor,
            max_debtors=max_debtors,
            workers=workers,
            per_page=per_page,
            start_year=start_year,
            timeout=timeout,
            retries=retries,
        )
        frame = clean_records(records)
        if frame.empty:
            raise RuntimeError("World Bank IDS API returned no valid bilateral debt rows")

        years = sorted(frame["year"].unique().tolist())
        status = "partial" if failed_debtors else "success"
        metadata = {
            "project": "Global Bilateral Debt Explorer",
            "source_name": "World Bank International Debt Statistics",
            "source_id": IDS_SOURCE_ID,
            "indicator_code": INDICATOR_CODE,
            "indicator_name": INDICATOR_NAME,
            "unit": "current US dollars",
            "generated_at_utc": utc_now_iso(),
            "status": status,
            "latest_year": int(max(years)),
            "earliest_year": int(min(years)),
            "debtor_count": int(frame["debtor_iso3"].nunique()),
            "creditor_count": int(frame["creditor_id"].nunique()),
            "record_count": int(len(frame)),
            "requested_debtor_count": int(requested_debtors),
            "failed_debtors": failed_debtors,
            "download_method": "World Bank IDS four-dimensional API; semicolon-delimited counterpart-area codes, 50-code batches, adaptive HTTP-400/404 splitting, time/all query with local year filtering, and pagination",
            "observed_creditor_area_count": int(frame["creditor_id"].nunique()),
            "requested_start_year": int(start_year),
            "api_url_template": f"{API_ROOT}/country/{{debtor}}/series/{INDICATOR_CODE}/counterpart-area/{{creditor_code_1;creditor_code_2;...}}/time/all",
            "api_reference": "https://worldbank.github.io/debt-data/creditor-composition/creditor-data-python.html",
        }
        if failed_debtors:
            metadata["runtime_notice"] = (
                f"The World Bank API did not return data for {len(failed_debtors)} of "
                f"{requested_debtors} debtor requests. The map contains successful observations only."
            )
        cache = {"metadata": metadata, "records": frame_to_records(frame)}
        write_cache(cache, cache_path)
        LOGGER.info(
            "Saved %s observations: %s debtors, %s creditor areas, years %s–%s (status=%s)",
            len(frame), metadata["debtor_count"], metadata["creditor_count"],
            metadata["earliest_year"], metadata["latest_year"], status,
        )
        return cache
    except Exception as exc:
        try:
            old_cache = read_cache(cache_path)
        except (OSError, ValueError):
            old_cache = None
        if old_cache and old_cache.get("records"):
            LOGGER.error("Live refresh failed; keeping the previous cache: %s", exc)
            old_cache.setdefault("metadata", {})["runtime_notice"] = (
                f"The latest World Bank refresh failed ({exc}). Displaying the previous saved data instead."
            )
            return old_cache
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh World Bank IDS bilateral creditor data")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH, help="Destination JSON cache")
    parser.add_argument("--debtor", help="Fetch a single debtor country, e.g. AGO (smoke test)")
    parser.add_argument("--max-debtors", type=int, help="Limit number of debtor countries (smoke test)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent debtor requests (default: 4; max: 8)")
    parser.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE, help="API page size (default: 10000)")
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR, help="First observation year to download (default: 2000; use 1960 for full available history)")
    parser.add_argument("--timeout", type=float, default=90.0, help="Read timeout in seconds (default: 90)")
    parser.add_argument("--retries", type=int, default=4, help="Retries per API page (default: 4)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    limited_run = bool(args.debtor) or args.max_debtors is not None
    if limited_run and args.cache.resolve() == DEFAULT_CACHE_PATH.resolve():
        LOGGER.error(
            "Refusing to write a limited smoke-test dataset over the main cache. "
            "Pass --cache data/ids_smoke_test.json for a single-debtor or limited run."
        )
        return 2
    try:
        update_cache(
            cache_path=args.cache,
            debtor=args.debtor,
            max_debtors=args.max_debtors,
            workers=args.workers,
            per_page=args.per_page,
            start_year=args.start_year,
            timeout=(20.0, args.timeout),
            retries=args.retries,
        )
    except Exception:
        LOGGER.exception("IDS data refresh failed")
        return 1
    return 0



# ===========================================================================
# National debt profile clients — Tier 1 (World Bank) and Tier 2 (IMF)
# ===========================================================================
# These national indicators complement (but are NOT interchangeable with) the
# IDS bilateral PPG series above. They supply a value for every country on the
# map, including countries that never report to IDS (Canada, Saudi Arabia …).
#
# Error contract
# --------------
# Every client separates two situations that the resolver must treat
# differently:
#
# * *Missing data* — the provider answered, but has no usable value. The
#   function returns ``None`` / an empty dict. This is a legitimate answer and
#   may be cached for 24 hours.
# * *Source failure* — timeout, DNS error, HTTP 5xx, malformed JSON. With
#   ``raise_on_error=True`` the function raises :class:`LiveSourceError` so the
#   caller can avoid caching an outage; with the default ``False`` it logs and
#   returns ``None`` / ``{}`` so a single call can never break the map.

WORLD_BANK_COUNTRY_INDICATOR = "DT.DOD.DLXF.CD"
WORLD_BANK_COUNTRY_INDICATOR_LABEL = "External debt stocks, long-term (DOD, current US$)"
WORLD_BANK_COUNTRY_API_ROOT = "https://api.worldbank.org/v2"
IMF_GG_DEBT_GDP_INDICATOR = "GG_DEBT_GDP"
IMF_GG_DEBT_GDP_API_URL = f"https://www.imf.org/external/datamapper/api/v1/{IMF_GG_DEBT_GDP_INDICATOR}"
IMF_GG_DEBT_GDP_LABEL = "General government gross debt (% of GDP)"
# Tier 2 tries these IMF series in order. Both measure general government gross debt
# as % of GDP: the World Economic Outlook covers ~195 countries up to the current
# year; the Global Debt Database covers fewer countries with a longer lag.
IMF_WEO_DEBT_INDICATOR = "GGXWDG_NGDP"
IMF_TIER2_INDICATORS: tuple[str, ...] = (IMF_WEO_DEBT_INDICATOR, IMF_GG_DEBT_GDP_INDICATOR)
IMF_TIER2_SOURCE_LABELS = {
    IMF_WEO_DEBT_INDICATOR: "IMF · World Economic Outlook · General government gross debt (% of GDP)",
    IMF_GG_DEBT_GDP_INDICATOR: "IMF · DataMapper · General government debt (% of GDP)",
}


def imf_indicator_url(indicator: str) -> str:
    return f"https://www.imf.org/external/datamapper/api/v1/{indicator}"

COUNTRY_PROFILE_TIMEOUT: tuple[float, float] = (5.0, 20.0)  # (connect, read) seconds
COUNTRY_PROFILE_RETRIES = 3
COUNTRY_PROFILE_PER_PAGE = 20_000
COUNTRY_PROFILE_WORKERS = 8
# Consecutive connection failures after which the per-country World Bank
# fallback stops issuing requests. Prevents ~250 doomed calls during an outage.
CIRCUIT_BREAKER_THRESHOLD = 5
# Bulk queries only need recent observations; the resolver rejects stale ones.
BULK_LOOKBACK_YEARS = 25

# Provider-specific codes that differ from ISO 3166-1 alpha-3 / World Bank.
IMF_CODE_ALIASES: dict[str, str] = {
    "PSE": "WBG",  # West Bank and Gaza
    "XKX": "UVK",  # Kosovo
}

USER_AGENT = "GlobalBilateralDebtExplorer/2.2 (+https://github.com/Jaudat150; open-data research)"


class LiveSourceError(RuntimeError):
    """A live API could not be reached or returned an unusable response."""


def _numeric_observation(value: Any) -> float | None:
    """Convert an API value to a finite, strictly positive float, or ``None``.

    Zero is rejected as well as negatives: no sovereign has exactly zero debt,
    and a zero in these series signals a placeholder rather than a measurement.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().casefold() in {"", "..", "nan", "null", "na", "n/a", "-"}:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _year_number(value: Any) -> int | None:
    """Parse a year-like API field such as ``"2024"`` or ``"2024Q4"``."""
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    if not match:
        return None
    year = int(match.group(0))
    return year if 1900 <= year <= 2100 else None


def _normalize_iso3(value: Any) -> str | None:
    """Return an upper-case ISO3 code, or ``None`` when it is not three letters."""
    code = str(value or "").strip().upper()
    return code if len(code) == 3 and code.isalpha() else None


def _current_year() -> int:
    return datetime.now(timezone.utc).year


def _country_profile_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: tuple[float, float] = COUNTRY_PROFILE_TIMEOUT,
    retries: int = COUNTRY_PROFILE_RETRIES,
) -> Any:
    """GET a JSON document with bounded retries, back-off, and timeouts.

    Retries on connection errors, timeouts, and HTTP 408/425/429/5xx (honouring
    ``Retry-After``). Non-retryable HTTP errors (e.g. 404) fail immediately.

    Raises:
        LiveSourceError: when every attempt fails or the body is not JSON.
    """
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    attempts = max(1, retries)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts - 1:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 1.5 ** attempt
                except (TypeError, ValueError):
                    delay = 1.5 ** attempt
                time.sleep(min(10.0, max(0.5, delay)))
                continue
            if 400 <= response.status_code < 500:
                raise LiveSourceError(f"HTTP {response.status_code} from {url}")
            response.raise_for_status()
            return response.json()
        except LiveSourceError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(min(6.0, 1.5 ** attempt))
    raise LiveSourceError(f"{url} failed after {attempts} attempt(s): {last_error}")


# ---------------------------------------------------------------------------
# Tier 1 — World Bank V2 · DT.DOD.DLXF.CD
# ---------------------------------------------------------------------------

def world_bank_country_url(iso3: str) -> str:
    """Return the canonical single-country Tier 1 endpoint (without query)."""
    return f"{WORLD_BANK_COUNTRY_API_ROOT}/country/{iso3}/indicator/{WORLD_BANK_COUNTRY_INDICATOR}"


def _world_bank_observation(iso3: str, country_name: str, year: int, value: float) -> dict[str, Any]:
    """Build the Tier 1 observation dictionary consumed by the resolver."""
    return {
        "iso3": iso3,
        "country_name": country_name,
        "value": value,
        "observation_year": year,
        "metric_type": "external_debt_usd_long_term",
        "metric_label": WORLD_BANK_COUNTRY_INDICATOR_LABEL,
        "metric_short": "External debt (US$)",
        "unit": "USD",
        "source_label": "World Bank · International Debt Statistics",
        "data_source": "World Bank API (live)",
        "indicator_code": WORLD_BANK_COUNTRY_INDICATOR,
        "source_url": f"{world_bank_country_url(iso3)}?format=json",
        "fallback_tier": 1,
        "is_estimate": False,
        "definition_note": (
            "Long-term external debt stock in current US$: public, publicly guaranteed and private "
            "nonguaranteed debt owed to non-residents. Not a public-debt-only measure."
        ),
    }


def parse_world_bank_country_indicator_payload(
    payload: Any,
    *,
    requested_iso3: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Parse a World Bank V2 indicator response into the latest non-null value per ISO3.

    A V2 response is ``[metadata, observations]``. Observations live at index
    ``[1]``; each has ``countryiso3code``, ``date`` and ``value``. Rows are
    compared by year rather than trusting the API's sort order, and null,
    ``".."``, zero and negative values are skipped.

    An error response (``[{"message": [...]}]``) or any unexpected shape returns
    an empty dict — i.e. "no data", never an exception.

    Args:
        payload: Decoded JSON body.
        requested_iso3: Keep only this country (useful for single-country calls).

    Returns:
        ``{ISO3: observation}`` for every country with at least one usable value.
    """
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        return {}
    wanted = _normalize_iso3(requested_iso3) if requested_iso3 else None

    latest: dict[str, tuple[int, float, str]] = {}
    for row in payload[1]:
        if not isinstance(row, dict):
            continue
        code = _normalize_iso3(row.get("countryiso3code"))
        if code is None or (wanted and code != wanted):
            continue
        year = _year_number(row.get("date"))
        value = _numeric_observation(row.get("value"))
        if year is None or value is None:
            continue
        country = row.get("country") if isinstance(row.get("country"), dict) else {}
        name = str(country.get("value") or code)
        if code not in latest or year > latest[code][0]:
            latest[code] = (year, value, name)

    return {code: _world_bank_observation(code, name, year, value) for code, (year, value, name) in latest.items()}


def fetch_world_bank_country_indicator(
    iso3: str,
    *,
    timeout: tuple[float, float] = COUNTRY_PROFILE_TIMEOUT,
    retries: int = COUNTRY_PROFILE_RETRIES,
    raise_on_error: bool = False,
) -> dict[str, Any] | None:
    """Tier 1 for one country via ``/v2/country/{ISO3}/indicator/DT.DOD.DLXF.CD``.

    Returns:
        The latest non-null observation, or ``None`` when the World Bank has no
        usable value (typical for high-income creditors such as Canada).

    Raises:
        LiveSourceError: only when ``raise_on_error`` is true and the request failed.
    """
    code = _normalize_iso3(iso3)
    if code is None:
        return None
    try:
        payload = _country_profile_get_json(
            world_bank_country_url(code),
            params={"format": "json", "per_page": 100},
            timeout=timeout,
            retries=retries,
        )
    except Exception as exc:  # any transport/decoding failure is a source failure
        if raise_on_error:
            raise exc if isinstance(exc, LiveSourceError) else LiveSourceError(str(exc)) from exc
        LOGGER.warning("World Bank Tier 1 failed for %s: %s", code, exc)
        return None
    return parse_world_bank_country_indicator_payload(payload, requested_iso3=code).get(code)


def fetch_world_bank_country_indicators_bulk(
    iso3_codes: list[str] | tuple[str, ...],
    *,
    timeout: tuple[float, float] = COUNTRY_PROFILE_TIMEOUT,
    lookback_years: int = BULK_LOOKBACK_YEARS,
) -> dict[str, dict[str, Any]]:
    """Fetch Tier 1 for many countries using paginated ``country/all`` requests.

    One or two HTTP requests replace ~250 single-country calls. The response
    covers every economy, so countries absent from the result genuinely have no
    recent observation.

    Raises:
        LiveSourceError: when the bulk request fails or pagination is malformed.
    """
    requested = {code for code in (_normalize_iso3(item) for item in iso3_codes) if code}
    if not requested:
        return {}
    end_year = _current_year()
    url = f"{WORLD_BANK_COUNTRY_API_ROOT}/country/all/indicator/{WORLD_BANK_COUNTRY_INDICATOR}"
    collected: dict[str, dict[str, Any]] = {}
    page, pages = 1, 1
    while page <= pages:
        payload = _country_profile_get_json(
            url,
            params={
                "format": "json",
                "per_page": COUNTRY_PROFILE_PER_PAGE,
                "page": page,
                "date": f"{end_year - lookback_years}:{end_year}",
            },
            timeout=timeout,
        )
        if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[0], dict):
            raise LiveSourceError("World Bank bulk indicator returned an unexpected response shape")
        try:
            pages = max(1, int(payload[0].get("pages", 1)))
        except (TypeError, ValueError):
            pages = 1
        for code, observation in parse_world_bank_country_indicator_payload(payload).items():
            previous = collected.get(code)
            if previous is None or observation["observation_year"] > previous["observation_year"]:
                collected[code] = observation
        page += 1
        if page > 50:
            raise LiveSourceError("World Bank bulk pagination exceeded 50 pages")
    return {code: value for code, value in collected.items() if code in requested}


def fetch_world_bank_country_indicators_individually(
    iso3_codes: list[str] | tuple[str, ...],
    *,
    timeout: tuple[float, float] = COUNTRY_PROFILE_TIMEOUT,
    workers: int = COUNTRY_PROFILE_WORKERS,
    breaker_threshold: int = CIRCUIT_BREAKER_THRESHOLD,
) -> dict[str, dict[str, Any]]:
    """Query the exact per-country Tier 1 endpoint concurrently, with a circuit breaker.

    Used when the bulk request fails (some proxies reject large responses) but
    the World Bank itself may still be up. After ``breaker_threshold``
    consecutive failures the remaining requests are skipped.

    Raises:
        LiveSourceError: when the breaker trips before any country succeeded,
            meaning the source is down rather than sparse.
    """
    codes = sorted({code for code in (_normalize_iso3(item) for item in iso3_codes) if code})
    results: dict[str, dict[str, Any]] = {}
    state = {"consecutive_failures": 0, "successes": 0}
    tripped = threading.Event()
    lock = threading.Lock()

    def lookup(code: str) -> tuple[str, dict[str, Any] | None]:
        if tripped.is_set():
            return code, None
        try:
            observation = fetch_world_bank_country_indicator(code, timeout=timeout, retries=1, raise_on_error=True)
        except LiveSourceError:
            with lock:
                state["consecutive_failures"] += 1
                if state["consecutive_failures"] >= breaker_threshold:
                    tripped.set()
            return code, None
        with lock:
            state["consecutive_failures"] = 0
            state["successes"] += 1
        return code, observation

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for future in as_completed([pool.submit(lookup, code) for code in codes]):
            code, observation = future.result()
            if observation is not None:
                results[code] = observation

    if tripped.is_set() and state["successes"] == 0:
        raise LiveSourceError("World Bank per-country fallback tripped the circuit breaker")
    if tripped.is_set():
        LOGGER.warning("World Bank per-country lookups stopped early after repeated failures")
    return results


def fetch_world_bank_external_debt(
    iso3_codes: list[str] | tuple[str, ...],
    *,
    timeout: tuple[float, float] = COUNTRY_PROFILE_TIMEOUT,
) -> dict[str, dict[str, Any]]:
    """Tier 1 for many countries: bulk request first, then exact per-country calls.

    Raises:
        LiveSourceError: when neither strategy could reach the World Bank.
    """
    try:
        return fetch_world_bank_country_indicators_bulk(iso3_codes, timeout=timeout)
    except LiveSourceError as exc:
        LOGGER.warning("World Bank bulk Tier 1 failed (%s); trying per-country endpoint", exc)
    return fetch_world_bank_country_indicators_individually(iso3_codes, timeout=timeout)


# ---------------------------------------------------------------------------
# Tier 2 — IMF DataMapper · GG_DEBT_GDP
# ---------------------------------------------------------------------------

def imf_code_for(iso3: str) -> str:
    """Translate an ISO3 / World Bank code to the IMF DataMapper code."""
    code = str(iso3 or "").strip().upper()
    return IMF_CODE_ALIASES.get(code, code)


def fetch_imf_gg_debt_gdp_payload(
    *,
    indicator: str = IMF_GG_DEBT_GDP_INDICATOR,
    timeout: tuple[float, float] = COUNTRY_PROFILE_TIMEOUT,
    raise_on_error: bool = False,
) -> dict[str, Any] | None:
    """Download one IMF government-debt series for all countries (one request).

    ``indicator`` is ``GG_DEBT_GDP`` (Global Debt Database, default) or
    ``GGXWDG_NGDP`` (World Economic Outlook).

    Returns:
        The decoded payload, or ``None`` on failure when ``raise_on_error`` is false.

    Raises:
        LiveSourceError: on failure when ``raise_on_error`` is true.
    """
    try:
        payload = _country_profile_get_json(imf_indicator_url(indicator), timeout=timeout)
        if not isinstance(payload, dict) or _imf_series(payload, indicator) is None:
            raise LiveSourceError(f"IMF DataMapper returned no {indicator} series")
        return payload
    except Exception as exc:  # any transport/decoding failure is a source failure
        if raise_on_error:
            raise exc if isinstance(exc, LiveSourceError) else LiveSourceError(str(exc)) from exc
        LOGGER.warning("IMF DataMapper Tier 2 failed: %s", exc)
        return None


def _imf_series(payload: Any, indicator: str = IMF_GG_DEBT_GDP_INDICATOR) -> dict[str, Any] | None:
    """Return ``payload['values'][indicator]``, tolerating a ``data`` wrapper."""
    if not isinstance(payload, dict):
        return None
    root = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    values = root.get("values") if isinstance(root, dict) else None
    series = values.get(indicator) if isinstance(values, dict) else None
    return series if isinstance(series, dict) else None


def parse_imf_gg_debt_gdp_payload(
    payload: Any,
    iso3: str,
    *,
    max_year: int | None = None,
    indicator: str | None = None,
) -> dict[str, Any] | None:
    """Extract the latest non-null government-debt value for one country.

    With ``indicator=None`` each series in :data:`IMF_TIER2_INDICATORS` present
    in ``payload`` is tried in order (World Economic Outlook first).

    Reads ``data["values"]["GG_DEBT_GDP"][code]`` where ``code`` is the IMF
    alias of ``iso3`` (``PSE`` → ``WBG``, ``XKX`` → ``UVK``).

    DataMapper series include World Economic Outlook *projections* five years
    ahead. Years after ``max_year`` (default: the current UTC year) are
    ignored so a 2030 forecast is never presented as the latest observation.

    Returns:
        A Tier 2 observation, or ``None`` when no usable value exists.
    """
    code = _normalize_iso3(iso3)
    if indicator is None:
        for candidate in IMF_TIER2_INDICATORS:
            observation = parse_imf_gg_debt_gdp_payload(payload, iso3, max_year=max_year, indicator=candidate)
            if observation is not None:
                return observation
        return None
    series = _imf_series(payload, indicator)
    if code is None or series is None:
        return None
    country_series = series.get(imf_code_for(code))
    if not isinstance(country_series, dict):
        return None

    ceiling = max_year if max_year is not None else _current_year()
    observations = [
        (year, value)
        for year, value in ((_year_number(k), _numeric_observation(v)) for k, v in country_series.items())
        if year is not None and value is not None and year <= ceiling
    ]
    if not observations:
        return None
    year, value = max(observations, key=lambda item: item[0])
    return {
        "iso3": code,
        "value": value,
        "observation_year": year,
        "metric_type": "general_government_debt_pct_gdp",
        "metric_label": IMF_GG_DEBT_GDP_LABEL,
        "metric_short": "Gov. debt (% of GDP)",
        "unit": "percent_of_gdp",
        "source_label": IMF_TIER2_SOURCE_LABELS.get(indicator, f"IMF · DataMapper · {indicator}"),
        "data_source": "IMF DataMapper API (live)",
        "indicator_code": indicator,
        "source_url": imf_indicator_url(indicator),
        "fallback_tier": 2,
        "is_estimate": year >= _current_year(),
        "definition_note": (
            "General government gross debt as a share of GDP (IMF World Economic Outlook). "
            "A ratio, not a US$ stock — do not rank it against Tier 1 values."
            + (" Current-year values are IMF staff estimates." if year >= _current_year() else "")
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
