"""Where each country's money goes, in US dollars — computed only from published data.

For each spending line the amount is either:

* **Reported in US$** by the source (military: SIPRI's ``MS.MIL.XPND.CD``), or
* **Derived** as *published share × GDP in current US$ for the same year*, e.g.
  health = ``SH.XPD.GHED.GD.ZS`` (% of GDP) × GDP. Lines that the source
  publishes as a share of central government expense (wages, subsidies and
  transfers) are multiplied by central government expense (% of GDP) and GDP,
  all for one common year.

No line is ever estimated, interpolated or mixed across years. If the factors
do not share a year, the line is reported as missing. Lines have different
scopes (general vs central government vs all sectors) and do not add up to the
total; the UI says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

import pandas as pd

from src.catalog import INDICATOR_BY_ID
from src.indicator_data import IndicatorLayer, load_layer


@dataclass(frozen=True)
class SpendingItem:
    id: str
    label: str
    icon: str
    scope: str
    chain: tuple[str, ...]          # indicator ids multiplied together (each in %), then × GDP
    direct_usd: str | None = None   # indicator id published directly in US$
    note: str = ""


SPENDING_ITEMS: tuple[SpendingItem, ...] = (
    SpendingItem("military", "Military & defence", "🛡️", "Government", ("military_gdp",), direct_usd="military_usd",
                 note="SIPRI definition: armed forces, defence ministries, paramilitary forces and military space."),
    SpendingItem("health", "Health care", "🏥", "General government", ("health_gov_gdp",),
                 note="Domestic government spending on health; excludes private spending and foreign aid."),
    SpendingItem("education", "Education", "🎓", "General government", ("education_gdp",),
                 note="Public spending on all levels of education."),
    SpendingItem("social", "Subsidies & social transfers", "🤝", "Central government",
                 ("subsidies_pct_expense", "central_gov_expense_gdp"),
                 note="Subsidies, grants and social benefits (pensions, welfare) paid by central government."),
    SpendingItem("wages", "Public-sector wages", "👷", "Central government",
                 ("wages_pct_expense", "central_gov_expense_gdp"),
                 note="Compensation of central government employees."),
    SpendingItem("interest", "Interest on public debt", "💳", "General government", ("interest_gdp",),
                 note="Interest paid to holders of government debt."),
    SpendingItem("rnd", "Research & development", "🔬", "All sectors (not government only)", ("rnd_gdp",),
                 note="Gross domestic R&D by government, business, universities and non-profits."),
)
TOTAL_ITEM = SpendingItem("total", "Total government spending", "🏛️", "General government", ("gov_expenditure_gdp",))
ITEM_BY_ID = {item.id: item for item in (*SPENDING_ITEMS, TOTAL_ITEM)}

REQUIRED_LAYERS: tuple[str, ...] = tuple(dict.fromkeys(
    ["gdp_usd", "population"]
    + [i for item in (*SPENDING_ITEMS, TOTAL_ITEM) for i in item.chain]
    + [item.direct_usd for item in SPENDING_ITEMS if item.direct_usd]
))


@dataclass(frozen=True)
class SpendingLine:
    item: SpendingItem
    iso3: str
    usd: float
    pct_gdp: float | None
    year: int
    per_capita: float | None
    method: str
    sources: str


def load_spending_layers(countries: Iterable[str], include_estimates: bool = True) -> dict[str, IndicatorLayer]:
    """Load every layer the spending calculation needs (cached for 24 h)."""
    codes = tuple(countries)
    return {iid: load_layer(iid, countries=codes, include_estimates=include_estimates) for iid in REQUIRED_LAYERS}


def _series(layer: IndicatorLayer | None, iso3: str, cap: int) -> dict[int, float]:
    if layer is None:
        return {}
    return {year: value for year, value in layer.series.get(iso3, {}).items() if year <= cap}


def _source_label(layer: IndicatorLayer, iso3: str, year: int) -> str:
    """Name the exact series that supplied ``year`` for ``iso3``."""
    for source in layer.indicator.sources:
        if year in layer.raw.get(source.code, {}).get(iso3, {}):
            return f"{source.label} ({source.code})"
    return " / ".join(f"{s.label} ({s.code})" for s in layer.indicator.sources)


def compute_line(
    item: SpendingItem,
    iso3: str,
    layers: Mapping[str, IndicatorLayer],
    *,
    current_year: int | None = None,
    include_estimates: bool = True,
) -> SpendingLine | None:
    """US$ amount for one spending line and country, or ``None`` if the data does not allow it."""
    current_year = current_year or datetime.now(timezone.utc).year
    cap = current_year if include_estimates else current_year - 1
    gdp_layer, pop_layer = layers.get("gdp_usd"), layers.get("population")
    gdp = _series(gdp_layer, iso3, cap)
    population = _series(pop_layer, iso3, cap)

    def per_person(usd: float, year: int) -> float | None:
        people = population.get(year)
        return usd / people if people else None

    if item.direct_usd:
        direct_layer = layers.get(item.direct_usd)
        direct = _series(direct_layer, iso3, cap)
        direct = {year: value for year, value in direct.items() if value >= 0}  # negative spending is a data error
        if direct:
            year = max(direct)
            usd = direct[year]
            pct = usd / gdp[year] * 100 if gdp.get(year) else None
            return SpendingLine(item, iso3, usd, pct, year, per_person(usd, year),
                                "Reported in US$ by the source", _source_label(direct_layer, iso3, year))

    components = [_series(layers.get(iid), iso3, cap) for iid in item.chain]
    if not gdp or not all(components):
        return None
    common = set(gdp).intersection(*(set(c) for c in components))
    if not common:
        return None
    common = {y for y in common if gdp[y] > 0 and all(component[y] >= 0 for component in components)}
    if not common:
        return None
    year = max(common)
    share = 1.0
    for component in components:
        share *= component[year] / 100.0
    usd = share * gdp[year]
    sources = " × ".join(_source_label(layers[iid], iso3, year) for iid in item.chain)
    sources += f" × GDP: {_source_label(gdp_layer, iso3, year)}"
    method = "Share × GDP, same year" if len(item.chain) == 1 else "Share of central expense × expense/GDP × GDP, same year"
    return SpendingLine(item, iso3, usd, share * 100, year, per_person(usd, year), method, sources)


def country_spending(
    iso3: str,
    layers: Mapping[str, IndicatorLayer],
    *,
    current_year: int | None = None,
    include_estimates: bool = True,
) -> tuple[list[SpendingLine], list[SpendingItem], SpendingLine | None]:
    """Return (available lines sorted by US$, missing items, total-spending context line)."""
    lines, missing = [], []
    for item in SPENDING_ITEMS:
        line = compute_line(item, iso3, layers, current_year=current_year, include_estimates=include_estimates)
        (lines.append(line) if line else missing.append(item))
    lines.sort(key=lambda line: line.usd, reverse=True)
    total = compute_line(TOTAL_ITEM, iso3, layers, current_year=current_year, include_estimates=include_estimates)
    return lines, missing, total


def spending_matrix(
    codes: Iterable[str],
    layers: Mapping[str, IndicatorLayer],
    names: Mapping[str, str] | None = None,
    *,
    include_estimates: bool = True,
    current_year: int | None = None,
) -> pd.DataFrame:
    """Tidy table: one row per country × spending line, with method and sources."""
    names = names or {}
    rows = []
    for iso3 in codes:
        for item in (*SPENDING_ITEMS, TOTAL_ITEM):
            line = compute_line(item, iso3, layers, current_year=current_year, include_estimates=include_estimates)
            if line is None:
                continue
            rows.append({
                "iso3": iso3, "country": names.get(iso3, iso3), "item_id": item.id, "item": item.label,
                "scope": item.scope, "usd": line.usd, "pct_gdp": line.pct_gdp, "usd_per_person": line.per_capita,
                "year": line.year, "method": line.method, "sources": line.sources,
            })
    return pd.DataFrame(rows, columns=["iso3", "country", "item_id", "item", "scope", "usd", "pct_gdp",
                                       "usd_per_person", "year", "method", "sources"])


def indicator_names() -> dict[str, str]:
    """Indicator id → name for every layer used, for documentation."""
    return {iid: INDICATOR_BY_ID[iid].name for iid in REQUIRED_LAYERS}
