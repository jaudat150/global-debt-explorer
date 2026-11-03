"""Live data for the World Atlas: fetch, cache, resolve and snapshot indicator layers.

For each indicator in :mod:`src.catalog`:

1. Every live series is downloaded in one request (World Bank ``country/all`` or
   the full IMF DataMapper series) and cached by Streamlit for 24 hours.
2. For each country the **freshest valid observation** across the indicator's
   series wins; ties go to the series listed first. Years after the current
   year (IMF projections) are always dropped; current-year IMF values are
   flagged as estimates and can be excluded.
3. If a provider is unreachable, the last successful download stored in
   ``data/indicator_snapshot.json`` is used and labelled as such.
4. Countries with no value anywhere are reported as missing — never zero — and
   the UI points to :data:`src.catalog.SOURCE_DIRECTORY` for newer data.

CLI::

    python -m src.indicator_data refresh            # download every indicator, update the snapshot
    python -m src.indicator_data show gov_debt_gdp  # print one layer
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.catalog import INDICATOR_BY_ID, INDICATORS, Indicator, SourceRef
from src.processor import _SourceHealth, st, utc_now_iso

LOGGER = logging.getLogger("global_debt_explorer.indicators")

CACHE_TTL_SECONDS = 86_400
WORLD_BANK_LOOKBACK_YEARS = 20
SERIES_YEARS_KEPT = 25
DEFAULT_INDICATOR_SNAPSHOT = Path(__file__).resolve().parents[1] / "data" / "indicator_snapshot.json"

# IMF DataMapper codes that differ from ISO3 / World Bank codes.
IMF_TO_ISO = {"WBG": "PSE", "UVK": "XKX"}

OK, UNREACHABLE = "ok", "unreachable"
_HEALTH = _SourceHealth()

Series = dict[str, dict[int, float]]  # ISO3 -> {year: value}


class IndicatorSourceError(RuntimeError):
    """A provider could not be reached or returned an unusable payload."""


class UnknownSeriesError(IndicatorSourceError):
    """The provider answered, but does not publish this series (bad or retired code)."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CountryValue:
    """The value shown for one country on one indicator layer."""

    iso3: str
    value: float
    year: int
    source: SourceRef
    is_estimate: bool = False
    from_snapshot: bool = False
    snapshot_date: str | None = None

    @property
    def source_label(self) -> str:
        label = f"{self.source.label} · {self.source.code}"
        if self.from_snapshot:
            label += f" · last-known copy {self.snapshot_date or ''}".rstrip()
        return label


@dataclass
class IndicatorLayer:
    """A resolved map layer for one indicator."""

    indicator: Indicator
    values: dict[str, CountryValue]
    series: Series
    source_status: dict[str, str]
    retrieved_at: str
    include_estimates: bool = True
    notes: list[str] = field(default_factory=list)
    raw: dict[str, Series] = field(default_factory=dict)  # series code -> per-source series (provenance-preserving)
    raw_from_snapshot: dict[str, str] = field(default_factory=dict)  # series code -> snapshot date

    @property
    def coverage(self) -> int:
        return len(self.values)

    @property
    def latest_year(self) -> int | None:
        return max((item.year for item in self.values.values()), default=None)

    @property
    def median_year(self) -> int | None:
        years = sorted(item.year for item in self.values.values())
        return years[len(years) // 2] if years else None

    @property
    def all_unreachable(self) -> bool:
        return bool(self.source_status) and all(status != OK for status in self.source_status.values())

    def rows(self) -> list[dict[str, Any]]:
        """Tidy rows (one per country) with full provenance, for tables and CSV."""
        return [
            {
                "iso3": item.iso3,
                "indicator_id": self.indicator.id,
                "indicator": self.indicator.name,
                "value": item.value,
                "unit": self.indicator.unit,
                "year": item.year,
                "is_estimate": item.is_estimate,
                "source": item.source.label,
                "series_code": item.source.code,
                "series_url": item.source.portal_url,
                "from_snapshot": item.from_snapshot,
                "retrieved_at_utc": self.retrieved_at,
            }
            for item in sorted(self.values.values(), key=lambda value: value.iso3)
        ]


# ---------------------------------------------------------------------------
# Parsing (pure)
# ---------------------------------------------------------------------------

def _finite(value: Any) -> float | None:
    """Return a finite float (negatives allowed), or ``None`` for nulls and junk."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().casefold() in {"", "..", "nan", "null", "na", "n/a", "-"}:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _year(value: Any) -> int | None:
    text = str(value or "").strip()[:4]
    return int(text) if text.isdigit() and 1900 <= int(text) <= 2100 else None


def _iso3(value: Any) -> str | None:
    code = str(value or "").strip().upper()
    return code if len(code) == 3 and code.isalpha() else None


def parse_world_bank_series(payload: Any, multiplier: float = 1.0) -> Series:
    """Parse a World Bank V2 response (``[meta, rows]``) into ISO3 → {year: value}.

    Aggregates (``WLD``, ``ARB`` …) are kept here and filtered by the caller's
    country list. Error envelopes and unexpected shapes return ``{}``.
    """
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        return {}
    series: Series = {}
    for row in payload[1]:
        if not isinstance(row, dict):
            continue
        code, year, value = _iso3(row.get("countryiso3code")), _year(row.get("date")), _finite(row.get("value"))
        if code and year is not None and value is not None:
            series.setdefault(code, {})[year] = value * multiplier
    return series


def parse_imf_series(payload: Any, code: str, multiplier: float = 1.0) -> Series:
    """Parse ``payload['values'][code]`` from IMF DataMapper into ISO3 → {year: value}."""
    if not isinstance(payload, dict):
        return {}
    root = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    values = root.get("values") if isinstance(root, dict) else None
    by_country = values.get(code) if isinstance(values, dict) else None
    if not isinstance(by_country, dict):
        return {}
    series: Series = {}
    for imf_code, years in by_country.items():
        iso = _iso3(IMF_TO_ISO.get(str(imf_code).upper(), imf_code))
        if not iso or not isinstance(years, dict):
            continue
        for raw_year, raw_value in years.items():
            year, value = _year(raw_year), _finite(raw_value)
            if year is not None and value is not None:
                series.setdefault(iso, {})[year] = value * multiplier
    return series


def latest_valid(
    years: Mapping[int, float] | None,
    *,
    current_year: int,
    include_estimates: bool,
    provider: str,
) -> tuple[int, float] | None:
    """Pick the most recent usable (year, value).

    Future years are always dropped (IMF projections). When
    ``include_estimates`` is false, IMF values for the current year are dropped
    too, leaving the latest year with reported (non-estimate) data.
    """
    if not years:
        return None
    ceiling = current_year if (include_estimates or provider != "imf") else current_year - 1
    usable = [(year, value) for year, value in years.items() if year <= ceiling]
    return max(usable, key=lambda item: item[0]) if usable else None


def resolve_layer(
    indicator: Indicator,
    downloads: Mapping[str, Series | None],
    *,
    status: Mapping[str, str],
    snapshot: Mapping[str, Mapping[str, Any]] | None = None,
    countries: Iterable[str] | None = None,
    current_year: int | None = None,
    include_estimates: bool = True,
    retrieved_at: str | None = None,
) -> IndicatorLayer:
    """Combine downloaded series into one map layer (no I/O).

    Args:
        indicator: Catalogue entry.
        downloads: ``SourceRef.code`` → parsed series, or ``None`` if unavailable.
        status: ``SourceRef.code`` → ``"ok"`` / ``"unreachable"``.
        snapshot: ``SourceRef.code`` → ``{"retrieved_at_utc", "series"}`` from a
            previous successful run; used only for unreachable sources.
        countries: Restrict output to these ISO3 codes (drops regional aggregates).
    """
    current_year = current_year or datetime.now(timezone.utc).year
    allowed = {code.upper() for code in countries} if countries else None
    snapshot = snapshot or {}
    values: dict[str, CountryValue] = {}
    merged_series: Series = {}
    notes: list[str] = []
    raw: dict[str, Series] = {}
    raw_from_snapshot: dict[str, str] = {}

    for source in indicator.sources:
        series = downloads.get(source.code)
        from_snapshot, snapshot_date = False, None
        if series is None and status.get(source.code) != OK:
            cached = snapshot.get(source.code) or {}
            if cached.get("series"):
                series = {iso: {int(y): float(v) for y, v in years.items()} for iso, years in cached["series"].items()}
                from_snapshot, snapshot_date = True, str(cached.get("retrieved_at_utc") or "")[:10]
                notes.append(f"{source.label} unreachable — using last-known copy from {snapshot_date}.")
            else:
                notes.append(f"{source.label} unreachable and no saved copy is available.")
        if not series:
            continue
        raw[source.code] = {
            iso: {y: v for y, v in years.items() if y <= current_year}
            for iso, years in series.items() if allowed is None or iso in allowed
        }
        if from_snapshot:
            raw_from_snapshot[source.code] = snapshot_date or ""
        for iso, years in series.items():
            if allowed is not None and iso not in allowed:
                continue
            for year, value in years.items():
                merged_series.setdefault(iso, {}).setdefault(year, value)
            pick = latest_valid(years, current_year=current_year, include_estimates=include_estimates, provider=source.provider)
            if pick is None:
                continue
            year, value = pick
            existing = values.get(iso)
            if existing is not None and existing.year >= year:
                continue  # earlier-ranked source already has an equally fresh or fresher value
            values[iso] = CountryValue(
                iso3=iso, value=value, year=year, source=source,
                is_estimate=source.provider == "imf" and year >= current_year,
                from_snapshot=from_snapshot, snapshot_date=snapshot_date,
            )

    trimmed = {
        iso: dict(sorted((y, v) for y, v in years.items() if current_year - SERIES_YEARS_KEPT <= y <= current_year))
        for iso, years in merged_series.items()
    }
    return IndicatorLayer(
        indicator=indicator, values=values, series=trimmed, source_status=dict(status),
        retrieved_at=retrieved_at or utc_now_iso(), include_estimates=include_estimates, notes=notes,
        raw=raw, raw_from_snapshot=raw_from_snapshot,
    )


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def indicator_snapshot_path() -> Path | None:
    """``GDE_INDICATOR_SNAPSHOT=off`` disables the snapshot; any other value is a path."""
    override = os.getenv("GDE_INDICATOR_SNAPSHOT", "").strip()
    if override.casefold() in {"off", "0", "false", "none", "disabled"}:
        return None
    return Path(override) if override else DEFAULT_INDICATOR_SNAPSHOT


@lru_cache(maxsize=2)
def _read_snapshot_file(path: str, _mtime: float) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)
    series = payload.get("series") if isinstance(payload, dict) else None
    return series if isinstance(series, dict) else {}


def read_indicator_snapshot() -> dict[str, Any]:
    """Return ``{series_code: {"retrieved_at_utc", "series"}}`` or ``{}``."""
    path = indicator_snapshot_path()
    if path is None or not path.exists():
        return {}
    try:
        return _read_snapshot_file(str(path), path.stat().st_mtime)
    except (OSError, ValueError) as exc:
        LOGGER.warning("Ignoring unreadable indicator snapshot: %s", exc)
        return {}


def write_indicator_snapshot(updates: Mapping[str, Series]) -> None:
    """Merge freshly downloaded series into the snapshot (atomic; never raises)."""
    path = indicator_snapshot_path()
    if path is None or not updates:
        return
    try:
        current = {key: dict(value) for key, value in read_indicator_snapshot().items()}
        stamp = utc_now_iso()
        cutoff = datetime.now(timezone.utc).year - SERIES_YEARS_KEPT
        for code, series in updates.items():
            current[code] = {
                "retrieved_at_utc": stamp,
                "series": {
                    iso: {str(y): round(v, 6) for y, v in sorted(years.items()) if y >= cutoff}
                    for iso, years in sorted(series.items())
                },
            }
        document = {"metadata": {"description": "Last successful download of each atlas series.", "updated_at_utc": stamp},
                    "series": dict(sorted(current.items()))}
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False)
        with handle:
            json.dump(document, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        os.replace(handle.name, path)
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.warning("Could not update indicator snapshot: %s", exc)


# ---------------------------------------------------------------------------
# Live downloads (cached 24 h; failures raise so they are never cached)
# ---------------------------------------------------------------------------

def download_world_bank(code: str, multiplier: float = 1.0) -> Series:
    """Download one WDI/IDS indicator for every economy (paginated)."""
    from src.fetcher import COUNTRY_PROFILE_PER_PAGE, _country_profile_get_json

    end = datetime.now(timezone.utc).year
    url = f"https://api.worldbank.org/v2/country/all/indicator/{code}"
    series: Series = {}
    page, pages = 1, 1
    while page <= pages:
        try:
            payload = _country_profile_get_json(
                url, params={"format": "json", "per_page": COUNTRY_PROFILE_PER_PAGE, "page": page,
                             "date": f"{end - WORLD_BANK_LOOKBACK_YEARS}:{end}"})
        except Exception as exc:
            raise IndicatorSourceError(f"World Bank {code}: {exc}") from exc
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise IndicatorSourceError(f"World Bank {code}: unexpected response")
        if "message" in payload[0]:
            raise UnknownSeriesError(f"World Bank {code}: {payload[0]['message']}")
        try:
            pages = max(1, int(payload[0].get("pages", 1)))
        except (TypeError, ValueError):
            pages = 1
        for iso, years in parse_world_bank_series(payload, multiplier).items():
            series.setdefault(iso, {}).update(years)
        page += 1
        if page > 20:
            raise IndicatorSourceError(f"World Bank {code}: pagination exceeded 20 pages")
    return series


def download_imf(code: str, multiplier: float = 1.0) -> Series:
    """Download one IMF DataMapper indicator for every country."""
    from src.fetcher import _country_profile_get_json

    try:
        payload = _country_profile_get_json(f"https://www.imf.org/external/datamapper/api/v1/{code}")
    except Exception as exc:
        raise IndicatorSourceError(f"IMF {code}: {exc}") from exc
    series = parse_imf_series(payload, code, multiplier)
    if not series:
        raise UnknownSeriesError(f"IMF {code}: empty or unknown series")
    return series


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False, max_entries=128)
def _cached_series(provider: str, code: str, multiplier: float) -> Series:
    series = download_world_bank(code, multiplier) if provider == "worldbank" else download_imf(code, multiplier)
    write_indicator_snapshot({code: series})
    return series


def fetch_source(source: SourceRef) -> tuple[Series | None, str]:
    """Download one series through the 24-hour cache, honouring provider back-off."""
    if not _HEALTH.available(source.provider):
        return None, UNREACHABLE
    try:
        series = _cached_series(source.provider, source.code, source.multiplier)
    except UnknownSeriesError as exc:
        LOGGER.warning("%s", exc)  # the provider is up; only this series is unusable
        return None, UNREACHABLE
    except IndicatorSourceError as exc:
        LOGGER.warning("%s", exc)
        _HEALTH.record(source.provider, ok=False)  # transport failure: back off the whole provider
        return None, UNREACHABLE
    except Exception as exc:  # never let one series break the page
        LOGGER.warning("Series %s failed: %s", source.code, exc)
        _HEALTH.record(source.provider, ok=False)
        return None, UNREACHABLE
    _HEALTH.record(source.provider, ok=True)
    return series, OK


def load_layer(
    indicator_id: str,
    *,
    countries: Iterable[str] | None = None,
    include_estimates: bool = True,
) -> IndicatorLayer:
    """Fetch (cached) and resolve one indicator layer. Never raises on network errors.

    Raises:
        KeyError: for an unknown ``indicator_id``.
    """
    indicator = INDICATOR_BY_ID[indicator_id]
    downloads: dict[str, Series | None] = {}
    status: dict[str, str] = {}
    for source in indicator.sources:
        downloads[source.code], status[source.code] = fetch_source(source)
    snapshot = read_indicator_snapshot() if UNREACHABLE in status.values() else {}
    return resolve_layer(indicator, downloads, status=status, snapshot=snapshot,
                         countries=countries, include_estimates=include_estimates)


def clear_indicator_cache() -> None:
    """Clear cached downloads and provider back-off (the dashboard's refresh button)."""
    clear = getattr(_cached_series, "clear", None)
    if callable(clear):
        clear()
    _HEALTH.reset()
    _read_snapshot_file.cache_clear()


def country_codes() -> tuple[str, ...]:
    """Every ISO 3166 country plus Kosovo (drops World Bank regional aggregates)."""
    import pycountry

    return tuple(sorted({country.alpha_3 for country in pycountry.countries} | {"XKX"}))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_value(value: float | None, unit: str) -> str:
    """Human-readable value in the indicator's own unit."""
    if value is None:
        return "No data"
    if unit in {"usd", "usd_per_capita"}:
        absolute = abs(value)
        for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if absolute >= threshold and unit == "usd":
                return f"US${value / threshold:,.2f}{suffix}"
        return f"US${value:,.0f}"
    if unit == "people":
        for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(value) >= threshold:
                return f"{value / threshold:,.1f}{suffix}"
        return f"{value:,.0f}"
    if unit == "percent_of_gdp":
        return f"{value:,.1f}% of GDP"
    if unit == "percent_of_gni":
        return f"{value:,.1f}% of GNI"
    if unit == "percent":
        return f"{value:,.1f}%"
    if unit == "years":
        return f"{value:,.1f} years"
    if unit == "months":
        return f"{value:,.1f} months"
    return f"{value:,.1f}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="World Atlas indicator data.")
    sub = parser.add_subparsers(dest="command", required=True)
    refresh = sub.add_parser("refresh", help="Download indicators and update data/indicator_snapshot.json")
    refresh.add_argument("ids", nargs="*", help="Indicator ids (default: all)")
    show = sub.add_parser("show", help="Print one resolved layer")
    show.add_argument("id")
    show.add_argument("codes", nargs="*", help="Optional ISO3 filter")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    from src.processor import quiet_streamlit_logs

    quiet_streamlit_logs()

    if args.command == "refresh":
        ids = args.ids or [indicator.id for indicator in INDICATORS]
        failures = 0
        for indicator_id in ids:
            layer = load_layer(indicator_id, countries=country_codes())
            failures += int(layer.all_unreachable)
            print(f"{indicator_id:<24} {layer.coverage:>4} countries · latest {layer.latest_year} · {layer.source_status}")
        return 1 if failures == len(ids) else 0

    layer = load_layer(args.id, countries=args.codes or country_codes())
    for row in layer.rows():
        flag = " (est.)" if row["is_estimate"] else ""
        print(f"{row['iso3']}  {row['year']}{flag:<7} {format_value(row['value'], row['unit']):<22} {row['series_code']}")
    print(f"coverage={layer.coverage} latest={layer.latest_year} median={layer.median_year} status={layer.source_status}")
    for note in layer.notes:
        print("note:", note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
