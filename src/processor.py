"""Normalize IDS bilateral observations and resolve 3-tier national debt profiles.

Part 1 cleans and aggregates the World Bank IDS creditor-composition cache.
Part 2 resolves a national debt profile for every ISO3 code through
World Bank → IMF → local fallback (see the section banner below).
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

try:  # Keep processor utilities importable in lightweight test environments.
    import streamlit as st

    try:  # Command-line use: silence "No runtime found" (emitted when cache decorators are created).
        from streamlit import runtime as _st_runtime

        if not _st_runtime.exists():
            logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)
    except Exception:  # pragma: no cover - never let log tuning break imports
        pass
except ImportError:  # pragma: no cover - exercised only without dashboard extras
    class _StreamlitCacheFallback:
        @staticmethod
        def cache_data(*_args: Any, **_kwargs: Any):
            def decorate(function):
                return function
            return decorate

    st = _StreamlitCacheFallback()  # type: ignore[assignment]

REQUIRED_COLUMNS = [
    "year",
    "debtor_iso3",
    "debtor_name",
    "creditor_id",
    "creditor_iso3",
    "creditor_name",
    "amount_usd",
]


def _text(value: Any, fallback: str = "") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return fallback
    result = str(value).strip()
    return fallback if result.casefold() in {"", "nan", "none", "null", "..", "n/a", "na"} else result


def clean_records(records: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """Validate, normalize, and de-duplicate raw debt records.

    ``creditor_id`` is the World Bank IDS counterpart-area identifier. It is
    intentionally distinct from ``creditor_iso3`` because the counterpart-area
    dimension can contain official creditor areas that are not sovereign states.
    Older cache records without creditor_id remain supported.
    """
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue

        debtor_iso3 = _text(record.get("debtor_iso3", record.get("debtor", ""))).upper()
        creditor_iso3 = _text(record.get("creditor_iso3", record.get("creditor", ""))).upper()
        creditor_id = _text(record.get("creditor_id", creditor_iso3 or record.get("creditor_name", "")))
        debtor_name = _text(record.get("debtor_name"), debtor_iso3)
        creditor_name = _text(record.get("creditor_name"), creditor_iso3 or creditor_id)
        raw_amount = record.get("amount_usd", record.get("amount", record.get("value")))
        raw_year = record.get("year", record.get("date", record.get("time")))

        try:
            amount = float(raw_amount)
            year_text = str(raw_year).strip()[:4]
            year = int(year_text)
        except (TypeError, ValueError, OverflowError):
            continue

        if not (1900 <= year <= 2100):
            continue
        if not math.isfinite(amount) or amount < 0:
            continue
        if len(debtor_iso3) != 3 or not debtor_iso3.isalpha():
            continue
        if not creditor_id:
            continue
        if not debtor_name or not creditor_name:
            continue

        rows.append(
            {
                "year": year,
                "debtor_iso3": debtor_iso3,
                "debtor_name": debtor_name,
                "creditor_id": creditor_id,
                "creditor_iso3": creditor_iso3 if len(creditor_iso3) == 3 else "",
                "creditor_name": creditor_name,
                "amount_usd": amount,
            }
        )

    if not rows:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    frame = pd.DataFrame(rows, columns=REQUIRED_COLUMNS)
    frame = (
        frame.groupby(
            [
                "year",
                "debtor_iso3",
                "debtor_name",
                "creditor_id",
                "creditor_iso3",
                "creditor_name",
            ],
            as_index=False,
            dropna=False,
        )["amount_usd"]
        .sum()
    )
    frame["year"] = frame["year"].astype(int)
    frame["amount_usd"] = frame["amount_usd"].astype(float)
    return frame.sort_values(
        ["year", "debtor_name", "amount_usd"], ascending=[True, True, False], kind="stable"
    ).reset_index(drop=True)


def aggregate_debt(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    """Total reported bilateral debt by debtor for a specific year."""
    columns = ["debtor_iso3", "debtor_name", "amount_usd"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    selected = frame.loc[frame["year"] == int(year)]
    if selected.empty:
        return pd.DataFrame(columns=columns)
    return (
        selected.groupby(["debtor_iso3", "debtor_name"], as_index=False)["amount_usd"]
        .sum()
        .sort_values("amount_usd", ascending=False, kind="stable")
        .reset_index(drop=True)
    )


def creditor_totals(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    """Total reported creditor claims for a specific year."""
    columns = ["creditor_id", "creditor_iso3", "creditor_name", "amount_usd"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    selected = frame.loc[frame["year"] == int(year)]
    if selected.empty:
        return pd.DataFrame(columns=columns)
    return (
        selected.groupby(
            ["creditor_id", "creditor_iso3", "creditor_name"], as_index=False
        )["amount_usd"]
        .sum()
        .sort_values("amount_usd", ascending=False, kind="stable")
        .reset_index(drop=True)
    )


def country_creditor_breakdown(frame: pd.DataFrame, year: int, debtor_iso3: str) -> pd.DataFrame:
    """Return creditor amount and share for one debtor and observation year."""
    columns = ["creditor_id", "creditor_iso3", "creditor_name", "amount_usd", "share_pct"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    selected = frame.loc[
        (frame["year"] == int(year)) & (frame["debtor_iso3"] == str(debtor_iso3).upper())
    ].copy()
    if selected.empty:
        return pd.DataFrame(columns=columns)
    grouped = (
        selected.groupby(
            ["creditor_id", "creditor_iso3", "creditor_name"], as_index=False
        )["amount_usd"]
        .sum()
        .sort_values("amount_usd", ascending=False, kind="stable")
        .reset_index(drop=True)
    )
    total = float(grouped["amount_usd"].sum())
    grouped["share_pct"] = (grouped["amount_usd"] / total * 100.0) if total > 0 else 0.0
    return grouped[columns]


def format_usd(value: float | int | None) -> str:
    """Format current USD as readable B/M/K values."""
    if value is None or pd.isna(value):
        return "—"
    amount = float(value)
    absolute = abs(amount)
    if absolute >= 1_000_000_000:
        return f"${amount / 1_000_000_000:,.2f}B"
    if absolute >= 1_000_000:
        return f"${amount / 1_000_000:,.1f}M"
    if absolute >= 1_000:
        return f"${amount / 1_000:,.1f}K"
    return f"${amount:,.0f}"


def read_cache(path: str | Path) -> dict[str, Any]:
    cache_path = Path(path)
    with cache_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError(f"Cache has an invalid structure: {cache_path}")
    if not isinstance(payload.get("metadata"), dict):
        payload["metadata"] = {}
    return payload


def write_cache(payload: Mapping[str, Any], path: str | Path) -> None:
    """Atomically replace the JSON cache to protect against interrupted runs."""
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    document = dict(payload)
    if not isinstance(document.get("metadata"), dict):
        document["metadata"] = {}
    if not isinstance(document.get("records"), list):
        raise ValueError("Cache records must be a list")

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=cache_path.parent,
        prefix=f".{cache_path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temp_path, cache_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def frame_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return [
        {
            **record,
            "year": int(record["year"]),
            "amount_usd": float(record["amount_usd"]),
        }
        for record in frame[REQUIRED_COLUMNS].to_dict(orient="records")
    ]


def quiet_streamlit_logs() -> None:
    """Silence Streamlit's "No runtime found" cache warnings when modules run as command-line tools."""
    for name in ("streamlit.runtime.caching.cache_data_api", "streamlit.runtime.caching", "streamlit"):
        logging.getLogger(name).setLevel(logging.ERROR)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()



# ===========================================================================
# National debt profiles — 3-tier resilience fallback
# ===========================================================================
#
#   Tier 1  World Bank V2 · DT.DOD.DLXF.CD   long-term external debt, US$
#      │ missing / stale / unreachable
#      ▼
#   Tier 2  IMF DataMapper · GG_DEBT_GDP     general government debt, % of GDP
#      │ missing / stale / unreachable
#      ▼
#   Tier 3  Local safety net
#             a) LOCAL_FALLBACK_DB  — curated, sourced estimates (Syria, Palestine…)
#             b) last-known-good snapshot of a live value whose source is down
#      │ nothing usable
#      ▼
#   Explicit "No verified data" profile (never zero) — still has a name,
#   metric type, source label and map coordinates.
#
# Live HTTP responses are cached by Streamlit for 24 hours. Failures are not
# cached (Streamlit never caches a raised exception), and a short in-process
# back-off stops a dead API from adding timeout latency to every rerun.
# Resolution itself is a pure function (:func:`resolve_country_profile`) so it
# is trivially unit-testable.

PROFILE_LOGGER = logging.getLogger("global_debt_explorer.country_profiles")

CACHE_TTL_SECONDS = 86_400
SOURCE_BACKOFF_SECONDS = 300
MAX_OBSERVATION_AGE_YEARS = int(os.getenv("GDE_MAX_OBSERVATION_AGE_YEARS", "10"))

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
LABEL_POINTS_PATH = DATA_DIR / "country_label_points.json"
DEFAULT_PROFILE_SNAPSHOT_PATH = DATA_DIR / "country_profiles_snapshot.json"

TIER_LABELS: dict[int | None, str] = {
    1: "Tier 1 · World Bank (live)",
    2: "Tier 2 · IMF (live)",
    3: "Tier 3 · Local fallback",
    None: "No verified source",
}

# Curated estimates for known reporting gaps. Every entry must carry a credible
# source, an observation period, the source's publication date, the date the
# entry was last reviewed, and a caveat describing its metric perimeter.
#
# ``prefer_over_live``: set True only when a documented reason shows the live
# series is materially incomplete for that country. When False (default) the
# entry is used only after Tier 1 and Tier 2 fail, and is otherwise attached to
# the live profile as ``alternative_estimate`` for context.
LOCAL_FALLBACK_DB: dict[str, dict[str, Any]] = {
    "SYR": {
        "value": 21_500_000_000.0,
        "value_low": 20_000_000_000.0,
        "value_high": 23_000_000_000.0,
        "display_value": "US$20–23B (estimated range)",
        "metric_type": "estimated_total_external_debt_usd_range",
        "metric_label": "Estimated total external debt (government-reported range)",
        "metric_short": "Est. external debt (US$)",
        "unit": "USD",
        "observation_year": 2025,
        "source_label": "Syrian government estimate · reported by PIIE (Mar 2025) and EUAA (Jul 2025)",
        "source_url": "https://www.piie.com/blogs/realtime-economics/2025/syria-needs-debt-restructuring-will-be-difficult",
        "source_timestamp": "2025-07",
        "source_date_label": "Source published Jul 2025 · reviewed Nov 2026",
        "reviewed_at": "2026-11-02",
        "prefer_over_live": False,
        "definition_note": (
            "Range attributed to the post-2024 government. The World Bank's 2023 figure (~US$4.1B) is described "
            "by PIIE as very incomplete; claims by Iran and Russia could put the total at US$30–50B. "
            "Not comparable with the World Bank long-term series."
        ),
    },
    "PSE": {
        "value": 4_800_000_000.0,
        "display_value": "US$4.8B (estimate)",
        "metric_type": "estimated_public_debt_usd",
        "metric_label": "Estimated Palestinian Authority public debt (bank + external)",
        "metric_short": "Est. public debt (US$)",
        "unit": "USD",
        "observation_year": 2025,
        "source_label": "World Bank · Palestinian Economic Monitor",
        "source_url": "https://thedocs.worldbank.org/en/doc/caf69bb9db6d729c6b35254c5815df9a-0280012026/original/Palestinian-Economic-Monitor-5-19-26web.pdf",
        "source_timestamp": "2026-05-19",
        "source_date_label": "Source published May 19, 2026 · reviewed Nov 2026",
        "reviewed_at": "2026-11-02",
        "prefer_over_live": False,
        "definition_note": (
            "End-2025 public debt estimate with a different debt perimeter from long-term external debt: it "
            "includes domestic bank borrowing and excludes large arrears to employees, the pension fund and "
            "private suppliers."
        ),
    },
}

COUNTRY_NAME_OVERRIDES = {
    "PSE": "West Bank and Gaza",
    "XKX": "Kosovo",
    "TWN": "Taiwan",
}

# Overseas territories that Natural Earth folds into their parent country.
EXTRA_LABEL_POINTS: dict[str, tuple[float, float]] = {
    "BES": (12.18, -68.25), "BVT": (-54.42, 3.36), "CCK": (-12.17, 96.84),
    "CXR": (-10.49, 105.62), "GLP": (16.25, -61.58), "GUF": (3.93, -53.13),
    "MTQ": (14.64, -61.02), "MYT": (-12.83, 45.15), "REU": (-21.12, 55.53),
    "SJM": (78.22, 15.65), "TKL": (-9.20, -171.85),
}


def _normalize_codes(codes: Iterable[Any]) -> tuple[str, ...]:
    """Upper-case, de-duplicate, sort and keep only three-letter alphabetic codes."""
    normalized = {str(code or "").strip().upper() for code in codes}
    return tuple(sorted(code for code in normalized if len(code) == 3 and code.isalpha()))


def _country_display_name(iso3: str) -> str:
    """Resolve a readable country name without requiring the map component."""
    if iso3 in COUNTRY_NAME_OVERRIDES:
        return COUNTRY_NAME_OVERRIDES[iso3]
    try:
        import pycountry

        country = pycountry.countries.get(alpha_3=iso3)
        if country:
            return str(getattr(country, "common_name", None) or country.name)
    except ImportError:  # pragma: no cover - pycountry is a hard requirement
        pass
    return iso3


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4)
def load_country_label_points(path: str | None = None) -> dict[str, tuple[float, float]]:
    """Return ISO3 → (latitude, longitude) label points for ~260 economies.

    Built from Natural Earth 1:10m (which includes small states missing from
    the 1:110m polygons used for drawing) plus a few overseas territories.
    A missing or corrupt file degrades to the territory table only.
    """
    points: dict[str, tuple[float, float]] = dict(EXTRA_LABEL_POINTS)
    source = Path(path) if path else LABEL_POINTS_PATH
    try:
        with source.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        for code, item in (payload.get("points") or {}).items():
            lat, lon = float(item["lat"]), float(item["lon"])
            if math.isfinite(lat) and math.isfinite(lon):
                points[str(code).upper()] = (lat, lon)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        PROFILE_LOGGER.warning("Country label points unavailable (%s); small states may lack coordinates", exc)
    return points


def country_coordinates(iso3: str) -> dict[str, float] | None:
    """Return ``{"latitude", "longitude"}`` for an ISO3 code, or ``None`` if unknown."""
    point = load_country_label_points().get(str(iso3 or "").upper())
    return {"latitude": point[0], "longitude": point[1]} if point else None


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _profile_display_value(value: float, unit: str) -> str:
    """Format a profile value in its own unit (US$ stock or % of GDP)."""
    if unit == "percent_of_gdp":
        return f"{value:,.1f}% of GDP"
    absolute = abs(value)
    if absolute >= 1_000_000_000_000:
        return f"US${value / 1_000_000_000_000:,.2f}T"
    if absolute >= 1_000_000_000:
        return f"US${value / 1_000_000_000:,.2f}B"
    if absolute >= 1_000_000:
        return f"US${value / 1_000_000:,.1f}M"
    if absolute >= 1_000:
        return f"US${value / 1_000:,.1f}K"
    return f"US${value:,.0f}"


def _summarize_alternative(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Compact view of a curated estimate attached to a live profile."""
    return {
        "display_value": entry.get("display_value"),
        "metric_label": entry.get("metric_label"),
        "source_label": entry.get("source_label"),
        "source_url": entry.get("source_url"),
        "source_date_label": entry.get("source_date_label"),
        "observation_year": entry.get("observation_year"),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def assess_observation(
    observation: Mapping[str, Any] | None,
    *,
    current_year: int,
    max_age_years: int = MAX_OBSERVATION_AGE_YEARS,
) -> str | None:
    """Return why an observation is unusable, or ``None`` when it is usable.

    An observation is rejected when it is missing, has a non-finite or
    non-positive value, lacks a year, or is older than ``max_age_years``. A
    rejected observation makes the resolver fall through to the next tier.
    """
    if not observation:
        return "no non-null observation"
    try:
        value = float(observation.get("value"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "value is not numeric"
    if not math.isfinite(value) or value <= 0:
        return "value is zero, negative or not finite"
    year = observation.get("observation_year")
    if year is None:
        return "observation has no year"
    if int(year) < current_year - max_age_years:
        return f"latest observation ({int(year)}) is older than {max_age_years} years"
    return None


# ---------------------------------------------------------------------------
# Last-known-good snapshot (part of the Tier 3 safety net)
# ---------------------------------------------------------------------------

_SNAPSHOT_FIELDS = (
    "iso3", "country_name", "value", "observation_year", "metric_type", "metric_label", "metric_short",
    "unit", "source_label", "indicator_code", "source_url", "fallback_tier", "definition_note",
)


def profile_snapshot_path() -> Path | None:
    """Snapshot location; ``GDE_PROFILE_SNAPSHOT=off`` disables the snapshot."""
    override = os.getenv("GDE_PROFILE_SNAPSHOT", "").strip()
    if override.casefold() in {"off", "0", "false", "none", "disabled"}:
        return None
    return Path(override) if override else DEFAULT_PROFILE_SNAPSHOT_PATH


@lru_cache(maxsize=4)
def _read_snapshot_cached(path: str, _mtime: float) -> dict[str, dict[str, dict[str, Any]]]:
    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)
    sources = payload.get("sources") if isinstance(payload, dict) else None
    return sources if isinstance(sources, dict) else {}


def read_profile_snapshot() -> dict[str, dict[str, dict[str, Any]]]:
    """Return ``{"world_bank": {ISO3: obs}, "imf": {ISO3: obs}}`` or ``{}``."""
    path = profile_snapshot_path()
    if path is None or not path.exists():
        return {}
    try:
        return _read_snapshot_cached(str(path), path.stat().st_mtime)
    except (OSError, ValueError) as exc:
        PROFILE_LOGGER.warning("Ignoring unreadable profile snapshot %s: %s", path, exc)
        return {}


def write_profile_snapshot(source: str, observations: Mapping[str, Mapping[str, Any]], *,
                           keep_indicator: str | None = None) -> None:
    """Merge fresh live observations for ``source`` into the snapshot file.

    Never raises: the snapshot is an optimisation, not a dependency. Uses an
    atomic replace so a crash cannot leave a half-written file.
    """
    path = profile_snapshot_path()
    if path is None or not observations:
        return
    try:
        current = read_profile_snapshot()
        merged = {name: dict(entries) for name, entries in current.items()}
        retrieved_at = utc_now_iso()
        bucket = merged.setdefault(source, {})
        for code, observation in observations.items():
            existing = bucket.get(str(code).upper()) or {}
            if keep_indicator and existing.get("indicator_code") == keep_indicator:
                continue  # never overwrite a preferred series with a secondary one
            record = {field: observation.get(field) for field in _SNAPSHOT_FIELDS}
            record["retrieved_at_utc"] = retrieved_at
            bucket[str(code).upper()] = record
        document = {
            "metadata": {
                "description": "Last-known-good live national debt observations (Tier 3 safety net).",
                "updated_at_utc": retrieved_at,
            },
            "sources": merged,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        )
        with handle:
            json.dump(document, handle, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False)
        os.replace(handle.name, path)
    except (OSError, ValueError, TypeError) as exc:
        PROFILE_LOGGER.warning("Could not update profile snapshot: %s", exc)


# ---------------------------------------------------------------------------
# Live source access: 24-hour cache + outage back-off
# ---------------------------------------------------------------------------

class _SourceHealth:
    """Remembers recent failures so an outage costs one timeout, not one per rerun."""

    def __init__(self, backoff_seconds: float = SOURCE_BACKOFF_SECONDS) -> None:
        self.backoff_seconds = backoff_seconds
        self._failed_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def available(self, source: str) -> bool:
        with self._lock:
            failed_at = self._failed_at.get(source)
        return failed_at is None or (time.monotonic() - failed_at) >= self.backoff_seconds

    def record(self, source: str, ok: bool) -> None:
        with self._lock:
            if ok:
                self._failed_at.pop(source, None)
            else:
                self._failed_at[source] = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._failed_at.clear()


_SOURCE_HEALTH = _SourceHealth()

# Source status values passed to the resolver.
OK, UNREACHABLE = "ok", "unreachable"


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False, max_entries=4)
def _cached_world_bank_many(iso3_codes: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Tier 1 for many codes, cached 24 h. Raises on failure so outages are not cached."""
    from src import fetcher

    observations = fetcher.fetch_world_bank_external_debt(iso3_codes)
    write_profile_snapshot("world_bank", observations)
    return observations


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False, max_entries=512)
def _cached_world_bank_one(iso3: str) -> dict[str, Any] | None:
    """Tier 1 for one code via the exact ``/country/{ISO3}`` route, cached 24 h."""
    from src import fetcher

    observation = fetcher.fetch_world_bank_country_indicator(iso3, raise_on_error=True)
    if observation:
        write_profile_snapshot("world_bank", {iso3: observation})
    return observation


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False, max_entries=4)
def _cached_imf_payload(indicator: str = "GG_DEBT_GDP") -> dict[str, Any]:
    """One full IMF government-debt payload, cached 24 h. Raises on failure."""
    from src import fetcher

    payload = fetcher.fetch_imf_gg_debt_gdp_payload(indicator=indicator, raise_on_error=True)
    if not payload:
        raise RuntimeError(f"IMF DataMapper returned no usable {indicator} payload")
    primary = fetcher.IMF_TIER2_INDICATORS[0]
    series = fetcher._imf_series(payload, indicator) or {}
    parsed = {}
    for imf_code in series:
        iso3 = next((iso for iso, alias in fetcher.IMF_CODE_ALIASES.items() if alias == imf_code), imf_code)
        observation = fetcher.parse_imf_gg_debt_gdp_payload(payload, iso3, indicator=indicator)
        if observation:
            parsed[iso3] = observation
    # The secondary series only fills countries the primary (World Economic Outlook) lacks.
    write_profile_snapshot("imf", parsed, keep_indicator=None if indicator == primary else primary)
    return payload


def _imf_payloads() -> tuple[dict[str, dict[str, Any]] | None, str]:
    """Fetch every Tier 2 IMF series; UNREACHABLE only if none could be fetched."""
    from src import fetcher

    payloads: dict[str, dict[str, Any]] = {}
    for indicator in fetcher.IMF_TIER2_INDICATORS:
        payload, _status = _guarded("imf", _cached_imf_payload, indicator)
        if payload:
            payloads[indicator] = payload
    return (payloads, OK) if payloads else (None, UNREACHABLE)


def _tier2_observation(imf_payload: Mapping[str, Any], iso3: str, *, current_year: int,
                       max_age_years: int) -> tuple[dict[str, Any] | None, str]:
    """First usable IMF observation across Tier 2 series, or (None, reason)."""
    from src import fetcher

    payloads = ([imf_payload[i] for i in fetcher.IMF_TIER2_INDICATORS if i in imf_payload]
                if any(i in imf_payload for i in fetcher.IMF_TIER2_INDICATORS) else [imf_payload])
    reasons = []
    for indicator in fetcher.IMF_TIER2_INDICATORS:
        for payload in payloads:
            observation = fetcher.parse_imf_gg_debt_gdp_payload(payload, iso3, max_year=current_year, indicator=indicator)
            if observation is None:
                continue
            reason = assess_observation(observation, current_year=current_year, max_age_years=max_age_years)
            if reason is None:
                return observation, ""
            reasons.append(f"{indicator}: {reason}")
    return None, "; ".join(reasons) or "no non-null observation"


def _guarded(source: str, call: Any, *args: Any) -> tuple[Any, str]:
    """Run a cached live call, translating failures into an ``UNREACHABLE`` status."""
    if not _SOURCE_HEALTH.available(source):
        return None, UNREACHABLE
    try:
        result = call(*args)
    except Exception as exc:  # any failure must degrade to the next tier, never crash the map
        PROFILE_LOGGER.warning("%s unavailable: %s", source, exc)
        _SOURCE_HEALTH.record(source, ok=False)
        return None, UNREACHABLE
    _SOURCE_HEALTH.record(source, ok=True)
    return result, OK


# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------

def _unavailable_profile(iso3: str, retrieved_at: str, trail: list[str]) -> dict[str, Any]:
    coordinates = country_coordinates(iso3)
    return {
        "iso3": iso3,
        "country_name": _country_display_name(iso3),
        "value": None,
        "value_low": None,
        "value_high": None,
        "display_value": "No verified data",
        "metric_type": "unavailable",
        "metric_label": "No verified national debt indicator available",
        "metric_short": "No data",
        "unit": "",
        "observation_year": None,
        "source_label": "No source available",
        "data_source": "Unavailable",
        "source_url": "",
        "source_timestamp": None,
        "source_date_label": "No source date",
        "retrieved_at_utc": retrieved_at,
        "fallback_tier": None,
        "tier_label": TIER_LABELS[None],
        "is_estimate": False,
        "is_stale_snapshot": False,
        "data_available": False,
        "coordinates": coordinates,
        "map_available": coordinates is not None,
        "definition_note": (
            "No value was returned by the World Bank or IMF and no curated local estimate is configured. "
            "This is not zero debt."
        ),
        "resolution_trail": trail,
        "alternative_estimate": None,
    }


def _profile_from_observation(
    iso3: str,
    observation: Mapping[str, Any],
    *,
    tier: int,
    data_source: str,
    retrieved_at: str,
    trail: list[str],
    source_date_label: str | None = None,
    is_stale_snapshot: bool = False,
    alternative: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    value = float(observation["value"])
    unit = str(observation.get("unit") or "USD")
    coordinates = country_coordinates(iso3)
    year = observation.get("observation_year")
    return {
        "iso3": iso3,
        "country_name": str(COUNTRY_NAME_OVERRIDES.get(iso3) or observation.get("country_name") or _country_display_name(iso3)),
        "value": value,
        "value_low": observation.get("value_low"),
        "value_high": observation.get("value_high"),
        "display_value": str(observation.get("display_value") or _profile_display_value(value, unit)),
        "metric_type": str(observation.get("metric_type") or "unknown"),
        "metric_label": str(observation.get("metric_label") or observation.get("metric_type") or "Unknown metric"),
        "metric_short": str(observation.get("metric_short") or observation.get("metric_label") or ""),
        "unit": unit,
        "observation_year": int(year) if year is not None else None,
        "source_label": str(observation.get("source_label") or "Unknown source"),
        "data_source": data_source,
        "source_url": str(observation.get("source_url") or ""),
        "source_timestamp": str(observation.get("source_timestamp") or observation.get("retrieved_at_utc") or retrieved_at),
        "source_date_label": source_date_label or str(observation.get("source_date_label") or f"Retrieved {retrieved_at[:10]} UTC"),
        "retrieved_at_utc": retrieved_at,
        "fallback_tier": tier,
        "tier_label": TIER_LABELS[tier],
        "is_estimate": bool(observation.get("is_estimate", tier == 3)),
        "is_stale_snapshot": is_stale_snapshot,
        "data_available": True,
        "coordinates": coordinates,
        "map_available": coordinates is not None,
        "definition_note": str(observation.get("definition_note") or ""),
        "resolution_trail": trail,
        "alternative_estimate": _summarize_alternative(alternative) if alternative else None,
    }


def resolve_country_profile(
    iso3: str,
    *,
    world_bank: Mapping[str, Any] | None,
    world_bank_status: str,
    imf_payload: Mapping[str, Any] | None,
    imf_status: str,
    snapshot: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    retrieved_at: str | None = None,
    current_year: int | None = None,
    max_age_years: int = MAX_OBSERVATION_AGE_YEARS,
    local_db: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve one country through Tier 1 → Tier 2 → Tier 3 without any I/O.

    Args:
        iso3: Upper-case ISO3 code.
        world_bank: Tier 1 observation for this country (``None`` if absent).
        world_bank_status: ``"ok"`` if the World Bank answered, ``"unreachable"`` otherwise.
        imf_payload: Full IMF DataMapper payload (``None`` if unavailable).
        imf_status: ``"ok"`` or ``"unreachable"``.
        snapshot: Last-known-good observations, used only for unreachable sources.
        retrieved_at: Timestamp stamped on the profile (defaults to now).
        current_year: Reference year for the staleness check (defaults to now).
        max_age_years: Observations older than this fall through to the next tier.
        local_db: Curated fallback entries (defaults to :data:`LOCAL_FALLBACK_DB`).

    Returns:
        A complete profile dict. ``data_available`` is False only when every
        tier came up empty; ``display_value``, ``metric_type`` and
        ``data_source`` are always populated.
    """
    from src import fetcher

    retrieved_at = retrieved_at or utc_now_iso()
    current_year = current_year or datetime.now(timezone.utc).year
    local_db = LOCAL_FALLBACK_DB if local_db is None else local_db
    snapshot = snapshot or {}
    curated = local_db.get(iso3)
    trail: list[str] = []

    def tier3(reason_prefix: str) -> dict[str, Any] | None:
        if curated:
            trail.append(f"Tier 3: curated local estimate used ({reason_prefix})")
            return _profile_from_observation(
                iso3, curated, tier=3, data_source="Local fallback database", retrieved_at=retrieved_at, trail=trail
            )
        for source, status, name in (("world_bank", world_bank_status, "World Bank"), ("imf", imf_status, "IMF")):
            if status != UNREACHABLE:
                continue
            cached = (snapshot.get(source) or {}).get(iso3)
            if assess_observation(cached, current_year=current_year, max_age_years=max_age_years) is None:
                when = str(cached.get("retrieved_at_utc") or "")[:10] or "an earlier run"
                trail.append(f"Tier 3: last-known {name} value from {when} used ({name} unreachable)")
                return _profile_from_observation(
                    iso3, cached, tier=3,
                    data_source=f"Last-known {name} snapshot",
                    source_date_label=f"Last retrieved {when} · live source unreachable",
                    retrieved_at=retrieved_at, trail=trail, is_stale_snapshot=True,
                )
        return None

    if curated and curated.get("prefer_over_live"):
        trail.append("Tier 3 preferred: curated entry is flagged prefer_over_live")
        return tier3("pinned") or _unavailable_profile(iso3, retrieved_at, trail)

    # Tier 1 — World Bank
    if world_bank_status == UNREACHABLE:
        trail.append("Tier 1 World Bank: source unreachable")
    else:
        reason = assess_observation(world_bank, current_year=current_year, max_age_years=max_age_years)
        if reason is None:
            trail.append("Tier 1 World Bank: latest non-null observation used")
            return _profile_from_observation(
                iso3, world_bank, tier=1, data_source="World Bank API (live)",  # type: ignore[arg-type]
                retrieved_at=retrieved_at, trail=trail, alternative=curated,
            )
        trail.append(f"Tier 1 World Bank: {reason}")

    # Tier 2 — IMF
    if imf_status == UNREACHABLE or imf_payload is None:
        trail.append("Tier 2 IMF: source unreachable")
    else:
        imf_observation, reason = _tier2_observation(imf_payload, iso3, current_year=current_year,
                                                     max_age_years=max_age_years)
        if imf_observation is not None:
            trail.append(f"Tier 2 IMF: latest non-null observation used ({imf_observation['indicator_code']})")
            return _profile_from_observation(
                iso3, imf_observation, tier=2, data_source="IMF DataMapper API (live)",  # type: ignore[arg-type]
                retrieved_at=retrieved_at, trail=trail, alternative=curated,
            )
        trail.append(f"Tier 2 IMF: {reason}")

    # Tier 3 — local safety net
    profile = tier3("live tiers had no usable value")
    if profile is not None:
        return profile
    trail.append("Tier 3: no curated estimate or usable snapshot")
    return _unavailable_profile(iso3, retrieved_at, trail)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_country_debt_profiles(iso3_codes: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Return a resolved profile for every syntactically valid ISO3 code.

    Uses one bulk World Bank request sequence (falling back to per-country
    calls) and one IMF payload, both cached for 24 hours. Never raises because
    of network failures — unreachable sources fall through to Tier 3.
    """
    codes = _normalize_codes(iso3_codes)
    if not codes:
        return {}
    world_bank, wb_status = _guarded("world_bank", _cached_world_bank_many, codes)
    imf_payload, imf_status = _imf_payloads()
    snapshot = read_profile_snapshot() if UNREACHABLE in (wb_status, imf_status) else {}
    retrieved_at = utc_now_iso()
    world_bank = world_bank or {}
    return {
        code: resolve_country_profile(
            code,
            world_bank=world_bank.get(code),
            world_bank_status=wb_status,
            imf_payload=imf_payload,
            imf_status=imf_status,
            snapshot=snapshot,
            retrieved_at=retrieved_at,
        )
        for code in codes
    }


def get_country_debt_profile(iso3: str) -> dict[str, Any]:
    """Resolve one country, using the exact ``/v2/country/{ISO3}/indicator/...`` route for Tier 1.

    Raises:
        ValueError: if ``iso3`` is not a three-letter code.
    """
    codes = _normalize_codes([iso3])
    if not codes:
        raise ValueError(f"Expected an ISO-3 country code, received {iso3!r}")
    code = codes[0]
    world_bank, wb_status = _guarded("world_bank", _cached_world_bank_one, code)
    imf_payload, imf_status = _imf_payloads()
    snapshot = read_profile_snapshot() if UNREACHABLE in (wb_status, imf_status) else {}
    return resolve_country_profile(
        code,
        world_bank=world_bank,
        world_bank_status=wb_status,
        imf_payload=imf_payload,
        imf_status=imf_status,
        snapshot=snapshot,
    )


def clear_country_debt_profile_cache() -> None:
    """Clear cached API responses and the outage back-off (the dashboard's Refresh button)."""
    for cached in (_cached_world_bank_many, _cached_world_bank_one, _cached_imf_payload):
        clear = getattr(cached, "clear", None)
        if callable(clear):
            clear()
    _SOURCE_HEALTH.reset()
    _read_snapshot_cached.cache_clear()


def summarize_profiles(profiles: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """Count profiles per tier, for dashboard status chips and the CLI."""
    summary = {"tier_1": 0, "tier_2": 0, "tier_3": 0, "unavailable": 0, "snapshot": 0, "total": len(profiles)}
    for profile in profiles.values():
        tier = profile.get("fallback_tier")
        summary[f"tier_{tier}" if tier in (1, 2, 3) else "unavailable"] += 1
        summary["snapshot"] += int(bool(profile.get("is_stale_snapshot")))
    return summary


def _all_country_codes() -> tuple[str, ...]:
    import pycountry

    return _normalize_codes([country.alpha_3 for country in pycountry.countries] + ["XKX"])


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m src.processor profiles [ISO3 ...] [--json]``.

    Resolves profiles outside Streamlit (useful for debugging a country and for
    refreshing ``data/country_profiles_snapshot.json`` in CI).
    """
    import argparse

    parser = argparse.ArgumentParser(description="Resolve 3-tier national debt profiles.")
    sub = parser.add_subparsers(dest="command", required=True)
    profiles_cmd = sub.add_parser("profiles", help="Resolve profiles (default: every ISO country)")
    profiles_cmd.add_argument("codes", nargs="*", help="ISO3 codes, e.g. CAN SAU SYR PSE")
    profiles_cmd.add_argument("--json", action="store_true", help="Print full JSON profiles")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    quiet_streamlit_logs()

    codes = _normalize_codes(args.codes) or _all_country_codes()
    profiles = get_country_debt_profiles(codes)
    if args.json:
        print(json.dumps(profiles, indent=2, ensure_ascii=False))
    else:
        for code in codes:
            item = profiles[code]
            print(f"{code}  {item['tier_label']:<27} {item['display_value']:<30} {item['country_name']}")
        print(json.dumps(summarize_profiles(profiles)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
