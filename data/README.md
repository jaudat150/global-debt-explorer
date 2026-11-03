# Data directory

| File | Created by | Purpose |
|---|---|---|
| `bilateral_debt_cache.json` | `python -m src.fetcher` | World Bank IDS PPG bilateral creditor-composition observations. |
| `ne_110m_admin_0_countries.geojson` | first dashboard launch | Natural Earth 1:110m country outlines used for drawing. |
| `country_label_points.json` | shipped | ISO3 → label point (Natural Earth 1:10m, public domain) for 241 economies, so small states without a 1:110m outline still get a real map coordinate. |
| `indicator_snapshot.json` | dashboard / `python -m src.indicator_data refresh` / weekly workflow | Last successful download of every atlas series (World Bank + IMF). Used **only** when a provider is unreachable, and labelled "last-known copy". Safe to delete. |
| `country_profiles_snapshot.json` | dashboard / `python -m src.processor profiles` / monthly workflow | Last-known-good World Bank and IMF observations. Used in Tier 3 **only** when a live API is unreachable. Safe to delete. |

National country profiles are not written into the bilateral cache. They are resolved at runtime through World Bank V2 → IMF DataMapper → local fallback (`LOCAL_FALLBACK_DB` in `src/processor.py`, then the snapshot above). Live responses are cached by Streamlit for 24 hours.

Set `GDE_PROFILE_SNAPSHOT=off` / `GDE_INDICATOR_SNAPSHOT=off` to disable a snapshot, or set either to another path.
