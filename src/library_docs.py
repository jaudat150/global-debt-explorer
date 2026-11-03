"""One source for the ``debt-atlas`` library guide, shown in the app and on the website."""

from __future__ import annotations

PYPI_URL = "https://pypi.org/project/debt-atlas/"
DOCS_URL = "https://github.com/Jaudat150/global-debt-explorer/blob/main/docs/LIBRARY.md"

TAGLINE = ("Every number in this atlas in one line of Python: 37 World Bank and IMF indicators, government spending in "
           "US dollars, national debt profiles and who-owes-whom lending, as pandas DataFrames with the source, series "
           "code and year of every value.")

INSTALL = "pip install debt-atlas"

QUICKSTART = '''import debt_atlas as da

da.latest("gov_debt_gdp", countries=["Japan", "Italy", "USA"])   # government debt, % of GDP
da.rank("military_usd", n=10)                                     # biggest military spenders, US$
da.spending("United States")                                      # where the money goes, in US$
da.who_owes("Kenya")                                              # Kenya's lenders and how much
da.owed_by("China")                                               # who owes China
da.series("inflation", "Argentina", start=2010)                   # history, one row per source
da.compare(["Egypt", "Pakistan", "Kenya"], ["gov_debt_gdp", "interest_gdp", "health_gov_gdp"])
da.profile("Syria")                                               # 3-tier national debt profile
da.download_all("atlas-data")                                     # every file + datapackage.json'''

FUNCTIONS: list[tuple[str, str]] = [
    ("indicators(category=None)", "Every indicator: id, name, unit, description, upstream series"),
    ("latest(indicator, countries=None)", "Latest value per country, with year, estimate flag and source"),
    ("rank(indicator, n=10, lowest=False)", "Top or bottom countries"),
    ("series(indicator, countries, start, end)", "Full history, one row per upstream series"),
    ("wide(indicator, countries)", "Year × country table"),
    ("compare(countries, indicators)", "Several indicators for several countries side by side"),
    ("spending(country=None, item=None)", "Defence, health, education, social transfers, wages, interest, R&D — US$, per person, % of GDP"),
    ("who_owes(country) / owed_by(country)", "Bilateral lenders / borrowers with amounts and shares (World Bank IDS)"),
    ("bilateral(debtor, creditor, year)", "The full who-owes-whom table"),
    ("profile(country)", "National debt profile with its source tier"),
    ("countries()", "ISO codes, names, coordinates"),
    ("live(series_code)", "Any series straight from the World Bank or IMF API"),
    ("download_all(folder)", "Every file plus a Frictionless datapackage.json"),
]

CLI = '''debt-atlas rank military_usd -n 10
debt-atlas latest gov_debt_gdp --countries Japan Italy
debt-atlas who-owes Pakistan
debt-atlas --csv spending "United States" > usa.csv
debt-atlas download ./atlas-data'''

NOTES = ("Countries can be names (\"Kenya\"), ISO2 (\"KE\") or ISO3 (\"KEN\"). Data is the weekly-validated snapshot "
         "this site uses, cached for 24 hours — no API keys. IMF current-year values are flagged `is_estimate`; "
         "missing values are absent, never zero.")
