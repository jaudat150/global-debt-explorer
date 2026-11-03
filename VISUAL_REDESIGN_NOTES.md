# Visual and interaction notes — v2.1.0

## Map interaction

- Country hover shows its full name, reporting role, reported debtor total, creditor claims, and national-profile coverage where available.
- Country popups use human-readable names and financial context. ISO-3 codes remain internal identifiers, not the primary user-facing label.
- The selected debtor is displayed as a named origin marker. Other reporting debtors are selectable markers with borrower totals.
- Creditor markers use full country names. Curved links are named as `Debtor → Creditor`; hover shows the amount and share, and click opens a detailed relationship popup.
- The floating card shows the selected relationship's debt stock, share of the debtor's total, total reported debt, creditor-area count, and year.
- National country profiles are shown with a distinct source tier, metric definition, unit, observation year, source label, and source date/retrieval timestamp.
- Countries without a usable value are labelled `No verified data`; missing data is never plotted as zero.
- Country coordinates come from Natural Earth geometry. No coordinates are fabricated when a boundary cannot be matched.
- Non-country counterpart areas remain in the full creditor breakdown but do not receive invented geographic locations.

## Streamlit widget state

Map selection events are written to `_gde_pending_debtor` or `_gde_pending_creditor`. `src/ui_state.py` consumes those pending values before the corresponding selectbox is instantiated. This prevents writing to `st.session_state` widget keys after Streamlit has created the widget in the current run.

## Data interpretation

- A map line represents a World Bank IDS reported bilateral debt relationship. It does not represent a physical route, asset location, or territorial ownership.
- IDS bilateral debt (`DT.DOD.BLAT.CD`), World Bank long-term external debt (`DT.DOD.DLXF.CD`), IMF general-government debt-to-GDP (`GG_DEBT_GDP`), and curated local estimates are distinct measures.
- USD debt stocks and percent-of-GDP ratios are never combined into one numeric ranking.
- Tier 3 local values are estimates with source dates and caveats, not verified live API observations.
