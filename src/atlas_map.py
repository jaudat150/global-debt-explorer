"""Choropleth map for one World Atlas indicator layer.

Colour rules (see the project's data-viz notes):

* **Sequential** indicators use one blue ramp, light → dark, with quantile
  breaks so outliers (hyperinflation, micro-states) cannot flatten the map.
* **Diverging** indicators (fiscal balance, growth, current account, FDI) use
  red ← neutral grey → blue around ``indicator.center``; red = below centre.
* Missing data is a flat light grey and the tooltip says "No data — not zero".

Countries without a 1:110m polygon (small island states) get a circle marker
at their Natural Earth label point so no country silently disappears.
"""

from __future__ import annotations

import bisect
import html
import math
from dataclasses import dataclass
from typing import Any, Callable

import folium
from branca.element import Element, MacroElement, Template
from folium.features import GeoJsonPopup, GeoJsonTooltip
from folium.plugins import Fullscreen

from src.catalog import Indicator
from src.indicator_data import IndicatorLayer, format_value
from src.processor import load_country_label_points
from src.debt_flows import CountryFlows, add_flow_arrows
from src.relationship_map import _add_basemap, _add_map_styles, _popup_html, country_locations, iso3_from_properties

SEQUENTIAL = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")
DIVERGING = ("#a02c2c", "#d24a49", "#ec9a98", "#f0efec", "#9ec5f4", "#3987e5", "#184f95")
NO_DATA = "#ffffff"          # drawn with a dashed grey outline so it never reads as the neutral midpoint
NO_DATA_OUTLINE = "#9aa3b1"


@dataclass(frozen=True)
class Bin:
    lower: float
    upper: float
    color: str
    label: str


@dataclass(frozen=True)
class Classifier:
    """Maps a value to a colour; ``bins`` drive the legend."""

    bins: tuple[Bin, ...]
    edges: tuple[float, ...]
    colors: tuple[str, ...]

    def color(self, value: float | None) -> str:
        if value is None or not math.isfinite(value):
            return NO_DATA
        return self.colors[min(len(self.colors) - 1, bisect.bisect_right(self.edges, value))]


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def _nice(value: float) -> float:
    """Round a break to two significant figures so legend labels read cleanly."""
    if value == 0 or not math.isfinite(value):
        return 0.0
    magnitude = 10 ** (math.floor(math.log10(abs(value))) - 1)
    return round(value / magnitude) * magnitude


def classify(indicator: Indicator, values: list[float]) -> Classifier:
    """Build quantile (sequential) or symmetric (diverging) colour classes."""
    finite = sorted(value for value in values if math.isfinite(value))
    fmt: Callable[[float], str] = lambda v: format_value(v, indicator.unit).replace(" of GDP", "").replace(" of GNI", "")

    if indicator.scale == "diverging":
        center = indicator.center
        spread = _quantile(sorted(abs(v - center) for v in finite), 0.9) if finite else 1.0
        step = _nice(spread / 2.5) or 1.0
        edges = (center - 2.5 * step, center - 1.5 * step, center - 0.5 * step,
                 center + 0.5 * step, center + 1.5 * step, center + 2.5 * step)
        colors = DIVERGING
    else:
        if not finite:
            return Classifier((), (), (SEQUENTIAL[3],))
        raw = [_quantile(finite, k / 7) for k in range(1, 7)]
        edges_list: list[float] = []
        for edge in (_nice(value) for value in raw):
            if not edges_list or edge > edges_list[-1]:
                edges_list.append(edge)
        edges = tuple(edges_list)
        # Spread the available colours across however many classes survived de-duplication.
        count = len(edges) + 1
        colors = tuple(SEQUENTIAL[round(i * (len(SEQUENTIAL) - 1) / max(1, count - 1))] for i in range(count))

    bounds = (-math.inf, *edges, math.inf)
    bins = []
    for index, color in enumerate(colors):
        low, high = bounds[index], bounds[index + 1]
        if math.isinf(low):
            label = f"< {fmt(high)}"
        elif math.isinf(high):
            label = f"≥ {fmt(low)}"
        else:
            label = f"{fmt(low)} – {fmt(high)}"
        bins.append(Bin(low, high, color, label))
    return Classifier(tuple(bins), tuple(edges), tuple(colors))


def build_atlas_map(
    *,
    boundary_geojson: dict[str, Any],
    layer: IndicatorLayer,
    name_by_code: dict[str, str],
    selected_iso3: str | None = None,
    flows: CountryFlows | None = None,
) -> tuple[folium.Map, Classifier]:
    """Render ``layer`` as a choropleth with tooltips, click-to-select and a legend.

    With ``flows`` the selected country's bilateral lending relationships are
    drawn on top as lender → borrower arrows with amount labels.
    """
    indicator = layer.indicator
    classifier = classify(indicator, [item.value for item in layer.values.values()])
    selected = (selected_iso3 or "").upper()

    map_obj = folium.Map(location=(18, 10), zoom_start=2, min_zoom=1, max_zoom=8, tiles=None,
                         prefer_canvas=True, world_copy_jump=True, control_scale=False)
    _add_basemap(map_obj)

    def describe(iso: str) -> tuple[str, list[tuple[str, str]], dict[str, str]]:
        name = name_by_code.get(iso, iso)
        item = layer.values.get(iso)
        if item is None:
            rows = [(indicator.short, "No data — not zero"),
                    ("Where to look", "See 'Find newer data' below the map")]
            return name, rows, {"value": "No data (not zero)", "year": "—", "source": "No live source has a value"}
        value_text = format_value(item.value, indicator.unit)
        year_text = f"{item.year}{' (IMF estimate)' if item.is_estimate else ''}"
        rows = [(indicator.short, value_text), ("Year", year_text), ("Source", item.source_label)]
        return name, rows, {"value": value_text, "year": year_text, "source": item.source_label}

    features = []
    polygon_codes: set[str] = set()
    for feature in boundary_geojson.get("features", []):
        props = dict(feature.get("properties") or {})
        iso = iso3_from_properties(props)
        if not iso:
            continue
        polygon_codes.add(iso)
        item = layer.values.get(iso)
        name, rows, tip = describe(iso)
        props.update({
            "_iso": iso, "_name": name, "_value": tip["value"], "_year": tip["year"], "_source": tip["source"],
            "_fill": classifier.color(item.value if item else None),
            "_selected": iso == selected,
            "_popup": _popup_html(name, rows, ("COUNTRY", iso)),
        })
        features.append({"type": "Feature", "properties": props, "geometry": feature.get("geometry")})

    folium.GeoJson(
        {"type": "FeatureCollection", "features": features},
        name=indicator.short,
        style_function=lambda f: {
            "fillColor": f["properties"]["_fill"],
            "fillOpacity": 0.55 if f["properties"]["_fill"] == NO_DATA else 0.92,
            "color": "#17243a" if f["properties"]["_selected"] else (
                NO_DATA_OUTLINE if f["properties"]["_fill"] == NO_DATA else "#ffffff"),
            "weight": 2.2 if f["properties"]["_selected"] else 0.8,
            "dashArray": "3 3" if f["properties"]["_fill"] == NO_DATA and not f["properties"]["_selected"] else None,
        },
        highlight_function=lambda _f: {"weight": 2.0, "color": "#17243a"},
        tooltip=GeoJsonTooltip(fields=["_name", "_value", "_year", "_source"],
                               aliases=["Country", indicator.short, "Year", "Source"], sticky=True, localize=False),
        popup=GeoJsonPopup(fields=["_popup"], labels=False, localize=False, class_name="gde-country-popup"),
        smooth_factor=0.7,
    ).add_to(map_obj)

    # Small states without an outline still get a visible, clickable point.
    for iso, (lat, lon) in load_country_label_points().items():
        if iso in polygon_codes or iso not in name_by_code:
            continue
        item = layer.values.get(iso)
        name, rows, tip = describe(iso)
        folium.CircleMarker(
            location=(lat, lon), radius=5 if item else 3.5,
            color="#ffffff" if item else "#9aa3b1", weight=2 if item else 1.2,
            fill=True, fill_color=classifier.color(item.value if item else None), fill_opacity=0.95 if item else 0.5,
            tooltip=f"{html.escape(name)} · {html.escape(tip['value'])} · {html.escape(tip['year'])}",
            popup=folium.Popup(_popup_html(name, rows, ("COUNTRY", iso)), max_width=320),
        ).add_to(map_obj)

    if flows is not None and not flows.empty:
        locations = {**load_country_label_points(), **country_locations(boundary_geojson)}
        add_flow_arrows(map_obj, flows, locations, selected_name=name_by_code.get(flows.iso3, flows.iso3))
    _add_legend(map_obj, indicator, classifier, layer)
    Fullscreen(position="topright", title="Expand map", title_cancel="Exit fullscreen").add_to(map_obj)
    _add_map_styles(map_obj)
    _add_atlas_styles(map_obj)
    return map_obj, classifier


def _add_legend(map_obj: folium.Map, indicator: Indicator, classifier: Classifier, layer: IndicatorLayer) -> None:
    swatches = "".join(
        f"<div class='atlas-legend-row'><span style='background:{b.color}'></span>{html.escape(b.label)}</div>"
        for b in classifier.bins
    )
    swatches += (f"<div class='atlas-legend-row'><span style='background:{NO_DATA};border:1px dashed {NO_DATA_OUTLINE}'>"
                 "</span>No data (not zero)</div>")
    year_note = f"Latest year {layer.latest_year} · median {layer.median_year}" if layer.values else "No values loaded"
    legend_html = (
        f"<div class='atlas-legend'><div class='atlas-legend-title'>{html.escape(indicator.short)}</div>"
        f"{swatches}<div class='atlas-legend-note'>{html.escape(year_note)}</div></div>"
    )
    control = MacroElement()
    control._template = Template(
        """
        {% macro script(this, kwargs) %}
        (function(){
          var legend = L.control({position: 'bottomleft'});
          legend.onAdd = function() {
            var div = L.DomUtil.create('div', '');
            div.innerHTML = {{ this.legend_json }};
            L.DomEvent.disableClickPropagation(div);
            return div;
          };
          legend.addTo({{ this._parent.get_name() }});
        })();
        {% endmacro %}
        """
    )
    import json

    control.legend_json = json.dumps(legend_html)
    map_obj.add_child(control)


def _add_atlas_styles(map_obj: folium.Map) -> None:
    map_obj.get_root().html.add_child(Element("""
    <style>
      .atlas-legend{background:#fff;border:1px solid #dfe5ec;border-radius:9px;padding:9px 11px;
        font:11px/1.35 'DM Sans',system-ui,sans-serif;color:#22344d;box-shadow:0 2px 10px rgba(25,43,66,.08);min-width:150px}
      .atlas-legend-title{font-weight:800;font-size:11px;margin-bottom:6px;color:#17243a}
      .atlas-legend-row{display:flex;align-items:center;gap:7px;margin:2px 0;color:#3d4b60}
      .atlas-legend-row span{width:14px;height:10px;border-radius:3px;display:inline-block;border:1px solid rgba(0,0,0,.06)}
      .atlas-legend-note{margin-top:6px;color:#7b8798;font-size:10px}
      .gde-popup-row{display:flex;justify-content:space-between;gap:12px;font:12px 'DM Sans',sans-serif;margin:3px 0}
      .gde-popup-row span{color:#738096}
    </style>
    """))
