# Changelog

## 3.4.0 — Python library, SEO website, team setup

- **`debt-atlas` Python library** (`pip install debt-atlas`, `import debt_atlas as da`): `latest`, `rank`, `series`,
  `wide`, `compare`, `spending`, `who_owes`, `owed_by`, `bilateral`, `profile`, `indicators`, `sources`, `live`,
  `download_all`; country names, ISO2 or ISO3 accepted; pandas-only, 24-hour disk cache, offline fallback; a
  `debt-atlas` command line. Published to PyPI automatically on every GitHub release (trusted publishing).
- **Static website for search engines** (`python -m src.website build`): a page for every country (~240) and indicator
  (37) with real numbers in titles and descriptions, FAQ, schema.org `Dataset`/`FAQPage`/`BreadcrumbList`, sitemap,
  robots.txt, Google Search Console verification; published weekly to GitHub Pages (`pages.yml`).
- Country names people search for (South Korea, DR Congo, Syria, Iran…) in exports, tables and URLs.
- Team setup: `docs/TEAM_PLAYBOOK.md` (roles, branch protection, review, releases, SEO and launch checklist),
  `.github/CODEOWNERS`.
- CI also builds and installs the library and builds the website.
- 17 new tests (152 total).

## 3.3.3 — Release

- Replaced Streamlit's deprecated `use_container_width` with `width="stretch"` (27 places) so the app keeps working
  on current and future Streamlit releases, including Streamlit Community Cloud. Minimum Streamlit is now 1.50.
- Release check: 135 tests; live validation 16/16, 0 warnings; every view and shareable link verified in a browser.

## 3.3.2 — Release candidate

- Fixed: on the Who-owes-whom map the "lent to" legend overlapped the selected-relationship card; it now sits
  bottom-left.
- Release check passed: 135 tests; live validation 16/16 with 0 warnings; all 45 upstream series current.

## 3.3.1 — National debt profile: wider IMF coverage

- Fixed: Saudi Arabia (and other countries absent from the IMF Global Debt Database) showed "No verified data" in
  the national debt profile although the atlas had a value. Tier 2 now tries the IMF World Economic Outlook series
  `GGXWDG_NGDP` (~195 countries, up to the current year) first, then `GG_DEBT_GDP` (Global Debt Database, 88
  countries). Both measure general government gross debt as % of GDP; the resolution trail names the series used.
- The offline snapshot keeps both series without letting the older Global Debt Database overwrite World Economic
  Outlook values.
- 2 new regression tests (134 total).

## 3.3.0 — Shareable links

- Every view has its own URL, e.g. `?view=atlas&indicator=military_gdp&country=KEN`,
  `?view=spending&country=USA&item=health`, `?view=debt&country=CHN`. The address bar follows what is on screen, and
  a **🔗 Share** button gives a copyable link to the exact view.
- Links are applied once per session (they never override later clicks); invalid or outdated values are ignored.
- One selected country is shared by every view, including Who owes whom.
- Validation: Costa Rica, Iceland and Panama recorded as known zero military spending (no standing armies); first
  live validation passes with 0 failures and 0 warnings.
- Command-line tools no longer print Streamlit's "No runtime found" warnings at import.
- New module `src/share.py`; 6 new tests (132 total).

## 3.2.3 — First live validation

- First validation against live data: all 45 series return data, none stale or empty, 165/165 Spending-tab military
  figures match SIPRI.
- Reference bands updated to current IMF figures (China GDP US$21.6tn 2026 est.; Norway GDP per capita US$115k 2026
  est.; Japan government debt ~207% of GDP and falling, IMF F&D Sep 2026).
- Spending lines never use negative source values; the validator now names each negative (FAIL) or zero (WARN)
  line with its country, year and source.

## 3.2.2 — Data validation

- `python -m src.validate`: offline check of `data/exports/` — EMPTY / THIN / STALE series, headline figures against
  published references (SIPRI, IMF WEO, UN WPP), no future years, plausible share-of-GDP bounds, and Spending-tab
  figures matching their source series. Exit code 1 on failures; `--out` writes a Markdown report.
- The weekly workflow validates the exports and publishes `data/exports/VALIDATION.md`.
- Command-line tools no longer print Streamlit's "No runtime found" warnings.

## 3.2.1 — Colour-coded links

- Every debt link has its own colour, so each lender and borrower can be told apart at a glance. The map lines,
  the map legend and the creditor donut chart share one assignment.
- Colours are guaranteed distinct on the same map: a country keeps its usual colour unless it would be too close
  (OKLab ΔE < 15) to one already shown, in which case the next clearly different colour is used. The 12-colour
  palette combines six validated hues with six chosen by farthest-point search.
- Atlas arrows: "borrowed from" lines are solid and "lent to" lines dashed; the legend lists every arrow with its
  colour, country and amount, plus how many more are in the table.
- The Who-owes-whom map's dotted lines are now solid, matching that convention.

## 3.2.0 — Spending in US$ and the Developers tab

- **💸 Spending tab:** "United States: Military & defence US$…" — what each government spends on defence, health,
  education, subsidies & social transfers, public-sector wages, interest and R&D, in current US$, per person and
  as % of GDP, next to total government spending. World ranking for any line (total US$, per person, % of GDP).
  Amounts come only from published series: military in US$ directly from SIPRI (`MS.MIL.XPND.CD`); other lines are
  the published share × GDP **for the same year** (never mixed years, never estimated). Every line shows its method
  and the exact series used.
- **🧑‍💻 Developers tab:** one-click ZIP of every dataset (latest values, full per-source time series, spending in US$,
  national debt profiles, who-owes-whom, country reference) with a Frictionless `datapackage.json` and
  `catalog.json`; instant downloads; a table of all 45 upstream API series with live links; World Bank, IMF and
  DBnomics (COFOG) examples in curl, Python and R; the project's Python API and CLI; a full data dictionary.
- **Static open-data files:** the weekly workflow now publishes `data/exports/*` so anyone can `read_csv` the data
  straight from GitHub.
- New indicators: military expenditure (US$), government health spending per person (US$), central government
  expense (% of GDP).
- New modules `src/spending.py`, `src/spending_view.py`, `src/exports.py`, `src/developer_view.py`; CLI
  `python -m src.exports build`. 12 new tests (117 total).

## 3.1.0 — Who owes whom arrows

- **Lending arrows everywhere.** Select any country and see arrows from each lender to the borrower, with the debt
  outstanding on the label (e.g. "LENT US$5.4B"). Orange = who the country borrowed from; teal = who owes the
  country. Selecting a lender (China, Japan, Saudi Arabia…) now shows the borrowers that owe it.
- Arrows on the World atlas (toggle **Show who owes whom**), on the Who-owes-whom map, and two new tables —
  "{country} owes" and "Owe {country}" — in the atlas and the Country profile.
- Arrowheads are rotated in screen (Web-Mercator) space so they point along the curve at any zoom.
- Labels are de-cluttered: the biggest flows get name pills; overlapping smaller ones become dots that keep their
  tooltip and click-to-select.
- Fixed: links crossing the Pacific (e.g. Canada → China) were drawn as a straight line across the whole map.
- New module `src/debt_flows.py`; 10 new tests (105 total).

## 3.0.0 — Global Debt & Fiscal Atlas

The project grows from a bilateral-debt explorer into a full public-finance atlas.

- **World atlas view:** choropleth of 34 indicators in six categories — debt & deficits, government spending,
  revenue & taxes, economy, external finance, people & development — from 42 live World Bank and IMF series.
- **Freshest value wins:** when several series share a definition (e.g. IMF WEO and World Bank WDI GDP growth),
  each country shows its most recent valid observation. IMF projections are dropped; current-year IMF estimates
  are flagged and can be hidden with **Reported data only**.
- **Country profile view:** headline tiles, a "where the money goes" chart (defence, health, education, interest,
  R&D as % of GDP), 25-year trends for any indicator, and research links (IMF country page, DataMapper profile,
  World Bank country data, budget documents, national statistics office).
- **Data & sources view:** live freshness check of every indicator, full-dataset CSV export with provenance, and a
  directory of 31 external sources (IMF GFS COFOG, DBnomics, SIPRI, WHO GHED, UNESCO UIS, BOOST, Open Budget
  Survey, UNU-WIDER GRD, OECD, AidData…) shown wherever data is missing or stale.
- **Resilience:** 24-hour caching, per-provider outage back-off, and `data/indicator_snapshot.json` (refreshed weekly
  by the new `refresh_indicators.yml` workflow) as an offline fallback.
- **Open-source project files:** `CITATION.cff`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `ROADMAP.md`, issue and PR
  templates, CI on Python 3.11–3.13, and `docs/DATA_SOURCES.md` generated from the catalogue.
- New CLIs: `python -m src.indicator_data show|refresh`, `python -m src.catalog [--markdown]`.
- 21 new tests (95 total).

## 2.2.0 — Resilience fallback hardening

Fixes countries that appeared as missing points or showed only the bilateral IDS figure.

- **Palestine, South Sudan, Kosovo, Western Sahara now render.** Natural Earth codes (`PSX`, `SDS`, `KOS`, `SAH`) are translated to ISO/World Bank codes; previously their profiles never matched a polygon.
- **Every country has coordinates.** Added `data/country_label_points.json` (Natural Earth 1:10m label points for 241 economies) so small states missing from the 1:110m outlines (Singapore, Bahrain, Maldives, Caribbean and Pacific islands) get a real map point.
- **No-data countries are plotted** as hollow grey "not zero" points instead of being dropped.
- **IDS debtor markers and the relationship card show the national profile.** The bilateral total (e.g. Syria's US$2.01B) is now labelled "Bilateral IDS total (not total debt)" with the national figure and its tier beside it.
- **IMF projections are excluded.** DataMapper series run five years ahead; Tier 2 now ignores years after the current year instead of reporting a 2030 forecast as the latest value.
- **IMF code aliases** (`PSE`→`WBG`, `XKX`→`UVK`) so Tier 2 can answer for Palestine and Kosovo.
- **Staleness guard.** Observations older than `GDE_MAX_OBSERVATION_AGE_YEARS` (default 10) fall through to the next tier. Zero values are treated as missing.
- **Tier 3 last-known-good snapshot.** Successful live responses are saved to `data/country_profiles_snapshot.json`; when an API is unreachable, the last known value is used (clearly labelled) before a "No verified data" result.
- **Outages are not cached.** Live calls raise on failure so Streamlit never stores an outage for 24 hours. A 5-minute in-process back-off prevents a dead API from adding timeout latency to every rerun. **Refresh country data** resets it.
- **Tier 1 resilience.** If the bulk `country/all` request fails, the exact `/country/{ISO3}/indicator/DT.DOD.DLXF.CD` route is queried concurrently behind a circuit breaker.
- **Curated estimates are no longer hidden** when a live tier wins: they are attached as `alternative_estimate` and shown as "Other estimate".
- Pure, I/O-free `resolve_country_profile()` with a `resolution_trail` explaining each tier decision (shown in the country panel).
- New CLI: `python -m src.processor profiles CAN SAU SYR PSE`.
- Monthly workflow also refreshes the country-profile snapshot.
- 35 new tests (74 total).

## 2.1.0 — Three-tier national profile resilience

- Added World Bank V2 national indicator lookup using `DT.DOD.DLXF.CD`, selecting the latest non-null observation per ISO-3 country.
- Added a paginated `country/all` V2 path for dashboard efficiency and retained the exact `/country/{ISO3}/indicator/...` lookup for focused retrieval.
- Added IMF DataMapper `GG_DEBT_GDP` fallback, selecting the latest non-null year and preserving the percent-of-GDP unit.
- Added curated, dated Syria and West Bank/Gaza local estimates with metric definitions, source URLs, source timestamps, and perimeter caveats.
- Added stable profile objects for every requested three-letter ISO-3 code, including explicit `No verified data` states rather than invented zero values.
- Added 24-hour Streamlit data caching for country profiles and the shared IMF series payload, plus manual cache refresh support.
- Attached Natural Earth coordinates in the map layer where a matching country boundary exists; missing geometry is labelled instead of assigned a fake point.
- Updated README, Windows quick start, data-directory guidance, and tests to explain distinct metric definitions and sources.
- Added tests for World Bank parsing, IMF parsing, fallback priority, network failure handling, and profile schema.

## 2.0.2 — Country-readable map and Streamlit selection fix

- Replaced ISO-only country polygon popups with full country names, data roles, debtor totals, creditor claims, and any matching selected-debtor relationship.
- Replaced two-letter-looking map pins with readable country-name labels for the selected debtor and mapped creditors.
- Added relationship tooltips and popups showing `Debtor → Creditor`, reported stock, share of the debtor's total, and observation year.
- Added global creditor totals to country hover/popups so users can understand a country in both debtor and creditor roles.
- Fixed Streamlit widget-state handling by applying map selections from pending state before each selectbox is instantiated.
- Added `src/ui_state.py` and regression tests for the previous session-state exception.

## 2.0.1 — Map basemap fix

- Replaced the keyless CARTO raster default with OpenStreetMap Standard tiles to remove the `API KEY REQUIRED` watermark for fresh installs.
- Added optional CARTO Positron support through a user-provided `CARTO_BASEMAP_API_KEY` environment variable.
- Documented OpenStreetMap tile attribution and usage-policy constraints.

## 2.0.0 — Reference-style dashboard

- Reworked the Streamlit experience around a large interactive debtor-creditor relationship map.
- Added creditor flags, curved dotted relationship links, and an on-map relationship summary card.
- Added side-by-side debtor and creditor ranking tables with adjacent-year comparisons.
- Added a selected-country creditor composition chart, full creditor-area table, and CSV export.
- Preserved the World Bank IDS source-6 ingestion pipeline, pagination, retries, adaptive batch splitting, and atomic cache writes.
