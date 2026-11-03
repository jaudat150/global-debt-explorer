# debt-atlas

**Sovereign debt, deficits, government spending and who-owes-whom data for every country — in one line of Python.**

`debt-atlas` is the Python library of the open-source [Global Debt & Fiscal Atlas](https://github.com/Jaudat150/global-debt-explorer).
It gives you 37 indicators from the World Bank and IMF, government spending in US dollars, national debt profiles and
bilateral lending between countries as tidy pandas DataFrames — every value with its source, series code and year.

```bash
pip install debt-atlas
```

## Quick start

```python
import debt_atlas as da

da.latest("gov_debt_gdp", countries=["Japan", "Italy", "USA"])   # government debt, % of GDP
da.rank("military_usd", n=10)                                     # biggest military spenders, US$
da.spending("United States")                                      # where the money goes, in US$
da.who_owes("Kenya")                                              # Kenya's lenders and how much
da.owed_by("China")                                               # who owes China
da.series("inflation", "Argentina", start=2010)                   # history, one row per source
da.compare(["Egypt", "Pakistan", "Kenya"], ["gov_debt_gdp", "interest_gdp", "health_gov_gdp"])
da.profile("Syria")                                               # 3-tier national debt profile
```

Countries can be given as names (`"Kenya"`), ISO2 (`"KE"`) or ISO3 (`"KEN"`).

## What is inside

| Function | Returns |
|---|---|
| `indicators(category=None)` | Every indicator: id, name, unit, description, upstream series |
| `latest(indicator, countries=None)` | Latest value per country, with year, estimate flag and source |
| `rank(indicator, n=10, lowest=False)` | Top or bottom countries |
| `series(indicator, countries, start, end, provider)` | Full history, one row per upstream series |
| `wide(indicator, countries)` | Year × country table |
| `compare(countries, indicators)` | Several indicators for several countries side by side |
| `spending(country=None, item=None)` | Military, health, education, social transfers, wages, interest, R&D, total — US$, per person, % of GDP |
| `who_owes(country)` / `owed_by(country)` | Bilateral lenders / borrowers with amounts and shares (World Bank IDS) |
| `bilateral(debtor, creditor, year)` | The full who-owes-whom table |
| `profile(country)` | National debt profile with source tier |
| `sources(topic=None)` | Where to find newer or more detailed data |
| `countries()` | ISO codes, names, coordinates |
| `live(series_code)` | Any series straight from the World Bank or IMF API |
| `download_all(folder)` | Every file plus a Frictionless `datapackage.json` |

Categories: `debt`, `spending`, `revenue`, `economy`, `flows`, `people`.

## Command line

```bash
debt-atlas rank military_usd -n 10
debt-atlas latest gov_debt_gdp --countries Japan Italy
debt-atlas who-owes Pakistan
debt-atlas spending "United States" --csv > usa.csv
debt-atlas download ./atlas-data
```

## Where the data comes from

The project rebuilds its data every week from the World Bank (World Development Indicators, International Debt
Statistics) and IMF (World Economic Outlook, Global Debt Database, Public Finances in Modern History) APIs, validates it
against published reference figures, and publishes it as CSV. The library downloads those files (cached for 24 hours
in `~/.cache/debt_atlas`) — fast, no API keys, and identical to what the web app shows.

- IMF values for the current year are staff estimates and are flagged `is_estimate`; projections are never included.
- Missing values are absent, never zero.
- Use another source with `da.Atlas("path/or/url")`, or set `DEBT_ATLAS_SOURCE`.

## Citing

Please cite the original data providers named in each row's `source` column, and this project
(see [CITATION.cff](https://github.com/Jaudat150/global-debt-explorer/blob/main/CITATION.cff)).

Code: MIT licence. Data: each provider's terms (World Bank CC BY 4.0, IMF, SIPRI, WHO, UNESCO).
