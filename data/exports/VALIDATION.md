# Data validation — 2026-11-03 11:18 UTC

**16 passed · 0 warnings · 0 failures**

## Reference

- PASS · USA military_usd = US$954.39bn (2025) · expected US$850.00bn–US$1.15tn · SIPRI: US$997bn in 2024
- PASS · CHN military_usd = US$335.52bn (2025) · expected US$220.00bn–US$380.00bn · SIPRI estimate: ~US$314bn in 2024
- PASS · USA gdp_usd = US$32.64tn (2026) · expected US$26.00tn–US$37.00tn · IMF WEO: US$29tn (2024), ~US$32.6tn (2026 est.)
- PASS · CHN gdp_usd = US$21.55tn (2026) · expected US$17.00tn–US$24.00tn · IMF WEO: US$19.6tn (2025), US$21.6tn (2026 est.)
- PASS · CHN population = 1.402bn (2026) · expected 1.380bn–1.430bn · UN WPP: ~1.41bn
- PASS · IND population = 1.477bn (2026) · expected 1.380bn–1.500bn · UN WPP: ~1.45bn
- PASS · JPN gov_debt_gdp = 199.4 (2026) · expected 180.0–250.0 · IMF: ~207% of GDP, falling toward ~190% (F&D, Sep 2026)
- PASS · USA gov_debt_gdp = 126.8 (2026) · expected 100.0–135.0 · IMF WEO: ~120% of GDP
- PASS · DEU gov_debt_gdp = 64.6 (2026) · expected 55.0–75.0 · IMF WEO: ~63% of GDP
- PASS · SAU gov_debt_gdp = 35.4 (2026) · expected 15.0–45.0 · IMF WEO: ~30% of GDP
- PASS · JPN life_expectancy = 84.0 (2024) · expected 82.0–87.0 · UN WPP: ~84 years
- PASS · NOR gdp_per_capita = US$115,188 (2026) · expected US$75,000–US$135,000 · IMF WEO: US$94k (2025), US$115k (2026 est.)

## Consistency

- PASS · 0 values dated after 2026 (projections must never appear)
- PASS · 0 negative spending lines
- PASS · Known zeros: CRI military — Costa Rica abolished its army in 1948; ISL military — Iceland has no standing armed forces; PAN military — Panama abolished its army in 1990
- PASS · Spending tab military figures match SIPRI series (165/165)

## Series coverage

| Indicator | Series | Countries | Newest | Status |
|---|---|---|---|---|
| gov_debt_gdp | GGXWDG_NGDP | 194 | 2026 | PASS  |
| gov_debt_gdp | GG_DEBT_GDP | 88 | 2024 | PASS  |
| fiscal_balance_gdp | GGXCNL_NGDP | 197 | 2026 | PASS  |
| external_debt_usd | DT.DOD.DECT.CD | 121 | 2024 | PASS  |
| external_debt_gni | DT.DOD.DECT.GN.ZS | 121 | 2024 | PASS  |
| debt_service_exports | DT.TDS.DECT.EX.ZS | 119 | 2024 | PASS  |
| interest_pct_revenue | GC.XPN.INTP.RV.ZS | 154 | 2024 | PASS  |
| private_debt_gdp | Privatedebt_all | 44 | 2024 | PASS  |
| gov_expenditure_gdp | exp | 151 | 2024 | PASS  |
| military_gdp | MS.MIL.XPND.GD.ZS | 164 | 2025 | PASS  |
| military_usd | MS.MIL.XPND.CD | 165 | 2025 | PASS  |
| military_pct_gov | MS.MIL.XPND.ZS | 161 | 2025 | PASS  |
| health_gov_gdp | SH.XPD.GHED.GD.ZS | 193 | 2024 | PASS  |
| health_gov_per_capita | SH.XPD.GHED.PC.CD | 193 | 2024 | PASS  |
| health_pct_gov | SH.XPD.GHED.GE.ZS | 193 | 2024 | PASS  |
| education_gdp | SE.XPD.TOTL.GD.ZS | 199 | 2025 | PASS  |
| education_pct_gov | SE.XPD.TOTL.GB.ZS | 195 | 2025 | PASS  |
| interest_gdp | ie | 151 | 2024 | PASS  |
| rnd_gdp | GB.XPD.RSDV.GD.ZS | 147 | 2024 | PASS  |
| central_gov_expense_gdp | GC.XPN.TOTL.GD.ZS | 156 | 2024 | PASS  |
| wages_pct_expense | GC.XPN.COMP.ZS | 154 | 2024 | PASS  |
| subsidies_pct_expense | GC.XPN.TRFT.ZS | 151 | 2024 | PASS  |
| gov_revenue_gdp | rev | 151 | 2024 | PASS  |
| tax_revenue_gdp | GC.TAX.TOTL.GD.ZS | 155 | 2024 | PASS  |
| gdp_usd | NGDPD | 197 | 2026 | PASS  |
| gdp_usd | NY.GDP.MKTP.CD | 213 | 2025 | PASS  |
| gdp_per_capita | NGDPDPC | 197 | 2026 | PASS  |
| gdp_per_capita | NY.GDP.PCAP.CD | 213 | 2025 | PASS  |
| gdp_growth | NGDP_RPCH | 197 | 2026 | PASS  |
| gdp_growth | NY.GDP.MKTP.KD.ZG | 213 | 2025 | PASS  |
| inflation | PCPIPCH | 197 | 2026 | PASS  |
| inflation | FP.CPI.TOTL.ZG | 193 | 2025 | PASS  |
| unemployment | LUR | 118 | 2026 | PASS  |
| unemployment | SL.UEM.TOTL.ZS | 186 | 2025 | PASS  |
| current_account_gdp | BCA_NGDPD | 196 | 2026 | PASS  |
| current_account_gdp | BN.CAB.XOKA.GD.ZS | 195 | 2025 | PASS  |
| fdi_inflows_gdp | BX.KLT.DINV.WD.GD.ZS | 203 | 2025 | PASS  |
| remittances_gdp | BX.TRF.PWKR.DT.GD.ZS | 190 | 2025 | PASS  |
| oda_received_gni | DT.ODA.ODAT.GN.ZS | 145 | 2023 | PASS  |
| reserves_months | FI.RES.TOTL.MO | 174 | 2025 | PASS  |
| population | LP | 197 | 2026 | PASS  |
| population | SP.POP.TOTL | 216 | 2025 | PASS  |
| life_expectancy | SP.DYN.LE00.IN | 216 | 2024 | PASS  |
| poverty_rate | SI.POV.DDAY | 167 | 2025 | PASS  |
| gini | SI.POV.GINI | 167 | 2025 | PASS  |
