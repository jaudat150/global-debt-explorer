"""Interactive, readable debtor-to-creditor relationship map.

Country names and relationship details are shown directly in map tooltips and
popups. A hidden action token travels with each popup so Streamlit can update
its own widgets on the next rerun without mutating an instantiated widget key.
"""

from __future__ import annotations

import html
import json
import math
import os
import re
from urllib.parse import quote
from typing import Any

import folium
import pycountry
from branca.element import Element, MacroElement, Template
from folium.features import GeoJsonPopup, GeoJsonTooltip
from folium.plugins import Fullscreen
from shapely.geometry import shape

from .map_geometry import color_for_creditor
from .processor import TIER_LABELS, load_country_label_points

# Natural Earth uses its own codes for a few territories. Without this mapping
# Palestine (PSX), South Sudan (SDS), Kosovo (KOS) and Western Sahara (SAH)
# never match World Bank / IMF / ISO data and silently vanish from the map.
NATURAL_EARTH_CODE_ALIASES: dict[str, str] = {
    "PSX": "PSE",
    "SDS": "SSD",
    "KOS": "XKX",
    "SAH": "ESH",
}

PROFILE_TIER_COLORS: dict[int | None, str] = {1: "#52749e", 2: "#16877b", 3: "#c18a38", None: "#9aa3b1"}


def iso3_from_properties(properties: dict[str, Any]) -> str:
    """Return the ISO3 / World Bank code for a Natural Earth feature."""
    for key in ("ADM0_A3", "ISO_A3", "ISO_A3_EH", "SOV_A3", "GU_A3"):
        value = str(properties.get(key, "")).upper()
        if len(value) == 3 and value.isalpha() and value != "-99":
            return NATURAL_EARTH_CODE_ALIASES.get(value, value)
    return ""


def _profile_rows(profile: dict[str, Any]) -> list[tuple[str, str]]:
    """Popup rows describing a national debt profile, including its tier and provenance."""
    available = bool(profile.get("data_available"))
    rows = [
        ("National debt indicator", str(profile.get("display_value") or "No verified data")),
        ("Metric definition", str(profile.get("metric_label") or "Not available")),
        ("Source tier", str(profile.get("tier_label") or TIER_LABELS.get(profile.get("fallback_tier"), "No verified source"))),
        ("Data source", str(profile.get("source_label") or profile.get("data_source") or "No source available")),
        ("Source date", str(profile.get("source_date_label") or "Not available")),
        ("Observation year", str(profile.get("observation_year") or "Not available")),
        ("Data status", "Available" if available else "Not available — not zero"),
    ]
    alternative = profile.get("alternative_estimate")
    if isinstance(alternative, dict) and alternative.get("display_value"):
        rows.append(("Other estimate", f"{alternative['display_value']} · {alternative.get('source_label') or 'curated source'}"))
    return rows


def country_locations(boundary_geojson: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Return ISO-3 -> (latitude, longitude) representative points."""
    locations: dict[str, tuple[float, float]] = {}
    for feature in boundary_geojson.get("features", []):
        properties = feature.get("properties") or {}
        iso3 = iso3_from_properties(properties)
        geometry_payload = feature.get("geometry")
        if not iso3 or not geometry_payload:
            continue
        try:
            point = shape(geometry_payload).representative_point()
            if not point.is_empty and math.isfinite(point.x) and math.isfinite(point.y):
                locations[iso3] = (float(point.y), float(point.x))
        except Exception:
            continue
    return locations


def flag_emoji(iso3: str) -> str:
    """Convert an ISO-3 country code to a flag emoji when it is a country."""
    country = pycountry.countries.get(alpha_3=str(iso3).upper())
    if country is None:
        return "🌐"
    iso2 = country.alpha_2.upper()
    return "".join(chr(127397 + ord(char)) for char in iso2)


def _country_name(iso3: str, fallback: str = "") -> str:
    country = pycountry.countries.get(alpha_3=str(iso3).upper())
    return fallback or (country.name if country else (iso3 or "Unknown country"))


def curved_connection(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    bend: float = 0.14,
    points: int = 42,
) -> list[tuple[float, float]]:
    """Create a quadratic Bézier curve between (lat, lon) endpoints.

    The curve stays inside one world copy (it never crosses the antimeridian):
    a Canada → China link arcs over the Atlantic instead of wrapping into a
    line drawn across the whole map.
    """
    lat1, lon1 = start
    lat2, lon2 = end
    x1, x2 = lon1, lon2
    y1, y2 = lat1, lat2
    dx, dy = x2 - x1, y2 - y1
    distance = math.hypot(dx, dy)
    if distance == 0:
        return [start, end]
    offset = min(15.0, max(1.2, distance * bend))
    sign = 1.0 if (int(abs(x1 + y1 + x2 + y2)) % 2 == 0) else -1.0
    control_x = (x1 + x2) / 2.0 - (dy / distance) * offset * sign
    control_y = (y1 + y2) / 2.0 + (dx / distance) * offset * sign
    result: list[tuple[float, float]] = []
    for index in range(points + 1):
        t = index / points
        one_minus = 1.0 - t
        x = one_minus * one_minus * x1 + 2.0 * one_minus * t * control_x + t * t * x2
        y = one_minus * one_minus * y1 + 2.0 * one_minus * t * control_y + t * t * y2
        result.append((y, max(-180.0, min(180.0, x))))
    return result


def short_money(value: float) -> str:
    """Compact US$ label for map pills: US$742M, US$5.4B, US$22B."""
    absolute = abs(value)
    if absolute >= 1e9:
        return f"US${value / 1e9:,.{0 if absolute >= 1e10 else 1}f}B"
    if absolute >= 1e6:
        return f"US${value / 1e6:,.0f}M"
    if absolute >= 1e3:
        return f"US${value / 1e3:,.0f}K"
    return f"US${value:,.0f}"


class PillPlacer:
    """Greedy label de-cluttering for name pills at the default world zoom.

    A pill occupies roughly 148×42 px to the right of its point; at zoom 2 that
    is about 50° of longitude by 13° of Mercator latitude. A pill that would
    overlap one already placed is refused, and the caller draws a dot instead.
    """

    def __init__(self, lon_span: float = 48.0, lat_span: float = 12.0) -> None:
        self.lon_span, self.lat_span = lon_span, lat_span
        self.placed: list[tuple[float, float]] = []

    @staticmethod
    def _y(lat: float) -> float:
        lat = max(-85.0, min(85.0, lat))
        return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))

    def place(self, position: tuple[float, float], *, force: bool = False) -> bool:
        y, x = self._y(position[0]), position[1]
        clash = any(abs(y - py) < self.lat_span and -self.lon_span < x - px < self.lon_span for py, px in self.placed)
        if clash and not force:
            return False
        self.placed.append((y, x))
        return True


def screen_angle(start: tuple[float, float], end: tuple[float, float]) -> float:
    """Bearing in degrees on the Web-Mercator screen (0 = east, clockwise positive)."""
    def mercator_y(lat: float) -> float:
        lat = max(-85.0, min(85.0, lat))
        return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))

    dx = ((end[1] - start[1] + 180.0) % 360.0) - 180.0
    dy = -(mercator_y(end[0]) - mercator_y(start[0]))  # screen y grows downwards
    return math.degrees(math.atan2(dy, dx))


def add_arrowhead(
    map_obj: folium.Map,
    curve: list[tuple[float, float]],
    color: str,
    *,
    at: float = 0.62,
    size: int = 15,
    tooltip: str | None = None,
) -> None:
    """Place a rotated triangle on ``curve`` pointing in its direction of travel.

    The head sits part-way along the curve (not at the end) so arrows converging
    on one country do not pile up on top of each other.
    """
    if len(curve) < 3:
        return
    index = min(len(curve) - 2, max(1, round((len(curve) - 1) * at)))
    angle = screen_angle(curve[index - 1], curve[index + 1])
    half = size / 2
    svg = (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'style="transform:rotate({angle:.1f}deg);transform-origin:50% 50%;overflow:visible">'
        f'<polygon points="{size},{half} 0,0 {size * 0.28:.1f},{half} 0,{size}" fill="{color}" '
        f'stroke="#ffffff" stroke-width="1.6" stroke-linejoin="round"/></svg>'
    )
    folium.Marker(
        location=curve[index],
        icon=folium.DivIcon(html=svg, icon_size=(size, size), icon_anchor=(half, half), class_name="gde-arrowhead"),
        tooltip=tooltip,
        z_index_offset=600,
    ).add_to(map_obj)


def extract_map_action(popup_value: Any) -> tuple[str, str] | None:
    """Read the non-visible action token embedded in a readable map popup."""
    if not isinstance(popup_value, str):
        return None
    match = re.search(r"GDE_ACTION\|(?P<kind>DEBTOR|CREDITOR|COUNTRY)\|(?P<value>[^<\s]+)", popup_value)
    if match:
        return match.group("kind"), match.group("value")
    legacy = re.fullmatch(r"(?P<kind>DEBTOR|CREDITOR|COUNTRY)\|(?P<value>[^|]+)", popup_value.strip())
    if legacy:
        return legacy.group("kind"), legacy.group("value")
    return None


def _action_token(kind: str, value: str) -> str:
    safe_kind = kind if kind in {"DEBTOR", "CREDITOR", "COUNTRY"} else "COUNTRY"
    safe_value = html.escape(str(value), quote=True)
    return (
        f'<span class="gde-action-token" data-gde-action="{safe_kind}|{safe_value}">'
        f"GDE_ACTION|{safe_kind}|{safe_value}</span>"
    )


def _popup_html(title: str, rows: list[tuple[str, str]], action: tuple[str, str] | None = None) -> str:
    safe_title = html.escape(title)
    row_html = "".join(
        f"<div class='gde-popup-row'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>"
        for label, value in rows
    )
    token = _action_token(*action) if action else ""
    return (
        "<div class='gde-country-popup'>"
        f"<div class='gde-popup-title'>{safe_title}</div>"
        f"{row_html}{token}</div>"
    )


def _country_name_icon(name: str, *, selected: bool = False, role: str = "Creditor", color: str = "#315fce") -> folium.DivIcon:
    safe_name = html.escape(name)
    safe_role = html.escape(role.upper())
    klass = "gde-country-pin selected" if selected else "gde-country-pin"
    icon_html = (
        f'<div class="{klass}" style="--pin-color:{color}">'
        f'<span class="gde-pin-dot"></span>'
        f'<span class="gde-pin-copy"><span class="gde-pin-role">{safe_role}</span>'
        f'<span class="gde-pin-name">{safe_name}</span></span></div>'
    )
    return folium.DivIcon(
        html=icon_html,
        class_name="gde-country-pin-wrapper",
        icon_size=(148, 42),
        icon_anchor=(8, 21),
    )


def _floating_card_html(
    debtor_name: str,
    debtor_iso3: str,
    creditor_name: str,
    creditor_iso3: str,
    amount_display: str,
    share_pct: float,
    total_display: str,
    creditor_count: int,
    year: int,
    national_profile: dict[str, Any] | None = None,
) -> str:
    debtor_flag = flag_emoji(debtor_iso3)
    national_html = ""
    if national_profile:
        national_value = html.escape(str(national_profile.get("display_value") or "No verified data"))
        national_meta = html.escape(
            f"{national_profile.get('metric_short') or national_profile.get('metric_label') or ''} · "
            f"{national_profile.get('tier_label') or 'No verified source'}"
            + (f" · {national_profile.get('observation_year')}" if national_profile.get("observation_year") else "")
        )
        national_html = (
            "<div class='debt-card-hint' style='margin-top:8px;border-top:1px solid #e8edf3;padding-top:8px'>"
            f"National debt profile: <strong>{national_value}</strong><br>{national_meta}</div>"
        )
    creditor_flag = flag_emoji(creditor_iso3) if creditor_iso3 else "🌐"
    debtor_safe = html.escape(debtor_name)
    creditor_safe = html.escape(creditor_name)
    return f"""
    <div class="debt-float-card">
      <div class="debt-card-topline"><span>SELECTED DEBT RELATIONSHIP</span><span>{year}</span></div>
      <div class="debt-card-headline">{debtor_safe} owes {creditor_safe}</div>
      <div class="debt-card-route">
        <span class="debt-card-country"><span class="debt-card-flag">{debtor_flag}</span>{debtor_safe}</span>
        <span class="debt-card-arrow">→</span>
        <span class="debt-card-country"><span class="debt-card-flag">{creditor_flag}</span>{creditor_safe}</span>
      </div>
      <div class="debt-card-primary-label">REPORTED BILATERAL DEBT STOCK · CURRENT US$</div>
      <div class="debt-card-primary-value">{amount_display}</div>
      <div class="debt-card-grid">
        <div><span>Share of debtor's reported total</span><strong>{share_pct:.1f}%</strong></div>
        <div><span>Bilateral IDS total (not total debt)</span><strong>{total_display}</strong></div>
        <div><span>Creditor areas reported</span><strong>{creditor_count:,}</strong></div>
        <div><span>Observation year</span><strong>{year}</strong></div>
      </div>
      {national_html}
      <div class="debt-card-hint">Select another creditor marker or use the relationship filter to compare lenders.</div>
    </div>
    """


def build_relationship_map(
    *,
    boundary_geojson: dict[str, Any],
    debtor_rows: list[dict[str, Any]],
    creditor_rows: list[dict[str, Any]],
    debtor_iso3: str,
    debtor_name: str,
    selected_creditor_id: str | None,
    total_amount_usd: float,
    year: int,
    max_connections: int = 8,
    creditor_totals_rows: list[dict[str, Any]] | None = None,
    country_profiles: dict[str, dict[str, Any]] | None = None,
    lending_rows: list[dict[str, Any]] | None = None,
    creditor_colors: dict[str, str] | None = None,
) -> tuple[folium.Map, dict[str, int]]:
    """Build a map with readable country popups and clickable debt relationships.

    ``debtor_rows`` contains country-level reported debt totals for the year.
    ``creditor_rows`` contains the selected debtor's complete creditor breakdown.
    ``creditor_totals_rows`` contains aggregate creditor claims for all debtors.
    ``lending_rows`` lists borrowers that owe the selected country (``debtor_iso3``,
    ``debtor_name``, ``amount_usd``); they are drawn as teal lender → borrower arrows.
    ``country_profiles`` contains an independent national indicator profile for
    every ISO3; values retain their metric type and source so USD stocks are not
    visually conflated with IMF percent-of-GDP ratios or local estimates.
    """
    country_profiles = {str(code).upper(): dict(profile) for code, profile in (country_profiles or {}).items()}
    # Polygon representative points win; Natural Earth 1:10m label points fill
    # in small states (Singapore, Maldives, Bahrain …) absent from the 1:110m
    # outlines, so every real country has a real coordinate.
    polygon_locations = country_locations(boundary_geojson)
    locations: dict[str, tuple[float, float]] = {**load_country_label_points(), **polygon_locations}
    for code, profile in country_profiles.items():
        position = locations.get(code)
        coordinates = profile.get("coordinates")
        if position is None and isinstance(coordinates, dict):
            position = (float(coordinates["latitude"]), float(coordinates["longitude"]))
            locations[code] = position
        profile["coordinates"] = ({"latitude": position[0], "longitude": position[1]} if position else None)
        profile["map_available"] = position is not None
        profile["has_boundary"] = code in polygon_locations
    selected_iso3 = str(debtor_iso3).upper()
    selected_position = locations.get(selected_iso3)
    source_position = selected_position or (15.0, 0.0)  # camera center only; no marker without real geometry

    ranked = sorted(creditor_rows, key=lambda row: float(row.get("amount_usd", 0) or 0), reverse=True)
    selected_row = next(
        (row for row in ranked if str(row.get("creditor_id", "")) == str(selected_creditor_id)),
        None,
    )
    if selected_row is None and ranked:
        selected_row = ranked[0]

    debtor_by_iso = {
        str(row.get("debtor_iso3", "")).upper(): row
        for row in debtor_rows
        if str(row.get("debtor_iso3", "")).upper()
    }
    global_creditor_by_iso: dict[str, dict[str, Any]] = {}
    for row in creditor_totals_rows or []:
        iso = str(row.get("creditor_iso3", "")).upper()
        if not iso:
            continue
        current = global_creditor_by_iso.setdefault(
            iso,
            {"creditor_name": row.get("creditor_name", iso), "amount_usd": 0.0},
        )
        current["amount_usd"] += float(row.get("amount_usd", 0) or 0)

    selected_relation_by_iso: dict[str, dict[str, Any]] = {}
    for row in ranked:
        iso = str(row.get("creditor_iso3", "")).upper()
        if iso and (iso not in selected_relation_by_iso or str(row.get("creditor_id", "")) == str(selected_creditor_id)):
            selected_relation_by_iso[iso] = row

    # Show the largest map-able sovereign creditors; all creditor areas remain in
    # the selected-country table and downloadable dataset below the map.
    all_geographic_rows = [
        row for row in ranked
        if str(row.get("creditor_iso3", "")).upper() in locations
        and str(row.get("creditor_iso3", "")).upper() != selected_iso3
        and float(row.get("amount_usd", 0) or 0) > 0
    ]
    geographic_rows = all_geographic_rows[:max_connections] if selected_position else []
    selected_geographic = next(
        (row for row in all_geographic_rows if selected_row and str(row.get("creditor_id", "")) == str(selected_row.get("creditor_id", ""))),
        None,
    )
    if selected_position is not None and selected_geographic is not None and all(
        str(row.get("creditor_id", "")) != str(selected_geographic.get("creditor_id", ""))
        for row in geographic_rows
    ):
        if len(geographic_rows) >= max_connections:
            geographic_rows[-1] = selected_geographic
        else:
            geographic_rows.append(selected_geographic)

    map_obj = folium.Map(
        location=source_position,
        zoom_start=2,
        min_zoom=1,
        max_zoom=8,
        tiles=None,
        control_scale=False,
        prefer_canvas=True,
        zoom_control=True,
        world_copy_jump=True,
    )
    _add_basemap(map_obj)

    prepared_features: list[dict[str, Any]] = []
    for feature in boundary_geojson.get("features", []):
        props = dict(feature.get("properties") or {})
        iso = iso3_from_properties(props)
        name = str(props.get("ADMIN") or props.get("NAME_EN") or props.get("NAME") or props.get("SOVEREIGNT") or _country_name(iso))
        debtor_record = debtor_by_iso.get(iso)
        creditor_record = global_creditor_by_iso.get(iso)
        selected_relation = selected_relation_by_iso.get(iso)
        national_profile = country_profiles.get(iso)
        role_labels: list[str] = []
        popup_rows: list[tuple[str, str]] = []
        tooltip_rows: dict[str, str] = {}
        action: tuple[str, str] | None = ("COUNTRY", iso) if iso in locations else None

        if debtor_record:
            debtor_amount = float(debtor_record.get("amount_usd", 0) or 0)
            role_labels.append("Reporting debtor")
            popup_rows.append(("Reported bilateral debt owed", _format_money(debtor_amount)))
            tooltip_rows["debtor"] = _format_money(debtor_amount)
            action = ("DEBTOR", iso)
        if creditor_record:
            creditor_amount = float(creditor_record.get("amount_usd", 0) or 0)
            role_labels.append("Creditor in this dataset")
            popup_rows.append(("Reported creditor claims", _format_money(creditor_amount)))
            tooltip_rows["creditor"] = _format_money(creditor_amount)
        if selected_relation:
            relation_amount = float(selected_relation.get("amount_usd", 0) or 0)
            relation_share = relation_amount / total_amount_usd * 100.0 if total_amount_usd > 0 else 0.0
            relation_text = f"{debtor_name} → {selected_relation.get('creditor_name', name)} · {_format_money(relation_amount)} · {relation_share:.1f}%"
            popup_rows.append(("Selected debtor → this creditor", relation_text))
        if national_profile:
            profile_available = bool(national_profile.get("data_available"))
            profile_value = str(national_profile.get("display_value") or "No verified data")
            profile_metric = str(national_profile.get("metric_label") or "National debt metric unavailable")
            profile_source = str(national_profile.get("source_label") or national_profile.get("data_source") or "No source available")
            tier = national_profile.get("fallback_tier")
            role_labels.append(f"National profile · Tier {tier}" if profile_available and tier else "No verified national indicator")
            popup_rows.extend(_profile_rows(national_profile))
            tooltip_rows["national_metric"] = f"{profile_value} · {profile_metric}"
            tooltip_rows["national_source"] = profile_source
        if not popup_rows:
            popup_rows.append(("Data coverage", "No reported bilateral or national debt observation for this year"))
        role_text = " · ".join(role_labels) if role_labels else "No observations for selected year"

        props["_gde_iso3"] = iso
        props["_gde_name"] = name
        props["_gde_role"] = role_text
        props["_gde_debtor_total"] = tooltip_rows.get("debtor", "No debtor observation")
        props["_gde_creditor_total"] = tooltip_rows.get("creditor", "No creditor observation")
        props["_gde_selected_relationship"] = (
            f"{debtor_name} → {selected_relation.get('creditor_name')} · {_format_money(float(selected_relation.get('amount_usd', 0) or 0))}"
            if selected_relation else "No selected relationship"
        )
        props["_gde_country_metric"] = tooltip_rows.get("national_metric", "No verified national profile")
        props["_gde_country_source"] = tooltip_rows.get("national_source", "No source available")
        props["_gde_profile_tier"] = int(national_profile.get("fallback_tier") or 0) if national_profile else 0
        point = locations.get(iso)
        props["_gde_latitude"] = point[0] if point else None
        props["_gde_longitude"] = point[1] if point else None
        props["_gde_popup_html"] = _popup_html(name, popup_rows, action)
        prepared_features.append({"type": "Feature", "properties": props, "geometry": feature.get("geometry")})

    world_layer = {"type": "FeatureCollection", "features": prepared_features}
    geojson = folium.GeoJson(
        world_layer,
        name="Country information",
        style_function=lambda feature: _country_feature_style(feature, selected_iso3),
        highlight_function=lambda feature: {
            "fillColor": "#d8e1ee", "fillOpacity": 0.92, "color": "#ffffff", "weight": 1.8,
        },
        tooltip=GeoJsonTooltip(
            fields=["_gde_name", "_gde_role", "_gde_debtor_total", "_gde_creditor_total", "_gde_selected_relationship", "_gde_country_metric", "_gde_country_source"],
            aliases=["Country", "Data role", "As debtor", "As creditor", "Selected relationship", "National debt metric", "National data source"],
            sticky=False,
            localize=False,
        ),
        popup=GeoJsonPopup(fields=["_gde_popup_html"], labels=False, localize=False, class_name="gde-country-popup"),
        smooth_factor=0.7,
    ).add_to(map_obj)

    # Debtor-country markers: click to switch the selected borrower. Their
    # tooltips/popups use full names and debt values, never ISO-only labels.
    debtor_set = {str(row.get("debtor_iso3", "")).upper() for row in debtor_rows}
    for row in debtor_rows:
        iso = str(row.get("debtor_iso3", "")).upper()
        position = locations.get(iso)
        if not position or iso == selected_iso3:
            continue
        name = str(row.get("debtor_name", _country_name(iso)))
        amount = float(row.get("amount_usd", 0) or 0)
        debtor_profile = country_profiles.get(iso)
        rows = [("Role", "Reporting debtor"), ("Bilateral IDS debt owed", _format_money(amount)), ("Bilateral observation year", str(year))]
        tooltip_text = f"{html.escape(name)} · Bilateral IDS {_format_money(amount)}"
        if debtor_profile:
            rows.extend(_profile_rows(debtor_profile))
            if debtor_profile.get("data_available"):
                tooltip_text += f" · National: {html.escape(str(debtor_profile.get('display_value')))}"
        content = _popup_html(name, rows, ("DEBTOR", iso))
        folium.CircleMarker(
            location=position,
            radius=4.0,
            color="#ffffff",
            weight=1.2,
            fill=True,
            fill_color="#5c7599",
            fill_opacity=0.95,
            tooltip=f"{tooltip_text} · Click to view borrower profile",
            popup=folium.Popup(content, max_width=300),
        ).add_to(map_obj)

    # Draw curved lines and named creditor markers. Every line/popup names both
    # sides of the relationship and reports its debt amount and share.
    pills = PillPlacer()
    if selected_position is not None:
        pills.place(source_position, force=True)
    for index, row in enumerate(geographic_rows):
        creditor_iso = str(row.get("creditor_iso3", "")).upper()
        end_position = locations.get(creditor_iso)
        if not end_position:
            continue
        creditor_id = str(row.get("creditor_id", creditor_iso))
        creditor_name = str(row.get("creditor_name", _country_name(creditor_iso)))
        amount = float(row.get("amount_usd", 0) or 0)
        share = amount / total_amount_usd * 100.0 if total_amount_usd > 0 else 0.0
        relation_label = f"{debtor_name} → {creditor_name}"
        # Lender → borrower: the arrow shows the direction the money was lent.
        curve = curved_connection(end_position, source_position, bend=0.13 + (index % 2) * 0.035)
        color = (creditor_colors or {}).get(creditor_id) or color_for_creditor(creditor_id)
        popup_content = _popup_html(
            relation_label,
            [("Debtor", debtor_name), ("Creditor", creditor_name), ("Reported debt stock", _format_money(amount)), ("Share of debtor's reported total", f"{share:.1f}%"), ("Observation year", str(year))],
            ("CREDITOR", creditor_id),
        )
        folium.PolyLine(
            curve, color="#ffffff", weight=5.0, opacity=0.86, smooth_factor=1.0,
            tooltip=f"{debtor_name} owes {creditor_name}",
        ).add_to(map_obj)
        folium.PolyLine(
            curve,
            color=color,
            weight=1.9 + min(2.5, share / 16.0),
            opacity=0.92,
            line_cap="round",
            smooth_factor=1.0,
            tooltip=f"{debtor_name} owes {creditor_name} · {_format_money(amount)} · {share:.1f}% of reported bilateral debt",
            popup=folium.Popup(popup_content, max_width=320),
        ).add_to(map_obj)
        add_arrowhead(map_obj, curve, color, size=13 + min(6, int(share / 10)),
                      tooltip=f"{creditor_name} lent {debtor_name} · {_format_money(amount)} outstanding")
        selected = bool(selected_row and str(selected_row.get("creditor_id", "")) == creditor_id)
        pill_tooltip = f"{creditor_name} lent {debtor_name} · {_format_money(amount)} · {share:.1f}% of its bilateral debt"
        if pills.place(end_position, force=selected):
            folium.Marker(
                location=end_position,
                icon=_country_name_icon(creditor_name, selected=selected, role=f"Lent {short_money(amount)} · {share:.0f}%", color=color),
                tooltip=pill_tooltip,
                popup=folium.Popup(popup_content, max_width=320),
                z_index_offset=650,
            ).add_to(map_obj)
        else:
            folium.CircleMarker(
                location=end_position, radius=5, color="#ffffff", weight=2, fill=True, fill_color=color, fill_opacity=1,
                tooltip=pill_tooltip, popup=folium.Popup(popup_content, max_width=320),
            ).add_to(map_obj)

    # Country-profile-only markers make countries outside IDS debtor coverage
    # discoverable (for example Canada, Saudi Arabia, or configured fallbacks).
    # Borrowers that owe the selected country (it acts as a lender).
    lent_drawn = 0
    if lending_rows and selected_position is not None:
        from src.debt_flows import CountryFlows, Flow, add_flow_arrows

        lent_total = sum(float(row.get("amount_usd", 0) or 0) for row in lending_rows)
        lent = [
            Flow(lender_iso3=selected_iso3, lender_name=debtor_name, borrower_iso3=str(row.get("debtor_iso3", "")).upper(),
                 borrower_name=str(row.get("debtor_name") or row.get("debtor_iso3")), amount_usd=float(row.get("amount_usd", 0) or 0),
                 share_pct=float(row.get("amount_usd", 0) or 0) / lent_total * 100 if lent_total else 0.0, year=int(year))
            for row in sorted(lending_rows, key=lambda r: float(r.get("amount_usd", 0) or 0), reverse=True)
            if float(row.get("amount_usd", 0) or 0) > 0
        ]
        lent_drawn = add_flow_arrows(map_obj, CountryFlows(selected_iso3, int(year), [], lent), locations,
                                     include_borrowed=False, selected_name=debtor_name, pills=pills,
                                     legend_position="bottomleft")["lent"]  # top-right holds the relationship card

    # Every resolved country gets a point: coloured by tier when data exists,
    # a hollow grey ring ("not zero") when every tier came up empty.
    profile_markers = 0
    unavailable_markers = 0
    for iso, profile in country_profiles.items():
        if iso == selected_iso3 or iso in debtor_by_iso or iso in selected_relation_by_iso:
            continue
        position = locations.get(iso)
        if not position:
            continue
        country_label = str(profile.get("country_name") or _country_name(iso))
        available = bool(profile.get("data_available"))
        tier = profile.get("fallback_tier") if available else None
        profile_popup = _popup_html(country_label, _profile_rows(profile), ("COUNTRY", iso))
        tier_text = str(profile.get("tier_label") or TIER_LABELS.get(tier, ""))
        folium.CircleMarker(
            location=position,
            radius=4.8 if available else 3.4,
            color="#ffffff" if available else PROFILE_TIER_COLORS[None],
            weight=1.4,
            fill=available,
            fill_color=PROFILE_TIER_COLORS.get(tier, PROFILE_TIER_COLORS[None]),
            fill_opacity=0.95 if available else 0.0,
            tooltip=(
                f"{html.escape(country_label)} · {html.escape(str(profile.get('display_value')))} · {html.escape(tier_text)}"
                if available else f"{html.escape(country_label)} · No verified data (not zero)"
            ),
            popup=folium.Popup(profile_popup, max_width=360),
            z_index_offset=350 if available else 200,
        ).add_to(map_obj)
        if available:
            profile_markers += 1
        else:
            unavailable_markers += 1

    # Selected debtor shown as a named origin pill. A country without bilateral
    # IDS rows still gets its national profile shown instead of a misleading zero.
    selected_debtor_amount = float(debtor_by_iso.get(selected_iso3, {}).get("amount_usd", total_amount_usd) or total_amount_usd)
    selected_profile = country_profiles.get(selected_iso3, {})
    debtor_popup_rows: list[tuple[str, str]] = [
        ("Role", "Selected country"),
        ("Reported bilateral debt owed", _format_money(total_amount_usd) if ranked else "No bilateral IDS observation"),
        ("Creditor areas reported", f"{len(ranked):,}"),
        ("Observation year", str(year)),
    ]
    if selected_profile:
        debtor_popup_rows.extend(_profile_rows(selected_profile))
    debtor_popup = _popup_html(
        debtor_name,
        debtor_popup_rows,
        ("COUNTRY", selected_iso3),
    )
    if selected_position is not None:
        folium.Marker(
            location=source_position,
            icon=_country_name_icon(debtor_name, selected=True, role="Selected country", color="#315fce"),
            tooltip=(f"Selected country · {debtor_name} · " + (f"{_format_money(total_amount_usd)} bilateral IDS debt" if ranked else str(selected_profile.get("display_value", "No verified national data")))),
            popup=folium.Popup(debtor_popup, max_width=320),
            z_index_offset=1000,
        ).add_to(map_obj)

    visible_positions = ([source_position] if selected_position is not None else []) + [
        locations[str(row.get("creditor_iso3", "")).upper()]
        for row in geographic_rows
        if str(row.get("creditor_iso3", "")).upper() in locations
    ] + [
        locations[str(row.get("debtor_iso3", "")).upper()]
        for row in sorted(lending_rows or [], key=lambda r: float(r.get("amount_usd", 0) or 0), reverse=True)[:8]
        if str(row.get("debtor_iso3", "")).upper() in locations and selected_position is not None
    ]
    if len(visible_positions) > 1:
        latitudes = [point[0] for point in visible_positions]
        longitudes = [point[1] for point in visible_positions]
        lat_pad = max(8.0, (max(latitudes) - min(latitudes)) * 0.18)
        lon_pad = max(8.0, (max(longitudes) - min(longitudes)) * 0.12)
        map_obj.fit_bounds(
            [[max(-85, min(latitudes) - lat_pad), max(-180, min(longitudes) - lon_pad)],
             [min(85, max(latitudes) + lat_pad), min(180, max(longitudes) + lon_pad)]],
            padding=(30, 30),
        )

    if selected_row is not None:
        creditor_iso = str(selected_row.get("creditor_iso3", "")).upper()
        card = _floating_card_html(
            debtor_name=debtor_name,
            debtor_iso3=selected_iso3,
            creditor_name=str(selected_row.get("creditor_name", "Creditor area")),
            creditor_iso3=creditor_iso,
            amount_display=_format_money(float(selected_row.get("amount_usd", 0) or 0)),
            share_pct=(float(selected_row.get("amount_usd", 0) or 0) / total_amount_usd * 100.0) if total_amount_usd > 0 else 0,
            total_display=_format_money(total_amount_usd),
            creditor_count=len(ranked),
            year=int(year),
            national_profile=selected_profile or None,
        )
        _add_floating_card(map_obj, card)
    else:
        profile = selected_profile or {}
        card = _floating_country_profile_card_html(debtor_name, selected_iso3, profile, int(year), total_amount_usd, len(ranked))
        _add_floating_card(map_obj, card)

    Fullscreen(position="topright", title="Expand map", title_cancel="Exit fullscreen").add_to(map_obj)
    _add_map_styles(map_obj)
    return map_obj, {
        "reporting_debtors_on_map": len(debtor_set.intersection(locations)),
        "geographic_creditor_connections": len(geographic_rows),
        "lending_connections": lent_drawn,
        "creditor_areas_total": len(ranked),
        "countries_with_debtor_data": len(debtor_by_iso),
        "countries_with_creditor_data": len(global_creditor_by_iso),
        "country_profile_markers": profile_markers,
        "unavailable_profile_markers": unavailable_markers,
        "country_profiles_available": sum(1 for item in country_profiles.values() if item.get("data_available")),
        "country_profiles_without_geometry": sum(1 for item in country_profiles.values() if item.get("data_available") and not item.get("map_available")),
    }


def _country_feature_style(feature: dict[str, Any], selected_iso3: str) -> dict[str, Any]:
    """Keep profile tiers visually distinct without comparing unlike units."""
    props = feature.get("properties") or {}
    iso = str(props.get("_gde_iso3") or "").upper()
    tier = int(props.get("_gde_profile_tier") or 0)
    if iso == selected_iso3:
        fill, line, weight, opacity = "#dce7f7", "#527cc4", 1.8, 0.88
    else:
        fill = {1: "#e4edf8", 2: "#e2f1ed", 3: "#fff0d7", 0: "#e6e9ee"}.get(tier, "#e6e9ee")
        line, weight, opacity = "#ffffff", 1.05, 0.72
    return {"fillColor": fill, "fillOpacity": opacity, "color": line, "weight": weight, "opacity": 1.0}


def _floating_country_profile_card_html(
    country_name: str,
    iso3: str,
    profile: dict[str, Any],
    year: int,
    bilateral_total_usd: float,
    creditor_count: int,
) -> str:
    """Create the map's summary card when the selected country lacks bilateral rows."""
    safe_name = html.escape(country_name)
    metric = html.escape(str(profile.get("metric_label") or "No verified national debt metric"))
    display = html.escape(str(profile.get("display_value") or "No verified data"))
    source = html.escape(str(profile.get("source_label") or "No source available"))
    source_date = html.escape(str(profile.get("source_date_label") or "Source date unavailable"))
    observed = profile.get("observation_year") or "Not available"
    tier = profile.get("fallback_tier")
    tier_label = f"Tier {tier}" if tier else "No source"
    alternative = profile.get("alternative_estimate") if isinstance(profile.get("alternative_estimate"), dict) else None
    alternative_html = (
        f"<div class='debt-card-hint'>Other estimate: <strong>{html.escape(str(alternative.get('display_value')))}</strong>"
        f" · {html.escape(str(alternative.get('source_label') or ''))}</div>"
        if alternative and alternative.get("display_value") else ""
    )
    return f"""
    <div class="debt-float-card">
      <div class="debt-card-topline"><span>COUNTRY DEBT PROFILE</span><span>{year}</span></div>
      <div class="debt-card-headline">{safe_name} <span style="color:#315fce">· {html.escape(iso3)}</span></div>
      <div class="debt-card-primary-label">{metric}</div>
      <div class="debt-card-primary-value">{display}</div>
      <div class="debt-card-grid">
        <div><span>Source tier</span><strong>{tier_label}</strong></div>
        <div><span>Indicator year</span><strong>{observed}</strong></div>
        <div><span>Bilateral IDS total</span><strong>{_format_money(bilateral_total_usd) if creditor_count else 'Not reported'}</strong></div>
        <div><span>Creditor areas</span><strong>{creditor_count:,}</strong></div>
      </div>
      {alternative_html}
      <div class="debt-card-hint">Source: {source} · {source_date}. National indicators have different definitions and must not be compared as if they were the same metric.</div>
    </div>
    """


def _add_basemap(map_obj: folium.Map) -> str:
    """Add a keyless OpenStreetMap basemap with the required attribution."""
    provider = os.getenv("GDE_MAP_BASEMAP", "openstreetmap").strip().casefold()
    carto_key = os.getenv("CARTO_BASEMAP_API_KEY", "").strip()
    if provider == "carto" and carto_key:
        encoded_key = quote(carto_key, safe="")
        folium.TileLayer(
            tiles=("https://{s}.basemaps.cartocdn.com/light_all/" f"{{z}}/{{x}}/{{y}}{{r}}.png?key={encoded_key}"),
            attr=('&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'),
            name="CARTO Positron", subdomains="abcd", control=False, show=True, max_zoom=20,
        ).add_to(map_obj)
        return "carto"
    folium.TileLayer(
        tiles="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
        name="OpenStreetMap", control=False, show=True, max_zoom=19,
    ).add_to(map_obj)
    return "openstreetmap"


def _format_money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:,.1f}K"
    return f"${value:,.0f}"


def _add_floating_card(map_obj: folium.Map, card_html: str) -> None:
    control = MacroElement()
    control._template = Template(
        """
        {% macro script(this, kwargs) %}
        (function(){
          var debtCard = L.control({position: 'topright'});
          debtCard.onAdd = function(map) {
            var div = L.DomUtil.create('div', 'gde-floating-control');
            div.innerHTML = {{ this.card_html_json }};
            L.DomEvent.disableClickPropagation(div);
            L.DomEvent.disableScrollPropagation(div);
            return div;
          };
          debtCard.addTo({{ this._parent.get_name() }});
        })();
        {% endmacro %}
        """
    )
    control.card_html_json = json.dumps(card_html)
    map_obj.add_child(control)


def _add_map_styles(map_obj: folium.Map) -> None:
    map_obj.get_root().html.add_child(
        Element(
            """
            <style>
              .leaflet-container { background:#eef1f5; font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }
              .leaflet-tile-pane { filter:grayscale(.88) saturate(.35) brightness(1.08) contrast(.94); }
              .leaflet-control-zoom a { color:#24344a!important; border-color:#e5eaf0!important; }
              .leaflet-control-attribution { font-size:9px!important; background:rgba(255,255,255,.78)!important; }
              .gde-country-pin-wrapper { background:transparent; border:0; }
              .gde-country-pin { display:flex;align-items:center;gap:7px;width:max-content;max-width:165px;min-height:32px;padding:4px 10px 4px 7px;border:1px solid #dfe6ef;border-radius:8px;background:rgba(255,255,255,.97);box-shadow:0 2px 10px rgba(24,39,61,.17);white-space:nowrap; }
              .gde-country-pin.selected { border:2px solid var(--pin-color,#315fce);box-shadow:0 3px 13px rgba(49,95,206,.25); }
              .gde-pin-dot { display:inline-block;flex:0 0 9px;width:9px;height:9px;border-radius:50%;background:var(--pin-color,#315fce);box-shadow:0 0 0 3px color-mix(in srgb,var(--pin-color,#315fce) 15%,transparent); }
              .gde-pin-copy { display:flex;flex-direction:column;line-height:1.08; }
              .gde-pin-role { font-size:7px;font-weight:900;letter-spacing:.12em;color:#8491a3; }
              .gde-pin-name { font-size:10px;font-weight:850;color:#23344d;max-width:132px;overflow:hidden;text-overflow:ellipsis; }
              .gde-country-popup { min-width:220px;color:#24344a;font:12px Inter,system-ui,sans-serif;padding:2px; }
              .gde-popup-title { font-size:14px;font-weight:850;color:#192b43;margin:0 0 9px; }
              .gde-popup-row { display:flex;justify-content:space-between;gap:12px;border-top:1px solid #edf0f4;padding:6px 0; }
              .gde-popup-row span { color:#7d8a9c;font-size:10px; }
              .gde-popup-row strong { color:#2a3d56;font-size:10px;text-align:right;max-width:62%; }
              .gde-action-token { display:none!important;visibility:hidden!important; }
              .leaflet-popup-content-wrapper { border-radius:9px;box-shadow:0 6px 22px rgba(24,39,61,.18); }
              .leaflet-popup-content { margin:10px 12px; }
              .debt-float-card { width:304px; max-width:calc(100vw - 90px); background:rgba(255,255,255,.98); border:1px solid #e1e6ee; border-radius:12px; box-shadow:0 7px 24px rgba(24,39,61,.16); padding:15px 16px 13px; color:#18283e; white-space:normal; }
              .debt-card-topline { display:flex;justify-content:space-between;align-items:center;font-size:8px;font-weight:900;letter-spacing:.11em;color:#7a8798;margin-bottom:7px; }
              .debt-card-topline span:last-child { color:#315fce;background:#edf3ff;border-radius:4px;padding:3px 6px;letter-spacing:.04em; }
              .debt-card-headline { color:#25374f;font-size:13px;font-weight:900;margin-bottom:8px; }
              .debt-card-route { display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:12px; }
              .debt-card-country { display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:800;color:#24344a; }
              .debt-card-flag { font-size:15px;line-height:1; }
              .debt-card-arrow { color:#315fce;font-size:15px;font-weight:900; }
              .debt-card-primary-label { color:#7c899b;font-size:8px;font-weight:900;letter-spacing:.08em; }
              .debt-card-primary-value { color:#14253d;font-size:25px;font-weight:900;letter-spacing:-.04em;margin:3px 0 11px; }
              .debt-card-grid { display:grid;grid-template-columns:1fr 1fr;gap:9px 13px;border-top:1px solid #edf0f4;padding-top:10px; }
              .debt-card-grid span { display:block;color:#8793a3;font-size:8px;font-weight:700;margin-bottom:3px; }
              .debt-card-grid strong { display:block;color:#32445c;font-size:11px;font-weight:850; }
              .debt-card-hint { color:#8c98a8;font-size:8px;line-height:1.4;border-top:1px solid #edf0f4;margin-top:10px;padding-top:8px; }
              @media(max-width:640px){.debt-float-card{width:225px;padding:10px}.debt-card-headline{font-size:11px}.debt-card-primary-value{font-size:20px}.gde-country-pin{max-width:130px}.gde-pin-name{max-width:98px}}
            </style>
            """
        )
    )
