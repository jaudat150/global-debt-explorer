# Contributing

Thank you for helping make public-finance data easier to explore. Students, researchers, journalists and developers
are all welcome — you do not need to be an economist or an expert programmer.

## Ways to help (no code needed)

- **Report a wrong or stale number.** Open a *Data issue* with the country, indicator, the value shown, and a link
  to a better source.
- **Suggest a data source** for the "Find newer data" directory.
- **Translate** labels or improve explanations for students.
- **Share how you used it** — teaching, a paper, an article. It helps others find the project.

## Development setup

```bash
git clone https://github.com/Jaudat150/global-debt-explorer.git
cd global-debt-explorer
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
streamlit run app.py
```

The test suite needs no network: every API call is mocked.

## Add an indicator (usually ~10 lines)

1. Find the series code on [data.worldbank.org](https://data.worldbank.org) or the
   [IMF DataMapper](https://www.imf.org/external/datamapper/).
2. Append an `Indicator(...)` to `INDICATORS` in `src/catalog.py`: id, name, category, unit, live series, a
   one-sentence description, any caveat, and at least one `directory` key pointing to newer or deeper data.
   If two series measure the **same definition**, list both — the freshest value per country wins.
   Never combine series with different definitions (e.g. central vs general government) in one indicator.
3. Check it: `python -m src.indicator_data show <your_id>`.
4. Regenerate the docs: `python -m src.catalog --markdown > docs/DATA_SOURCES.md`.
5. Run the tests and open a pull request.

## Add a curated estimate for a reporting gap

Edit `LOCAL_FALLBACK_DB` in `src/processor.py`. Every entry needs a value or range, metric definition,
observation year, **source URL**, source publication date, review date and a caveat. Estimates without a
citable source will not be merged.

## Ground rules for data

- Missing is never zero. Never fill a gap with an invented value.
- Every number shown must carry its source, series code and year.
- Do not rank or add values with different units or definitions.
- Projections are never shown as observations.

## Pull requests

- One topic per PR; include tests for new logic.
- Run `python -m unittest discover -s tests` before pushing (CI runs it on Python 3.11–3.13).
- Describe what changed for users, not only for code.

By contributing you agree that your contribution is licensed under the MIT License and that you will follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Maintainers and releases

How the team splits work, reviews pull requests, publishes releases (Zenodo DOI + PyPI) and grows the project's reach:
see [docs/TEAM_PLAYBOOK.md](docs/TEAM_PLAYBOOK.md).
