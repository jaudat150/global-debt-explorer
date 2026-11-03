WINDOWS QUICK START (Python 3.12)

1) Open CMD in this folder (the one containing app.py and requirements.txt).
2) If this project already has .venv, keep it. Otherwise create it:
   python -m venv .venv
3) Activate:
   .venv\Scripts\activate.bat
4) Install/refresh dependencies:
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
5) Test the IDS API with one debtor, using a separate cache so the main cache
   cannot be overwritten by this limited test:
   python -m src.fetcher --debtor AGO --cache data\ids_smoke_test.json
6) If that test succeeds, fetch the full dataset:
   python -m src.fetcher
7) Start the dashboard:
   python -m streamlit run app.py

Do not copy .venv from another project. Python 3.12 is supported.

The fetcher uses the World Bank IDS four-dimensional API (source 6). It selects
creditor counterpart-area IDs with semicolon-delimited codes (not
counterpart-area/all), paginates results, and retries transient errors.
By default it fetches years 2000 through the current year. Use
`python -m src.fetcher --start-year 1960` for all available historical years.


API batching note: the fetcher uses at most 50 creditor counterpart-area IDs per request. It queries `time/all` because IDS time dimension IDs use the `YR####` form, then filters the requested year window locally. If the IDS API rejects a batch with HTTP 400 or 404, the batch is automatically split. A single non-queryable area is explicitly logged and skipped so other creditors for that debtor can still be retained.
