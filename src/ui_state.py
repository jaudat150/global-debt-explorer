"""Widget-safe handoff of Leaflet map selections to Streamlit selectboxes.

A map click is delivered after a Streamlit widget with a fixed key has already
been instantiated in that run. Instead of mutating that widget key immediately,
store a pending selection and consume it before the widget is created next run.
"""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from typing import Any


def apply_pending_debtor_selection(
    state: MutableMapping[str, Any],
    debtor_codes: Sequence[str],
    *,
    widget_key: str = "gde_debtor",
) -> None:
    """Apply a pending debtor change before the debtor selectbox is created."""
    pending = state.pop("_gde_pending_debtor", None)
    if pending in debtor_codes:
        state[widget_key] = pending
        state["_gde_reset_creditor"] = True
    if state.get(widget_key) not in debtor_codes and debtor_codes:
        state[widget_key] = debtor_codes[0]


def apply_pending_creditor_selection(
    state: MutableMapping[str, Any],
    creditor_ids: Sequence[str],
    *,
    widget_key: str = "gde_creditor",
) -> None:
    """Apply a pending creditor change before the creditor selectbox is created."""
    pending = state.pop("_gde_pending_creditor", None)
    reset = bool(state.pop("_gde_reset_creditor", False))
    if pending in creditor_ids:
        state[widget_key] = pending
    elif reset or state.get(widget_key) not in creditor_ids:
        state[widget_key] = creditor_ids[0] if creditor_ids else ""
