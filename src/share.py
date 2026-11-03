"""Shareable links: every view, country and indicator has its own URL.

Example::

    https://<your-app>/?view=atlas&category=spending&indicator=military_gdp&country=KEN
    https://<your-app>/?view=spending&country=USA&item=health
    https://<your-app>/?view=debt&country=CHN

Query parameters are applied **once per session**, before any widget is created
(so they never fight the user's later clicks), and the address bar is kept in
sync with what is on screen, so copying the URL always shares the current view.

The parsing and building functions are pure (no Streamlit) and unit-tested.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping
from urllib.parse import urlencode, urlsplit, urlunsplit

from src.catalog import CATEGORY_BY_ID, INDICATOR_BY_ID

VIEWS = ("atlas", "country", "spending", "debt", "sources", "developers", "python")
VIEW_KEY = "gde_view"
COUNTRY_KEY = "gde_selected_country"          # shared selection (atlas_view.SELECTED_KEY)
CATEGORY_KEY = "gde_atlas_category"
SPENDING_ITEM_KEY = "gde_spending_rank_item"
APPLIED_FLAG = "_gde_query_applied"
PENDING_DEBTOR_KEY = "_gde_pending_debtor"   # consumed by the Who-owes-whom selectbox


def indicator_key(category: str) -> str:
    return f"gde_atlas_indicator_{category}"


def _first(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def parse_query(params: Mapping[str, Any], *, countries: set[str], spending_items: set[str]) -> dict[str, Any]:
    """Translate raw query parameters into validated session-state updates.

    Unknown or invalid values are ignored rather than raising, so a mistyped or
    outdated link still opens the app.
    """
    updates: dict[str, Any] = {}
    view = _first(params.get("view")).lower()
    if view in VIEWS:
        updates[VIEW_KEY] = view

    country = _first(params.get("country")).upper()
    if country in countries:
        updates[COUNTRY_KEY] = country
        updates[PENDING_DEBTOR_KEY] = country

    indicator = _first(params.get("indicator"))
    category = _first(params.get("category")).lower()
    if indicator in INDICATOR_BY_ID:
        category = INDICATOR_BY_ID[indicator].category  # the indicator decides its category
        updates[indicator_key(category)] = indicator
    if category in CATEGORY_BY_ID:
        updates[CATEGORY_KEY] = category

    item = _first(params.get("item")).lower()
    if item in spending_items:
        updates[SPENDING_ITEM_KEY] = item
    return updates


def build_query(state: Mapping[str, Any]) -> dict[str, str]:
    """The minimal query string describing what is on screen."""
    view = state.get(VIEW_KEY) or "atlas"
    query: dict[str, str] = {"view": view}
    country = state.get(COUNTRY_KEY)
    if view == "debt":
        country = state.get("gde_debtor") or country
    if country and view not in ("developers", "sources", "python"):
        query["country"] = str(country)
    if view == "atlas":
        category = state.get(CATEGORY_KEY) or "debt"
        query["category"] = category
        indicator = state.get(indicator_key(category))
        if indicator:
            query["indicator"] = str(indicator)
    if view == "spending" and state.get(SPENDING_ITEM_KEY):
        query["item"] = str(state[SPENDING_ITEM_KEY])
    return query


def share_url(base_url: str | None, query: Mapping[str, str]) -> str:
    """Absolute link for ``query`` on the app's own address (falls back to a relative link)."""
    encoded = urlencode(query)
    if not base_url:
        return f"?{encoded}"
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", encoded, ""))


# ---------------------------------------------------------------------------
# Streamlit glue
# ---------------------------------------------------------------------------

def apply_query_once(state: MutableMapping[str, Any], params: Mapping[str, Any], *,
                     countries: set[str], spending_items: set[str]) -> None:
    """Apply link parameters on the first run of a session only."""
    if state.get(APPLIED_FLAG):
        return
    state[APPLIED_FLAG] = True
    state.update(parse_query(params, countries=countries, spending_items=spending_items))


def sync_query(query_params: Any, state: Mapping[str, Any]) -> dict[str, str]:
    """Write the current view to the address bar (only when it changed, to avoid reruns)."""
    wanted = build_query(state)
    current = {key: _first(query_params.get(key)) for key in query_params}
    if current != wanted:
        query_params.from_dict(wanted)
    return wanted
