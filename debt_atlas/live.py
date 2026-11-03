"""Minimal live client for the World Bank and IMF APIs (standard library + pandas only)."""

from __future__ import annotations

import json
import math
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from debt_atlas._meta import __version__

IMF_TO_ISO = {"WBG": "PSE", "UVK": "XKX"}
_HEADERS = {"User-Agent": f"debt-atlas/{__version__}", "Accept": "application/json"}


def _get(url: str, timeout: float) -> object:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_HEADERS), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def fetch_series(code: str, *, provider: str, timeout: float = 60.0, include_projections: bool = False) -> pd.DataFrame:
    """Return ``iso3, year, value, series_code, provider`` for every country.

    IMF projections (years after the current one) are dropped unless
    ``include_projections`` is true. World Bank regional aggregates are kept;
    filter them with :meth:`Atlas.countries`.
    """
    rows = []
    if provider == "worldbank":
        page, pages = 1, 1
        while page <= pages:
            payload = _get(f"https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&per_page=20000&page={page}", timeout)
            if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
                break
            pages = int(payload[0].get("pages", 1))
            for row in payload[1]:
                value, iso3 = _number(row.get("value")), str(row.get("countryiso3code") or "")
                if value is not None and len(iso3) == 3:
                    rows.append({"iso3": iso3, "year": int(row["date"][:4]), "value": value})
            page += 1
    elif provider == "imf":
        payload = _get(f"https://www.imf.org/external/datamapper/api/v1/{code}", timeout)
        current = datetime.now(timezone.utc).year
        for imf_code, years in (payload.get("values", {}).get(code, {}) or {}).items():  # type: ignore[union-attr]
            for year, value in years.items():
                number = _number(value)
                if number is not None and (include_projections or int(year) <= current):
                    rows.append({"iso3": IMF_TO_ISO.get(imf_code, imf_code), "year": int(year), "value": number})
    else:
        raise ValueError("provider must be 'worldbank' or 'imf'")
    frame = pd.DataFrame(rows, columns=["iso3", "year", "value"])
    frame["series_code"], frame["provider"] = code, provider
    return frame.sort_values(["iso3", "year"]).reset_index(drop=True)
