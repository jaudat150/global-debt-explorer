"""Open-data exports: every dataset in the project as CSV/JSON + a Frictionless Data Package.

Used by the Developers tab (in-app downloads and a one-click ZIP bundle) and by
CI, which publishes the files to ``data/exports/`` weekly so anyone can read
them straight from GitHub without running the app::

    python -m src.exports build              # writes data/exports/*
    python -m src.exports build --out /tmp/x # somewhere else

Every row carries provenance (source, series code, year, retrieval time) and
``datapackage.json`` documents every column, so the bundle loads directly into
pandas, R (``frictionless``/``datapackage``), Excel or a database.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from src.catalog import CATEGORIES, COMMON_NAMES, INDICATORS, SOURCE_DIRECTORY
from src.indicator_data import IndicatorLayer, country_codes, load_layer

LOGGER = logging.getLogger("global_debt_explorer.exports")
EXPORT_DIR = Path(__file__).resolve().parents[1] / "data" / "exports"
from debt_atlas._meta import RAW_BASE as REPO_RAW_BASE  # single place for the repository name

FIELD_DOCS: dict[str, str] = {
    "iso3": "ISO 3166-1 alpha-3 country code (XKX = Kosovo)",
    "iso2": "ISO 3166-1 alpha-2 country code",
    "country": "Country name", "name": "Country name",
    "latitude": "Label point latitude (Natural Earth)", "longitude": "Label point longitude (Natural Earth)",
    "indicator_id": "Indicator id (see catalog.json)", "indicator": "Indicator name",
    "value": "Observed value in the indicator's unit", "unit": "Unit of the value",
    "year": "Observation year", "is_estimate": "True when the value is an IMF current-year staff estimate",
    "source": "Publisher / dataset", "series_code": "Upstream series code", "series_url": "Human-readable page for the series",
    "provider": "worldbank or imf", "from_snapshot": "True when taken from the last saved copy because the API was unreachable",
    "retrieved_at_utc": "When the value was retrieved (UTC)",
    "debtor_iso3": "Borrowing country ISO3", "debtor_name": "Borrowing country",
    "creditor_id": "World Bank IDS counterpart-area code", "creditor_iso3": "Lending country ISO3 (blank for non-sovereign creditor areas)",
    "creditor_name": "Lender", "amount_usd": "Public and publicly guaranteed bilateral debt outstanding, current US$",
    "item_id": "Spending line id", "item": "Spending line", "scope": "Government scope of the line",
    "usd": "Amount in current US$", "pct_gdp": "Amount as % of GDP", "usd_per_person": "Amount per person, current US$",
    "method": "How the US$ amount was obtained", "sources": "Series used for every factor",
    "display_value": "Formatted value", "metric_type": "Machine-readable metric type", "metric_label": "Metric definition",
    "fallback_tier": "1 = World Bank, 2 = IMF, 3 = local fallback, blank = no data",
    "observation_year": "Observation year", "source_label": "Source", "source_url": "Source link",
    "source_date_label": "Source publication or retrieval date", "is_estimate_profile": "True for curated estimates",
    "data_available": "False when no tier had a value (not zero)", "definition_note": "Definition and caveats",
}


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

def catalog_document() -> dict[str, Any]:
    """Machine-readable catalogue: categories, indicators with every upstream API, source directory."""
    return {
        "generated_at_utc": _now(),
        "categories": [asdict(category) for category in CATEGORIES],
        "indicators": [
            {
                "id": i.id, "name": i.name, "short": i.short, "category": i.category, "unit": i.unit, "scale": i.scale,
                "description": i.description, "caveat": i.caveat,
                "sources": [{"provider": s.provider, "code": s.code, "label": s.label, "multiplier": s.multiplier,
                             "api_url": s.api_url, "portal_url": s.portal_url} for s in i.sources],
                "find_newer_data": [SOURCE_DIRECTORY[k].url for k in i.directory],
            }
            for i in INDICATORS
        ],
        "source_directory": [asdict(source) for source in SOURCE_DIRECTORY.values()],
    }


def countries_frame() -> pd.DataFrame:
    import pycountry

    from src.processor import load_country_label_points

    points = load_country_label_points()
    rows = []
    for code in country_codes():
        country = pycountry.countries.get(alpha_3=code)
        lat_lon = points.get(code, (None, None))
        rows.append({
            "iso3": code, "iso2": country.alpha_2 if country else ("XK" if code == "XKX" else ""),
            "name": COMMON_NAMES.get(code) or ((getattr(country, "common_name", None) or country.name) if country else code),
            "latitude": lat_lon[0], "longitude": lat_lon[1],
        })
    return pd.DataFrame(rows)


def load_all_layers(codes: Iterable[str] | None = None, include_estimates: bool = True,
                    progress: Any = None) -> dict[str, IndicatorLayer]:
    """Load every catalogue indicator (cached 24 h). ``progress(i, n, label)`` is optional."""
    codes = tuple(codes or country_codes())
    layers = {}
    for position, indicator in enumerate(INDICATORS, start=1):
        layers[indicator.id] = load_layer(indicator.id, countries=codes, include_estimates=include_estimates)
        if progress:
            progress(position, len(INDICATORS), indicator.short)
    return layers


def latest_values_frame(layers: Mapping[str, IndicatorLayer]) -> pd.DataFrame:
    rows = [row for layer in layers.values() for row in layer.rows()]
    return pd.DataFrame(rows, columns=["iso3", "indicator_id", "indicator", "value", "unit", "year", "is_estimate",
                                       "source", "series_code", "series_url", "from_snapshot", "retrieved_at_utc"])


def timeseries_frame(layers: Mapping[str, IndicatorLayer]) -> pd.DataFrame:
    """Every year of every series, one row per source — nothing merged, nothing filled."""
    rows = []
    for layer in layers.values():
        for source in layer.indicator.sources:
            for iso3, years in layer.raw.get(source.code, {}).items():
                for year, value in sorted(years.items()):
                    rows.append({
                        "iso3": iso3, "indicator_id": layer.indicator.id, "year": year, "value": value,
                        "unit": layer.indicator.unit, "provider": source.provider, "series_code": source.code,
                        "source": source.label, "from_snapshot": source.code in layer.raw_from_snapshot,
                    })
    return pd.DataFrame(rows, columns=["iso3", "indicator_id", "year", "value", "unit", "provider", "series_code",
                                       "source", "from_snapshot"])


def bilateral_export_frame() -> pd.DataFrame:
    from src.debt_flows import bilateral_frame

    return bilateral_frame().copy()


def profiles_frame(codes: Iterable[str] | None = None) -> pd.DataFrame:
    from src.processor import get_country_debt_profiles

    profiles = get_country_debt_profiles(tuple(codes or country_codes()))
    keep = ["iso3", "country_name", "value", "display_value", "metric_type", "metric_label", "unit", "observation_year",
            "fallback_tier", "source_label", "source_url", "source_date_label", "is_estimate", "data_available",
            "definition_note"]
    frame = pd.DataFrame([{key: profile.get(key) for key in keep} for profile in profiles.values()], columns=keep)
    return frame.rename(columns={"country_name": "country", "is_estimate": "is_estimate_profile"})


def spending_frame(layers: Mapping[str, IndicatorLayer], codes: Iterable[str] | None = None,
                   names: Mapping[str, str] | None = None) -> pd.DataFrame:
    from src.spending import spending_matrix

    return spending_matrix(tuple(codes or country_codes()), layers, names)


def resource_columns() -> dict[str, list[str]]:
    """Column names of every export, available without any network access."""
    from src.processor import REQUIRED_COLUMNS
    from src.spending import spending_matrix

    return {
        "countries": ["iso3", "iso2", "name", "latitude", "longitude"],
        "latest_values": list(latest_values_frame({}).columns),
        "timeseries": list(timeseries_frame({}).columns),
        "spending_usd": list(spending_matrix((), {}).columns),
        "national_debt_profiles": ["iso3", "country", "value", "display_value", "metric_type", "metric_label", "unit",
                                   "observation_year", "fallback_tier", "source_label", "source_url", "source_date_label",
                                   "is_estimate_profile", "data_available", "definition_note"],
        "bilateral_debt": list(REQUIRED_COLUMNS),
    }


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _field_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "number"
    return "string"


RESOURCE_INFO: dict[str, tuple[str, str]] = {
    "countries": ("countries.csv", "Country reference: ISO codes, names and map coordinates."),
    "latest_values": ("latest_values.csv", "Latest value of every indicator for every country, with provenance."),
    "timeseries": ("timeseries.csv.gz", "Every year of every upstream series (one row per source; nothing merged or filled)."),
    "spending_usd": ("spending_usd.csv", "Where each country spends its money, in current US$, with method and sources."),
    "national_debt_profiles": ("national_debt_profiles.csv", "3-tier national debt profile for every country (World Bank → IMF → local)."),
    "bilateral_debt": ("bilateral_debt.csv.gz", "Who owes whom: World Bank IDS bilateral PPG debt by debtor, creditor and year."),
}


def datapackage(frames: Mapping[str, pd.DataFrame]) -> dict[str, Any]:
    """Frictionless Data Package descriptor (https://specs.frictionlessdata.io/data-package/)."""
    resources = []
    for name, frame in frames.items():
        path, description = RESOURCE_INFO[name]
        resource = {
            "name": name, "path": path, "format": "csv", "mediatype": "text/csv", "encoding": "utf-8",
            "description": description, "rows": int(len(frame)),
            "schema": {"fields": [{"name": column, "type": _field_type(frame[column]),
                                   "description": FIELD_DOCS.get(column, "")} for column in frame.columns]},
        }
        if path.endswith(".gz"):
            resource["compression"] = "gz"
        resources.append(resource)
    return {
        "name": "global-debt-fiscal-atlas",
        "title": "Global Debt & Fiscal Atlas — open data export",
        "description": "Sovereign debt, deficits, government spending, revenue, macro and development indicators for every "
                       "country, plus bilateral who-owes-whom debt, with full provenance.",
        "version": _version(),
        "created": _now(),
        "homepage": "https://github.com/Jaudat150/global-debt-explorer",
        "licenses": [{"name": "CC-BY-4.0", "title": "Data: see each provider's terms (World Bank CC BY 4.0, IMF terms, SIPRI, WHO, UNESCO)"}],
        "sources": [{"title": "World Bank Indicators API", "path": "https://api.worldbank.org/v2/"},
                    {"title": "IMF DataMapper API", "path": "https://www.imf.org/external/datamapper/api/"}],
        "contributors": [{"title": "Jaudat Faisal Al-Husein", "role": "author"}],
        "resources": resources,
    }


def _version() -> str:
    try:
        return (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _csv_bytes(frame: pd.DataFrame, gz: bool) -> bytes:
    if gz:
        buffer = io.BytesIO()
        frame.to_csv(buffer, index=False, compression={"method": "gzip", "mtime": 0})
        return buffer.getvalue()
    return frame.to_csv(index=False).encode("utf-8")


BUNDLE_README = """# Global Debt & Fiscal Atlas — data bundle

Generated {created} (version {version}).

| File | Rows | What it is |
|---|---|---|
{rows}

* `datapackage.json` documents every column (Frictionless Data Package).
* `catalog.json` lists every indicator, its upstream API URLs, and where to find newer data.
* Missing values are absent, never zero. Each row names its source, series code and year.

Load in Python:  `pandas.read_csv("latest_values.csv")`  ·  R:  `readr::read_csv("latest_values.csv")`

Cite the project (CITATION.cff in the repository) and the original data providers.
"""


def build_bundle(frames: Mapping[str, pd.DataFrame]) -> bytes:
    """ZIP containing every dataset, datapackage.json, catalog.json and a README."""
    descriptor = datapackage(frames)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, frame in frames.items():
            path = RESOURCE_INFO[name][0]
            archive.writestr(path, _csv_bytes(frame, path.endswith(".gz")))
        archive.writestr("datapackage.json", json.dumps(descriptor, indent=2, ensure_ascii=False))
        archive.writestr("catalog.json", json.dumps(catalog_document(), indent=2, ensure_ascii=False))
        rows = "\n".join(f"| `{RESOURCE_INFO[n][0]}` | {len(f):,} | {RESOURCE_INFO[n][1]} |" for n, f in frames.items())
        archive.writestr("README.md", BUNDLE_README.format(created=descriptor["created"], version=descriptor["version"], rows=rows))
    return buffer.getvalue()


def build_all_frames(include_estimates: bool = True, progress: Any = None) -> dict[str, pd.DataFrame]:
    codes = country_codes()
    layers = load_all_layers(codes, include_estimates, progress)
    countries = countries_frame()
    names = dict(zip(countries["iso3"], countries["name"]))
    return {
        "countries": countries,
        "latest_values": latest_values_frame(layers),
        "timeseries": timeseries_frame(layers),
        "spending_usd": spending_frame(layers, codes, names),
        "national_debt_profiles": profiles_frame(codes),
        "bilateral_debt": bilateral_export_frame(),
    }


def write_exports(out_dir: Path = EXPORT_DIR, include_estimates: bool = True) -> dict[str, int]:
    """Write every dataset plus datapackage.json and catalog.json to ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = build_all_frames(include_estimates)
    for name, frame in frames.items():
        path = RESOURCE_INFO[name][0]
        (out_dir / path).write_bytes(_csv_bytes(frame, path.endswith(".gz")))
    (out_dir / "datapackage.json").write_text(json.dumps(datapackage(frames), indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "catalog.json").write_text(json.dumps(catalog_document(), indent=2, ensure_ascii=False), encoding="utf-8")
    return {name: len(frame) for name, frame in frames.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export every dataset as CSV + Frictionless Data Package.")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Write data/exports/")
    build.add_argument("--out", type=Path, default=EXPORT_DIR)
    build.add_argument("--reported-only", action="store_true", help="Exclude IMF current-year estimates")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    from src.processor import quiet_streamlit_logs

    quiet_streamlit_logs()
    counts = write_exports(args.out, include_estimates=not args.reported_only)
    for name, rows in counts.items():
        print(f"{RESOURCE_INFO[name][0]:<30} {rows:>8,} rows")
    return 0 if counts.get("latest_values") else 1


if __name__ == "__main__":
    raise SystemExit(main())
