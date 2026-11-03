"""Build creditor-colored, equal-area slices clipped to country boundaries.

Each country's outline is divided into bands whose projected areas match the
reported creditor shares. This is a visualization of composition, not a map
of territorial ownership or where a creditor's loans are physically located.
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import math
import logging
from pathlib import Path
from typing import Any

import requests
from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.affinity import rotate
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box, mapping, shape
from shapely.ops import transform

LOGGER = logging.getLogger(__name__)

NATURAL_EARTH_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
    "ne_110m_admin_0_countries.geojson"
)
DEFAULT_GEOJSON_PATH = Path(__file__).resolve().parents[1] / "data" / "ne_110m_admin_0_countries.geojson"

PALETTE = [
    "#5B8FF9", "#61DDAA", "#65789B", "#F6BD16", "#7262FD", "#78D3F8",
    "#9661BC", "#F6903D", "#008685", "#F08BB4", "#6DC8EC", "#9270CA",
    "#FF9D4D", "#269A99", "#D46BFF", "#5D7092", "#E8684A", "#A0D911",
    "#2F54EB", "#FA8C16", "#13C2C2", "#EB2F96", "#8C8C8C", "#52C41A",
]


class BoundaryDataError(RuntimeError):
    """Raised when country boundary data cannot be loaded."""


def download_country_geojson(path: str | Path = DEFAULT_GEOJSON_PATH) -> dict[str, Any]:
    """Load Natural Earth 1:110m country boundaries, downloading once if absent."""
    target = Path(path)
    if target.exists() and target.stat().st_size > 100_000:
        try:
            with target.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("Existing country GeoJSON is invalid; downloading a clean copy")

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(
            NATURAL_EARTH_URL,
            headers={"User-Agent": "GlobalBilateralDebtExplorer/2.0"},
            timeout=(15, 90),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
            raise BoundaryDataError("Natural Earth did not return a valid GeoJSON FeatureCollection")
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(target)
        return payload
    except (requests.RequestException, ValueError, OSError) as exc:
        raise BoundaryDataError(
            "Could not download Natural Earth country boundaries. Check your internet connection "
            f"and try again. Details: {exc}"
        ) from exc


# Categorical palette for relationship lines: six validated hues first, then six
# chosen by farthest-point search in OKLab (medium lightness, visible on the light map). ``assign_distinct_colors`` never puts two colours closer than
# ``min_delta`` (OKLab ΔE × 100) on the same map.
DISTINCT_PALETTE = (
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6",
    "#8c314c", "#d90bd9", "#8c7207", "#d90b72", "#4caed9", "#31568c",
)


def _oklab(hex_color: str) -> tuple[float, float, float]:
    def linear(channel: int) -> float:
        value = channel / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    text = hex_color.lstrip("#")
    r, g, b = (linear(int(text[i:i + 2], 16)) for i in (0, 2, 4))
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def color_distance(first: str, second: str) -> float:
    """Perceptual distance (OKLab ΔE × 100); below ~15 colours are hard to tell apart."""
    a, b = _oklab(first), _oklab(second)
    return math.dist(a, b) * 100


def assign_distinct_colors(keys: list[str], min_delta: float = 15.0) -> dict[str, str]:
    """Give each key (most important first) a colour that stays clearly distinct on one map.

    Each key starts at its own hash-chosen palette slot, so a country usually keeps
    the same colour across maps (colour follows the entity, not its rank). Only on
    a clash does it move to the next slot that is at least ``min_delta`` away from
    every colour already used; if none is, the most distant free colour is used.
    """
    assigned: dict[str, str] = {}
    for key in keys:
        if key in assigned:
            continue
        start = int.from_bytes(hashlib.sha256(str(key).encode("utf-8")).digest()[:4], "big") % len(DISTINCT_PALETTE)
        used = list(assigned.values())
        candidates = [DISTINCT_PALETTE[(start + step) % len(DISTINCT_PALETTE)] for step in range(len(DISTINCT_PALETTE))]
        choice = next((c for c in candidates if c not in used and all(color_distance(c, u) >= min_delta for u in used)), None)
        if choice is None:
            free = [c for c in candidates if c not in used] or candidates
            choice = max(free, key=lambda c: min((color_distance(c, u) for u in used), default=999))
        assigned[key] = choice
    return assigned


def color_for_creditor(creditor_id: str) -> str:
    """Return a stable, deterministic color for a World Bank counterpart-area ID."""
    key = str(creditor_id)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big")
    if index % 3 == 0:
        return PALETTE[index % len(PALETTE)]
    hue = (index % 360) / 360.0
    saturation = 0.58 + (digest[4] / 255.0) * 0.20
    value = 0.78 + (digest[5] / 255.0) * 0.18
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return "#{:02X}{:02X}{:02X}".format(round(red * 255), round(green * 255), round(blue * 255))


def _iso_from_properties(properties: dict[str, Any]) -> str:
    for key in ("ADM0_A3", "ISO_A3", "SOV_A3", "GU_A3"):
        value = str(properties.get(key, "")).upper()
        if len(value) == 3 and value.isalpha() and value != "-99":
            return value
    return ""


def _polygonal_only(geometry: Any) -> Any:
    """Strip line/point remnants left by a polygon intersection."""
    if geometry.is_empty:
        return GeometryCollection()
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygons = []
        for item in geometry.geoms:
            cleaned = _polygonal_only(item)
            if isinstance(cleaned, Polygon):
                polygons.append(cleaned)
            elif isinstance(cleaned, MultiPolygon):
                polygons.extend(list(cleaned.geoms))
        return MultiPolygon(polygons) if polygons else GeometryCollection()
    if hasattr(geometry, "geoms"):
        polygons = []
        for item in geometry.geoms:
            cleaned = _polygonal_only(item)
            if isinstance(cleaned, Polygon):
                polygons.append(cleaned)
            elif isinstance(cleaned, MultiPolygon):
                polygons.extend(list(cleaned.geoms))
        return MultiPolygon(polygons) if polygons else GeometryCollection()
    return GeometryCollection()


def _area_cut(geometry: Any, min_x: float, max_x: float, min_y: float, max_y: float, target_area: float) -> float:
    """Find an x-coordinate whose left-hand clipped area equals target_area."""
    if target_area <= 0:
        return min_x
    if target_area >= geometry.area:
        return max_x
    low, high = min_x, max_x
    padding = max(1.0, (max_x - min_x) * 0.01)
    for _ in range(30):
        middle = (low + high) / 2.0
        left_area = geometry.intersection(box(min_x - padding, min_y - padding, middle, max_y + padding)).area
        if left_area < target_area:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _country_slices(
    debtor_iso3: str,
    debtor_name: str,
    creditor_rows: list[dict[str, Any]],
    country_geometry: Any,
    max_map_slices: int = 7,
    angle_degrees: float = 18.0,
) -> list[dict[str, Any]]:
    rows = [row for row in creditor_rows if float(row.get("amount_usd", 0) or 0) > 0]
    rows.sort(key=lambda row: float(row["amount_usd"]), reverse=True)
    if not rows:
        return []

    # Keep the largest creditors individually; combine the long tail to make
    # country-shaped segments legible. The detailed pie/table still shows all.
    if len(rows) > max_map_slices:
        main_rows = rows[: max_map_slices - 1]
        other_rows = rows[max_map_slices - 1 :]
        other_total = sum(float(row["amount_usd"]) for row in other_rows)
        main_rows.append(
            {
                "creditor_id": "__OTHER__",
                "creditor_iso3": "",
                "creditor_name": f"Other creditors ({len(other_rows)})",
                "amount_usd": other_total,
            }
        )
        rows = main_rows

    total_amount = sum(float(row["amount_usd"]) for row in rows)
    if total_amount <= 0:
        return []

    geometry = make_valid(country_geometry)
    if geometry.is_empty:
        return []
    representative = geometry.representative_point()
    local_crs = CRS.from_proj4(
        f"+proj=laea +lat_0={representative.y:.6f} +lon_0={representative.x:.6f} "
        "+datum=WGS84 +units=m +no_defs"
    )
    to_local = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True).transform
    to_wgs84 = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True).transform
    projected = transform(to_local, geometry)
    projected = make_valid(projected)
    if projected.is_empty or projected.area <= 0:
        return []

    rotation_origin = projected.centroid
    rotated = rotate(projected, angle_degrees, origin=rotation_origin, use_radians=False)
    min_x, min_y, max_x, max_y = rotated.bounds
    total_area = rotated.area
    previous_x = min_x - max(1.0, (max_x - min_x) * 0.01)
    cumulative_amount = 0.0
    features: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        cumulative_amount += float(row["amount_usd"])
        next_x = _area_cut(
            rotated,
            min_x,
            max_x,
            min_y,
            max_y,
            total_area * cumulative_amount / total_amount,
        )
        if index == len(rows) - 1:
            next_x = max_x + max(1.0, (max_x - min_x) * 0.01)
        band = box(previous_x, min_y - 1, next_x, max_y + 1)
        piece = _polygonal_only(rotated.intersection(band))
        previous_x = next_x
        if piece.is_empty or piece.area <= total_area * 1e-8:
            continue

        unrotated = rotate(piece, -angle_degrees, origin=rotation_origin, use_radians=False)
        geographic = transform(to_wgs84, unrotated)
        geographic = _polygonal_only(make_valid(geographic))
        if geographic.is_empty:
            continue
        # A small simplification keeps Leaflet responsive without changing the
        # visual identity of the 1:110m country boundary.
        geographic = geographic.simplify(0.015, preserve_topology=True)
        creditor_id = str(row.get("creditor_id") or row.get("creditor_iso3") or row["creditor_name"])
        amount = float(row["amount_usd"])
        share = amount / total_amount * 100.0
        properties = {
            "debtor_iso3": debtor_iso3,
            "debtor_name": debtor_name,
            "creditor_id": creditor_id,
            "creditor_iso3": str(row.get("creditor_iso3") or ""),
            "creditor_name": str(row["creditor_name"]),
            "amount_usd": amount,
            "share_pct": share,
            "amount_display": _format_usd(amount),
            "share_display": f"{share:.1f}%",
            "color": color_for_creditor(creditor_id),
        }
        features.append({"type": "Feature", "properties": properties, "geometry": mapping(geographic)})

    return features


def _format_usd(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:,.1f}K"
    return f"${value:,.0f}"


def build_creditor_map_geojson(
    debt_rows: list[dict[str, Any]],
    country_geojson: dict[str, Any],
    max_map_slices: int = 7,
) -> dict[str, Any]:
    """Return one GeoJSON polygon feature per creditor-colored country slice.

    ``debt_rows`` must contain one observation year and have columns/keys:
    debtor_iso3, debtor_name, creditor_id, creditor_iso3, creditor_name, amount_usd.
    """
    country_geometries: dict[str, Any] = {}
    for feature in country_geojson.get("features", []):
        properties = feature.get("properties") or {}
        iso = _iso_from_properties(properties)
        if iso and feature.get("geometry"):
            country_geometries[iso] = shape(feature["geometry"])

    by_debtor: dict[str, list[dict[str, Any]]] = {}
    for row in debt_rows:
        debtor = str(row.get("debtor_iso3", "")).upper()
        if debtor:
            by_debtor.setdefault(debtor, []).append(row)

    output: list[dict[str, Any]] = []
    missing: list[str] = []
    for debtor_iso3, rows in by_debtor.items():
        geometry = country_geometries.get(debtor_iso3)
        if geometry is None:
            missing.append(debtor_iso3)
            continue
        debtor_name = str(rows[0].get("debtor_name", debtor_iso3))
        output.extend(
            _country_slices(
                debtor_iso3=debtor_iso3,
                debtor_name=debtor_name,
                creditor_rows=rows,
                country_geometry=geometry,
                max_map_slices=max_map_slices,
            )
        )
    if missing:
        LOGGER.info("No Natural Earth boundary match for %d IDS debtor codes: %s", len(missing), ", ".join(missing[:20]))
    return {"type": "FeatureCollection", "features": output}
