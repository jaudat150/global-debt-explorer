"""Streamlit view: where each country spends its money, in US dollars."""

from __future__ import annotations

import html

import plotly.graph_objects as go
import streamlit as st

from src.atlas_view import (
    BLUE, GRID, INK, MUTED, CountryIndex, _country_selectbox, _include_estimates_toggle, _source_link_list, COUNTRY_KEY,
)
from src.indicator_data import clear_indicator_cache, format_value
from src.relationship_map import flag_emoji
from src.spending import ITEM_BY_ID, SPENDING_ITEMS, country_spending, load_spending_layers, spending_matrix

TOTAL_ICON = "🏛️"
METRICS = {
    "usd": ("Total US$", lambda v: format_value(v, "usd")),
    "usd_per_person": ("US$ per person", lambda v: f"US${v:,.0f}"),
    "pct_gdp": ("% of GDP", lambda v: f"{v:,.2f}%"),
}


def _row_html(icon: str, label: str, amount: str, detail: str, note: str) -> str:
    return (
        "<div style='display:grid;grid-template-columns:34px 1fr auto;gap:10px;align-items:center;"
        "padding:10px 12px;border:1px solid #e6ebf1;border-radius:9px;background:#fff;margin:6px 0'>"
        f"<div style='font-size:20px'>{icon}</div>"
        f"<div><div style='font-weight:800;color:{INK}'>{html.escape(label)}</div>"
        f"<div style='font-size:11px;color:{MUTED}'>{html.escape(note)}</div></div>"
        f"<div style='text-align:right'><div style='font:800 19px Manrope,sans-serif;color:{INK}'>{html.escape(amount)}</div>"
        f"<div style='font-size:11px;color:{MUTED}'>{html.escape(detail)}</div></div></div>"
    )


def _hbar(labels: list[str], values: list[float], texts: list[str], height: int) -> go.Figure:
    figure = go.Figure(go.Bar(
        x=values, y=labels, orientation="h", marker=dict(color=BLUE, line=dict(width=0)), text=texts,
        textposition="outside", cliponaxis=False, hovertemplate="%{y}: %{text}<extra></extra>",
    ))
    figure.update_layout(
        height=height, margin=dict(l=8, r=110, t=8, b=8), plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        font=dict(family="DM Sans, sans-serif", size=12, color=INK), barcornerradius=4, bargap=0.35,
        xaxis=dict(showgrid=True, gridcolor=GRID, tickfont=dict(color=MUTED)),
        yaxis=dict(autorange="reversed", tickfont=dict(color=INK)),
    )
    return figure


def render_spending(index: CountryIndex) -> None:
    """Per-country spending lines in US$, and a world ranking for any line."""
    top = st.columns([1.6, 1, 1], vertical_alignment="bottom")
    with top[0]:
        iso3 = _country_selectbox(index, "COUNTRY", COUNTRY_KEY)
    with top[1]:
        include_estimates = _include_estimates_toggle("gde_spending_reported_only")
    with top[2]:
        if st.button("Refresh live data", key="gde_spending_refresh", width="stretch"):
            clear_indicator_cache()
            st.rerun()
    name = index.names.get(iso3, iso3)

    with st.spinner("Loading spending data from SIPRI, WHO, UNESCO, IMF and the World Bank…"):
        layers = load_spending_layers(index.codes, include_estimates)
    lines, missing, total = country_spending(iso3, layers, include_estimates=include_estimates)

    left, right = st.columns([1.05, 1], gap="medium")
    with left, st.container(border=True):
        st.markdown(f"<div class='panel-title'>{flag_emoji(iso3)} Where {html.escape(name)} spends its money</div>"
                    "<div class='panel-subtitle'>Latest published year for each line, in current US dollars.</div>",
                    unsafe_allow_html=True)
        if total:
            st.markdown(_row_html(TOTAL_ICON, f"{name}: total government spending", format_value(total.usd, "usd"),
                                  f"{total.pct_gdp:.1f}% of GDP · {total.year}", "All levels of government · context for the lines below"),
                        unsafe_allow_html=True)
        for line in lines:
            detail = " · ".join(filter(None, [
                f"{line.pct_gdp:.2f}% of GDP" if line.pct_gdp is not None else "",
                f"US${line.per_capita:,.0f} per person" if line.per_capita else "",
                str(line.year),
            ]))
            st.markdown(_row_html(line.item.icon, f"{name}: {line.item.label}", format_value(line.usd, "usd"), detail,
                                  f"{line.item.scope} · {line.method}"), unsafe_allow_html=True)
        if not lines:
            st.info(f"No spending line for {name} could be computed from the live sources.")
        if missing:
            st.caption(f"No published data for {name}: " + ", ".join(f"{i.icon} {i.label}" for i in missing) + ".")
        st.caption("Lines come from different sources, scopes (general vs central government vs all sectors) and years, "
                   "so they do not add up to the total.")

    with right, st.container(border=True):
        st.markdown("<div class='panel-title'>Compared side by side (US$)</div>", unsafe_allow_html=True)
        if lines:
            st.plotly_chart(
                _hbar([f"{l.item.icon} {l.item.label}" for l in lines], [l.usd for l in lines],
                      [f"{format_value(l.usd, 'usd')} · {l.year}" for l in lines], 80 + 48 * len(lines)),
                width="stretch", config={"displayModeBar": False},
            )
        with st.expander("Sources and method for each line"):
            for line in lines:
                st.markdown(f"**{line.item.icon} {line.item.label}** — {line.item.note}  \n"
                            f"<span style='font-size:12px;color:{MUTED}'>{html.escape(line.method)}: "
                            f"{html.escape(line.sources)}</span>", unsafe_allow_html=True)
        st.markdown("**Full functional breakdown** (social protection, public order, housing, environment, culture…):"
                    + _source_link_list(["imf_gfs_cofog", "dbnomics_cofog", "eurostat_cofog", "boost", "open_budget"]),
                    unsafe_allow_html=True)

    # ------------------------------------------------------------ world ranking
    with st.container(border=True):
        st.markdown("<div class='panel-title'>World ranking</div>"
                    "<div class='panel-subtitle'>Which countries spend the most on each line.</div>", unsafe_allow_html=True)
        controls = st.columns([1.4, 1, .8], vertical_alignment="bottom")
        with controls[0]:
            item_id = st.selectbox("Spending line", [i.id for i in SPENDING_ITEMS] + ["total"], key="gde_spending_rank_item",
                                   format_func=lambda iid: f"{ITEM_BY_ID[iid].icon} {ITEM_BY_ID[iid].label}")
        with controls[1]:
            metric = st.segmented_control("Measure", list(METRICS), default="usd", key="gde_spending_metric",
                                          format_func=lambda m: METRICS[m][0]) or "usd"
        with controls[2]:
            top_n = st.number_input("Show top", 5, 60, 20, step=5, key="gde_spending_topn")
        matrix = spending_matrix(index.codes, layers, index.names, include_estimates=include_estimates)
        ranked = matrix[(matrix["item_id"] == item_id)].dropna(subset=[metric]).sort_values(metric, ascending=False)
        label, fmt = METRICS[metric]
        if ranked.empty:
            st.info("No country has data for this line in the live sources right now.")
        else:
            shown = ranked.head(int(top_n))
            st.plotly_chart(
                _hbar([f"{flag_emoji(c)} {n}" for c, n in zip(shown["iso3"], shown["country"])], shown[metric].tolist(),
                      [f"{fmt(v)} · {y}" for v, y in zip(shown[metric], shown["year"])], 70 + 30 * len(shown)),
                width="stretch", config={"displayModeBar": False},
            )
            st.caption(f"{len(ranked)} countries have data · {label} · each country's latest year · "
                       f"{ITEM_BY_ID[item_id].scope}")
        st.download_button(
            "Download spending for every country and line (CSV, with method and sources) ↓",
            matrix.to_csv(index=False).encode("utf-8"), file_name="spending_by_country_usd.csv", mime="text/csv",
            key="gde_spending_dl",
        )

