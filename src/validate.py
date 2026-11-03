"""Validate the published exports: coverage, freshness, sanity and internal consistency.

Reads ``data/exports/`` only (no network), so it runs in seconds after
``python -m src.exports build``::

    python -m src.validate                # human-readable report, exit 1 on failures
    python -m src.validate --out report.md

Checks
------
* **Coverage** — every upstream series in the catalogue returned data; flags
  EMPTY (no rows), THIN (< 30 countries) and STALE (newest year older than 3 years).
* **Reference figures** — headline values must fall inside wide ranges around
  published figures (SIPRI, IMF WEO, UN). Ranges tolerate normal revisions and a
  year or two of change; a miss usually means a unit, multiplier or code error.
* **Consistency** — no future years; shares of GDP in plausible bounds; the
  spending table agrees with the series it was derived from.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.catalog import INDICATOR_BY_ID, INDICATORS

EXPORT_DIR = Path(__file__).resolve().parents[1] / "data" / "exports"
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


@dataclass(frozen=True)
class Reference:
    indicator_id: str
    iso3: str
    low: float
    high: float
    reference: str


# Wide bands around published figures. Update when a band no longer reflects reality.
REFERENCES: tuple[Reference, ...] = (
    Reference("military_usd", "USA", 850e9, 1.15e12, "SIPRI: US$997bn in 2024"),
    Reference("military_usd", "CHN", 220e9, 380e9, "SIPRI estimate: ~US$314bn in 2024"),
    Reference("gdp_usd", "USA", 26e12, 37e12, "IMF WEO: US$29tn (2024), ~US$32.6tn (2026 est.)"),
    Reference("gdp_usd", "CHN", 17e12, 24e12, "IMF WEO: US$19.6tn (2025), US$21.6tn (2026 est.)"),
    Reference("population", "CHN", 1.38e9, 1.43e9, "UN WPP: ~1.41bn"),
    Reference("population", "IND", 1.38e9, 1.50e9, "UN WPP: ~1.45bn"),
    Reference("gov_debt_gdp", "JPN", 180, 250, "IMF: ~207% of GDP, falling toward ~190% (F&D, Sep 2026)"),
    Reference("gov_debt_gdp", "USA", 100, 135, "IMF WEO: ~120% of GDP"),
    Reference("gov_debt_gdp", "DEU", 55, 75, "IMF WEO: ~63% of GDP"),
    Reference("gov_debt_gdp", "SAU", 15, 45, "IMF WEO: ~30% of GDP"),
    Reference("life_expectancy", "JPN", 82, 87, "UN WPP: ~84 years"),
    Reference("gdp_per_capita", "NOR", 75_000, 135_000, "IMF WEO: US$94k (2025), US$115k (2026 est.)"),
)

# Zeros that are correct: countries with no standing armed forces report no military spending.
KNOWN_ZEROS: dict[tuple[str, str], str] = {
    ("CRI", "military"): "Costa Rica abolished its army in 1948",
    ("ISL", "military"): "Iceland has no standing armed forces",
    ("PAN", "military"): "Panama abolished its army in 1990",
}

# Plausible bounds for share-of-GDP indicators (inclusive). Outside = data or unit error.
BOUNDS: dict[str, tuple[float, float]] = {
    "gov_debt_gdp": (0, 400), "fiscal_balance_gdp": (-60, 60), "military_gdp": (0, 45), "health_gov_gdp": (0, 25),
    "education_gdp": (0, 20), "interest_gdp": (0, 25), "rnd_gdp": (0, 8), "gov_expenditure_gdp": (3, 120),
    "gov_revenue_gdp": (1, 150), "tax_revenue_gdp": (0, 70), "central_gov_expense_gdp": (1, 120),
    "unemployment": (0, 60), "life_expectancy": (30, 95), "gini": (15, 75),
}


@dataclass
class Finding:
    status: str
    area: str
    message: str


def _read(directory: Path, name: str) -> pd.DataFrame:
    path = directory / name
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing — run `python -m src.exports build` first")
    return pd.read_csv(path, keep_default_na=False, na_values=[""])


def check_coverage(latest: pd.DataFrame, timeseries: pd.DataFrame, current_year: int) -> tuple[list[Finding], pd.DataFrame]:
    findings: list[Finding] = []
    rows = []
    by_series = timeseries.groupby("series_code").agg(countries=("iso3", "nunique"), newest=("year", "max"))
    for indicator in INDICATORS:
        layer = latest[latest["indicator_id"] == indicator.id]
        for source in indicator.sources:
            stats = by_series.loc[source.code] if source.code in by_series.index else None
            countries = int(stats["countries"]) if stats is not None else 0
            newest = int(stats["newest"]) if stats is not None else None
            if countries == 0:
                status, note = (WARN if len(indicator.sources) > 1 and not layer.empty else FAIL), "EMPTY — check the series code"
            elif countries < 30:
                status, note = WARN, "THIN — under 30 countries"
            elif newest is not None and newest < current_year - 3:
                status, note = WARN, f"STALE — newest year {newest}"
            else:
                status, note = PASS, ""
            rows.append({"indicator": indicator.id, "series": source.code, "provider": source.provider,
                         "countries": countries, "newest_year": newest, "status": status, "note": note})
            if status != PASS:
                findings.append(Finding(status, "coverage", f"{indicator.id} · {source.code}: {note} ({countries} countries)"))
        if layer.empty:
            findings.append(Finding(FAIL, "coverage", f"{indicator.id}: no country has a value"))
    return findings, pd.DataFrame(rows)


def check_references(latest: pd.DataFrame) -> list[Finding]:
    findings = []
    indexed = latest.set_index(["indicator_id", "iso3"])
    for ref in REFERENCES:
        if (ref.indicator_id, ref.iso3) not in indexed.index:
            findings.append(Finding(WARN, "reference", f"{ref.iso3} {ref.indicator_id}: no value to check ({ref.reference})"))
            continue
        row = indexed.loc[(ref.indicator_id, ref.iso3)]
        value, year = float(row["value"]), int(row["year"])
        ok = ref.low <= value <= ref.high
        shown = _fmt(value, INDICATOR_BY_ID[ref.indicator_id].unit)
        findings.append(Finding(PASS if ok else FAIL, "reference",
                                f"{ref.iso3} {ref.indicator_id} = {shown} ({year}) · expected {_fmt(ref.low, INDICATOR_BY_ID[ref.indicator_id].unit)}–"
                                f"{_fmt(ref.high, INDICATOR_BY_ID[ref.indicator_id].unit)} · {ref.reference}"))
    return findings


def check_consistency(latest: pd.DataFrame, spending: pd.DataFrame, current_year: int) -> list[Finding]:
    findings = []
    future = latest[latest["year"] > current_year]
    findings.append(Finding(FAIL if len(future) else PASS, "consistency",
                            f"{len(future)} values dated after {current_year} (projections must never appear)"))
    for indicator_id, (low, high) in BOUNDS.items():
        values = latest[latest["indicator_id"] == indicator_id]
        bad = values[(values["value"] < low) | (values["value"] > high)]
        if len(bad):
            sample = ", ".join(f"{r.iso3}={r.value:,.1f}" for r in bad.head(5).itertuples())
            findings.append(Finding(WARN, "consistency", f"{indicator_id}: {len(bad)} outside {low}–{high} ({sample})"))
    if not spending.empty:
        lines = spending[spending["item_id"] != "total"]

        def describe(rows: pd.DataFrame) -> str:
            return "; ".join(f"{r.iso3} {r.item_id} = {r.usd:,.0f} ({r.year}) from {getattr(r, 'sources', '?')}"
                             for r in rows.head(10).itertuples())

        negative = lines[lines["usd"] < 0]
        findings.append(Finding(FAIL if len(negative) else PASS, "consistency",
                                f"{len(negative)} negative spending lines" + (f": {describe(negative)}" if len(negative) else "")))
        zero = lines[lines["usd"] == 0]
        known = zero[[(iso, item) in KNOWN_ZEROS for iso, item in zip(zero["iso3"], zero["item_id"])]]
        zero = zero.drop(known.index)
        if len(known):
            findings.append(Finding(PASS, "consistency", "Known zeros: " + "; ".join(
                f"{iso} {item} — {KNOWN_ZEROS[(iso, item)]}" for iso, item in zip(known["iso3"], known["item_id"]))))
        if len(zero):
            findings.append(Finding(WARN, "consistency",
                                    f"{len(zero)} spending lines are exactly 0 — valid when the source reports none "
                                    f"(e.g. no armed forces), otherwise check: {describe(zero)}"))
        military = lines[(lines["item_id"] == "military") & (lines["method"] == "Reported in US$ by the source")]
        reported = latest[latest["indicator_id"] == "military_usd"].set_index("iso3")["value"]
        mismatched = [iso for iso, usd in zip(military["iso3"], military["usd"])
                      if iso in reported.index and abs(usd - reported[iso]) > 1]
        findings.append(Finding(FAIL if mismatched else PASS, "consistency",
                                f"Spending tab military figures match SIPRI series ({len(military) - len(mismatched)}/{len(military)})"))
        over = spending[(spending["item_id"] == "total") & (spending["pct_gdp"] > 120)]
        if len(over):
            findings.append(Finding(WARN, "consistency", f"{len(over)} countries with total spending > 120% of GDP"))
    return findings


def _fmt(value: float, unit: str) -> str:
    if unit in {"usd", "usd_per_capita"}:
        for threshold, suffix in ((1e12, "tn"), (1e9, "bn"), (1e6, "m")):
            if abs(value) >= threshold:
                return f"US${value / threshold:,.2f}{suffix}"
        return f"US${value:,.0f}"
    if unit == "people":
        return f"{value / 1e9:,.3f}bn" if value >= 1e9 else f"{value / 1e6:,.1f}m"
    return f"{value:,.1f}"


def run(directory: Path = EXPORT_DIR, current_year: int | None = None) -> tuple[list[Finding], pd.DataFrame]:
    current_year = current_year or datetime.now(timezone.utc).year
    latest = _read(directory, "latest_values.csv")
    timeseries = _read(directory, "timeseries.csv.gz")
    spending = _read(directory, "spending_usd.csv") if (directory / "spending_usd.csv").exists() else pd.DataFrame()
    coverage_findings, coverage = check_coverage(latest, timeseries, current_year)
    findings = coverage_findings + check_references(latest) + check_consistency(latest, spending, current_year)
    return findings, coverage


def render(findings: list[Finding], coverage: pd.DataFrame) -> str:
    counts = {status: sum(f.status == status for f in findings) for status in (PASS, WARN, FAIL)}
    lines = [f"# Data validation — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
             f"**{counts[PASS]} passed · {counts[WARN]} warnings · {counts[FAIL]} failures**", ""]
    for area in ("coverage", "reference", "consistency"):
        items = [f for f in findings if f.area == area and (f.status != PASS or area != "coverage")]
        if items:
            lines += [f"## {area.title()}", ""] + [f"- {f.status} · {f.message}" for f in items] + [""]
    lines += ["## Series coverage", "", "| Indicator | Series | Countries | Newest | Status |", "|---|---|---|---|---|"]
    for row in coverage.itertuples(index=False):
        lines.append(f"| {row.indicator} | {row.series} | {row.countries} | {row.newest_year or '—'} | {row.status} {row.note} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate data/exports.")
    parser.add_argument("--dir", type=Path, default=EXPORT_DIR)
    parser.add_argument("--out", type=Path, help="Also write the report as Markdown")
    args = parser.parse_args(argv)
    try:
        findings, coverage = run(args.dir)
    except FileNotFoundError as exc:
        print(exc)
        return 2
    report = render(findings, coverage)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
    print(report)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    return 1 if any(f.status == FAIL for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
