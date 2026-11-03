"""Static, search-engine-friendly website generated from ``data/exports``.

Streamlit apps render with JavaScript, which search engines index poorly. This
module writes plain HTML pages that load instantly and rank well:

* ``/``                      home: search, highlights, every indicator
* ``/country/<slug>/``       one page per country (≈240): key figures, where the money goes,
                             who it owes, who owes it, FAQ — all in readable sentences
* ``/indicator/<slug>/``     one page per indicator (37): full ranking with sources
* ``/data/``                 every dataset with download links (Google Dataset Search)
* ``/support/``              donation options (PATREON_URL, CRYPTO_DONATIONS) and other ways to help
* ``sitemap.xml``, ``robots.txt``, ``404.html``

Every page carries a unique title and description with real numbers, a canonical
URL, Open Graph tags and schema.org JSON-LD (Dataset, BreadcrumbList, FAQPage).

    python -m src.website build --out site --base-url https://jaudat150.github.io/global-debt-explorer/ \\
                                --app-url https://<your-app>.streamlit.app

No network access: it only reads the exports built by ``python -m src.exports build``.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import pandas as pd

from debt_atlas._meta import RAW_BASE, REPO_URL, SITE_URL
from src.catalog import CATEGORIES, CATEGORY_BY_ID, COMMON_NAMES, INDICATOR_BY_ID, INDICATORS, SOURCE_DIRECTORY
from src.indicator_data import format_value

EXPORT_DIR = Path(__file__).resolve().parents[1] / "data" / "exports"
SITE_NAME = "Global Debt & Fiscal Atlas"
AUTHOR = "Jaudat Faisal Al-Husein"
UNINHABITED = {"ATA", "BVT", "HMD", "SGS", "UMI", "ATF", "IOT"}
HEADLINE = ("gov_debt_gdp", "fiscal_balance_gdp", "interest_gdp", "military_usd", "gdp_usd", "gdp_growth", "inflation", "population")


def slugify(text: str) -> str:
    """'Côte d'Ivoire' → 'cote-divoire'."""
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = ascii_text.replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-") or "x"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class SiteData:
    countries: pd.DataFrame
    latest: pd.DataFrame
    timeseries: pd.DataFrame
    spending: pd.DataFrame
    profiles: pd.DataFrame
    bilateral: pd.DataFrame
    generated: str

    @classmethod
    def load(cls, folder: Path = EXPORT_DIR) -> "SiteData":
        def read(name: str) -> pd.DataFrame:
            path = folder / name
            if not path.exists():
                raise FileNotFoundError(f"{path} missing — run `python -m src.exports build` first")
            return pd.read_csv(path, keep_default_na=False, na_values=[""])

        countries = read("countries.csv")
        countries = countries[~countries["iso3"].isin(UNINHABITED)].copy()
        countries["name"] = [COMMON_NAMES.get(code, name) for code, name in zip(countries["iso3"], countries["name"])]
        countries["slug"] = countries["name"].map(slugify)
        countries.loc[countries["slug"].duplicated(keep=False), "slug"] = (
            countries["slug"] + "-" + countries["iso3"].str.lower())
        stamp = datetime.fromtimestamp((folder / "latest_values.csv").stat().st_mtime, timezone.utc)
        return cls(countries=countries.reset_index(drop=True), latest=read("latest_values.csv"),
                   timeseries=read("timeseries.csv.gz"), spending=read("spending_usd.csv"),
                   profiles=read("national_debt_profiles.csv"), bilateral=read("bilateral_debt.csv.gz"),
                   generated=stamp.strftime("%Y-%m-%d"))

    @property
    def names(self) -> dict[str, str]:
        return dict(zip(self.countries["iso3"], self.countries["name"]))

    @property
    def slugs(self) -> dict[str, str]:
        return dict(zip(self.countries["iso3"], self.countries["slug"]))

    def value(self, indicator: str, iso3: str) -> dict[str, Any] | None:
        rows = self.latest[(self.latest["indicator_id"] == indicator) & (self.latest["iso3"] == iso3)]
        return rows.iloc[0].to_dict() if len(rows) else None

    def flows(self, iso3: str, *, as_debtor: bool) -> tuple[pd.DataFrame, int | None]:
        key = "debtor_iso3" if as_debtor else "creditor_iso3"
        rows = self.bilateral[(self.bilateral[key] == iso3) & (self.bilateral["amount_usd"] > 0)]
        if rows.empty:
            return rows, None
        year = int(rows["year"].max())
        rows = rows[rows["year"] == year]
        group = ["creditor_name", "creditor_iso3"] if as_debtor else ["debtor_name", "debtor_iso3"]
        grouped = rows.groupby(group, as_index=False, dropna=False)["amount_usd"].sum().sort_values("amount_usd", ascending=False)
        grouped["share"] = grouped["amount_usd"] / grouped["amount_usd"].sum() * 100
        return grouped, year


def fmt(row: dict[str, Any] | None, unit: str) -> str:
    return format_value(float(row["value"]), unit) if row else "—"


def money(value: float) -> str:
    return format_value(float(value), "usd")


# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------

@dataclass
class Page:
    path: str            # e.g. "country/kenya/" ("" for home)
    title: str
    description: str
    body: str
    jsonld: list[dict[str, Any]]
    priority: float = 0.6


CSS = """
:root{--ink:#17243a;--muted:#5d6b80;--line:#e3e8ef;--paper:#fff;--canvas:#f5f7f9;--blue:#2a78d6;--blue-d:#1c5cab;--orange:#eb6834;--teal:#16877b}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans",Arial,sans-serif;color:var(--ink);background:var(--canvas)}
a{color:var(--blue-d)}a:hover{color:var(--ink)}
header.top{background:var(--paper);border-bottom:1px solid var(--line)}
.wrap{max-width:1080px;margin:0 auto;padding:0 16px}
.top .wrap{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:56px;flex-wrap:wrap}
.brand{font-weight:800;text-decoration:none;color:var(--ink);letter-spacing:-.01em}
nav a{margin-left:14px;text-decoration:none;font-size:14px;color:var(--muted)}nav a:hover{color:var(--ink)}
main{padding:24px 0 48px}
.crumbs{font-size:13px;color:var(--muted);margin-bottom:8px}.crumbs a{color:var(--muted)}
h1{font-size:clamp(26px,4vw,38px);line-height:1.15;letter-spacing:-.02em;margin:.2em 0 .4em}
h2{font-size:22px;margin:1.8em 0 .5em;letter-spacing:-.01em}
.lead{font-size:18px;color:#2b3a52;max-width:820px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:18px 0}
.card{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .k{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700}
.card .v{font-size:22px;font-weight:800;margin-top:2px}.card .s{font-size:12px;color:var(--muted)}
table{width:100%;border-collapse:collapse;background:var(--paper);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:#f0f3f7;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tablewrap{overflow-x:auto}
.bar{display:inline-block;height:8px;border-radius:4px;background:var(--blue);vertical-align:middle;margin-right:6px}
.btn{display:inline-block;background:var(--blue);color:#fff;text-decoration:none;padding:10px 16px;border-radius:8px;font-weight:700;margin:4px 8px 4px 0}
.btn.alt{background:var(--paper);color:var(--ink);border:1px solid var(--line)}
.note{font-size:13px;color:var(--muted)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}
.faq dt{font-weight:700;margin-top:12px}.faq dd{margin:4px 0 0}
code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}
pre{background:#0f1b2d;color:#e6edf6;padding:12px 14px;border-radius:8px;overflow-x:auto}
.search{display:flex;gap:8px;max-width:520px;margin:14px 0}
.search input{flex:1;padding:11px 12px;border:1px solid var(--line);border-radius:8px;font-size:16px}
.pill{display:inline-block;background:#e8f0fb;color:var(--blue-d);border-radius:999px;padding:2px 10px;font-size:12px;font-weight:700}
ul.cols{columns:3 220px;padding-left:18px}
footer{border-top:1px solid var(--line);background:var(--paper);font-size:13px;color:var(--muted);padding:20px 0}
@media (prefers-color-scheme:dark){:root{--ink:#e8edf5;--muted:#9aa8bb;--line:#2a3445;--paper:#161d29;--canvas:#0f141d}
 .lead{color:#c9d3e1}th{background:#1c2433}.pill{background:#1d2a40;color:#8fb8f0}a{color:#8fb8f0}}
"""


def render(page: Page, *, base_url: str, app_url: str | None, generated: str, verification: str | None) -> str:
    depth = page.path.count("/")
    root = "../" * depth
    canonical = base_url.rstrip("/") + "/" + page.path
    jsonld = "".join(f'<script type="application/ld+json">{json.dumps(item, ensure_ascii=False)}</script>' for item in page.jsonld)
    app_link = f'<a href="{esc(app_url)}">Interactive app</a>' if app_url else ""
    verify = f'<meta name="google-site-verification" content="{esc(verification)}">' if verification else ""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(page.title)}</title>
<meta name="description" content="{esc(page.description)}">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:type" content="website"><meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{esc(page.title)}"><meta property="og:description" content="{esc(page.description)}">
<meta property="og:url" content="{esc(canonical)}"><meta name="twitter:card" content="summary">
<meta name="author" content="{AUTHOR}">{verify}
<link rel="stylesheet" href="{root}style.css">
{jsonld}
</head><body>
<header class="top"><div class="wrap"><a class="brand" href="{root or './'}">🌐 {SITE_NAME}</a>
<nav><a href="{root}indicator/">Indicators</a><a href="{root}country/">Countries</a><a href="{root}data/">Data &amp; API</a>{app_link}<a href="{REPO_URL}">GitHub</a><a href="{root}support/">❤ Support</a></nav></div></header>
<main><div class="wrap">{page.body}</div></main>
<footer><div class="wrap">{SITE_NAME} — open source (MIT) by {AUTHOR} and contributors · Data: World Bank, IMF, SIPRI, WHO, UNESCO and other
providers named on each value · Updated {generated} · <a href="{REPO_URL}">Source code</a> · <a href="{root}data/">Download the data</a> · <a href="{root}support/">Support the project</a></div></footer>
</body></html>
"""


def breadcrumb(items: list[tuple[str, str]], base_url: str, page_path: str) -> tuple[str, dict[str, Any]]:
    """``items`` are (name, relative href); absolute URLs for the schema are resolved against ``page_path``."""
    from urllib.parse import urljoin

    page_url = base_url.rstrip("/") + "/" + page_path
    links = " › ".join(f'<a href="{esc(href)}">{esc(name)}</a>' if href else esc(name) for name, href in items)
    schema = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": name, "item": urljoin(page_url, href) if href else page_url}
        for i, (name, href) in enumerate(items)]}
    return f'<div class="crumbs">{links}</div>', schema


def app_deeplink(app_url: str | None, **params: str) -> str | None:
    return f"{app_url.rstrip('/')}/?{urlencode(params)}" if app_url else None


def sparkline(points: list[tuple[int, float]], width: int = 240, height: int = 48) -> str:
    if len(points) < 2:
        return ""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span_x, span_y = (x1 - x0) or 1, (y1 - y0) or 1
    coords = " ".join(f"{(x - x0) / span_x * (width - 8) + 4:.1f},{height - 4 - (y - y0) / span_y * (height - 8):.1f}" for x, y in points)
    last_x, last_y = coords.split(" ")[-1].split(",")
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="trend {x0}–{x1}">'
            f'<polyline fill="none" stroke="#2a78d6" stroke-width="2" points="{coords}"/>'
            f'<circle cx="{last_x}" cy="{last_y}" r="3.5" fill="#2a78d6"/></svg>')


def dataset_schema(name: str, description: str, url: str, *, keywords: list[str], variables: list[str],
                   spatial: str | None, generated: str, files: Iterable[str]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "@context": "https://schema.org", "@type": "Dataset", "name": name, "description": description, "url": url,
        "keywords": keywords, "variableMeasured": variables, "dateModified": generated, "isAccessibleForFree": True,
        "creator": {"@type": "Person", "name": AUTHOR, "url": REPO_URL},
        "isBasedOn": ["https://data.worldbank.org/", "https://www.imf.org/external/datamapper/", "https://www.sipri.org/databases/milex"],
        "temporalCoverage": f"2000/{generated[:4]}",
        "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": f"{RAW_BASE}/{f}"} for f in files],
    }
    if spatial:
        schema["spatialCoverage"] = {"@type": "Place", "name": spatial}
    return schema


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def country_page(data: SiteData, iso3: str, *, base_url: str, app_url: str | None) -> Page:
    name = data.names[iso3]
    slug = data.slugs[iso3]
    path = f"country/{slug}/"
    v = {iid: data.value(iid, iso3) for iid in INDICATOR_BY_ID}

    # Lead sentence, built only from values that exist.
    facts = []
    if v["gov_debt_gdp"]:
        facts.append(f"{name}'s government debt is {fmt(v['gov_debt_gdp'], 'percent_of_gdp')} ({int(v['gov_debt_gdp']['year'])})")
    if v["fiscal_balance_gdp"]:
        balance = float(v["fiscal_balance_gdp"]["value"])
        facts.append(f"a budget {'deficit' if balance < 0 else 'surplus'} of {abs(balance):.1f}% of GDP")
    if v["military_usd"]:
        facts.append(f"military spending of {fmt(v['military_usd'], 'usd')} ({int(v['military_usd']['year'])})")
    lenders, lend_year = data.flows(iso3, as_debtor=True)
    borrowers, owed_year = data.flows(iso3, as_debtor=False)
    sovereign_lenders = lenders[lenders["creditor_iso3"].fillna("").astype(str).str.len() == 3]
    if len(sovereign_lenders):
        top = sovereign_lenders.iloc[0]
        facts.append(f"its largest bilateral lender is {data.names.get(top['creditor_iso3'], top['creditor_name'])} ({money(top['amount_usd'])})")
    lead = (("; ".join(facts) + ".") if facts else f"Public finance, debt and spending data for {name}.").replace("'s government", "’s government")
    lead = lead[0].upper() + lead[1:]

    summary = "; ".join(facts[:3]).replace(f"{name}'s", f"{name}’s")
    description = summary[0].upper() + summary[1:] + f". Who {name} owes, where its government spends money, and 37 indicators with sources." if facts else ""
    if not facts:
        description = f"{name}: government debt, spending, who it owes and 37 public-finance indicators from the World Bank and IMF, with sources."

    crumbs_html, crumbs_schema = breadcrumb([("Home", "../../"), ("Countries", "../"), (name, "")], base_url, path)
    cards = "".join(
        f'<div class="card"><div class="k">{esc(INDICATOR_BY_ID[iid].short)}</div><div class="v">{esc(fmt(v[iid], INDICATOR_BY_ID[iid].unit))}</div>'
        f'<div class="s">{int(v[iid]["year"])}{" est." if v[iid].get("is_estimate") in (True, "True") else ""} · {esc(v[iid]["source"])}</div></div>'
        for iid in HEADLINE if v.get(iid))

    debt_series = data.timeseries[(data.timeseries["iso3"] == iso3) & (data.timeseries["series_code"] == "GGXWDG_NGDP")]
    spark = sparkline(sorted(zip(debt_series["year"].astype(int), debt_series["value"].astype(float))))
    spark_html = f'<p class="note">Government debt, % of GDP, {int(debt_series["year"].min())}–{int(debt_series["year"].max())}:<br>{spark}</p>' if spark else ""

    all_spending = data.spending[data.spending["iso3"] == iso3]
    total_row = all_spending[all_spending["item_id"] == "total"]
    spending = all_spending[all_spending["item_id"] != "total"].sort_values("usd", ascending=False)
    total_note = (f'<p>Total government spending: <strong>{esc(money(total_row.iloc[0].usd))}</strong> '
                  f'({total_row.iloc[0].pct_gdp:.1f}% of GDP, {int(total_row.iloc[0].year)}).</p>') if len(total_row) else ""
    if len(spending):
        biggest = spending["usd"].max()
        rows = "".join(
            f'<tr><td>{esc(r.item)}</td><td class="n"><span class="bar" style="width:{max(2, r.usd / biggest * 120):.0f}px"></span>{esc(money(r.usd))}</td>'
            f'<td class="n">{"" if pd.isna(r.pct_gdp) else f"{r.pct_gdp:.1f}%"}</td>'
            f'<td class="n">{"" if pd.isna(r.usd_per_person) else f"US${r.usd_per_person:,.0f}"}</td><td class="n">{int(r.year)}</td></tr>'
            for r in spending.itertuples())
        spending_html = (f'<h2 id="spending">Where {esc(name)}’s government spends its money</h2>{total_note}'
                         '<div class="tablewrap"><table><thead><tr><th>Spending line</th><th class="n">US$</th><th class="n">% of GDP</th>'
                         f'<th class="n">Per person</th><th class="n">Year</th></tr></thead><tbody>{rows}</tbody></table></div>'
                         '<p class="note">Lines come from different sources, scopes and years and do not add up to the total. '
                         'Military: SIPRI; health: WHO; education and R&amp;D: UNESCO; interest, totals: IMF.</p>')
    else:
        spending_html = ""

    def flow_table(frame: pd.DataFrame, year: int | None, who: str, code_col: str, name_col: str) -> str:
        if frame.empty:
            return f'<p class="note">No bilateral lending recorded for {esc(name)} in this role (World Bank IDS covers ~120 low- and middle-income borrowers).</p>'
        rows = []
        for r in frame.head(12).itertuples():
            code = str(getattr(r, code_col) or "")
            other = data.names.get(code, getattr(r, name_col))
            label = f'<a href="../{esc(data.slugs[code])}/">{esc(other)}</a>' if code in data.slugs else esc(other)
            rows.append(f'<tr><td>{label}</td><td class="n">{esc(money(r.amount_usd))}</td><td class="n">{r.share:.1f}%</td></tr>')
        more = f'<p class="note">+{len(frame) - 12} more.</p>' if len(frame) > 12 else ""
        return (f'<div class="tablewrap"><table><thead><tr><th>{who}</th><th class="n">Debt outstanding ({year})</th><th class="n">Share</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>{more}')

    flows_html = (f'<h2 id="who-owes">Who {esc(name)} owes — and who owes {esc(name)}</h2><div class="grid2">'
                  f'<div><h3>{esc(name)} borrowed from</h3>{flow_table(lenders, lend_year, "Lender", "creditor_iso3", "creditor_name")}</div>'
                  f'<div><h3>Owe {esc(name)}</h3>{flow_table(borrowers, owed_year, "Borrower", "debtor_iso3", "debtor_name")}</div></div>'
                  '<p class="note">Public and publicly guaranteed debt owed to official bilateral creditors (World Bank International Debt Statistics).</p>')

    table_rows = []
    for category in CATEGORIES:
        for indicator in (i for i in INDICATORS if i.category == category.id):
            row = v.get(indicator.id)
            table_rows.append(
                f'<tr><td><a href="../../indicator/{slugify(indicator.id)}/">{esc(indicator.name)}</a></td>'
                f'<td class="n">{esc(fmt(row, indicator.unit))}</td><td class="n">{int(row["year"]) if row else "—"}</td>'
                f'<td>{esc(row["source"]) if row else "<span class=note>No value in live sources</span>"}</td></tr>')
    figures_html = ('<h2 id="all-figures">All figures</h2><div class="tablewrap"><table><thead><tr><th>Indicator</th><th class="n">Value</th>'
                    f'<th class="n">Year</th><th>Source</th></tr></thead><tbody>{"".join(table_rows)}</tbody></table></div>')

    profile = data.profiles[data.profiles["iso3"] == iso3]
    profile_html = ""
    if len(profile) and bool(profile.iloc[0]["data_available"]):
        p = profile.iloc[0]
        tier = {1: "World Bank", 2: "IMF", 3: "curated estimate"}.get(int(p["fallback_tier"]) if not pd.isna(p["fallback_tier"]) else 0, "")
        profile_html = (f'<p><span class="pill">National debt profile · {esc(tier)}</span> {esc(p["metric_label"])}: '
                        f'<strong>{esc(p["display_value"])}</strong> ({esc("" if pd.isna(p["observation_year"]) else int(p["observation_year"]))}).</p>')

    faq = []
    if v["gov_debt_gdp"]:
        faq.append((f"How much debt does {name} have?",
                    f"{name}’s general government gross debt is {fmt(v['gov_debt_gdp'], 'percent_of_gdp')} in {int(v['gov_debt_gdp']['year'])} "
                    f"({v['gov_debt_gdp']['source']})."
                    + (f" Its total external debt is {fmt(v['external_debt_usd'], 'usd')} ({int(v['external_debt_usd']['year'])}, World Bank)." if v["external_debt_usd"] else "")))
    if len(sovereign_lenders):
        names = ", ".join(f"{data.names.get(r.creditor_iso3, r.creditor_name)} ({money(r.amount_usd)})" for r in sovereign_lenders.head(3).itertuples())
        faq.append((f"Who does {name} owe money to?",
                    f"In {lend_year}, {name}’s largest official bilateral lenders were {names}."))
    if len(borrowers):
        names = ", ".join(f"{data.names.get(r.debtor_iso3, r.debtor_name)} ({money(r.amount_usd)})" for r in borrowers.head(3).itertuples())
        faq.append((f"Which countries owe {name} money?", f"In {owed_year}, the largest borrowers from {name} were {names}."))
    military = spending[spending["item_id"] == "military"]
    if len(military):
        m = military.iloc[0]
        faq.append((f"How much does {name} spend on its military?",
                    f"{money(m.usd)} in {int(m.year)}" + (f", {m.pct_gdp:.1f}% of GDP" if not pd.isna(m.pct_gdp) else "") + " (SIPRI)."))
    health = spending[spending["item_id"] == "health"]
    if len(health):
        h = health.iloc[0]
        faq.append((f"How much does {name}’s government spend on health care?",
                    f"{money(h.usd)} in {int(h.year)}, {h.pct_gdp:.1f}% of GDP (WHO Global Health Expenditure Database)."))
    faq_html = ('<h2 id="faq">Frequently asked questions</h2><dl class="faq">'
                + "".join(f"<dt>{esc(q)}</dt><dd>{esc(a)}</dd>" for q, a in faq) + "</dl>") if faq else ""

    deeplink = app_deeplink(app_url, view="country", country=iso3)
    actions = ((f'<a class="btn" href="{esc(deeplink)}">Explore {esc(name)} interactively</a>' if deeplink else "")
               + f'<a class="btn alt" href="#who-owes">Who {esc(name)} owes</a><a class="btn alt" href="#spending">Spending</a>')
    library = (f'<h2>Use this data</h2><pre>pip install debt-atlas\n\nimport debt_atlas as da\n'
               f'da.latest("gov_debt_gdp", countries=["{iso3}"])\nda.who_owes("{iso3}")\nda.spending("{iso3}")</pre>'
               f'<p class="note">Or download every dataset as CSV on the <a href="../../data/">data page</a>.</p>')

    body = (f'{crumbs_html}<h1>{esc(name)}: government debt, spending and who it owes</h1><p class="lead">{esc(lead)}</p>'
            f'{actions}<div class="cards">{cards}</div>{spark_html}{profile_html}{spending_html}{flows_html}{figures_html}{faq_html}{library}'
            f'<p class="note">Updated {data.generated}. IMF current-year values are staff estimates. Missing values are not zero.</p>')

    schemas = [crumbs_schema, dataset_schema(
        f"{name} public debt, government spending and bilateral lending data",
        description, base_url.rstrip("/") + "/" + path,
        keywords=[f"{name} debt", f"{name} government debt", f"{name} external debt", f"{name} military spending",
                  f"who does {name} owe", "public finance", "World Bank", "IMF"],
        variables=[INDICATOR_BY_ID[i].name for i in INDICATOR_BY_ID if v.get(i)], spatial=name,
        generated=data.generated, files=["latest_values.csv", "spending_usd.csv", "bilateral_debt.csv.gz"])]
    if faq:
        schemas.append({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]})
    title = f"{name} Debt, Government Spending & Who It Owes ({data.generated[:4]}) | {SITE_NAME}"
    return Page(path, title, description[:300], body, schemas, priority=0.8)


def indicator_page(data: SiteData, indicator_id: str, *, base_url: str, app_url: str | None) -> Page:
    indicator = INDICATOR_BY_ID[indicator_id]
    path = f"indicator/{slugify(indicator_id)}/"
    rows = data.latest[(data.latest["indicator_id"] == indicator_id) & data.latest["iso3"].isin(data.slugs)].sort_values("value", ascending=False)
    latest_year = int(rows["year"].max()) if len(rows) else None
    top = rows.head(3)
    top_text = ", ".join(f"{data.names[r.iso3]} ({format_value(r.value, indicator.unit)})" for r in top.itertuples())
    description = (f"{indicator.name} for {len(rows)} countries, latest {latest_year}. Highest: {top_text}. "
                   f"Ranking with year and source for every country.") if len(rows) else indicator.description
    crumbs_html, crumbs_schema = breadcrumb([("Home", "../../"), ("Indicators", "../"), (indicator.name, "")], base_url, path)
    biggest = rows["value"].abs().max() if len(rows) else 1
    table = "".join(
        f'<tr><td class="n">{rank}</td><td><a href="../../country/{esc(data.slugs[r.iso3])}/">{esc(data.names[r.iso3])}</a></td>'
        f'<td class="n"><span class="bar" style="width:{max(2, abs(r.value) / biggest * 120):.0f}px"></span>{esc(format_value(r.value, indicator.unit))}</td>'
        f'<td class="n">{int(r.year)}{" est." if r.is_estimate in (True, "True") else ""}</td><td>{esc(r.source)}</td></tr>'
        for rank, r in enumerate(rows.itertuples(), start=1))
    series = "".join(f'<li><a href="{esc(s.portal_url)}">{esc(s.label)} · {esc(s.code)}</a> — <a href="{esc(s.api_url)}">API</a></li>'
                     for s in indicator.sources)
    newer = "".join(f'<li><a href="{esc(SOURCE_DIRECTORY[k].url)}">{esc(SOURCE_DIRECTORY[k].name)}</a> · {esc(SOURCE_DIRECTORY[k].publisher)}</li>'
                    for k in indicator.directory)
    deeplink = app_deeplink(app_url, view="atlas", indicator=indicator_id)
    body = (f'{crumbs_html}<h1>{esc(indicator.name)} by country</h1>'
            f'<p class="lead">{esc(indicator.description)} {esc(indicator.caveat)}</p>'
            + (f'<a class="btn" href="{esc(deeplink)}">See it on the interactive map</a>' if deeplink else "")
            + f'<p><span class="pill">{esc(CATEGORY_BY_ID[indicator.category].label)}</span> {len(rows)} countries · latest year {latest_year or "—"}</p>'
            f'<div class="tablewrap"><table><thead><tr><th class="n">#</th><th>Country</th><th class="n">Value</th><th class="n">Year</th><th>Source</th></tr></thead>'
            f'<tbody>{table}</tbody></table></div>'
            f'<h2>Sources</h2><ul>{series}</ul><h2>Newer or more detailed data</h2><ul>{newer}</ul>'
            f'<h2>Use this data</h2><pre>import debt_atlas as da\nda.rank("{indicator_id}", n=20)\nda.series("{indicator_id}", ["KEN", "EGY"], start=2010)</pre>')
    schema = dataset_schema(f"{indicator.name} — all countries", description, base_url.rstrip("/") + "/" + path,
                            keywords=[indicator.name, f"{indicator.short} by country", "ranking", "World Bank", "IMF"],
                            variables=[indicator.name], spatial="World", generated=data.generated,
                            files=["latest_values.csv", "timeseries.csv.gz"])
    title = f"{indicator.name} by Country — {latest_year or ''} Ranking | {SITE_NAME}"
    return Page(path, title, description[:300], body, [crumbs_schema, schema], priority=0.7)


def index_pages(data: SiteData, *, base_url: str, app_url: str | None) -> list[Page]:
    countries = "".join(f'<li><a href="{esc(r.slug)}/">{esc(r.name)}</a></li>' for r in data.countries.sort_values("name").itertuples())
    crumbs_c, schema_c = breadcrumb([("Home", "../"), ("Countries", "")], base_url, "country/")
    country_index = Page("country/", f"Debt and Government Spending by Country — All Countries | {SITE_NAME}",
                         f"Government debt, spending, and who owes whom for {len(data.countries)} countries and territories.",
                         f'{crumbs_c}<h1>All countries</h1><ul class="cols">{countries}</ul>', [schema_c], priority=0.7)
    sections = []
    for category in CATEGORIES:
        items = "".join(f'<li><a href="{slugify(i.id)}/">{esc(i.name)}</a></li>' for i in INDICATORS if i.category == category.id)
        sections.append(f"<h2>{category.icon} {esc(category.label)}</h2><p>{esc(category.description)}</p><ul>{items}</ul>")
    crumbs_i, schema_i = breadcrumb([("Home", "../"), ("Indicators", "")], base_url, "indicator/")
    indicator_index = Page("indicator/", f"Public Finance Indicators by Country — {len(INDICATORS)} Rankings | {SITE_NAME}",
                           "Rankings of government debt, deficits, military, health and education spending, taxes, growth, "
                           "inflation and more for every country.", f'{crumbs_i}<h1>All indicators</h1>{"".join(sections)}',
                           [schema_i], priority=0.7)
    return [country_index, indicator_index]


def data_page(data: SiteData, *, base_url: str) -> Page:
    from src.exports import RESOURCE_INFO

    rows = "".join(f'<tr><td><a href="{RAW_BASE}/{path}">{esc(path)}</a></td><td>{esc(desc)}</td></tr>' for path, desc in RESOURCE_INFO.values())
    body = (f'<h1>Download the data</h1><p class="lead">Every dataset of the {SITE_NAME} as CSV, rebuilt weekly from the World Bank and '
            'IMF APIs and validated against published figures. Each row names its source, series code and year.</p>'
            f'<div class="tablewrap"><table><thead><tr><th>File</th><th>Contents</th></tr></thead><tbody>{rows}'
            f'<tr><td><a href="{RAW_BASE}/datapackage.json">datapackage.json</a></td><td>Frictionless Data Package: every column documented</td></tr>'
            f'<tr><td><a href="{RAW_BASE}/catalog.json">catalog.json</a></td><td>Every indicator with its upstream API URLs</td></tr>'
            '</tbody></table></div>'
            '<h2>Python</h2><pre>pip install debt-atlas\n\nimport debt_atlas as da\nda.rank("military_usd", n=10)\nda.who_owes("Pakistan")\n'
            'da.spending("United States")\nda.download_all("atlas-data")</pre>'
            f'<h2>pandas / R</h2><pre>pd.read_csv("{RAW_BASE}/latest_values.csv")\nreadr::read_csv("{RAW_BASE}/latest_values.csv")</pre>'
            f'<h2>Cite</h2><p>Cite the original providers (see each row’s <code>source</code>) and this project — '
            f'<a href="{REPO_URL}/blob/main/CITATION.cff">CITATION.cff</a>.</p>')
    description = (f"Download sovereign debt, government spending, bilateral lending and {len(INDICATORS)} public-finance indicators for "
                   "every country as CSV — World Bank and IMF data with provenance, updated weekly.")
    schema = dataset_schema(f"{SITE_NAME} — complete dataset", description, base_url.rstrip("/") + "/data/",
                            keywords=["sovereign debt dataset", "government spending dataset", "bilateral debt", "public finance data",
                                      "World Bank", "IMF", "open data"],
                            variables=[i.name for i in INDICATORS], spatial="World", generated=data.generated,
                            files=[p for p, _ in RESOURCE_INFO.values()])
    return Page("data/", f"Download Global Debt & Government Spending Data (CSV) | {SITE_NAME}", description, body, [schema], priority=0.9)


def home_page(data: SiteData, *, base_url: str, app_url: str | None) -> Page:
    def top(indicator_id: str, n: int = 5) -> str:
        indicator = INDICATOR_BY_ID[indicator_id]
        rows = data.latest[(data.latest["indicator_id"] == indicator_id) & data.latest["iso3"].isin(data.slugs)].nlargest(n, "value")
        items = "".join(f'<li><a href="country/{esc(data.slugs[r.iso3])}/">{esc(data.names[r.iso3])}</a> — {esc(format_value(r.value, indicator.unit))} ({int(r.year)})</li>'
                        for r in rows.itertuples())
        return f'<div class="card"><div class="k"><a href="indicator/{slugify(indicator_id)}/">{esc(indicator.name)}</a></div><ol>{items}</ol></div>'

    options = "".join(f'<option value="{esc(r.name)}" data-slug="{esc(r.slug)}"></option>' for r in data.countries.sort_values("name").itertuples())
    slug_map = json.dumps({r.name.casefold(): r.slug for r in data.countries.itertuples()})
    app_button = f'<a class="btn" href="{esc(app_url)}">Open the interactive map</a>' if app_url else ""
    body = (f'<h1>Who owes what, who spends what — for every country</h1>'
            f'<p class="lead">Government debt, deficits, military, health and education spending, and who lends to whom, for '
            f'{len(data.countries)} countries — live from the World Bank and IMF, with the source and year of every number. Free and open source.</p>'
            f'<form class="search" onsubmit="var m={slug_map};var s=m[this.q.value.trim().toLowerCase()];if(s){{location.href=\'country/\'+s+\'/\'}}return false">'
            f'<input name="q" list="countries" placeholder="Search a country, e.g. Kenya" aria-label="Country"><button class="btn">Go</button></form>'
            f'<datalist id="countries">{options}</datalist>{app_button}<a class="btn alt" href="data/">Download the data</a>'
            f'<h2>Highlights</h2><div class="cards">{top("gov_debt_gdp")}{top("military_usd")}{top("interest_gdp")}{top("health_gov_gdp")}</div>'
            f'<h2>Browse</h2><p><a href="indicator/">All {len(INDICATORS)} indicators</a> · <a href="country/">All countries</a></p>'
            '<h2>For researchers and developers</h2><pre>pip install debt-atlas\n\nimport debt_atlas as da\nda.who_owes("Kenya")</pre>'
            f'<p class="note">Updated {data.generated}. Data: World Bank, IMF, SIPRI, WHO, UNESCO. Missing values are never shown as zero.</p>')
    description = ("Free, open-source atlas of government debt, deficits, military, health and education spending and who owes whom "
                   "for every country — World Bank and IMF data with sources, updated weekly.")
    schemas = [
        {"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME, "url": base_url, "description": description,
         "author": {"@type": "Person", "name": AUTHOR}},
        {"@context": "https://schema.org", "@type": "SoftwareSourceCode", "name": SITE_NAME, "codeRepository": REPO_URL,
         "programmingLanguage": "Python", "license": "https://opensource.org/licenses/MIT", "author": {"@type": "Person", "name": AUTHOR}},
    ]
    return Page("", f"{SITE_NAME} — Government Debt, Spending & Who Owes Whom for Every Country", description, body, schemas, priority=1.0)


@dataclass
class Funding:
    """Donation options, read from repository variables at build time (no code change to add them)."""

    patreon_url: str | None = None
    crypto: list[tuple[str, str]] = field(default_factory=list)   # (label, address), e.g. ("USDT (TRC-20)", "T...")

    @classmethod
    def from_values(cls, patreon_url: str | None, crypto_text: str | None) -> "Funding":
        """``crypto_text`` holds one ``label|address`` per line."""
        crypto = []
        for line in (crypto_text or "").splitlines():
            if "|" in line:
                label, address = (part.strip() for part in line.split("|", 1))
                if label and address:
                    crypto.append((label, address))
        url = (patreon_url or "").strip() or None
        return cls(patreon_url=url if url and url.startswith("https://") else None, crypto=crypto)

    @property
    def has_donations(self) -> bool:
        return bool(self.patreon_url or self.crypto)


def support_page(funding: Funding) -> Page:
    parts = [f'<h1>Support the {SITE_NAME}</h1>'
             '<p class="lead">The atlas is free, has no ads and is built in spare time. Donations pay for hosting and the hours '
             'spent keeping every number current and sourced.</p>']
    if funding.patreon_url:
        parts.append(f'<p><a class="btn" href="{esc(funding.patreon_url)}" rel="noopener">❤ Support on Patreon</a></p>')
    if funding.crypto:
        rows = "".join(f'<tr><td>{esc(label)}</td><td><code>{esc(address)}</code></td></tr>' for label, address in funding.crypto)
        parts.append('<h2>Crypto</h2><div class="tablewrap"><table><thead><tr><th>Network</th><th>Address</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></div>'
                     '<p class="note">Send only the named coin on the named network; anything else is lost.</p>')
    if not funding.has_donations:
        parts.append('<p class="note">Donation options are coming soon.</p>')
    parts.append('<h2>Other ways to help</h2><ul>'
                 f'<li><a href="{REPO_URL}">Star the project on GitHub</a> and share it.</li>'
                 f'<li><a href="{REPO_URL}/issues/new/choose">Report a stale or wrong number</a>.</li>'
                 f'<li><a href="{REPO_URL}/blob/main/CONTRIBUTING.md">Contribute</a> an indicator, a fix or a translation.</li>'
                 f'<li>Cite the project in your work (<a href="{REPO_URL}/blob/main/CITATION.cff">CITATION.cff</a>).</li></ul>')
    description = f"Ways to support the free, open-source {SITE_NAME}: donations, contributions and citations."
    return Page("support/", f"Support the project | {SITE_NAME}", description, "".join(parts), [], priority=0.3)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(out: Path, *, base_url: str = SITE_URL, app_url: str | None = None, export_dir: Path = EXPORT_DIR,
          verification: str | None = None, funding: Funding | None = None) -> dict[str, int]:
    data = SiteData.load(export_dir)
    base_url = base_url if base_url.endswith("/") else base_url + "/"
    pages = [home_page(data, base_url=base_url, app_url=app_url), data_page(data, base_url=base_url)]
    pages += index_pages(data, base_url=base_url, app_url=app_url)
    pages += [country_page(data, iso3, base_url=base_url, app_url=app_url) for iso3 in data.countries["iso3"]]
    pages += [indicator_page(data, i.id, base_url=base_url, app_url=app_url) for i in INDICATORS]
    pages.append(support_page(funding or Funding()))

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "style.css").write_text(CSS.strip() + "\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    for page in pages:
        target = out / page.path / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(page, base_url=base_url, app_url=app_url, generated=data.generated, verification=verification), encoding="utf-8")
    not_found = Page("404/", f"Page not found | {SITE_NAME}", "Page not found.",
                     f'<h1>Page not found</h1><p><a href="{base_url}">Go to the home page</a> or browse '
                     f'<a href="{base_url}country/">all countries</a>.</p>', [])
    (out / "404.html").write_text(render(not_found, base_url=base_url, app_url=app_url, generated=data.generated, verification=None)
                                  .replace('href="../style.css"', f'href="{base_url}style.css"'), encoding="utf-8")
    urls = "".join(f"<url><loc>{esc(base_url + p.path)}</loc><lastmod>{data.generated}</lastmod><priority>{p.priority:.1f}</priority></url>"
                   for p in pages)
    (out / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                                     encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base_url}sitemap.xml\n", encoding="utf-8")
    return {"pages": len(pages), "countries": len(data.countries), "indicators": len(INDICATORS)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the static SEO website from data/exports.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, default=Path("site"))
    b.add_argument("--base-url", default=os.getenv("SITE_BASE_URL") or SITE_URL)
    b.add_argument("--app-url", default=os.getenv("APP_URL") or None)
    b.add_argument("--exports", type=Path, default=EXPORT_DIR)
    b.add_argument("--google-verification", default=os.getenv("GOOGLE_SITE_VERIFICATION") or None)
    b.add_argument("--patreon-url", default=os.getenv("PATREON_URL") or None)
    b.add_argument("--crypto", default=os.getenv("CRYPTO_DONATIONS") or None, help='one "label|address" per line')
    args = parser.parse_args(argv)
    stats = build(args.out, base_url=args.base_url, app_url=args.app_url, export_dir=args.exports,
                  verification=args.google_verification, funding=Funding.from_values(args.patreon_url, args.crypto))
    print(f"Built {stats['pages']} pages ({stats['countries']} countries, {stats['indicators']} indicators) → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
