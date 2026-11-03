"""Who owes whom: bilateral lending arrows for any selected country.

From the World Bank IDS bilateral cache (public and publicly guaranteed debt
owed to official bilateral creditors) this module answers, for one country:

* **Borrowed from** — which governments it owes, and how much.
* **Lent to** — which reporting borrowers owe it, and how much.

Arrows always point **from lender to borrower** (the direction the money was
lent); amounts are debt *outstanding* at the end of the year shown.

Coverage caveat: only ~120 low- and middle-income countries report to IDS.
Debts between high-income countries (e.g. Japan → USA) are not in this data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import folium
import pandas as pd
from branca.element import MacroElement, Template

from src.indicator_data import format_value
from src.map_geometry import assign_distinct_colors
from src.processor import clean_records, read_cache, st
from src.relationship_map import PillPlacer, _country_name_icon, _popup_html, add_arrowhead, curved_connection, short_money

BILATERAL_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "bilateral_debt_cache.json"
BORROWED_COLOR = "#eb6834"   # used for "borrowed from" table markers
LENT_COLOR = "#16877b"       # used for "lent to" table markers
LENT_DASH = "8 6"            # lent-to arrows are dashed; borrowed-from arrows are solid


def flow_key(flow: Flow, direction: str) -> str:
    """Colour key: the IDS creditor code for lenders (as on the Who-owes-whom map), ISO3 for borrowers."""
    return (flow.creditor_id or flow.lender_iso3) if direction == "borrowed" else flow.borrower_iso3
COVERAGE_NOTE = (
    "World Bank International Debt Statistics: public and publicly guaranteed debt owed to official bilateral "
    "creditors, reported by ~120 low- and middle-income borrowers. Debts between high-income countries are not covered."
)


@dataclass(frozen=True)
class Flow:
    """Debt outstanding from one borrower to one lender in one year."""

    lender_iso3: str
    lender_name: str
    borrower_iso3: str
    borrower_name: str
    amount_usd: float
    share_pct: float  # share of the *selected* country's borrowed or lent total
    year: int
    creditor_id: str = ""


@dataclass
class CountryFlows:
    iso3: str
    year: int | None
    borrowed: list[Flow] = field(default_factory=list)
    lent: list[Flow] = field(default_factory=list)

    @property
    def borrowed_total(self) -> float:
        return sum(flow.amount_usd for flow in self.borrowed)

    @property
    def lent_total(self) -> float:
        return sum(flow.amount_usd for flow in self.lent)

    @property
    def empty(self) -> bool:
        return not self.borrowed and not self.lent


@st.cache_data(show_spinner=False, max_entries=2)
def load_bilateral_frame(path: str = str(BILATERAL_CACHE_PATH), modified: float = 0.0) -> pd.DataFrame:
    """Load and normalise the IDS bilateral cache (empty frame if it is missing)."""
    del modified  # cache key only
    try:
        return clean_records(read_cache(path)["records"])
    except (OSError, ValueError, KeyError):
        return pd.DataFrame(columns=["year", "debtor_iso3", "debtor_name", "creditor_id", "creditor_iso3", "creditor_name", "amount_usd"])


def bilateral_frame() -> pd.DataFrame:
    path = BILATERAL_CACHE_PATH
    return load_bilateral_frame(str(path), path.stat().st_mtime if path.exists() else 0.0)


def country_flows(frame: pd.DataFrame, iso3: str, year: int | None = None, names: dict[str, str] | None = None) -> CountryFlows:
    """Who ``iso3`` borrowed from and lent to, for ``year`` (default: latest year it appears).

    Borrowed flows include non-sovereign creditor areas (e.g. "Multiple Lenders")
    with an empty ``lender_iso3``; they are listed in tables but cannot be drawn.
    """
    code = str(iso3 or "").upper()
    names = names or {}
    if frame.empty or not code:
        return CountryFlows(code, None)
    involved = frame[(frame["debtor_iso3"] == code) | (frame["creditor_iso3"] == code)]
    if involved.empty:
        return CountryFlows(code, None)
    chosen = int(year) if year is not None and (involved["year"] == int(year)).any() else int(involved["year"].max())
    rows = frame[frame["year"] == chosen]

    borrowed_rows = rows[(rows["debtor_iso3"] == code) & (rows["amount_usd"] > 0)]
    borrowed_rows = (borrowed_rows.groupby(["creditor_id", "creditor_iso3", "creditor_name", "debtor_name"], as_index=False)["amount_usd"]
                     .sum().sort_values("amount_usd", ascending=False))
    borrowed_total = float(borrowed_rows["amount_usd"].sum())
    borrowed = [
        Flow(
            lender_iso3=str(r.creditor_iso3 or ""), lender_name=names.get(str(r.creditor_iso3), str(r.creditor_name)),
            borrower_iso3=code, borrower_name=names.get(code, str(r.debtor_name)),
            amount_usd=float(r.amount_usd), share_pct=float(r.amount_usd) / borrowed_total * 100 if borrowed_total else 0.0,
            year=chosen, creditor_id=str(r.creditor_id),
        )
        for r in borrowed_rows.itertuples(index=False)
    ]

    lent_rows = rows[(rows["creditor_iso3"] == code) & (rows["amount_usd"] > 0)]
    lent_rows = (lent_rows.groupby(["debtor_iso3", "debtor_name", "creditor_name"], as_index=False)["amount_usd"]
                 .sum().sort_values("amount_usd", ascending=False))
    lent_total = float(lent_rows["amount_usd"].sum())
    lent = [
        Flow(
            lender_iso3=code, lender_name=names.get(code, str(r.creditor_name)),
            borrower_iso3=str(r.debtor_iso3), borrower_name=names.get(str(r.debtor_iso3), str(r.debtor_name)),
            amount_usd=float(r.amount_usd), share_pct=float(r.amount_usd) / lent_total * 100 if lent_total else 0.0,
            year=chosen,
        )
        for r in lent_rows.itertuples(index=False)
    ]
    return CountryFlows(code, chosen, borrowed, lent)


def money(value: float) -> str:
    return format_value(value, "usd")


def flows_table(flows: list[Flow], *, counterpart: str) -> pd.DataFrame:
    """Readable table: counterpart country, amount, share, year."""
    return pd.DataFrame([
        {
            counterpart: flow.lender_name if counterpart == "Lender" else flow.borrower_name,
            "Debt outstanding": money(flow.amount_usd),
            "Share": f"{flow.share_pct:.1f}%",
            "Year": flow.year,
        }
        for flow in flows
    ])


def add_flow_arrows(
    map_obj: folium.Map,
    flows: CountryFlows,
    locations: dict[str, tuple[float, float]],
    *,
    max_each: int = 8,
    include_borrowed: bool = True,
    include_lent: bool = True,
    selected_name: str | None = None,
    pills: PillPlacer | None = None,
    legend_position: str = "bottomright",
) -> dict[str, int]:
    """Draw lender → borrower arrows with amount labels for the selected country.

    Returns counts of drawn arrows. Countries without coordinates are skipped
    (they remain in the tables).
    """
    origin = locations.get(flows.iso3)
    drawn = {"borrowed": 0, "lent": 0}
    if origin is None or flows.empty:
        return drawn
    name = selected_name or flows.iso3
    if pills is None:
        pills = PillPlacer()
        pills.place(origin, force=True)

    legend_rows: dict[str, list[tuple[str, str, str]]] = {"borrowed": [], "lent": []}

    borrowed_flows = [f for f in flows.borrowed if f.lender_iso3 in locations and f.lender_iso3 != flows.iso3][:max_each]
    lent_flows = [f for f in flows.lent if f.borrower_iso3 in locations and f.borrower_iso3 != flows.iso3][:max_each]
    colors = {
        "borrowed": assign_distinct_colors([flow_key(f, "borrowed") for f in borrowed_flows]),
        "lent": assign_distinct_colors([flow_key(f, "lent") for f in lent_flows]),
    }

    def draw(flow: Flow, lender_pos: tuple[float, float], borrower_pos: tuple[float, float], direction: str,
             pill_pos: tuple[float, float], pill_role: str, pill_name: str, action_iso3: str, index: int) -> None:
        color = colors[direction][flow_key(flow, direction)]
        dash = LENT_DASH if direction == "lent" else None
        legend_rows[direction].append((color, pill_name, short_money(flow.amount_usd)))
        curve = curved_connection(lender_pos, borrower_pos, bend=0.14 + (index % 2) * 0.04)
        sentence = f"{flow.borrower_name} owes {flow.lender_name} {money(flow.amount_usd)} ({flow.year})"
        weight = 1.8 + min(4.0, flow.share_pct / 12.0)
        popup = _popup_html(sentence, [
            ("Lender", flow.lender_name), ("Borrower", flow.borrower_name),
            ("Debt outstanding", money(flow.amount_usd)),
            ("Share", f"{flow.share_pct:.1f}% of {name}'s {'borrowing' if direction == 'borrowed' else 'lending'} in this data"),
            ("Year", str(flow.year)),
        ], ("COUNTRY", action_iso3))
        folium.PolyLine(curve, color="#ffffff", weight=weight + 3, opacity=0.85).add_to(map_obj)
        folium.PolyLine(curve, color=color, weight=weight, opacity=0.95, line_cap="round", dash_array=dash,
                        tooltip=sentence, popup=folium.Popup(popup, max_width=340)).add_to(map_obj)
        add_arrowhead(map_obj, curve, color, size=13 + min(6, int(flow.share_pct / 10)), tooltip=sentence)
        if pills.place(pill_pos):
            folium.Marker(pill_pos, icon=_country_name_icon(pill_name, role=pill_role, color=color),
                          tooltip=sentence, popup=folium.Popup(popup, max_width=340), z_index_offset=700).add_to(map_obj)
        else:  # label would overlap a bigger flow's label: a dot keeps the hover and click
            folium.CircleMarker(pill_pos, radius=5, color="#ffffff", weight=2, fill=True, fill_color=color, fill_opacity=1,
                                tooltip=sentence, popup=folium.Popup(popup, max_width=340)).add_to(map_obj)

    if include_borrowed:
        for flow in borrowed_flows:
            lender = locations[flow.lender_iso3]
            draw(flow, lender, origin, "borrowed", lender, f"Lent {short_money(flow.amount_usd)}", flow.lender_name,
                 flow.lender_iso3, drawn["borrowed"])
            drawn["borrowed"] += 1
    if include_lent:
        for flow in lent_flows:
            borrower = locations[flow.borrower_iso3]
            draw(flow, origin, borrower, "lent", borrower, f"Owes {short_money(flow.amount_usd)}", flow.borrower_name,
                 flow.borrower_iso3, drawn["lent"])
            drawn["lent"] += 1
    if drawn["borrowed"] or drawn["lent"]:
        _add_flow_legend(map_obj, flows, name, legend_rows, position=legend_position)
    return drawn


def _swatch(color: str, dashed: bool) -> str:
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    return (f"<svg width='22' height='8' aria-hidden='true'><line x1='1' y1='4' x2='21' y2='4' stroke='{color}' "
            f"stroke-width='3' stroke-linecap='round'{dash}/></svg>")


def _add_flow_legend(map_obj: folium.Map, flows: CountryFlows, name: str,
                     legend_rows: dict[str, list[tuple[str, str, str]]], *, position: str = "bottomright") -> None:
    import html
    import json

    sections = []
    for direction, title, total, count in (
        ("borrowed", f"{name} borrowed from", flows.borrowed_total, len(flows.borrowed)),
        ("lent", f"{name} lent to", flows.lent_total, len(flows.lent)),
    ):
        rows = legend_rows.get(direction) or []
        if not rows:
            continue
        line_style = "dashed" if direction == "lent" else "solid"
        items = "".join(
            f"<div class='flow-row'>{_swatch(color, direction == 'lent')}<b>{html.escape(label)}</b>"
            f"<em>{html.escape(amount)}</em></div>" for color, label, amount in rows)
        more = count - len(rows)
        extra = f"<div class='flow-more'>+{more} more in the table below the map</div>" if more > 0 else ""
        sections.append(f"<div class='flow-head'>{html.escape(title)} · {money(total)} <span>({line_style})</span></div>"
                        f"{items}{extra}")
    legend = (
        f"<div class='flow-legend'><div class='flow-title'>Who owes whom · {flows.year}</div>{''.join(sections)}"
        "<div class='flow-note'>Each colour is one country. Arrows point lender → borrower. Amounts are bilateral "
        "official debt outstanding (World Bank IDS).</div></div>"
    )
    control = MacroElement()
    control._template = Template("""
        {% macro script(this, kwargs) %}
        (function(){
          var box = L.control({position: {{ this.position_json }}});
          box.onAdd = function() { var d = L.DomUtil.create('div', ''); d.innerHTML = {{ this.legend_json }};
            L.DomEvent.disableClickPropagation(d); return d; };
          box.addTo({{ this._parent.get_name() }});
        })();
        {% endmacro %}
    """)
    control.legend_json = json.dumps(legend)
    control.position_json = json.dumps(position if position in {"topleft", "topright", "bottomleft", "bottomright"} else "bottomright")
    map_obj.add_child(control)
    map_obj.get_root().html.add_child(folium.Element("""
    <style>
      .flow-legend{background:#fff;border:1px solid #dfe5ec;border-radius:9px;padding:9px 11px;width:250px;max-height:330px;overflow:auto;
        font:11px/1.4 'DM Sans',system-ui,sans-serif;color:#22344d;box-shadow:0 2px 10px rgba(25,43,66,.08)}
      .flow-title{font-weight:800;margin-bottom:5px;color:#17243a}
      .flow-head{font-weight:700;margin:6px 0 2px;color:#17243a}
      .flow-head span{font-weight:400;color:#7b8798}
      .flow-row{display:flex;align-items:center;gap:7px;margin:2px 0}
      .flow-row b{font-weight:600;flex:1}
      .flow-row em{font-style:normal;color:#52627a;font-variant-numeric:tabular-nums}
      .flow-more{color:#7b8798;font-size:10px;margin-top:1px}
      .flow-note{margin-top:5px;color:#7b8798;font-size:10px}
      .gde-arrowhead{background:none;border:none}
    </style>
    """))
