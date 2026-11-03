"""The ``Atlas`` client: every dataset of the Global Debt & Fiscal Atlas as pandas DataFrames.

Data comes from the project's published exports (rebuilt weekly from the World
Bank and IMF APIs, validated, with provenance on every row). Files are cached
on disk for 24 hours. Every method accepts countries as ISO3 (``"KEN"``), ISO2
(``"KE"``) or names (``"Kenya"``).

    >>> import debt_atlas as da
    >>> da.latest("gov_debt_gdp", countries=["Japan", "USA"])
    >>> da.who_owes("Kenya")            # who Kenya borrowed from
    >>> da.spending("United States")    # where the money goes, in US$
"""

from __future__ import annotations

import gzip
import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from debt_atlas._meta import RAW_BASE, __version__

FILES = {
    "countries": "countries.csv",
    "latest": "latest_values.csv",
    "timeseries": "timeseries.csv.gz",
    "spending": "spending_usd.csv",
    "profiles": "national_debt_profiles.csv",
    "bilateral": "bilateral_debt.csv.gz",
    "catalog": "catalog.json",
    "datapackage": "datapackage.json",
}
CACHE_TTL_SECONDS = 24 * 3600
USER_AGENT = f"debt-atlas/{__version__} (+https://pypi.org/project/debt-atlas/)"


class AtlasError(RuntimeError):
    """Raised when data cannot be loaded or an argument is not recognised."""


def _default_cache_dir() -> Path:
    base = os.getenv("DEBT_ATLAS_CACHE") or os.getenv("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "debt_atlas"


def _local_repo_exports() -> Path | None:
    """When running inside the project repository, use its data/exports folder as a fallback."""
    candidate = Path(__file__).resolve().parents[1] / "data" / "exports"
    return candidate if (candidate / FILES["latest"]).exists() else None


class Atlas:
    """Access the atlas data.

    Args:
        source: Base URL or local folder holding the export files. Defaults to the
            project's GitHub-published exports, falling back to a local
            ``data/exports`` folder when available.
        cache_dir: Where downloaded files are cached (``~/.cache/debt_atlas``).
            ``None`` disables the disk cache.
        refresh: Ignore the cache and download again.
    """

    def __init__(self, source: str | os.PathLike[str] | None = None, *, cache_dir: str | os.PathLike[str] | None | bool = True,
                 refresh: bool = False, timeout: float = 60.0) -> None:
        self.source = str(source) if source is not None else (os.getenv("DEBT_ATLAS_SOURCE") or RAW_BASE)
        self.cache_dir = _default_cache_dir() if cache_dir is True else (Path(cache_dir) if cache_dir else None)
        self.refresh = refresh
        self.timeout = timeout
        self._frames: dict[str, Any] = {}

    # ------------------------------------------------------------------ IO
    def _is_remote(self) -> bool:
        return self.source.startswith(("http://", "https://"))

    def _fetch_bytes(self, filename: str) -> bytes:
        if not self._is_remote():
            path = Path(self.source) / filename
            if not path.exists():
                raise AtlasError(f"{path} not found. Run `python -m src.exports build` or pass another source.")
            return path.read_bytes()

        cached = self.cache_dir / filename if self.cache_dir else None
        if cached and cached.exists() and not self.refresh and time.time() - cached.stat().st_mtime < CACHE_TTL_SECONDS:
            return cached.read_bytes()
        url = f"{self.source.rstrip('/')}/{filename}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if cached and cached.exists():
                return cached.read_bytes()  # stale cache beats no data
            local = _local_repo_exports()
            if local and (local / filename).exists():
                return (local / filename).read_bytes()
            raise AtlasError(f"Could not download {url}: {exc}") from exc
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(payload)
        return payload

    def _frame(self, key: str) -> pd.DataFrame:
        if key not in self._frames:
            filename = FILES[key]
            raw = self._fetch_bytes(filename)
            if filename.endswith(".gz"):
                raw = gzip.decompress(raw)
            frame = pd.read_csv(io.BytesIO(raw), keep_default_na=False, na_values=[""])
            if "iso3" in frame.columns:
                frame["iso3"] = frame["iso3"].astype(str)
            self._frames[key] = frame
        return self._frames[key]

    def _json(self, key: str) -> dict[str, Any]:
        if key not in self._frames:
            self._frames[key] = json.loads(self._fetch_bytes(FILES[key]).decode("utf-8"))
        return self._frames[key]

    # ------------------------------------------------------------ lookups
    def resolve_country(self, country: str) -> str:
        """Turn ``"Kenya"``, ``"KE"`` or ``"ken"`` into ``"KEN"``."""
        text = str(country).strip()
        countries = self._frame("countries")
        upper = text.upper()
        if upper in set(countries["iso3"]):
            return upper
        by_iso2 = countries.loc[countries["iso2"].astype(str).str.upper() == upper, "iso3"]
        if len(by_iso2):
            return str(by_iso2.iloc[0])
        names = countries["name"].astype(str)
        exact = countries.loc[names.str.casefold() == text.casefold(), "iso3"]
        if len(exact):
            return str(exact.iloc[0])
        partial = countries.loc[names.str.casefold().str.contains(text.casefold(), regex=False), "iso3"]
        if len(partial) == 1:
            return str(partial.iloc[0])
        hint = f" Did you mean: {', '.join(countries.loc[partial.index, 'name'].head(5))}?" if len(partial) else ""
        raise AtlasError(f"Unknown country {country!r}.{hint}")

    def _codes(self, countries: str | Iterable[str] | None) -> list[str] | None:
        if countries is None:
            return None
        if isinstance(countries, str):
            countries = [countries]
        return [self.resolve_country(item) for item in countries]

    def _check_indicator(self, indicator: str) -> None:
        known = {item["id"] for item in self._json("catalog")["indicators"]}
        if indicator not in known:
            close = [k for k in sorted(known) if indicator.split("_")[0] in k][:6]
            raise AtlasError(f"Unknown indicator {indicator!r}. See indicators().{' Close matches: ' + ', '.join(close) if close else ''}")

    def _with_names(self, frame: pd.DataFrame) -> pd.DataFrame:
        if "country" in frame.columns or "iso3" not in frame.columns:
            return frame
        names = self._frame("countries")[["iso3", "name"]].rename(columns={"name": "country"})
        merged = frame.merge(names, on="iso3", how="left")
        columns = ["iso3", "country"] + [c for c in merged.columns if c not in ("iso3", "country")]
        return merged[columns]

    # ------------------------------------------------------------- catalog
    def indicators(self, category: str | None = None) -> pd.DataFrame:
        """Every indicator: id, name, category, unit, description and upstream series."""
        rows = [
            {"id": i["id"], "name": i["name"], "category": i["category"], "unit": i["unit"],
             "description": i["description"], "caveat": i["caveat"],
             "series": ", ".join(s["code"] for s in i["sources"]),
             "providers": ", ".join(sorted({s["provider"] for s in i["sources"]}))}
            for i in self._json("catalog")["indicators"]
        ]
        frame = pd.DataFrame(rows)
        return frame[frame["category"] == category].reset_index(drop=True) if category else frame

    def sources(self, topic: str | None = None) -> pd.DataFrame:
        """Recommended external sources for newer or more detailed data."""
        frame = pd.DataFrame(self._json("catalog")["source_directory"])
        if topic:
            frame = frame[frame["topics"].apply(lambda topics: topic in topics)]
        return frame.reset_index(drop=True)

    def countries(self) -> pd.DataFrame:
        """ISO3, ISO2, name and map coordinates for every country."""
        return self._frame("countries").copy()

    # ------------------------------------------------------------- values
    def latest(self, indicator: str, countries: str | Iterable[str] | None = None) -> pd.DataFrame:
        """Latest value per country, with year, estimate flag and source."""
        self._check_indicator(indicator)
        frame = self._frame("latest")
        frame = frame[frame["indicator_id"] == indicator]
        codes = self._codes(countries)
        if codes is not None:
            frame = frame[frame["iso3"].isin(codes)]
        return self._with_names(frame).sort_values("value", ascending=False).reset_index(drop=True)

    def rank(self, indicator: str, n: int = 10, *, lowest: bool = False) -> pd.DataFrame:
        """Top (or bottom) ``n`` countries for an indicator."""
        frame = self.latest(indicator)
        return (frame.nsmallest(n, "value") if lowest else frame.nlargest(n, "value")).reset_index(drop=True)

    def series(self, indicator: str, countries: str | Iterable[str] | None = None, *, start: int | None = None,
               end: int | None = None, provider: str | None = None) -> pd.DataFrame:
        """Full history, one row per country, year and upstream series (nothing merged or filled)."""
        self._check_indicator(indicator)
        frame = self._frame("timeseries")
        frame = frame[frame["indicator_id"] == indicator]
        codes = self._codes(countries)
        if codes is not None:
            frame = frame[frame["iso3"].isin(codes)]
        if start is not None:
            frame = frame[frame["year"] >= start]
        if end is not None:
            frame = frame[frame["year"] <= end]
        if provider:
            frame = frame[frame["provider"] == provider]
        return self._with_names(frame).sort_values(["iso3", "series_code", "year"]).reset_index(drop=True)

    def wide(self, indicator: str, countries: str | Iterable[str] | None = None, **kwargs: Any) -> pd.DataFrame:
        """History as a year × country table (first-listed upstream series wins per cell)."""
        frame = self.series(indicator, countries, **kwargs)
        order = [s.strip() for s in self.indicators().set_index("id").loc[indicator, "series"].split(",")]
        frame = frame.assign(_rank=frame["series_code"].map({code: i for i, code in enumerate(order)}))
        frame = frame.sort_values("_rank").drop_duplicates(["iso3", "year"])
        return frame.pivot(index="year", columns="iso3", values="value").sort_index()

    def compare(self, countries: str | Iterable[str], indicators: Iterable[str]) -> pd.DataFrame:
        """Latest value of several indicators for several countries, side by side."""
        codes = self._codes(countries) or []
        frames = []
        for indicator in indicators:
            values = self.latest(indicator, codes).set_index("iso3")
            frames.append(values["value"].rename(indicator))
        table = pd.concat(frames, axis=1).reindex(codes)
        names = self._frame("countries").set_index("iso3")["name"]
        table.index = [f"{names.get(code, code)} ({code})" for code in table.index]
        return table

    # ------------------------------------------------------------ themes
    def spending(self, country: str | None = None, item: str | None = None) -> pd.DataFrame:
        """Where governments spend their money, in current US$, per person and % of GDP.

        Items: military, health, education, social, wages, interest, rnd, total.
        """
        frame = self._frame("spending")
        if country is not None:
            frame = frame[frame["iso3"] == self.resolve_country(country)]
        if item is not None:
            frame = frame[frame["item_id"] == item]
        return frame.sort_values("usd", ascending=False).reset_index(drop=True)

    def profile(self, country: str) -> dict[str, Any]:
        """National debt profile (World Bank → IMF → local fallback) for one country."""
        code = self.resolve_country(country)
        rows = self._frame("profiles")
        row = rows[rows["iso3"] == code]
        if row.empty:
            raise AtlasError(f"No profile for {code}")
        return {key: (None if pd.isna(value) else value) for key, value in row.iloc[0].to_dict().items()}

    def bilateral(self, debtor: str | None = None, creditor: str | None = None, year: int | None = None) -> pd.DataFrame:
        """World Bank IDS bilateral debt (public and publicly guaranteed), any filter combination."""
        frame = self._frame("bilateral")
        if debtor is not None:
            frame = frame[frame["debtor_iso3"] == self.resolve_country(debtor)]
        if creditor is not None:
            frame = frame[frame["creditor_iso3"] == self.resolve_country(creditor)]
        if year is not None:
            frame = frame[frame["year"] == int(year)]
        return frame.sort_values(["year", "amount_usd"], ascending=[False, False]).reset_index(drop=True)

    def who_owes(self, country: str, year: int | None = None) -> pd.DataFrame:
        """Who ``country`` borrowed from (its lenders), latest year by default, with shares."""
        frame = self.bilateral(debtor=country)
        return self._flows(frame, year, counterpart=("creditor_name", "creditor_iso3"), label="lender")

    def owed_by(self, country: str, year: int | None = None) -> pd.DataFrame:
        """Which borrowers owe ``country`` (it is the lender), latest year by default, with shares."""
        frame = self.bilateral(creditor=country)
        return self._flows(frame, year, counterpart=("debtor_name", "debtor_iso3"), label="borrower")

    @staticmethod
    def _flows(frame: pd.DataFrame, year: int | None, *, counterpart: tuple[str, str], label: str) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=[label, f"{label}_iso3", "amount_usd", "share_pct", "year"])
        chosen = int(year) if year is not None else int(frame["year"].max())
        rows = frame[frame["year"] == chosen]
        grouped = rows.groupby(list(counterpart), as_index=False, dropna=False)["amount_usd"].sum()
        grouped = grouped[grouped["amount_usd"] > 0].sort_values("amount_usd", ascending=False)
        total = grouped["amount_usd"].sum()
        grouped["share_pct"] = grouped["amount_usd"] / total * 100 if total else 0.0
        grouped["year"] = chosen
        return grouped.rename(columns={counterpart[0]: label, counterpart[1]: f"{label}_iso3"}).reset_index(drop=True)

    # ------------------------------------------------------------ export
    def download_all(self, folder: str | os.PathLike[str]) -> Path:
        """Save every file (CSV + catalog.json + datapackage.json) to ``folder``."""
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        for filename in FILES.values():
            (target / filename).write_bytes(self._fetch_bytes(filename))
        return target

    def live(self, series_code: str, *, provider: str | None = None) -> pd.DataFrame:
        """Fetch one upstream series straight from the World Bank or IMF API (no cache, no snapshot)."""
        from debt_atlas.live import fetch_series

        if provider is None:
            matches = [s["provider"] for i in self._json("catalog")["indicators"] for s in i["sources"] if s["code"] == series_code]
            provider = matches[0] if matches else ("imf" if "." not in series_code else "worldbank")
        return self._with_names(fetch_series(series_code, provider=provider, timeout=self.timeout))
