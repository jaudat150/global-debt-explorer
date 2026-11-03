"""Global Bilateral Debt Explorer — relationship-map dashboard.

The layout is inspired by clean operational analytics dashboards: a large
geographic relationship map with curved links and a floating detail card,
followed by compact debtor/creditor rankings and a country profile.
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import pycountry
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

from src.map_geometry import DEFAULT_GEOJSON_PATH, BoundaryDataError, download_country_geojson
from src.processor import (
    aggregate_debt,
    country_creditor_breakdown,
    creditor_totals,
    format_usd,
    get_country_debt_profiles,
    clear_country_debt_profile_cache,
    summarize_profiles,
    read_cache,
)
from src.relationship_map import build_relationship_map, extract_map_action, flag_emoji, iso3_from_properties
from src.ui_state import apply_pending_creditor_selection, apply_pending_debtor_selection
from src import atlas_view, developer_view, share, spending_view

LOGGER = logging.getLogger("global_debt_explorer.app")
ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "data" / "bilateral_debt_cache.json"

st.set_page_config(
    page_title="Global Debt & Fiscal Atlas",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="collapsed",
)

CSS = r"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Manrope:wght@500;600;700;800&display=swap');
:root{--ink:#17243a;--muted:#738096;--line:#e1e6ed;--paper:#fff;--canvas:#f5f7f9;--blue:#315fce;--green:#149b70;--red:#d9534f;}
html,body,[class*="css"]{font-family:'DM Sans',Inter,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--ink)}
.stApp{background:var(--canvas)}
[data-testid="stVerticalBlockBorderWrapper"]{border:1px solid #dce3eb!important;border-radius:10px!important;background:#fff!important;box-shadow:0 1px 4px rgba(25,43,66,.035)}
[data-testid="stHeader"]{background:rgba(245,247,249,.94)}
.block-container{max-width:1680px;padding-top:3.1rem;padding-bottom:2.5rem}
#MainMenu,footer{visibility:hidden}
header[data-testid="stHeader"]{height:2.2rem}
h1,h2,h3{font-family:Manrope,'DM Sans',sans-serif;color:var(--ink);letter-spacing:-.035em}
.hero{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin:.3rem 0 1rem}
.hero-kicker{font-size:10px;text-transform:uppercase;letter-spacing:.17em;font-weight:800;color:#5472a5;margin-bottom:8px}
.hero-title,#gde-hero-title{font-family:Manrope,sans-serif;font-size:clamp(25px,3vw,38px);line-height:1.08;letter-spacing:-.055em;font-weight:800;color:#19283e!important;opacity:1!important;visibility:visible!important;margin:0}
.hero-subtitle{font-size:13px;line-height:1.55;color:#778397;margin:7px 0 0;max-width:790px}
.hero-right{display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.header-chip{font-size:10px;font-weight:800;letter-spacing:.05em;border:1px solid #dce5f1;color:#47618b;background:#edf3fc;padding:7px 10px;border-radius:999px;white-space:nowrap}
.header-chip.live{background:#edf8f3;color:#16825d;border-color:#d6eee2}
.header-dot{display:inline-block;width:6px;height:6px;background:#16a36d;border-radius:50%;margin-right:6px}
.kpi-strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:.7rem 0 1rem}
.kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 13px;box-shadow:0 1px 3px rgba(24,39,61,.035)}
.kpi-label{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:#8490a1;font-weight:800}
.kpi-value{font-family:Manrope,sans-serif;font-size:20px;letter-spacing:-.04em;font-weight:800;color:#1a2a42;margin-top:3px;line-height:1.2}
.kpi-note{font-size:10px;color:#8b96a5;margin-top:3px}
.map-shell{background:#fff;border:1px solid #dce2e9;border-radius:10px;overflow:hidden;box-shadow:0 2px 7px rgba(25,43,66,.055);margin-bottom:14px}
.map-head{display:flex;justify-content:space-between;align-items:center;padding:12px 15px;border-bottom:1px solid #e6eaf0;gap:12px}
.map-head-title{font-family:Manrope,sans-serif;font-size:14px;font-weight:800;color:#1c2b40}
.map-head-sub{font-size:10px;color:#8994a4;margin-top:2px}
.map-head-right{font-size:10px;font-weight:700;color:#6d7a8e;white-space:nowrap}
.map-caption{font-size:10px;line-height:1.5;color:#8692a2;padding:8px 14px 10px;border-top:1px solid #edf0f4;background:#fff}
.controls-row{background:#fff;border:1px solid var(--line);border-radius:10px;padding:9px 12px;margin:.7rem 0 .8rem}
[data-testid="stSelectbox"] label,[data-testid="stSlider"] label{font-size:10px!important;letter-spacing:.04em;font-weight:800!important;color:#6c7b90!important}
[data-testid="stSelectbox"] [data-baseweb="select"]>div{min-height:36px;border-radius:7px;border-color:#dce3eb;background:#fff}
.panel-card{background:#fff;border:1px solid var(--line);border-radius:10px;box-shadow:0 1px 4px rgba(25,43,66,.04);overflow:hidden;height:100%}
.panel-head{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-bottom:1px solid #e7ebf0;min-height:44px}
.panel-title{font-family:Manrope,sans-serif;font-size:13px;font-weight:800;color:#1e2d42}
.panel-subtitle{font-size:9px;color:#8994a4;margin-top:2px}
.panel-body{padding:0 12px 9px}
.panel-foot{font-size:9px;color:#8994a4;padding:0 13px 10px}
.info-mark{display:inline-flex;align-items:center;justify-content:center;border:1px solid #b9c3d0;color:#8793a3;border-radius:50%;width:13px;height:13px;font-size:9px;margin-left:4px}
.rank-table{width:100%;border-collapse:collapse;font-size:10px;color:#334258}
.rank-table th{font-size:9px;color:#8390a1;text-align:left;font-weight:800;padding:8px 6px;border-bottom:1px solid #e8edf2;white-space:nowrap}
.rank-table td{padding:8px 6px;border-bottom:1px solid #eff2f5;vertical-align:middle;white-space:nowrap}
.rank-table tr:last-child td{border-bottom:0}
.rank-num{color:#8a95a4;width:22px}
.rank-country{font-weight:700;color:#26364d;max-width:180px;overflow:hidden;text-overflow:ellipsis}
.rank-value{font-weight:700;color:#2d3b50;font-variant-numeric:tabular-nums}
.rank-prev{color:#8692a2;font-variant-numeric:tabular-nums}
.trend-up{color:#159b70;background:#eaf7f1;padding:3px 6px;border-radius:999px;font-weight:800;font-size:9px}
.trend-down{color:#cf5550;background:#fff0ef;padding:3px 6px;border-radius:999px;font-weight:800;font-size:9px}
.trend-flat{color:#8894a4;background:#f2f4f7;padding:3px 6px;border-radius:999px;font-weight:800;font-size:9px}
.country-profile{background:#fff;border:1px solid var(--line);border-radius:10px;box-shadow:0 1px 4px rgba(25,43,66,.04);padding:14px 16px}
.profile-kicker{font-size:9px;letter-spacing:.13em;text-transform:uppercase;color:#7388a8;font-weight:800;margin-bottom:5px}
.profile-title{font-family:Manrope,sans-serif;font-size:21px;font-weight:800;letter-spacing:-.04em;color:#1c2c43}
.profile-note{font-size:10px;color:#8793a3;margin-top:3px}
.profile-stat{border:1px solid #edf0f4;background:#fafbfd;border-radius:7px;padding:9px 10px;margin:4px 0}
.profile-stat-label{font-size:8px;color:#8995a5;text-transform:uppercase;letter-spacing:.08em;font-weight:800}
.profile-stat-value{font-size:15px;font-weight:800;color:#22344d;margin-top:2px;font-family:Manrope,sans-serif}
.legend-inline{display:flex;flex-wrap:wrap;gap:7px 13px;padding:7px 14px 9px;border-top:1px solid #eff2f5}
.legend-item{display:inline-flex;align-items:center;gap:5px;font-size:9px;color:#66758a}
.legend-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.data-note{font-size:10px;line-height:1.6;color:#7d899b;background:#f6f8fa;border:1px solid #e7ecf1;border-radius:8px;padding:10px 12px}
.stDownloadButton button,.stButton button{border-radius:7px!important;font-size:10px!important;font-weight:800!important}
.stDownloadButton button[kind="secondary"]{border-color:#dce3eb!important;color:#4d5e76!important;background:#fff!important}
[data-testid="stExpander"]{background:#fff;border:1px solid var(--line);border-radius:9px}
[data-testid="stDataFrame"]{border:1px solid #e6ebf0;border-radius:8px;overflow:hidden}
hr{border-color:#e6ebf0}
@media(max-width:900px){.hero{display:block}.hero-right{justify-content:flex-start;margin-top:10px}.kpi-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.rank-table{font-size:9px}.rank-table th,.rank-table td{padding:7px 4px}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


@st.cache_data(ttl=600, show_spinner=False)
def load_data(path: str, modified: float) -> tuple[dict[str, Any], pd.DataFrame]:
    del modified
    payload = read_cache(path)
    frame = pd.DataFrame(payload.get("records", []))
    required = {"year", "debtor_iso3", "debtor_name", "creditor_name", "amount_usd"}
    if not required.issubset(frame.columns):
        raise ValueError("The cache schema is incomplete. Run `python -m src.fetcher` to refresh it.")
    for col, default in (("creditor_id", ""), ("creditor_iso3", "")):
        if col not in frame.columns:
            frame[col] = default
    frame["year"] = pd.to_numeric(frame["year"], errors="coerce")
    frame["amount_usd"] = pd.to_numeric(frame["amount_usd"], errors="coerce")
    frame = frame.dropna(subset=["year", "amount_usd"])
    frame = frame.loc[frame["amount_usd"] > 0].copy()
    frame["year"] = frame["year"].astype(int)
    for col in ("debtor_iso3", "creditor_iso3", "creditor_id"):
        frame[col] = frame[col].fillna("").astype(str)
    for col in ("debtor_name", "creditor_name"):
        frame[col] = frame[col].fillna("").astype(str)
    frame["debtor_iso3"] = frame["debtor_iso3"].str.upper()
    frame["creditor_iso3"] = frame["creditor_iso3"].str.upper()
    return payload.get("metadata", {}), frame


@st.cache_data(show_spinner="Loading Natural Earth country boundaries…")
def load_boundaries(path: str, modified: float) -> dict[str, Any]:
    del modified
    return download_country_geojson(path)


def _short_money(value: float) -> str:
    return format_usd(value)


def _change(current: float, previous: float | None) -> tuple[str, str]:
    if previous is None or pd.isna(previous) or previous == 0:
        return "—", "trend-flat"
    pct = (current / previous - 1.0) * 100.0
    if pct > 0.05:
        return f"▲ {pct:+.1f}%", "trend-up"
    if pct < -0.05:
        return f"▼ {pct:+.1f}%", "trend-down"
    return "→ 0.0%", "trend-flat"


def _ranking_rows(
    current: pd.DataFrame,
    previous: pd.DataFrame,
    *,
    name_column: str,
    id_column: str,
    flag_column: str | None,
    limit: int = 6,
) -> list[dict[str, str]]:
    previous_map = dict(zip(previous[id_column].astype(str), previous["amount_usd"].astype(float))) if not previous.empty else {}
    result: list[dict[str, str]] = []
    for rank, row in enumerate(current.head(limit).itertuples(index=False), start=1):
        record = row._asdict()
        item_id = str(record[id_column])
        amount = float(record["amount_usd"])
        prior = previous_map.get(item_id)
        trend, trend_class = _change(amount, prior)
        if flag_column and record.get(flag_column):
            flag = flag_emoji(str(record[flag_column]))
        else:
            flag = "🌐" if flag_column else "🏳️"
        result.append({
            "rank": str(rank),
            "flag": flag,
            "name": str(record[name_column]),
            "current": _short_money(amount),
            "previous": _short_money(prior) if prior is not None else "—",
            "trend": trend,
            "trend_class": trend_class,
        })
    return result


def _render_rank_table(title: str, subtitle: str, rows: list[dict[str, str]]) -> str:
    body = "".join(
        "<tr>"
        f"<td class='rank-num'>{html.escape(row['rank'])}</td>"
        f"<td><span>{html.escape(row['flag'])}</span> <span class='rank-country'>{html.escape(row['name'])}</span></td>"
        f"<td class='rank-value'>{html.escape(row['current'])}</td>"
        f"<td class='rank-prev'>{html.escape(row['previous'])}</td>"
        f"<td><span class='{html.escape(row['trend_class'])}'>{html.escape(row['trend'])}</span></td>"
        "</tr>"
        for row in rows
    ) or "<tr><td colspan='5' style='padding:18px;color:#8793a3'>No observations available.</td></tr>"
    return f"""
    <div class="panel-card">
      <div class="panel-head"><div><div class="panel-title">{html.escape(title)} <span class="info-mark">i</span></div><div class="panel-subtitle">{html.escape(subtitle)}</div></div></div>
      <div class="panel-body"><table class="rank-table"><thead><tr><th>#</th><th>Country / area</th><th>Current</th><th>Prev.</th><th>Change</th></tr></thead><tbody>{body}</tbody></table></div>
      <div class="panel-foot">Current and previous refer to adjacent reported observation years. Missing prior values are shown as —.</div>
    </div>
    """


def _donut(breakdown: pd.DataFrame, creditor_colors: dict[str, str] | None = None) -> go.Figure:
    shown = breakdown.head(9).copy()
    if len(breakdown) > 9:
        rest = breakdown.iloc[9:]
        shown = pd.concat([shown, pd.DataFrame([{
            "creditor_id": "__OTHER__", "creditor_name": f"Other areas ({len(rest)})",
            "amount_usd": float(rest["amount_usd"].sum()),
            "share_pct": float(rest["share_pct"].sum()),
        }])], ignore_index=True)
    from src.map_geometry import color_for_creditor
    colors = ["#c4ccd6" if str(x) == "__OTHER__" else (creditor_colors or {}).get(str(x)) or color_for_creditor(str(x))
              for x in shown["creditor_id"]]
    total = float(breakdown["amount_usd"].sum())
    figure = go.Figure(go.Pie(
        labels=shown["creditor_name"], values=shown["amount_usd"], hole=.70,
        sort=False, marker={"colors": colors, "line": {"color": "#fff", "width": 2}},
        textinfo="percent", textposition="inside",
        hovertemplate="<b>%{label}</b><br>%{percent}<br>$%{value:,.0f}<extra></extra>",
    ))
    figure.update_layout(
        height=270, margin={"l": 2, "r": 2, "t": 4, "b": 4},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False, font={"family": "DM Sans, sans-serif", "size": 10, "color": "#5c6b80"},
        annotations=[{"text": f"<b>{format_usd(total)}</b><br><span style='font-size:9px;color:#8995a5'>TOTAL</span>",
                      "x": .5, "y": .5, "showarrow": False, "font": {"size": 15, "color": "#22334a"}}],
    )
    return figure


def _money_table(breakdown: pd.DataFrame) -> pd.DataFrame:
    result = breakdown.copy()
    result["Creditor area"] = result.apply(
        lambda row: f"{flag_emoji(str(row['creditor_iso3']))}  {row['creditor_name']}" if row.get("creditor_iso3") else f"🌐  {row['creditor_name']}",
        axis=1,
    )
    result["Debt stock"] = result["amount_usd"].map(format_usd)
    result["Share"] = result["share_pct"].map(lambda value: f"{value:.2f}%")
    return result[["Creditor area", "Debt stock", "Share"]]



def _map_legend(breakdown: pd.DataFrame, selected_creditor_id: str | None = None, limit: int = 8,
                creditor_colors: dict[str, str] | None = None) -> str:
    if breakdown.empty:
        return ""
    from src.map_geometry import color_for_creditor
    items = []
    all_geographic = breakdown.loc[breakdown["creditor_iso3"].fillna("").astype(str).str.len() == 3]
    geographic = all_geographic.head(limit).copy()
    if selected_creditor_id and not (geographic["creditor_id"].astype(str) == str(selected_creditor_id)).any():
        selected_rows = all_geographic.loc[all_geographic["creditor_id"].astype(str) == str(selected_creditor_id)]
        if not selected_rows.empty:
            geographic = pd.concat([geographic, selected_rows.head(1)], ignore_index=True)
    for row in geographic.itertuples(index=False):
        color = (creditor_colors or {}).get(str(row.creditor_id)) or color_for_creditor(str(row.creditor_id))
        items.append(
            f"<span class='legend-item'><span class='legend-dot' style='background:{color}'></span>"
            f"{html.escape(flag_emoji(str(row.creditor_iso3)))} {html.escape(str(row.creditor_name))}</span>"
        )
    return "<div class='legend-inline'>" + "".join(items) + "</div>" if items else ""

def _empty_state(message: str) -> None:
    st.markdown(
        f"<div class='country-profile'><div class='profile-kicker'>No data available</div><div class='profile-title'>The debt atlas is waiting for data</div><p style='font-size:12px;color:#768397'>{html.escape(message)}</p><code>python -m src.fetcher</code></div>",
        unsafe_allow_html=True,
    )


def render_debt_view() -> None:
    """Bilateral debtor → creditor relationship map (World Bank IDS)."""
    if not CACHE_PATH.exists():
        st.title("Global Bilateral Debt Explorer")
        _empty_state("The local cache is missing. Run the World Bank IDS fetcher first.")
        st.stop()
    try:
        metadata, frame = load_data(str(CACHE_PATH), CACHE_PATH.stat().st_mtime)
    except Exception as exc:
        LOGGER.exception("Could not load debt cache")
        st.title("Global Bilateral Debt Explorer")
        st.error(f"Could not read the debt cache: {exc}")
        st.stop()
    if frame.empty:
        st.title("Global Bilateral Debt Explorer")
        _empty_state("No non-null bilateral observations are in the local cache yet.")
        st.stop()

    years = sorted(frame["year"].unique().tolist())
    latest_year = int(metadata.get("latest_year") or years[-1])
    if latest_year not in years:
        latest_year = years[-1]

    # Dashboard identity and data status.
    generated = str(metadata.get("generated_at_utc", ""))[:10] or "local cache"
    status = str(metadata.get("status", "success")).casefold()
    status_chip = "PARTIAL SNAPSHOT" if status == "partial" else "DATA SNAPSHOT"
    st.markdown(
        f"""
        <div class="hero">
          <div><div class="hero-kicker">OPEN DATA · PUBLIC FINANCE · WORLD BANK IDS</div>
          <h1 id="gde-hero-title" class="hero-title" style="color:#19283e!important;opacity:1!important">Global Bilateral Debt Explorer</h1>
          <p class="hero-subtitle">Explore public borrowers, official creditor relationships and bilateral debt stocks — country by country, year by year.</p></div>
          <div class="hero-right"><span class="header-chip live"><span class="header-dot"></span>{status_chip}</span><span class="header-chip">UPDATED {html.escape(generated)}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    control_cols = st.columns([.85, 1.45, 1.2], gap="medium", vertical_alignment="bottom")
    with control_cols[0]:
        selected_year = int(st.selectbox("OBSERVATION YEAR", years, index=years.index(latest_year), key="gde_year"))
    year_frame = frame.loc[frame["year"] == selected_year].copy()
    # Load Natural Earth geometry once before building the country selector. The
    # geometry catalog extends the selector beyond the ~120 IDS debtor reporters
    # so high-income countries and fallback-covered areas can still be explored.
    try:
        boundaries = load_boundaries(
            str(DEFAULT_GEOJSON_PATH),
            DEFAULT_GEOJSON_PATH.stat().st_mtime if DEFAULT_GEOJSON_PATH.exists() else 0.0,
        )
    except BoundaryDataError as exc:
        LOGGER.warning("Country boundary data unavailable during profile setup: %s", exc)
        boundaries = {"type": "FeatureCollection", "features": []}

    name_by_code: dict[str, str] = {}
    for feature in boundaries.get("features", []):
        props = feature.get("properties") or {}
        code = iso3_from_properties(props)
        if code:
            name_by_code[code] = str(props.get("ADMIN") or props.get("NAME_EN") or props.get("NAME") or code)
    for country in pycountry.countries:
        name_by_code.setdefault(country.alpha_3, country.name)
    name_by_code.update({"PSE": "West Bank and Gaza", "XKX": "Kosovo", "TWN": "Taiwan"})
    if not year_frame.empty:
        for iso, name in year_frame[["debtor_iso3", "debtor_name"]].drop_duplicates().itertuples(index=False, name=None):
            name_by_code[str(iso).upper()] = str(name)

    country_code_set = set(name_by_code) | (
        set(year_frame["debtor_iso3"].dropna().astype(str).str.upper()) if not year_frame.empty else set()
    )
    debtor_codes = sorted(country_code_set, key=lambda code: name_by_code.get(code, code).casefold())
    # One World Bank bulk request sequence and one IMF DataMapper payload provide
    # Tier 1/Tier 2 coverage for all map countries. Streamlit caches the result for
    # 24 hours; the local dictionary is the final safety net.
    if st.button("Refresh country data", key="gde-refresh-country-profiles", help="Clear the 24-hour country profile cache and query the sources again"):
        clear_country_debt_profile_cache()
        st.rerun()
    with st.spinner("Resolving national debt coverage · World Bank → IMF → local fallback…"):
        try:
            country_profiles = get_country_debt_profiles(tuple(debtor_codes))
        except Exception as exc:  # defensive: the resolver itself never raises on network errors
            LOGGER.exception("Country profile retrieval failed")
            st.warning(f"Country-level fallback data could not be refreshed: {exc}")
            country_profiles = {}
    tier_summary = summarize_profiles(country_profiles)
    if country_profiles and tier_summary["tier_1"] + tier_summary["tier_2"] == 0:
        st.warning(
            "The World Bank and IMF APIs are unreachable right now, so the map is using the local fallback "
            "(curated estimates and last-known values). Use **Refresh country data** to retry."
        )

    # Consume map events before creating the selectbox with this key.
    apply_pending_debtor_selection(st.session_state, debtor_codes)
    with control_cols[1]:
        selected_iso3 = st.selectbox(
            "SELECT DEBTOR COUNTRY", debtor_codes,
            format_func=lambda code: f"{flag_emoji(code)}  {name_by_code.get(code, code)} ({code})",
            key="gde_debtor",
        )
    debtors = aggregate_debt(year_frame, selected_year)
    creditors = creditor_totals(year_frame, selected_year)
    selected_breakdown = country_creditor_breakdown(year_frame, selected_year, selected_iso3)
    # One colour per creditor, clearly distinct, shared by the map lines, legend and donut.
    from src.map_geometry import assign_distinct_colors
    creditor_colors = assign_distinct_colors(selected_breakdown["creditor_id"].astype(str).tolist()) if not selected_breakdown.empty else {}
    if selected_breakdown.empty:
        selected_profile_notice = country_profiles.get(selected_iso3, {})
        if selected_profile_notice.get("data_available"):
            st.info("No bilateral IDS creditor breakdown is reported for this country/year. Its separate national debt indicator is shown in the country profile and map popup.")
        else:
            st.info("No bilateral IDS creditor breakdown is reported for this country/year, and no verified national indicator is available. Missing data is not zero.")
    with control_cols[2]:
        creditor_options = selected_breakdown["creditor_id"].astype(str).tolist() if not selected_breakdown.empty else []
        apply_pending_creditor_selection(st.session_state, creditor_options)
        selected_creditor_id = st.selectbox(
            "HIGHLIGHT RELATIONSHIP",
            creditor_options,
            format_func=lambda value: str(selected_breakdown.loc[selected_breakdown["creditor_id"].astype(str) == str(value), "creditor_name"].iloc[0]) if value else "No creditor available",
            key="gde_creditor",
            disabled=not creditor_options,
        )

    total_tracked = float(year_frame["amount_usd"].sum())
    top_debtor = debtors.iloc[0] if not debtors.empty else None
    top_creditor = creditors.iloc[0] if not creditors.empty else None
    metric_html = f"""
    <div class="kpi-strip">
      <div class="kpi"><div class="kpi-label">Reported bilateral debt</div><div class="kpi-value">{format_usd(total_tracked)}</div><div class="kpi-note">Sum of observations in this cache · {selected_year}</div></div>
      <div class="kpi"><div class="kpi-label">Largest debtor</div><div class="kpi-value">{html.escape(str(top_debtor['debtor_name'])) if top_debtor is not None else '—'}</div><div class="kpi-note">{format_usd(float(top_debtor['amount_usd'])) if top_debtor is not None else 'No observation'}</div></div>
      <div class="kpi"><div class="kpi-label">Largest creditor area</div><div class="kpi-value">{html.escape(str(top_creditor['creditor_name'])) if top_creditor is not None else '—'}</div><div class="kpi-note">{format_usd(float(top_creditor['amount_usd'])) if top_creditor is not None else 'No observation'}</div></div>
      <div class="kpi"><div class="kpi-label">Reporting coverage</div><div class="kpi-value">{year_frame['debtor_iso3'].nunique():,} countries</div><div class="kpi-note">{year_frame['creditor_id'].nunique():,} creditor areas with observations</div></div>
    </div>
    """
    st.markdown(metric_html, unsafe_allow_html=True)

    # Main map panel — the large relationship map is the visual anchor.
    with st.container(border=True):
        map_head_left, map_head_right = st.columns([4, 1], vertical_alignment="center")
        with map_head_left:
            st.markdown(
                "<div class='panel-title'>Bilateral debt relationships <span class='info-mark'>i</span></div><div class='panel-subtitle'>Click a country or a curved link to see who owes whom, the reported amount and the creditor share.</div>",
                unsafe_allow_html=True,
            )
        with map_head_right:
            st.markdown(f"<div style='text-align:right;font-size:10px;font-weight:800;color:#748197'>WORLD MAP · {selected_year}</div>", unsafe_allow_html=True)
        try:
            selected_total = float(selected_breakdown["amount_usd"].sum()) if not selected_breakdown.empty else 0.0
            map_object, map_stats = build_relationship_map(
                boundary_geojson=boundaries,
                debtor_rows=debtors.to_dict(orient="records"),
                creditor_rows=selected_breakdown.to_dict(orient="records"),
                creditor_totals_rows=creditors.to_dict(orient="records"),
                debtor_iso3=selected_iso3,
                debtor_name=name_by_code.get(selected_iso3, selected_iso3),
                selected_creditor_id=selected_creditor_id or None,
                total_amount_usd=selected_total,
                year=selected_year,
                max_connections=8,
                country_profiles=country_profiles,
                creditor_colors=creditor_colors,
                lending_rows=(
                    year_frame.loc[year_frame["creditor_iso3"] == selected_iso3]
                    .groupby(["debtor_iso3", "debtor_name"], as_index=False)["amount_usd"].sum()
                    .to_dict(orient="records")
                ),
            )
            map_state = st_folium(
                map_object,
                height=470,
                use_container_width=True,
                returned_objects=["last_active_drawing", "last_object_clicked_popup", "last_object_clicked_count"],
                key=f"gde-relationship-map-{selected_year}-{selected_iso3}-{selected_creditor_id}",
            )
            map_state = map_state or {}
            popup_value = map_state.get("last_object_clicked_popup")
            changed_to: str | None = None
            changed_creditor: str | None = None
            map_action = extract_map_action(popup_value)
            if map_action:
                kind, value = map_action
                if kind in {"DEBTOR", "COUNTRY"} and value in debtor_codes:
                    changed_to = value
                elif kind == "CREDITOR" and value in creditor_options:
                    changed_creditor = value
            active_feature = map_state.get("last_active_drawing")
            if isinstance(active_feature, dict):
                props = active_feature.get("properties") or {}
                clicked_iso = str(props.get("_gde_iso3") or props.get("ADM0_A3") or props.get("ISO_A3") or "").upper()
                if clicked_iso in debtor_codes:
                    changed_to = clicked_iso
            if changed_to and changed_to != selected_iso3:
                # Never write to the active selectbox key after it has been created.
                # Apply the new value before widget creation on the next rerun.
                st.session_state["_gde_pending_debtor"] = changed_to
                st.session_state["_gde_pending_creditor"] = None
                st.rerun()
            if changed_creditor and changed_creditor != selected_creditor_id:
                st.session_state["_gde_pending_creditor"] = changed_creditor
                st.rerun()
            legend_html = _map_legend(selected_breakdown, selected_creditor_id, creditor_colors=creditor_colors)
            if legend_html:
                st.markdown(legend_html, unsafe_allow_html=True)
            st.markdown(
                f"<div class='map-caption'>Selected country: <strong>{html.escape(name_by_code.get(selected_iso3, selected_iso3))}</strong>. Curved lines show bilateral PPG creditor relationships where IDS data exists. Country fills and markers also show separately sourced national indicators (World Bank long-term external debt, IMF government debt as % GDP, or curated local estimates); unlike metrics are labelled and are not directly comparable. Arrows point lender → borrower: {map_stats['geographic_creditor_connections']} lenders to this country and {map_stats.get('lending_connections', 0)} borrowers from it are drawn; National profiles: {tier_summary['tier_1']} World Bank · {tier_summary['tier_2']} IMF · {tier_summary['tier_3']} local fallback{f" ({tier_summary['snapshot']} last-known)" if tier_summary['snapshot'] else ""} · {tier_summary['unavailable']} without verified data (hollow grey points, not zero).</div>",
                unsafe_allow_html=True,
            )
        except BoundaryDataError as exc:
            st.error(str(exc))
            st.info("The debt cache remains available. The geographic boundary file could not be loaded; check your connection and reload.")
        except Exception as exc:
            LOGGER.exception("Relationship map failed")
            st.error(f"Could not render the relationship map: {exc}")

    # Compact twin ranking tables directly under the map, as in the reference image.
    st.markdown("<div style='height:5px'></div>", unsafe_allow_html=True)
    debtors_previous = aggregate_debt(frame, selected_year - 1)
    creditors_previous = creditor_totals(frame, selected_year - 1)
    debtor_rows = _ranking_rows(
        debtors,
        debtors_previous,
        name_column="debtor_name",
        id_column="debtor_iso3",
        flag_column="debtor_iso3",
        limit=7,
    )
    creditor_rows = _ranking_rows(
        creditors,
        creditors_previous,
        name_column="creditor_name",
        id_column="creditor_id",
        flag_column="creditor_iso3",
        limit=7,
    )
    left, right = st.columns(2, gap="medium")
    with left:
        left_html = _render_rank_table("Top debtor countries", f"Largest reported PPG bilateral debt stocks · {selected_year}", debtor_rows)
        st.markdown(left_html, unsafe_allow_html=True)
        st.download_button(
            "Download debtor ranking ↓",
            debtors.to_csv(index=False).encode("utf-8"),
            file_name=f"top-debtors-{selected_year}.csv",
            mime="text/csv",
            key="download-debtor-ranking",
        )
    with right:
        right_html = _render_rank_table("Top creditor areas", f"Largest reported creditor claims in this dataset · {selected_year}", creditor_rows)
        st.markdown(right_html, unsafe_allow_html=True)
        st.download_button(
            "Download creditor ranking ↓",
            creditors.to_csv(index=False).encode("utf-8"),
            file_name=f"top-creditors-{selected_year}.csv",
            mime="text/csv",
            key="download-creditor-ranking",
        )

    # Selected country's detailed panel keeps the full analysis accessible below.
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    profile_total = float(selected_breakdown["amount_usd"].sum()) if not selected_breakdown.empty else 0.0
    largest_relation = selected_breakdown.iloc[0] if not selected_breakdown.empty else None
    national_profile = country_profiles.get(selected_iso3, {})
    national_value = str(national_profile.get("display_value") or "No verified data")
    national_metric = str(national_profile.get("metric_label") or "No national indicator available")
    national_source = str(national_profile.get("source_label") or "No source available")
    national_source_date = str(national_profile.get("source_date_label") or "Source date unavailable")
    national_source_url = str(national_profile.get("source_url") or "")
    national_source_link = (
        f"<a href='{html.escape(national_source_url, quote=True)}' target='_blank' rel='noopener'>Open source ↗</a>"
        if national_source_url.startswith("https://") else ""
    )
    national_year = str(national_profile.get("observation_year") or "—")
    national_tier_label = str(national_profile.get("tier_label") or "Unavailable")
    alternative = national_profile.get("alternative_estimate") if isinstance(national_profile.get("alternative_estimate"), dict) else None
    alternative_html = (
        f"<div class='profile-stat'><div class='profile-stat-label'>Other estimate (curated)</div>"
        f"<div class='profile-stat-value'>{html.escape(str(alternative.get('display_value')))}</div>"
        f"<div class='profile-note'>{html.escape(str(alternative.get('source_label') or ''))} · {html.escape(str(alternative.get('source_date_label') or ''))}</div></div>"
        if alternative and alternative.get("display_value") else ""
    )
    trail = national_profile.get("resolution_trail") or []
    trail_html = (
        "<div class='profile-note' style='margin-top:6px'><strong>How this value was chosen:</strong> "
        + " → ".join(html.escape(str(step)) for step in trail) + "</div>"
        if trail else ""
    )
    profile_col, mix_col = st.columns([.9, 1.6], gap="medium")
    with profile_col:
        st.markdown(
            f"""
            <div class="country-profile">
              <div class="profile-kicker">Selected country · national debt profile</div>
              <div class="profile-title">{flag_emoji(selected_iso3)} {html.escape(name_by_code.get(selected_iso3, selected_iso3))}</div>
              <div class="profile-note">ISO-3 {selected_iso3} · dashboard year {selected_year}</div>
              <div style="height:10px"></div>
              <div class="profile-stat"><div class="profile-stat-label">National indicator value</div><div class="profile-stat-value">{html.escape(national_value)}</div></div>
              <div class="profile-note">{html.escape(national_metric)}</div>
              <div class="profile-stat"><div class="profile-stat-label">Data source</div><div class="profile-stat-value" style="font-size:11px">{html.escape(national_source)}</div><div class="profile-note">{html.escape(national_source_date)} · {national_source_link}</div></div>
              <div class="profile-stat"><div class="profile-stat-label">Source tier · observation year</div><div class="profile-stat-value">{national_tier_label} · {html.escape(national_year)}</div></div>
              {alternative_html}
              <div class="profile-stat"><div class="profile-stat-label">Reported bilateral IDS debt</div><div class="profile-stat-value">{format_usd(profile_total) if not selected_breakdown.empty else 'Not reported'}</div></div>
              <div class="profile-note">{html.escape(str(national_profile.get('definition_note') or 'A missing value is not zero debt.'))}</div>
              {trail_html}
            </div>
            """,
            unsafe_allow_html=True,
        )
    with mix_col:
        with st.container(border=True):
            st.markdown(
                f"<div class='panel-title'>Creditor composition <span class='info-mark'>i</span></div><div class='panel-subtitle'>Share of {html.escape(name_by_code.get(selected_iso3, selected_iso3))}'s reported bilateral debt stock · {selected_year}</div>",
                unsafe_allow_html=True,
            )
            if selected_breakdown.empty:
                st.info("No positive creditor-level observations are available for this country and year.")
            else:
                chart_col, table_col = st.columns([.8, 1.2], gap="small", vertical_alignment="center")
                with chart_col:
                    st.plotly_chart(_donut(selected_breakdown, creditor_colors), width="stretch", config={"displayModeBar": False})
                with table_col:
                    table = _money_table(selected_breakdown)
                    st.dataframe(
                        table,
                        width="stretch",
                        hide_index=True,
                        height=265,
                        column_config={
                            "Creditor area": st.column_config.TextColumn("Creditor area", width="large"),
                            "Debt stock": st.column_config.TextColumn("Debt stock", width="medium"),
                            "Share": st.column_config.TextColumn("Share", width="small"),
                        },
                    )
                    st.download_button(
                        "Download full creditor breakdown ↓",
                        selected_breakdown.to_csv(index=False).encode("utf-8"),
                        file_name=f"{selected_iso3.lower()}-creditor-breakdown-{selected_year}.csv",
                        mime="text/csv",
                        width="stretch",
                        key="download-country-breakdown",
                    )

    with st.expander("Data coverage, methodology & attribution", expanded=False):
        info_left, info_right = st.columns(2)
        with info_left:
            st.markdown("**Source and indicator**")
            st.write("World Bank International Debt Statistics (IDS), source 6.")
            st.code("DT.DOD.BLAT.CD", language="text")
            st.write("PPG, bilateral debt outstanding and disbursed, current US dollars.")
            st.markdown("[World Bank IDS](https://www.worldbank.org/en/programs/debt-statistics/ids) · [IDS API guide](https://worldbank.github.io/debt-data/creditor-composition/creditor-data-python.html)")
        with info_right:
            st.markdown("**How to interpret the map**")
            st.write("Dotted curves connect a selected debtor to sovereign creditor countries represented by the largest reported creditor areas. Curves are relationship links, not physical transport routes or asset locations. Some IDS counterpart areas are regions or official creditor groupings rather than sovereign countries; these remain in the creditor table but do not receive map markers.")
            st.write("The country-level resilience layer uses a separate three-tier sequence: (1) World Bank DT.DOD.DLXF.CD, long-term external debt stocks in current US dollars; (2) IMF GG_DEBT_GDP, general government gross debt as a percentage of GDP; and (3) a local safety net — curated, sourced estimates for reporting gaps such as Syria and West Bank and Gaza, then the last value successfully retrieved from a live source if that source is down. Live responses are cached for 24 hours; observations older than ten years fall through to the next tier. Every value keeps its metric label, observation year, source URL and source date.")
            st.write("Important: DT.DOD.DLXF.CD includes public, publicly guaranteed and private nonguaranteed long-term external debt; it is not public debt only. IMF percent-of-GDP values, USD external debt stocks, and local estimate perimeters are different measures and must not be added, ranked or compared on a common numeric scale. Missing values are not zero.")
        st.caption(f"Cache generated: {metadata.get('generated_at_utc', 'not recorded')} · Cache status: {status} · {len(frame):,} normalized observation rows · {min(years)}–{max(years)}")
        if status == "partial":
            st.warning("This cache is marked partial by the fetcher. Some debtor requests may not have completed; use caution when interpreting totals.")

    st.markdown(
        "<div style='text-align:center;color:#9aa4b2;font-size:9px;letter-spacing:.08em;padding:18px 0 2px'>GLOBAL BILATERAL DEBT EXPLORER · OPEN DATA FOR PUBLIC FINANCE · WORLD BANK IDS</div>",
        unsafe_allow_html=True,
    )


VIEWS: dict[str, tuple[str, str, str]] = {
    "atlas": ("🗺️ World atlas", "Global Debt & Fiscal Atlas",
              "Debt, deficits, government spending, revenue, growth and development for every country — live from the World Bank and IMF, with the source and year of every number."),
    "country": ("📊 Country profile", "Country profile",
                "Headline numbers, where the money goes, long-run trends and where to research further."),
    "spending": ("💸 Spending", "Where the money goes",
                 "What each government spends on defence, health, education, social transfers, wages, interest and research — in US dollars, from published data."),
    "debt": ("🔗 Who owes whom", "Global Bilateral Debt Explorer",
             "Explore public borrowers, official creditor relationships and bilateral debt stocks — country by country, year by year."),
    "sources": ("📚 Data & sources", "Data, freshness & sources",
                "How current every indicator is, where to find newer data, and how to cite."),
    "developers": ("🧑‍💻 Developers", "Developers & researchers",
                   "Download every dataset, read every API, and build on this project."),
}


def _view_header(view: str) -> None:
    _label, title, subtitle = VIEWS[view]
    st.markdown(
        f"""
        <div class="hero">
          <div><div class="hero-kicker">OPEN DATA · PUBLIC FINANCE · WORLD BANK · IMF</div>
          <h1 class="hero-title" style="color:#19283e!important;opacity:1!important">{html.escape(title)}</h1>
          <p class="hero-subtitle">{html.escape(subtitle)}</p></div>
          <div class="hero-right"><span class="header-chip live"><span class="header-dot"></span>LIVE APIs · 24 H CACHE</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _share_button() -> None:
    """Popover with a copyable link to exactly what is on screen."""
    query = share.build_query(st.session_state)
    url = share.share_url(getattr(st.context, "url", None), query)
    with st.popover("🔗 Share", width="stretch"):
        st.markdown("**Link to this exact view**")
        st.code(url, language=None)
        st.caption("Anyone opening it sees the same view, country and indicator. The address bar always has it too.")


def main() -> None:
    from src.indicator_data import country_codes
    from src.spending import ITEM_BY_ID

    share.apply_query_once(st.session_state, st.query_params,
                           countries=set(country_codes()), spending_items=set(ITEM_BY_ID))
    pending = st.session_state.pop(atlas_view.PENDING_VIEW_KEY, None)
    if pending in VIEWS:
        st.session_state["gde_view"] = pending  # applied before the switcher widget is created
    switcher, share_col = st.columns([7, 1], vertical_alignment="center")
    with switcher:
        view = st.segmented_control(
            "View", list(VIEWS), default="atlas", key="gde_view", label_visibility="collapsed",
            format_func=lambda key: VIEWS[key][0],
        ) or "atlas"
    with share_col:
        _share_button()
    try:
        _render_view(view)
    finally:
        share.sync_query(st.query_params, st.session_state)


def _render_view(view: str) -> None:
    if view == "debt":
        # One selected country across every view.
        selected = st.session_state.get(atlas_view.SELECTED_KEY)
        if selected and selected != st.session_state.get("gde_debtor"):
            st.session_state["_gde_pending_debtor"] = selected
        render_debt_view()
        if st.session_state.get("gde_debtor"):
            st.session_state[atlas_view.SELECTED_KEY] = st.session_state["gde_debtor"]
        return

    _view_header(view)
    try:
        boundaries = load_boundaries(
            str(DEFAULT_GEOJSON_PATH),
            DEFAULT_GEOJSON_PATH.stat().st_mtime if DEFAULT_GEOJSON_PATH.exists() else 0.0,
        )
    except BoundaryDataError as exc:
        LOGGER.warning("Country boundaries unavailable: %s", exc)
        st.warning(f"Country outlines could not be loaded ({exc}). Data tables still work.")
        boundaries = {"type": "FeatureCollection", "features": []}
    index = atlas_view.build_country_index(boundaries)
    if view == "atlas":
        atlas_view.render_atlas(boundaries, index)
    elif view == "country":
        atlas_view.render_country(index)
    elif view == "spending":
        spending_view.render_spending(index)
    elif view == "developers":
        developer_view.render_developers()
    else:
        atlas_view.render_sources(index)
    st.markdown(
        "<div style='text-align:center;color:#9aa4b2;font-size:9px;letter-spacing:.08em;padding:18px 0 2px'>"
        "GLOBAL DEBT &amp; FISCAL ATLAS · OPEN SOURCE (MIT) · DATA © WORLD BANK, IMF AND LISTED PROVIDERS</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
