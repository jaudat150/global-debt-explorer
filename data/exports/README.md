# Open-data exports

Rebuilt every week by `.github/workflows/refresh_indicators.yml` (`python -m src.exports build`).
Read any file straight from GitHub, e.g.

```python
import pandas as pd
pd.read_csv("https://raw.githubusercontent.com/Jaudat150/global-debt-explorer/main/data/exports/latest_values.csv")
```

| File | Contents |
|---|---|
| `countries.csv` | ISO codes, names, coordinates |
| `latest_values.csv` | Latest value of every indicator for every country, with provenance |
| `timeseries.csv.gz` | Every year of every upstream series (one row per source) |
| `spending_usd.csv` | Where each country spends its money, in current US$, with method and sources |
| `national_debt_profiles.csv` | 3-tier national debt profile for every country |
| `bilateral_debt.csv.gz` | Who owes whom (World Bank IDS bilateral debt, all years) |
| `datapackage.json` | Frictionless Data Package: every column documented |
| `catalog.json` | Every indicator, its upstream API URLs and where to find newer data |

The files appear after the first workflow run. Data remain under each provider's terms; cite the provider in each row's `source` column.
