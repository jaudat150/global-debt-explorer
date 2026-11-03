"""Streamlit views: World Atlas, Country profile, and Data & sources.

Kept out of ``app.py`` so each view is small and testable. All data access goes
through :mod:`src.indicator_data`, which never raises on network errors.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import pycountry
import streamlit as st
from streamlit_folium import st_folium

from src.atlas_map import build_atlas_map
from src.debt_flows import COVERAGE_NOTE, CountryFlows, bilateral_frame, country_flows, flows_table, money
from src.catalog import (
    CATEGORIES, CATEGORY_BY_ID, INDICATOR_BY_ID, INDICATORS, SOURCE_DIRECTORY, SPENDING_LINES,
    Indicator, country_links, indicators_in,
)
from src.indicator_data import (
    OK, IndicatorLayer, clear_indicator_cache, country_codes, format_value, load_layer,
)
from src.relationship_map import extract_map_action, flag_emoji, iso3_from_properties

BLUE = "#2a78d6"
INK, MUTED, GRID = "#17243a", "#738096", "#e8edf3"
COUNTRY_KEY = "gde_country_select"          # widget key (cleared by Streamlit when not rendered)
SELECTED_KEY = "gde_selected_country"        # persistent selection shared by every view
PENDING_VIEW_KEY = "_gde_pending_view"       # consumed by app.py before the view switcher renders


@dataclass(frozen=True)
class CountryIndex:
    """Display names and ISO2 codes for every mappable country."""

    names: dict[str, str]
    iso2: dict[str, str]
    codes: tuple[str, ...]


def build_country_index(boundaries: dict[str, Any]) -> CountryIndex:
    names: dict[str, str] = {}
    iso2: dict[str, str] = {}
    for country in pycountry.countries:
        names[country.alpha_3] = str(getattr(country, "common_name", None) or country.name)
        iso2[country.alpha_3] = country.alpha_2
    for feature in boundaries.get("features", []):
        props = feature.get("properties") or {}
        code = iso3_from_properties(props)
        if code and code in names:
            names[code] = str(props.get("ADMIN") or names[code])
    names.update({"PSE": "Palestine (West Bank and Gaza)", "XKX": "Kosovo", "TWN": "Taiwan"})
    iso2.setdefault("XKX", "XK")
    uninhabited = {"ATA", "BVT", "HMD", "SGS", "UMI", "ATF", "IOT"}
    codes = tuple(sorted((set(names) & set(country_codes())) - uninhabited, key=lambda c: names[c].casefold()))
    return CountryIndex(names, iso2, codes)


# ---------------------------------------------------------------------------
# Shared widgets
# ---------------------------------------------------------------------------

def _status_chips(layer: IndicatorLayer) -> str:
    chips = []
    for source in layer.indicator.sources:
        status = layer.source_status.get(source.code)
        used_snapshot = any(v.from_snapshot and v.source.code == source.code for v in layer.values.values())
        if status == OK:
            text, cls = "LIVE", "live"
        elif used_snapshot:
            text, cls = "LAST-KNOWN COPY", ""
        else:
            text, cls = "UNREACHABLE", ""
        chips.append(f"<span class='header-chip {cls}' title='{html.escape(source.label)}'>"
                     f"{html.escape(source.code)} · {text}</span>")
    return " ".join(chips)


def _kpi(label: str, value: str, note: str) -> str:
    return (f"<div class='kpi'><div class='kpi-label'>{html.escape(label)}</div>"
            f"<div class='kpi-value'>{html.escape(value)}</div><div class='kpi-note'>{html.escape(note)}</div></div>")


def _source_link_list(keys: tuple[str, ...] | list[str]) -> str:
    items = []
    for key in keys:
        source = SOURCE_DIRECTORY.get(key)
        if source:
            note = f" — {html.escape(source.note)}" if source.note else ""
            items.append(f"<li><a href='{html.escape(source.url, quote=True)}' target='_blank' rel='noopener'>"
                         f"{html.escape(source.name)}</a> · {html.escape(source.publisher)} · "
                         f"<em>{html.escape(source.updated)}</em>{note}</li>")
    return "<ul style='margin:4px 0 0 18px;font-size:13px'>" + "".join(items) + "</ul>"


def _country_selectbox(index: CountryIndex, label: str, key: str) -> str:
    desired = st.session_state.get(SELECTED_KEY)
    if desired in index.codes and st.session_state.get(key) != desired:
        st.session_state[key] = desired  # safe: the widget has not been created yet this run
    if st.session_state.get(key) not in index.codes:
        st.session_state[key] = "SYR" if "SYR" in index.codes else index.codes[0]
    value = st.selectbox(label, index.codes, key=key,
                         format_func=lambda c: f"{flag_emoji(c)}  {index.names.get(c, c)} ({c})")
    st.session_state[SELECTED_KEY] = value
    return value


def _include_estimates_toggle(key: str) -> bool:
    return not st.toggle("Reported data only", value=False, key=key,
                         help="Hide IMF current-year staff estimates and show the latest reported year instead. "
                              "Future-year projections are never shown.")


def _bar_chart(labels: list[str], values: list[float], texts: list[str], height: int = 300) -> go.Figure:
    figure = go.Figure(go.Bar(
        x=values, y=labels, orientation="h", marker=dict(color=BLUE, line=dict(width=0)),
        text=texts, textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{text}<extra></extra>",
    ))
    figure.update_layout(
        height=height, margin=dict(l=8, r=90, t=8, b=8), plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        font=dict(family="DM Sans, sans-serif", size=12, color=INK), barcornerradius=4, bargap=0.38,
        xaxis=dict(showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor="#cfd6df", ticksuffix="%",
                   tickfont=dict(color=MUTED)),
        yaxis=dict(autorange="reversed", tickfont=dict(color=INK)),
    )
    return figure


def render_flow_tables(flows: CountryFlows, name: str, *, framed: bool = True, limit: int = 12) -> None:
    """Two side-by-side tables: who the country owes, and who owes the country."""
    def body() -> None:
        if flows.empty:
            st.info(f"No bilateral lending relationships for {name} in the World Bank IDS data. {COVERAGE_NOTE}")
            return
        st.markdown(
            f"<div class='panel-subtitle'>Debt outstanding at end-{flows.year}. Arrows on the map point lender → borrower.</div>",
            unsafe_allow_html=True,
        )
        left, right = st.columns(2, gap="medium")
        with left:
            st.markdown(f"**🟠 {html.escape(name)} owes · {money(flows.borrowed_total) if flows.borrowed else '—'}**")
            if flows.borrowed:
                st.dataframe(flows_table(flows.borrowed[:limit], counterpart="Lender"), hide_index=True, width="stretch")
                if len(flows.borrowed) > limit:
                    st.caption(f"+{len(flows.borrowed) - limit} smaller lenders")
            else:
                st.caption("Not a reporting borrower in this data (typical for high-income countries).")
        with right:
            st.markdown(f"**🟢 Owe {html.escape(name)} · {money(flows.lent_total) if flows.lent else '—'}**")
            if flows.lent:
                st.dataframe(flows_table(flows.lent[:limit], counterpart="Borrower"), hide_index=True, width="stretch")
                if len(flows.lent) > limit:
                    st.caption(f"+{len(flows.lent) - limit} smaller borrowers")
            else:
                st.caption("No reporting borrower owes this country in this data.")
        st.caption(COVERAGE_NOTE)

    if framed:
        with st.container(border=True):
            st.markdown(f"<div class='panel-title'>Who owes whom · {html.escape(name)}</div>", unsafe_allow_html=True)
            body()
    else:
        body()


# ---------------------------------------------------------------------------
# World Atlas
# ---------------------------------------------------------------------------

def render_atlas(boundaries: dict[str, Any], index: CountryIndex) -> None:
    """Choropleth explorer for every catalogue indicator."""
    category_id = st.segmented_control(
        "CATEGORY", [c.id for c in CATEGORIES], default="debt", key="gde_atlas_category",
        format_func=lambda cid: f"{CATEGORY_BY_ID[cid].icon} {CATEGORY_BY_ID[cid].label}",
    ) or "debt"
    options = [indicator.id for indicator in indicators_in(category_id)]
    top = st.columns([2.2, .9, .8], gap="medium", vertical_alignment="bottom")
    with top[0]:
        indicator_id = st.selectbox("INDICATOR", options, key=f"gde_atlas_indicator_{category_id}",
                                    format_func=lambda iid: INDICATOR_BY_ID[iid].name)
    with top[1]:
        include_estimates = _include_estimates_toggle("gde_atlas_reported_only")
    with top[2]:
        if st.button("Refresh live data", key="gde_atlas_refresh", width="stretch",
                     help="Clear the 24-hour cache and re-query the APIs"):
            clear_indicator_cache()
            st.rerun()

    indicator = INDICATOR_BY_ID[indicator_id]
    with st.spinner(f"Loading {indicator.short} from {', '.join(s.label for s in indicator.sources)}…"):
        layer = load_layer(indicator_id, countries=index.codes, include_estimates=include_estimates)

    if not layer.values:
        st.warning("No live or saved data could be loaded for this indicator right now. "
                   "The sources below publish it directly — use **Refresh live data** to retry.")
    for note in layer.notes:
        st.caption(f"ℹ️ {note}")

    estimates = sum(1 for v in layer.values.values() if v.is_estimate)
    st.markdown(
        "<div class='kpi-strip'>"
        + _kpi("Countries with data", f"{layer.coverage} / {len(index.codes)}", f"{len(index.codes) - layer.coverage} without a value — shown grey, not zero")
        + _kpi("Most recent year", str(layer.latest_year or "—"), f"{estimates} IMF current-year estimates" if estimates else "Reported values")
        + _kpi("Typical year", str(layer.median_year or "—"), "Median observation year across countries")
        + _kpi("Category", CATEGORY_BY_ID[indicator.category].label, indicator.unit.replace("_", " "))
        + "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(f"<div style='margin:6px 0 10px'>{_status_chips(layer)}</div>", unsafe_allow_html=True)

    selected = st.session_state.get(SELECTED_KEY)
    flows = country_flows(bilateral_frame(), selected, names=index.names) if selected in index.codes else None
    show_arrows = st.toggle("Show who owes whom (lending arrows)", value=True, key="gde_atlas_arrows",
                            help="Arrows point from lender to borrower for the selected country, with the debt outstanding.")
    with st.container(border=True):
        st.markdown(f"<div class='panel-title'>{html.escape(indicator.name)}</div>"
                    f"<div class='panel-subtitle'>{html.escape(indicator.description)}"
                    f"{(' ' + html.escape(indicator.caveat)) if indicator.caveat else ''}</div>", unsafe_allow_html=True)
        map_obj, _classifier = build_atlas_map(boundary_geojson=boundaries, layer=layer,
                                               name_by_code=index.names, selected_iso3=selected,
                                               flows=flows if show_arrows else None)
        state = st_folium(map_obj, height=520, use_container_width=True,
                          returned_objects=["last_object_clicked_popup"], key=f"gde-atlas-map-{indicator_id}") or {}
        action = extract_map_action(state.get("last_object_clicked_popup"))
        if action and action[1] in index.codes and action[1] != selected:
            st.session_state[SELECTED_KEY] = action[1]
            st.rerun()
        if selected in index.codes:
            item = layer.values.get(selected)
            summary = (f"{format_value(item.value, indicator.unit)} ({item.year}{' est.' if item.is_estimate else ''})"
                       if item else "no value in the live sources")
            info_col, button_col = st.columns([3, 1], vertical_alignment="center")
            info_col.markdown(f"Selected: **{flag_emoji(selected)} {html.escape(index.names.get(selected, selected))}** — "
                              f"{html.escape(summary)}. Click any country on the map to select it.")
            if button_col.button("Open country profile →", key="gde_atlas_open_country", width="stretch"):
                st.session_state[PENDING_VIEW_KEY] = "country"
                st.rerun()

    if selected in index.codes and flows is not None:
        render_flow_tables(flows, index.names.get(selected, selected))
    elif selected not in index.codes:
        st.caption("Click any country on the map to see who it borrows from and who owes it money.")

    frame = pd.DataFrame(layer.rows())
    if not frame.empty:
        frame.insert(1, "country", frame["iso3"].map(lambda c: index.names.get(c, c)))
        frame["display"] = [format_value(v, indicator.unit) for v in frame["value"]]
        left, right = st.columns(2, gap="medium")
        for column, title, ascending in ((left, "Highest", False), (right, "Lowest", True)):
            with column:
                st.markdown(f"**{title} · {html.escape(indicator.short)}**")
                ranked = frame.sort_values("value", ascending=ascending).head(10)
                st.dataframe(
                    ranked[["country", "display", "year", "is_estimate", "source"]].rename(columns={
                        "country": "Country", "display": indicator.short, "year": "Year",
                        "is_estimate": "Estimate", "source": "Source"}),
                    hide_index=True, width="stretch", height=388,
                )
        with st.expander(f"All {len(frame)} countries · download with full provenance"):
            st.dataframe(frame.drop(columns=["display"]), hide_index=True, width="stretch", height=360)
            st.download_button("Download CSV ↓", frame.drop(columns=["display"]).to_csv(index=False).encode("utf-8"),
                               file_name=f"{indicator_id}.csv", mime="text/csv", key=f"gde-atlas-dl-{indicator_id}")

    missing = [index.names[c] for c in index.codes if c not in layer.values]
    with st.expander(f"🔎 Find newer or more detailed data for {indicator.short}", expanded=not layer.values):
        series_links = "".join(
            f"<li><a href='{s.portal_url}' target='_blank' rel='noopener'>{html.escape(s.label)} · {s.code}</a>"
            f" — <a href='{s.api_url}' target='_blank' rel='noopener'>API</a></li>" for s in indicator.sources)
        st.markdown(f"**Live series used here**<ul style='margin:4px 0 0 18px;font-size:13px'>{series_links}</ul>",
                    unsafe_allow_html=True)
        st.markdown("**Newer, more detailed or national data**" + _source_link_list(indicator.directory),
                    unsafe_allow_html=True)
        if missing:
            st.markdown(f"**No value in the live sources ({len(missing)})** — check the national statistics office "
                        f"or the sources above: " + ", ".join(sorted(missing)))


# ---------------------------------------------------------------------------
# Country profile
# ---------------------------------------------------------------------------

HEADLINE_INDICATORS = ("gov_debt_gdp", "fiscal_balance_gdp", "gdp_growth", "inflation", "gdp_per_capita", "population")


def render_country(index: CountryIndex) -> None:
    """One country: headline numbers, where the money goes, trends and research links."""
    top = st.columns([1.6, 1, 1], vertical_alignment="bottom")
    with top[0]:
        iso3 = _country_selectbox(index, "COUNTRY", COUNTRY_KEY)
    with top[1]:
        include_estimates = _include_estimates_toggle("gde_country_reported_only")
    with top[2]:
        if st.button("Refresh live data", key="gde_country_refresh"):
            clear_indicator_cache()
            st.rerun()
    name = index.names.get(iso3, iso3)

    with st.spinner("Loading indicators…"):
        layers = {iid: load_layer(iid, countries=index.codes, include_estimates=include_estimates)
                  for iid in (*HEADLINE_INDICATORS, *(i.id for i in SPENDING_LINES), "gov_expenditure_gdp", "gov_revenue_gdp")}

    st.markdown(f"<div class='profile-title' style='margin:6px 0 2px'>{flag_emoji(iso3)} {html.escape(name)}</div>"
                f"<div class='profile-note'>ISO-3 {iso3} · every figure shows its own year and source</div>",
                unsafe_allow_html=True)
    tiles = []
    for iid in HEADLINE_INDICATORS:
        layer, indicator = layers[iid], INDICATOR_BY_ID[iid]
        item = layer.values.get(iso3)
        note = (f"{item.year}{' est.' if item.is_estimate else ''} · {item.source.label}" if item else "No data — see sources below")
        tiles.append(_kpi(indicator.short, format_value(item.value if item else None, indicator.unit), note))
    st.markdown("<div class='kpi-strip' style='grid-template-columns:repeat(3,minmax(0,1fr))'>" + "".join(tiles) + "</div>",
                unsafe_allow_html=True)

    money_col, trend_col = st.columns([1, 1], gap="medium")
    with money_col, st.container(border=True):
        st.markdown("<div class='panel-title'>Where the money goes</div>"
                    "<div class='panel-subtitle'>Selected spending lines, % of GDP. Lines come from different sources "
                    "and years and do not add up to total spending.</div>", unsafe_allow_html=True)
        labels, values, texts, missing = [], [], [], []
        for indicator in SPENDING_LINES:
            item = layers[indicator.id].values.get(iso3)
            if item is None:
                missing.append(indicator.short)
                continue
            labels.append(indicator.short)
            values.append(item.value)
            texts.append(f"{item.value:,.1f}% · {item.year}")
        if values:
            st.plotly_chart(_bar_chart(labels, values, texts, height=70 + 46 * len(values)),
                            width="stretch", config={"displayModeBar": False})
        else:
            st.info("No spending-line data is available for this country from the live sources.")
        totals = []
        for iid in ("gov_expenditure_gdp", "gov_revenue_gdp"):
            item = layers[iid].values.get(iso3)
            if item:
                totals.append(f"{INDICATOR_BY_ID[iid].short}: **{item.value:,.1f}% of GDP** ({item.year})")
        if totals:
            st.markdown(" · ".join(totals))
        if missing:
            st.caption("No live value for: " + ", ".join(missing) + ".")
        st.caption("Full functional breakdown (defence, health, education, social protection, public order, economic "
                   "affairs, environment, housing, culture, general services):")
        st.markdown(_source_link_list(["imf_gfs_cofog", "dbnomics_cofog", "boost", "open_budget"]), unsafe_allow_html=True)

    with trend_col, st.container(border=True):
        st.markdown("<div class='panel-title'>Trend</div>", unsafe_allow_html=True)
        trend_id = st.selectbox("Indicator", [i.id for i in INDICATORS], key="gde_country_trend",
                                format_func=lambda iid: f"{CATEGORY_BY_ID[INDICATOR_BY_ID[iid].category].icon} {INDICATOR_BY_ID[iid].name}")
        layer = layers.get(trend_id) or load_layer(trend_id, countries=index.codes, include_estimates=include_estimates)
        years = layer.series.get(iso3, {})
        cutoff = layer.values[iso3].year if iso3 in layer.values else None
        points = sorted((y, v) for y, v in years.items() if cutoff is None or y <= cutoff)
        if points:
            figure = go.Figure(go.Scatter(
                x=[p[0] for p in points], y=[p[1] for p in points], mode="lines+markers",
                line=dict(color=BLUE, width=2), marker=dict(size=8, color=BLUE, line=dict(color="#ffffff", width=2)),
                hovertemplate="%{x}: %{y:,.2f}<extra></extra>",
            ))
            figure.update_layout(height=330, margin=dict(l=8, r=8, t=8, b=8), plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
                                 font=dict(family="DM Sans, sans-serif", size=12, color=INK), hovermode="x unified",
                                 xaxis=dict(showgrid=False, tickfont=dict(color=MUTED)),
                                 yaxis=dict(gridcolor=GRID, tickfont=dict(color=MUTED), title=None))
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
            st.caption(f"{INDICATOR_BY_ID[trend_id].name} · {layer.values[iso3].source_label if iso3 in layer.values else ''}")
        else:
            st.info("No time series is available for this country and indicator.")
            st.markdown(_source_link_list(INDICATOR_BY_ID[trend_id].directory), unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown(f"<div class='panel-title'>Who owes whom · {html.escape(name)}</div>", unsafe_allow_html=True)
        render_flow_tables(country_flows(bilateral_frame(), iso3, names=index.names), name, framed=False)
    with st.container(border=True):
        st.markdown(f"<div class='panel-title'>Research {html.escape(name)} further</div>", unsafe_allow_html=True)
        links = "".join(f"<li><a href='{html.escape(url, quote=True)}' target='_blank' rel='noopener'>{html.escape(label)}</a></li>"
                        for label, url in country_links(iso3, index.iso2.get(iso3)))
        st.markdown(f"<ul style='margin:4px 0 0 18px;font-size:13px'>{links}</ul>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data & sources
# ---------------------------------------------------------------------------

def render_sources(index: CountryIndex) -> None:
    """Freshness of every indicator, the full source directory, and how to cite."""
    st.markdown("<div class='panel-title'>How fresh is the data?</div>"
                "<div class='panel-subtitle'>Every indicator is downloaded live from the World Bank and IMF APIs and "
                "cached for 24 hours. For each country the most recent valid value wins. If an API is down, the last "
                "successful download is used and labelled.</div>", unsafe_allow_html=True)
    if st.button("Check all indicators now", key="gde_sources_check", type="primary"):
        st.session_state["gde_sources_checked"] = True
    if st.session_state.get("gde_sources_checked"):
        rows, combined = [], []
        progress = st.progress(0.0, text="Querying live sources…")
        for position, indicator in enumerate(INDICATORS, start=1):
            layer = load_layer(indicator.id, countries=index.codes)
            combined.extend(layer.rows())
            rows.append({
                "Category": CATEGORY_BY_ID[indicator.category].label, "Indicator": indicator.name,
                "Series": ", ".join(s.code for s in indicator.sources),
                "Countries": layer.coverage, "Latest year": layer.latest_year, "Median year": layer.median_year,
                "Status": " · ".join(f"{k}: {v}" for k, v in layer.source_status.items()),
            })
            progress.progress(position / len(INDICATORS), text=f"Checked {indicator.short}")
        progress.empty()
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=420)
        if combined:
            full = pd.DataFrame(combined)
            full.insert(1, "country", full["iso3"].map(lambda c: index.names.get(c, c)))
            st.download_button("Download the full dataset — every indicator, latest value, with provenance (CSV) ↓",
                               full.to_csv(index=False).encode("utf-8"), file_name="global-debt-atlas-latest.csv",
                               mime="text/csv", key="gde-sources-full-dl")

    st.markdown("<div style='height:10px'></div><div class='panel-title'>Where to find newer or deeper data</div>"
                "<div class='panel-subtitle'>When a country or year is missing here, these are the best places to look.</div>",
                unsafe_allow_html=True)
    for category in CATEGORIES:
        keys = [key for key, source in SOURCE_DIRECTORY.items() if category.id in source.topics]
        with st.expander(f"{category.icon} {category.label} · {len(keys)} sources"):
            st.markdown(_source_link_list(keys), unsafe_allow_html=True)

    st.markdown("<div style='height:10px'></div><div class='panel-title'>Cite this project</div>", unsafe_allow_html=True)
    st.markdown("If you use this tool or its data exports in research or teaching, please cite it "
                "(see `CITATION.cff` in the repository) **and** the original data providers listed with every value.")
