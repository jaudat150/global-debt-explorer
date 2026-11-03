"""Indicator catalogue and external source directory for the World Atlas.

Everything the atlas can map is declared here as data: which live API series
feed an indicator, how to display it, and — when the live APIs cannot supply a
value — where a user should look for newer or more detailed data.

Adding an indicator is a pure data change: append an :class:`Indicator` to
:data:`INDICATORS`. ``tests/test_atlas.py`` validates the catalogue, and
``python -m src.catalog --markdown`` regenerates ``docs/DATA_SOURCES.md``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Literal

Provider = Literal["worldbank", "imf"]
Unit = Literal["percent_of_gdp", "percent", "usd", "usd_per_capita", "people", "years", "months", "index", "percent_of_gni"]


@dataclass(frozen=True)
class SourceRef:
    """One live series that measures an indicator.

    ``multiplier`` converts the provider's unit to the indicator's unit (the
    IMF publishes GDP in US$ billions and population in millions).
    """

    provider: Provider
    code: str
    label: str
    multiplier: float = 1.0

    @property
    def portal_url(self) -> str:
        """Human-readable page for this exact series."""
        if self.provider == "worldbank":
            return f"https://data.worldbank.org/indicator/{self.code}"
        return f"https://www.imf.org/external/datamapper/{self.code}"

    @property
    def api_url(self) -> str:
        if self.provider == "worldbank":
            return f"https://api.worldbank.org/v2/country/all/indicator/{self.code}?format=json"
        return f"https://www.imf.org/external/datamapper/api/v1/{self.code}"


@dataclass(frozen=True)
class Indicator:
    """A mappable indicator.

    Attributes:
        id: Stable identifier used in URLs, the snapshot and CSV exports.
        scale: ``"sequential"`` for magnitudes, ``"diverging"`` for signed values
            centred on ``center`` (fiscal balance, growth, current account).
        sources: Live series that share the same definition, in priority order.
            The freshest valid observation wins; ties go to the earlier source.
        directory: Keys of :data:`SOURCE_DIRECTORY` entries with newer, more
            detailed or national data.
        spending_line: Included in the country "where the money goes" chart.
    """

    id: str
    name: str
    short: str
    category: str
    unit: Unit
    sources: tuple[SourceRef, ...]
    description: str
    scale: Literal["sequential", "diverging"] = "sequential"
    center: float = 0.0
    caveat: str = ""
    directory: tuple[str, ...] = ()
    spending_line: bool = False


@dataclass(frozen=True)
class ExternalSource:
    """An external dataset or portal recommended for newer or deeper data."""

    key: str
    name: str
    publisher: str
    url: str
    topics: tuple[str, ...]
    access: str
    updated: str
    note: str = ""


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    icon: str
    description: str


CATEGORIES: tuple[Category, ...] = (
    Category("debt", "Debt & deficits", "🏦", "How much governments and countries owe, and what it costs them."),
    Category("spending", "Government spending", "🧾", "Where public money goes: defence, health, education, interest, wages, subsidies."),
    Category("revenue", "Revenue & taxes", "💰", "How governments raise money."),
    Category("economy", "Economy", "📈", "Output, growth, prices, jobs and the external balance."),
    Category("flows", "External finance", "🌍", "Aid, investment, remittances and reserves."),
    Category("people", "People & development", "👥", "Population, longevity, poverty and inequality."),
)

CATEGORY_BY_ID = {category.id: category for category in CATEGORIES}


def _wb(code: str, label: str, multiplier: float = 1.0) -> SourceRef:
    return SourceRef("worldbank", code, label, multiplier)


def _imf(code: str, label: str, multiplier: float = 1.0) -> SourceRef:
    return SourceRef("imf", code, label, multiplier)


INDICATORS: tuple[Indicator, ...] = (
    # ------------------------------------------------------------------ debt
    Indicator(
        "gov_debt_gdp", "General government gross debt (% of GDP)", "Gov. debt", "debt", "percent_of_gdp",
        (_imf("GGXWDG_NGDP", "IMF World Economic Outlook"), _imf("GG_DEBT_GDP", "IMF Global Debt Database")),
        "Gross debt of all levels of government (central, state, local, social security) as a share of GDP.",
        directory=("imf_weo", "imf_gdd", "imf_fiscal_monitor", "imf_dsa", "nso"),
    ),
    Indicator(
        "fiscal_balance_gdp", "Fiscal balance — net lending/borrowing (% of GDP)", "Fiscal balance", "debt", "percent_of_gdp",
        (_imf("GGXCNL_NGDP", "IMF World Economic Outlook"),),
        "Revenue minus expenditure of general government. Negative = deficit.",
        scale="diverging", directory=("imf_weo", "imf_fiscal_monitor", "oecd"),
    ),
    Indicator(
        "external_debt_usd", "Total external debt stocks (current US$)", "External debt", "debt", "usd",
        (_wb("DT.DOD.DECT.CD", "World Bank International Debt Statistics"),),
        "Public, publicly guaranteed and private debt owed to non-residents, plus IMF credit and short-term debt.",
        caveat="Covers low- and middle-income IDS reporters only; high-income countries are not in this series.",
        directory=("ids", "imf_qeds", "imf_dsa"),
    ),
    Indicator(
        "external_debt_gni", "External debt stocks (% of GNI)", "External debt / GNI", "debt", "percent_of_gni",
        (_wb("DT.DOD.DECT.GN.ZS", "World Bank International Debt Statistics"),),
        "Total external debt relative to gross national income.",
        caveat="IDS reporters only (low- and middle-income economies).", directory=("ids", "imf_dsa"),
    ),
    Indicator(
        "debt_service_exports", "Total debt service (% of exports)", "Debt service / exports", "debt", "percent",
        (_wb("DT.TDS.DECT.EX.ZS", "World Bank International Debt Statistics"),),
        "Principal and interest paid on external debt as a share of exports of goods, services and primary income.",
        caveat="IDS reporters only.", directory=("ids", "imf_dsa"),
    ),
    Indicator(
        "interest_pct_revenue", "Interest payments (% of revenue)", "Interest / revenue", "debt", "percent",
        (_wb("GC.XPN.INTP.RV.ZS", "World Bank WDI (IMF GFS)"),),
        "Central government interest payments as a share of revenue — a direct measure of debt burden.",
        caveat="Central government; coverage and recency vary by country.", directory=("imf_gfs", "imf_fiscal_monitor", "boost"),
    ),
    Indicator(
        "private_debt_gdp", "Private debt (% of GDP)", "Private debt", "debt", "percent_of_gdp",
        (_imf("Privatedebt_all", "IMF Global Debt Database"),),
        "Debt of households and non-financial corporations.",
        directory=("imf_gdd", "bis"),
    ),
    # -------------------------------------------------------------- spending
    Indicator(
        "gov_expenditure_gdp", "General government expenditure (% of GDP)", "Total spending", "spending", "percent_of_gdp",
        (_imf("exp", "IMF Public Finances in Modern History"),),
        "Total spending of general government relative to GDP.",
        caveat="Historical dataset; may lag the latest year. Check the IMF Fiscal Monitor for current estimates.",
        directory=("imf_fiscal_monitor", "imf_fpp", "imf_gfs_cofog", "oecd"),
    ),
    Indicator(
        "military_gdp", "Military expenditure (% of GDP)", "Military", "spending", "percent_of_gdp",
        (_wb("MS.MIL.XPND.GD.ZS", "SIPRI via World Bank WDI"),),
        "All current and capital spending on armed forces, defence ministries and military space activities.",
        directory=("sipri", "imf_gfs_cofog"), spending_line=True,
    ),
    Indicator(
        "military_usd", "Military expenditure (current US$)", "Military (US$)", "spending", "usd",
        (_wb("MS.MIL.XPND.CD", "SIPRI via World Bank WDI"),),
        "Military spending converted to US dollars at market exchange rates.", directory=("sipri",),
    ),
    Indicator(
        "military_pct_gov", "Military expenditure (% of government spending)", "Military / budget", "spending", "percent",
        (_wb("MS.MIL.XPND.ZS", "SIPRI via World Bank WDI"),),
        "Military spending as a share of general government expenditure.", directory=("sipri",),
    ),
    Indicator(
        "health_gov_gdp", "Government health expenditure (% of GDP)", "Health", "spending", "percent_of_gdp",
        (_wb("SH.XPD.GHED.GD.ZS", "WHO GHED via World Bank WDI"),),
        "Domestic general government spending on health (excludes external aid and private spending).",
        directory=("who_ghed", "imf_gfs_cofog"), spending_line=True,
    ),
    Indicator(
        "health_gov_per_capita", "Government health expenditure per person (current US$)", "Health per person", "spending", "usd_per_capita",
        (_wb("SH.XPD.GHED.PC.CD", "WHO GHED via World Bank WDI"),),
        "Domestic general government health spending divided by population.", directory=("who_ghed",),
    ),
    Indicator(
        "health_pct_gov", "Government health expenditure (% of government spending)", "Health / budget", "spending", "percent",
        (_wb("SH.XPD.GHED.GE.ZS", "WHO GHED via World Bank WDI"),),
        "Share of the government budget spent on health.", directory=("who_ghed",),
    ),
    Indicator(
        "education_gdp", "Government education expenditure (% of GDP)", "Education", "spending", "percent_of_gdp",
        (_wb("SE.XPD.TOTL.GD.ZS", "UNESCO UIS via World Bank WDI"),),
        "Public spending on education at all levels.",
        directory=("unesco_uis", "imf_gfs_cofog"), spending_line=True,
    ),
    Indicator(
        "education_pct_gov", "Government education expenditure (% of government spending)", "Education / budget", "spending", "percent",
        (_wb("SE.XPD.TOTL.GB.ZS", "UNESCO UIS via World Bank WDI"),),
        "Share of the government budget spent on education.", directory=("unesco_uis",),
    ),
    Indicator(
        "interest_gdp", "Government interest expense (% of GDP)", "Interest", "spending", "percent_of_gdp",
        (_imf("ie", "IMF Public Finances in Modern History"),),
        "Interest paid on government debt relative to GDP — money that funds no public service.",
        caveat="Historical dataset; may lag the latest year.",
        directory=("imf_fiscal_monitor", "imf_fpp", "imf_gfs"), spending_line=True,
    ),
    Indicator(
        "rnd_gdp", "Research & development expenditure (% of GDP)", "R&D", "spending", "percent_of_gdp",
        (_wb("GB.XPD.RSDV.GD.ZS", "UNESCO UIS via World Bank WDI"),),
        "Gross domestic spending on R&D by government, business, universities and non-profits.",
        caveat="Total R&D, not government-only.", directory=("unesco_uis", "oecd"), spending_line=True,
    ),
    Indicator(
        "central_gov_expense_gdp", "Central government expense (% of GDP)", "Central gov. expense", "spending", "percent_of_gdp",
        (_wb("GC.XPN.TOTL.GD.ZS", "World Bank WDI (IMF GFS)"),),
        "Cash payments for operating activities of central government (excludes capital spending).",
        caveat="Central government only; coverage and recency vary.", directory=("imf_gfs", "imf_fiscal_monitor", "boost"),
    ),
    Indicator(
        "wages_pct_expense", "Compensation of employees (% of expense)", "Public wages", "spending", "percent",
        (_wb("GC.XPN.COMP.ZS", "World Bank WDI (IMF GFS)"),),
        "Public-sector wage bill as a share of central government expense.",
        caveat="Central government; coverage and recency vary.", directory=("imf_gfs", "boost"),
    ),
    Indicator(
        "subsidies_pct_expense", "Subsidies and other transfers (% of expense)", "Subsidies & transfers", "spending", "percent",
        (_wb("GC.XPN.TRFT.ZS", "World Bank WDI (IMF GFS)"),),
        "Subsidies, grants and social benefits as a share of central government expense.",
        caveat="Central government; coverage and recency vary.", directory=("imf_gfs", "boost", "imf_gfs_cofog"),
    ),
    # --------------------------------------------------------------- revenue
    Indicator(
        "gov_revenue_gdp", "General government revenue (% of GDP)", "Revenue", "revenue", "percent_of_gdp",
        (_imf("rev", "IMF Public Finances in Modern History"),),
        "Total revenue of general government (taxes, social contributions, grants, other).",
        caveat="Historical dataset; may lag the latest year.",
        directory=("imf_fiscal_monitor", "unu_wider_grd", "oecd"),
    ),
    Indicator(
        "tax_revenue_gdp", "Tax revenue (% of GDP)", "Tax revenue", "revenue", "percent_of_gdp",
        (_wb("GC.TAX.TOTL.GD.ZS", "World Bank WDI (IMF GFS)"),),
        "Compulsory transfers to central government for public purposes.",
        caveat="Central government only.", directory=("unu_wider_grd", "oecd", "imf_gfs"),
    ),
    # --------------------------------------------------------------- economy
    Indicator(
        "gdp_usd", "GDP (current US$)", "GDP", "economy", "usd",
        (_imf("NGDPD", "IMF World Economic Outlook", 1e9), _wb("NY.GDP.MKTP.CD", "World Bank WDI")),
        "Gross domestic product at current prices and market exchange rates.", directory=("imf_weo", "wdi", "nso"),
    ),
    Indicator(
        "gdp_per_capita", "GDP per capita (current US$)", "GDP per capita", "economy", "usd_per_capita",
        (_imf("NGDPDPC", "IMF World Economic Outlook"), _wb("NY.GDP.PCAP.CD", "World Bank WDI")),
        "GDP divided by mid-year population.", directory=("imf_weo", "wdi"),
    ),
    Indicator(
        "gdp_growth", "Real GDP growth (annual %)", "GDP growth", "economy", "percent",
        (_imf("NGDP_RPCH", "IMF World Economic Outlook"), _wb("NY.GDP.MKTP.KD.ZG", "World Bank WDI")),
        "Annual change in inflation-adjusted GDP.", scale="diverging", directory=("imf_weo", "nso"),
    ),
    Indicator(
        "inflation", "Inflation, consumer prices (annual %)", "Inflation", "economy", "percent",
        (_imf("PCPIPCH", "IMF World Economic Outlook"), _wb("FP.CPI.TOTL.ZG", "World Bank WDI")),
        "Annual average change in consumer prices.", directory=("imf_weo", "nso"),
        caveat="Hyperinflation outliers are capped in the colour scale but shown exactly in tooltips.",
    ),
    Indicator(
        "unemployment", "Unemployment rate (%)", "Unemployment", "economy", "percent",
        (_imf("LUR", "IMF World Economic Outlook"), _wb("SL.UEM.TOTL.ZS", "ILO modelled estimate via World Bank WDI")),
        "Share of the labour force without work but available for and seeking employment.",
        caveat="National definitions (IMF) and ILO-harmonised estimates (World Bank) can differ.",
        directory=("ilostat", "nso"),
    ),
    Indicator(
        "current_account_gdp", "Current account balance (% of GDP)", "Current account", "economy", "percent_of_gdp",
        (_imf("BCA_NGDPD", "IMF World Economic Outlook"), _wb("BN.CAB.XOKA.GD.ZS", "World Bank WDI")),
        "Trade, income and transfer balance with the rest of the world. Negative = deficit.",
        scale="diverging", directory=("imf_weo",),
    ),
    # ----------------------------------------------------------------- flows
    Indicator(
        "fdi_inflows_gdp", "Foreign direct investment, net inflows (% of GDP)", "FDI inflows", "flows", "percent_of_gdp",
        (_wb("BX.KLT.DINV.WD.GD.ZS", "World Bank WDI"),),
        "Net new investment by foreign investors acquiring lasting management interest.",
        scale="diverging", directory=("unctad", "wdi"),
    ),
    Indicator(
        "remittances_gdp", "Personal remittances received (% of GDP)", "Remittances", "flows", "percent_of_gdp",
        (_wb("BX.TRF.PWKR.DT.GD.ZS", "World Bank WDI"),),
        "Money sent home by migrant workers.", directory=("knomad", "wdi"),
    ),
    Indicator(
        "oda_received_gni", "Net official development assistance received (% of GNI)", "Aid received", "flows", "percent_of_gni",
        (_wb("DT.ODA.ODAT.GN.ZS", "OECD DAC via World Bank WDI"),),
        "Concessional loans and grants from official donors.", directory=("oecd", "aiddata"),
    ),
    Indicator(
        "reserves_months", "Total reserves (months of imports)", "Reserves cover", "flows", "months",
        (_wb("FI.RES.TOTL.MO", "World Bank WDI"),),
        "How many months of imports foreign-exchange reserves could pay for.", directory=("imf_weo", "wdi"),
    ),
    # ---------------------------------------------------------------- people
    Indicator(
        "population", "Population", "Population", "people", "people",
        (_imf("LP", "IMF World Economic Outlook", 1e6), _wb("SP.POP.TOTL", "World Bank WDI")),
        "Mid-year resident population.", directory=("un_wpp", "nso"),
    ),
    Indicator(
        "life_expectancy", "Life expectancy at birth (years)", "Life expectancy", "people", "years",
        (_wb("SP.DYN.LE00.IN", "UN Population Division via World Bank WDI"),),
        "Years a newborn would live if current mortality patterns stayed the same.", directory=("un_wpp",),
    ),
    Indicator(
        "poverty_rate", "Extreme poverty headcount (% of population)", "Extreme poverty", "people", "percent",
        (_wb("SI.POV.DDAY", "World Bank Poverty & Inequality Platform"),),
        "Share living below the international poverty line.",
        caveat="Survey-based: years differ widely by country. The international line was revised in 2025.",
        directory=("pip",),
    ),
    Indicator(
        "gini", "Gini index (income inequality)", "Inequality (Gini)", "people", "index",
        (_wb("SI.POV.GINI", "World Bank Poverty & Inequality Platform"),),
        "0 = perfect equality, 100 = one person has all income.",
        caveat="Survey-based: years differ widely by country.", directory=("pip",),
    ),
)

INDICATOR_BY_ID = {indicator.id: indicator for indicator in INDICATORS}
SPENDING_LINES = tuple(indicator for indicator in INDICATORS if indicator.spending_line)


SOURCE_DIRECTORY: dict[str, ExternalSource] = {
    source.key: source
    for source in (
        ExternalSource("wdi", "World Development Indicators", "World Bank",
                       "https://databank.worldbank.org/source/world-development-indicators",
                       ("economy", "spending", "people", "flows"), "API + bulk download", "Several times a year"),
        ExternalSource("ids", "International Debt Statistics", "World Bank",
                       "https://www.worldbank.org/en/programs/debt-statistics/ids",
                       ("debt",), "API + bulk download", "Annually (December)",
                       "Debtor-by-creditor external debt for ~120 low- and middle-income countries."),
        ExternalSource("imf_weo", "World Economic Outlook database", "IMF",
                       "https://www.imf.org/en/Publications/WEO/weo-database",
                       ("economy", "debt"), "Download + DataMapper API", "April and October",
                       "Includes current-year estimates and five-year projections."),
        ExternalSource("imf_datamapper", "IMF DataMapper", "IMF", "https://www.imf.org/external/datamapper/",
                       ("economy", "debt", "spending", "revenue"), "Open JSON API", "With each WEO / Fiscal Monitor"),
        ExternalSource("imf_fiscal_monitor", "Fiscal Monitor", "IMF", "https://www.imf.org/en/Publications/FM",
                       ("debt", "spending", "revenue"), "Report + statistical tables", "April and October",
                       "Most current general government debt, deficit, revenue and spending estimates."),
        ExternalSource("imf_gdd", "Global Debt Database", "IMF", "https://www.imf.org/external/datamapper/datasets/GDD",
                       ("debt",), "DataMapper API", "Annually", "Public and private debt back to 1950."),
        ExternalSource("imf_fpp", "Public Finances in Modern History", "IMF",
                       "https://www.imf.org/external/datamapper/datasets/FPP",
                       ("spending", "revenue"), "DataMapper API", "Periodically", "Long-run revenue, spending and interest."),
        ExternalSource("imf_gfs_cofog", "Government Expenditure by Function (COFOG)", "IMF Government Finance Statistics",
                       "https://data.imf.org/Datasets/GFS_COFOG",
                       ("spending",), "IMF data portal (SDMX API)", "Annually",
                       "The definitive 'where the money goes' breakdown: defence, health, education, social protection, "
                       "public order, economic affairs, environment, housing, culture, general public services."),
        ExternalSource("dbnomics_cofog", "IMF GFS COFOG mirror (DBnomics)", "DBnomics / Banque de France",
                       "https://db.nomics.world/IMF/GFSCOFOG",
                       ("spending",), "Free REST API, no key", "Mirrors the IMF release",
                       "Easiest programmatic access to COFOG; also mirrors Eurostat gov_10a_exp."),
        ExternalSource("eurostat_cofog", "Government expenditure by function (gov_10a_exp)", "Eurostat",
                       "https://db.nomics.world/Eurostat/gov_10a_exp",
                       ("spending",), "API + download", "Annually (February)", "Detailed COFOG for EU/EEA countries."),
        ExternalSource("imf_gfs", "Government Finance Statistics", "IMF", "https://data.imf.org/",
                       ("spending", "revenue", "debt"), "IMF data portal (SDMX API)", "Annually"),
        ExternalSource("imf_dsa", "Debt Sustainability Analyses (low-income countries)", "IMF / World Bank",
                       "https://www.imf.org/external/pubs/ft/dsa/dsalist.pdf",
                       ("debt",), "PDF list linking country reports", "Monthly",
                       "Risk of debt distress rating for every PRGT-eligible country."),
        ExternalSource("imf_qeds", "Quarterly External Debt Statistics", "World Bank / IMF",
                       "https://www.worldbank.org/en/programs/debt-statistics/qeds",
                       ("debt",), "Download", "Quarterly", "External debt for high-income economies not in IDS."),
        ExternalSource("boost", "BOOST Open Budget Portal", "World Bank",
                       "https://www.worldbank.org/en/programs/boost-portal/country-data",
                       ("spending",), "Download", "Varies by country",
                       "Line-item budget execution data by ministry, program and region."),
        ExternalSource("open_budget", "Open Budget Survey", "International Budget Partnership",
                       "https://internationalbudget.org/open-budget-survey",
                       ("spending", "revenue"), "Download", "Every two years",
                       "Budget transparency scores and links to each government's budget documents."),
        ExternalSource("sipri", "SIPRI Military Expenditure Database", "Stockholm International Peace Research Institute",
                       "https://www.sipri.org/databases/milex", ("spending",), "Download (Excel)", "Annually (April)",
                       "Newer than the World Bank copy; includes US$ amounts and per-capita figures."),
        ExternalSource("who_ghed", "Global Health Expenditure Database", "WHO", "https://apps.who.int/nha/database/",
                       ("spending",), "Download", "Annually (December)"),
        ExternalSource("unesco_uis", "UIS Data Browser", "UNESCO Institute for Statistics",
                       "https://databrowser.uis.unesco.org/", ("spending", "people"), "API + download", "Twice a year"),
        ExternalSource("unu_wider_grd", "Government Revenue Dataset", "UNU-WIDER / ICTD", "https://www.ictd.ac/dataset/grd/",
                       ("revenue",), "Download", "Annually", "Harmonised tax and non-tax revenue for ~200 countries."),
        ExternalSource("oecd", "OECD Data Explorer", "OECD", "https://data-explorer.oecd.org/",
                       ("revenue", "spending", "flows", "debt"), "SDMX API + download", "Continuous",
                       "Revenue Statistics, COFOG, aid (CRS) and government debt for OECD and partner countries."),
        ExternalSource("bis", "Credit to the non-financial sector", "Bank for International Settlements",
                       "https://data.bis.org/", ("debt",), "API + download", "Quarterly"),
        ExternalSource("aiddata", "Global Chinese Development Finance Dataset", "AidData",
                       "https://www.aiddata.org/data/aiddatas-global-chinese-development-finance-dataset-version-3-0",
                       ("debt", "flows"), "Download", "Periodic releases", "Project-level Chinese loans and grants."),
        ExternalSource("bu_china", "Chinese Loans to Africa Database", "Boston University Global Development Policy Center",
                       "https://www.bu.edu/gdp/chinese-loans-to-africa-database/", ("debt",), "Download", "Annually"),
        ExternalSource("unctad", "UNCTADstat", "UNCTAD", "https://unctadstat.unctad.org/", ("flows",), "Download", "Annually"),
        ExternalSource("knomad", "Remittances data", "KNOMAD / World Bank", "https://www.knomad.org/data/remittances",
                       ("flows",), "Download", "Twice a year"),
        ExternalSource("ilostat", "ILOSTAT", "International Labour Organization", "https://ilostat.ilo.org/data/",
                       ("economy",), "API + download", "Continuous"),
        ExternalSource("pip", "Poverty and Inequality Platform", "World Bank", "https://pip.worldbank.org/",
                       ("people",), "API + download", "Several times a year"),
        ExternalSource("un_wpp", "World Population Prospects", "UN DESA Population Division",
                       "https://population.un.org/wpp/", ("people",), "Download", "Every two years"),
        ExternalSource("owid", "Our World in Data", "Global Change Data Lab", "https://ourworldindata.org/government-spending",
                       ("spending", "debt", "economy", "people"), "Download + API", "Continuous",
                       "Long-run charts with documented sources; good for teaching."),
        ExternalSource("dbnomics", "DBnomics", "Banque de France / CEPREMAP", "https://db.nomics.world/",
                       ("debt", "spending", "revenue", "economy", "flows"), "Free REST API, no key", "Continuous",
                       "Aggregates IMF, World Bank, OECD, Eurostat, BIS and 80+ providers behind one API."),
        ExternalSource("nso", "National statistics offices directory", "UN Statistics Division",
                       "https://unstats.un.org/home/nso_sites/", ("economy", "debt", "spending", "people"), "Portal",
                       "Varies", "Usually the most recent official figures, before international databases update."),
    )
}


# Names people actually search for, used for page titles, URLs and tables.
COMMON_NAMES: dict[str, str] = {
    "BOL": "Bolivia", "BRN": "Brunei", "CIV": "Côte d'Ivoire", "COD": "DR Congo", "COG": "Republic of the Congo",
    "CPV": "Cape Verde", "CZE": "Czechia", "FSM": "Micronesia", "GBR": "United Kingdom", "HKG": "Hong Kong",
    "IRN": "Iran", "KNA": "Saint Kitts and Nevis", "KOR": "South Korea", "LAO": "Laos", "MAC": "Macao",
    "MDA": "Moldova", "MKD": "North Macedonia", "PRK": "North Korea", "PSE": "Palestine", "RUS": "Russia",
    "SWZ": "Eswatini", "SYR": "Syria", "TUR": "Turkey", "TWN": "Taiwan", "TZA": "Tanzania", "USA": "United States",
    "VAT": "Vatican City", "VCT": "Saint Vincent and the Grenadines", "VEN": "Venezuela", "VNM": "Vietnam",
    "XKX": "Kosovo", "BHS": "Bahamas", "GMB": "Gambia", "EGY": "Egypt", "YEM": "Yemen", "KGZ": "Kyrgyzstan",
    "SVK": "Slovakia", "STP": "São Tomé and Príncipe", "TLS": "Timor-Leste", "FLK": "Falkland Islands",
}


def country_links(iso3: str, iso2: str | None = None) -> list[tuple[str, str]]:
    """Return (label, url) links for researching one country beyond this app."""
    code = iso3.upper()
    links = [
        ("IMF country page (Article IV, programmes, DSA)", f"https://www.imf.org/en/Countries/{code}"),
        ("IMF DataMapper country profile", f"https://www.imf.org/external/datamapper/profile/{code}"),
    ]
    if iso2:
        links.append(("World Bank country data", f"https://data.worldbank.org/country/{iso2.upper()}"))
    links.extend([
        ("Budget documents (Open Budget Survey)", SOURCE_DIRECTORY["open_budget"].url),
        ("National statistics office", SOURCE_DIRECTORY["nso"].url),
    ])
    return links


def indicators_in(category_id: str) -> list[Indicator]:
    return [indicator for indicator in INDICATORS if indicator.category == category_id]


def markdown_catalog() -> str:
    """Render the catalogue and source directory as Markdown (docs/DATA_SOURCES.md)."""
    lines = [
        "# Data sources",
        "",
        "_Generated by `python -m src.catalog --markdown`. Do not edit by hand — edit `src/catalog.py`._",
        "",
        "Every indicator is fetched live from the listed APIs and cached for 24 hours. When several series share a",
        "definition, the **most recent valid observation wins** for each country. IMF values for the current year are",
        "staff estimates and are flagged; years beyond the current year (projections) are never shown.",
        "",
        "## Indicators",
        "",
    ]
    for category in CATEGORIES:
        lines += [f"### {category.icon} {category.label}", "", category.description, "",
                  "| Indicator | Live series | Unit | Notes | Newer / deeper data |", "|---|---|---|---|---|"]
        for indicator in indicators_in(category.id):
            series = "<br>".join(f"[{s.code}]({s.portal_url}) · {s.label}" for s in indicator.sources)
            deeper = ", ".join(f"[{SOURCE_DIRECTORY[k].name}]({SOURCE_DIRECTORY[k].url})" for k in indicator.directory)
            notes = " ".join(filter(None, [indicator.description, indicator.caveat]))
            lines.append(f"| **{indicator.name}** `{indicator.id}` | {series} | {indicator.unit} | {notes} | {deeper} |")
        lines.append("")
    lines += ["## Where to find newer or more detailed data", "",
              "| Source | Publisher | Topics | Access | Updated | Why use it |", "|---|---|---|---|---|---|"]
    for source in sorted(SOURCE_DIRECTORY.values(), key=lambda item: item.name.casefold()):
        lines.append(f"| [{source.name}]({source.url}) | {source.publisher} | {', '.join(source.topics)} | "
                     f"{source.access} | {source.updated} | {source.note} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the indicator catalogue.")
    parser.add_argument("--markdown", action="store_true", help="Print docs/DATA_SOURCES.md content")
    args = parser.parse_args()
    if args.markdown:
        print(markdown_catalog())
    else:
        for indicator in INDICATORS:
            print(f"{indicator.category:<9} {indicator.id:<24} {', '.join(s.code for s in indicator.sources)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
