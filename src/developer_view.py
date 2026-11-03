"""Streamlit view for developers and researchers: all data, all APIs, all downloads."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.catalog import INDICATORS, SOURCE_DIRECTORY, markdown_catalog
from src.exports import (
    FIELD_DOCS, REPO_RAW_BASE, RESOURCE_INFO, build_all_frames, build_bundle, catalog_document, countries_frame,
    datapackage, resource_columns,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BUNDLE_KEY = "_gde_dev_bundle"

PROVIDER_DOCS = [
    ("World Bank Indicators API — basic call structure", "https://datahelpdesk.worldbank.org/knowledgebase/articles/898581"),
    ("World Bank Indicators API — overview", "https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api"),
    ("World Bank IDS (debtor × creditor) API guide", "https://worldbank.github.io/debt-data/creditor-composition/creditor-data-python.html"),
    ("IMF DataMapper API", "https://www.imf.org/external/datamapper/api/"),
    ("IMF data portal API (SDMX, incl. GFS COFOG)", "https://data.imf.org/en/Resource-Pages/IMF-API"),
    ("DBnomics (IMF, OECD, Eurostat, BIS… one free API)", "https://db.nomics.world/"),
]

EXAMPLES = {
    "World Bank (curl)": ("bash", 'curl "https://api.worldbank.org/v2/country/KEN;GHA/indicator/MS.MIL.XPND.GD.ZS?format=json&date=2015:2026&per_page=500"'),
    "World Bank (Python)": ("python", '''import requests
url = "https://api.worldbank.org/v2/country/all/indicator/SH.XPD.GHED.GD.ZS"
meta, rows = requests.get(url, params={"format": "json", "per_page": 20000, "date": "2015:2026"}, timeout=30).json()
latest = {}
for r in rows:                      # rows are [{"countryiso3code", "date", "value", ...}]
    if r["value"] is not None and r["countryiso3code"]:
        latest.setdefault(r["countryiso3code"], (int(r["date"]), r["value"]))'''),
    "IMF DataMapper (Python)": ("python", '''import requests
data = requests.get("https://www.imf.org/external/datamapper/api/v1/GGXWDG_NGDP/USA/JPN/KEN", timeout=30).json()
series = data["values"]["GGXWDG_NGDP"]   # {"USA": {"2024": 121.0, ...}, ...}
# Years after the current one are WEO projections — drop them for observations.'''),
    "IMF DataMapper (R)": ("r", '''library(jsonlite)
x <- fromJSON("https://www.imf.org/external/datamapper/api/v1/GGXCNL_NGDP")
kenya <- unlist(x$values$GGXCNL_NGDP$KEN)'''),
    "Spending by function / COFOG via DBnomics (Python)": ("python", '''import requests
# Browse series: https://db.nomics.world/IMF/GFSCOFOG — copy a series code, then:
r = requests.get("https://api.db.nomics.world/v22/series/IMF/GFSCOFOG",
                 params={"observations": 1, "limit": 50, "q": "health"}, timeout=60).json()
for s in r["series"]["docs"]:
    print(s["series_code"], s["series_name"])'''),
}

PYTHON_API = '''# Run from the repository root (pip install -r requirements.txt)
from src.indicator_data import load_layer, format_value
layer = load_layer("gov_debt_gdp")                 # any id from catalog.json
kenya = layer.values["KEN"]                         # CountryValue(value, year, source, is_estimate, ...)
print(format_value(kenya.value, layer.indicator.unit), kenya.year, kenya.source_label)
layer.series["KEN"]                                 # {year: value} history
layer.rows()                                        # tidy rows with provenance

from src.spending import load_spending_layers, country_spending
from src.indicator_data import country_codes
layers = load_spending_layers(country_codes())
lines, missing, total = country_spending("USA", layers)
for l in lines: print(l.item.label, round(l.usd / 1e9, 1), "bn US$", l.year, l.sources)

from src.debt_flows import bilateral_frame, country_flows
flows = country_flows(bilateral_frame(), "CHN")     # .borrowed / .lent lists of Flow
print([(f.borrower_name, f.amount_usd) for f in flows.lent[:5]])

from src.processor import get_country_debt_profile
get_country_debt_profile("SYR")                     # 3-tier national debt profile with resolution_trail

from src.exports import build_all_frames, build_bundle
frames = build_all_frames()                         # dict of pandas DataFrames
open("atlas-data.zip", "wb").write(build_bundle(frames))'''

CLI = '''python -m src.catalog                          # list every indicator and series code
python -m src.catalog --markdown               # regenerate docs/DATA_SOURCES.md
python -m src.indicator_data show military_gdp USA CHN RUS
python -m src.indicator_data refresh           # download everything, update the offline snapshot
python -m src.processor profiles SYR PSE CAN   # national debt profiles + tier trail (--json for all fields)
python -m src.exports build                    # write data/exports/* (CSV + datapackage.json + catalog.json)
python -m src.fetcher                          # rebuild the World Bank IDS bilateral cache'''


def _file_download(label: str, path: Path, mime: str, key: str) -> None:
    if path.exists():
        st.download_button(f"{label} · {path.stat().st_size / 1e6:,.1f} MB ↓", path.read_bytes(), file_name=path.name,
                           mime=mime, key=key, width="stretch")
    else:
        st.button(f"{label} · not generated yet", disabled=True, key=key, width="stretch")


def render_developers() -> None:
    """Every dataset, every API, every way to use this project's data."""
    downloads, static, apis, code, dictionary = st.tabs(
        ["⬇️ Downloads", "🌐 Static data URLs", "🔌 Upstream APIs", "🐍 Python & CLI", "📖 Data dictionary"])

    with downloads:
        st.markdown("#### Everything in one ZIP")
        st.write("Every indicator (latest values **and** full time series), spending in US$, national debt profiles, "
                 "who-owes-whom bilateral debt, the country reference, `catalog.json` and a Frictionless "
                 "`datapackage.json` describing every column.")
        reported_only = st.toggle("Exclude IMF current-year estimates", key="gde_dev_reported_only")
        if st.button("Build the full data bundle", type="primary", key="gde_dev_build"):
            bar = st.progress(0.0, text="Downloading indicators…")
            frames = build_all_frames(include_estimates=not reported_only,
                                      progress=lambda i, n, label: bar.progress(i / n, text=f"Loaded {label}"))
            bar.progress(1.0, text="Packaging…")
            st.session_state[BUNDLE_KEY] = (build_bundle(frames), {name: len(f) for name, f in frames.items()},
                                            {name: f for name, f in frames.items() if name != "timeseries"},
                                            datapackage(frames))
            bar.empty()
        bundle = st.session_state.get(BUNDLE_KEY)
        if bundle:
            payload, counts, frames, _descriptor = bundle
            st.download_button(f"Download global-debt-atlas-data.zip · {len(payload) / 1e6:,.1f} MB ↓", payload,
                               file_name="global-debt-atlas-data.zip", mime="application/zip", key="gde_dev_zip",
                               width="stretch")
            st.dataframe(pd.DataFrame([{"File": RESOURCE_INFO[n][0], "Rows": c, "Contents": RESOURCE_INFO[n][1]}
                                       for n, c in counts.items()]), hide_index=True, width="stretch")
            cols = st.columns(2)
            for position, (name, frame) in enumerate(frames.items()):
                with cols[position % 2]:
                    st.download_button(f"{RESOURCE_INFO[name][0].replace('.gz', '')} only ↓",
                                       frame.to_csv(index=False).encode("utf-8"),
                                       file_name=RESOURCE_INFO[name][0].replace(".gz", ""), mime="text/csv",
                                       key=f"gde_dev_file_{name}", width="stretch")

        st.markdown("#### Available instantly (no API calls)")
        cols = st.columns(2)
        with cols[0]:
            st.download_button("countries.csv — ISO codes, names, coordinates ↓", countries_frame().to_csv(index=False).encode("utf-8"),
                               file_name="countries.csv", mime="text/csv", key="gde_dev_countries", width="stretch")
            st.download_button("catalog.json — every indicator + API URL ↓", json.dumps(catalog_document(), indent=2, ensure_ascii=False),
                               file_name="catalog.json", mime="application/json", key="gde_dev_catalog", width="stretch")
            st.download_button("DATA_SOURCES.md — human-readable source guide ↓", markdown_catalog(),
                               file_name="DATA_SOURCES.md", mime="text/markdown", key="gde_dev_md", width="stretch")
        with cols[1]:
            _file_download("bilateral_debt_cache.json — who owes whom, all years", DATA_DIR / "bilateral_debt_cache.json",
                           "application/json", "gde_dev_bilateral")
            _file_download("indicator_snapshot.json — last saved copy of every series", DATA_DIR / "indicator_snapshot.json",
                           "application/json", "gde_dev_snapshot")
            _file_download("country_label_points.json — coordinates", DATA_DIR / "country_label_points.json",
                           "application/json", "gde_dev_points")

    with static:
        st.write("A GitHub Actions workflow rebuilds these files every week and commits them to the repository, so you can "
                 "read them directly — no server, no key, no rate limit beyond GitHub's.")
        rows = [{"File": path, "URL": f"{REPO_RAW_BASE}/{path}", "Contents": description}
                for path, description in RESOURCE_INFO.values()]
        rows += [{"File": "datapackage.json", "URL": f"{REPO_RAW_BASE}/datapackage.json", "Contents": "Schema of every file"},
                 {"File": "catalog.json", "URL": f"{REPO_RAW_BASE}/catalog.json", "Contents": "Indicators, upstream APIs, sources"}]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"URL": st.column_config.LinkColumn("URL")})
        st.code(f'''import pandas as pd
latest = pd.read_csv("{REPO_RAW_BASE}/latest_values.csv")
spending = pd.read_csv("{REPO_RAW_BASE}/spending_usd.csv")
history = pd.read_csv("{REPO_RAW_BASE}/timeseries.csv.gz")      # gzip is detected automatically
owes = pd.read_csv("{REPO_RAW_BASE}/bilateral_debt.csv.gz")''', language="python")
        st.code(f'''library(readr)
latest <- read_csv("{REPO_RAW_BASE}/latest_values.csv")''', language="r")
        st.caption("Forked the project? Replace `Jaudat150/global-debt-explorer` with your fork and enable Actions.")

    with apis:
        st.write("Every live series this project uses. Click an API link to see the raw JSON.")
        series = pd.DataFrame([
            {"Indicator": i.name, "id": i.id, "Provider": s.provider, "Series code": s.code, "Publisher": s.label,
             "API": s.api_url, "Portal": s.portal_url}
            for i in INDICATORS for s in i.sources])
        st.dataframe(series, hide_index=True, width="stretch", height=420,
                     column_config={"API": st.column_config.LinkColumn("API"), "Portal": st.column_config.LinkColumn("Portal")})
        st.markdown("**Provider documentation**")
        st.markdown("\n".join(f"- [{label}]({url})" for label, url in PROVIDER_DOCS))
        st.markdown("**Examples**")
        for title, (language, snippet) in EXAMPLES.items():
            with st.expander(title):
                st.code(snippet, language=language)
        st.markdown("**Other open data sources with APIs**")
        st.markdown("\n".join(f"- [{s.name}]({s.url}) · {s.publisher} · {s.access}"
                              for s in SOURCE_DIRECTORY.values() if "API" in s.access))

    with code:
        st.markdown("#### Python")
        st.code(PYTHON_API, language="python")
        st.markdown("#### Command line")
        st.code(CLI, language="bash")
        st.markdown("Contribute an indicator in ~10 lines: see `CONTRIBUTING.md`. Cite the project via `CITATION.cff`.")

    with dictionary:
        descriptor = (st.session_state.get(BUNDLE_KEY) or (None, None, None, None))[3]
        for name, columns in resource_columns().items():
            path, description = RESOURCE_INFO[name]
            with st.expander(f"{path} — {description}"):
                st.dataframe(pd.DataFrame([{"Column": c, "Meaning": FIELD_DOCS.get(c, "")} for c in columns]),
                             hide_index=True, width="stretch")
        if descriptor:
            st.download_button("datapackage.json ↓", json.dumps(descriptor, indent=2), file_name="datapackage.json",
                               mime="application/json", key="gde_dev_dp")
        st.markdown("**Licences:** code MIT; data remain under each provider's terms — World Bank (CC BY 4.0), IMF, SIPRI, "
                    "WHO, UNESCO. Always cite the provider shown in each row's `source` column.")
