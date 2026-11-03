"""debt-atlas — sovereign debt, deficits, government spending and who-owes-whom data for every country.

Quick start::

    import debt_atlas as da

    da.indicators()                                   # what is available
    da.latest("gov_debt_gdp", countries=["Japan", "Italy"])
    da.rank("military_usd", n=10)                     # biggest military spenders
    da.series("inflation", "Argentina", start=2010)   # history, per source
    da.spending("United States")                      # where the money goes, in US$
    da.who_owes("Kenya")                              # Kenya's lenders
    da.owed_by("China")                               # who owes China
    da.profile("Syria")                               # 3-tier national debt profile
    da.download_all("atlas-data")                     # every file + datapackage.json

Every value carries its source, series code and year. Cite the original
providers and this project (see CITATION.cff).
"""

from __future__ import annotations

from typing import Any

from debt_atlas._meta import REPO_URL, SITE_URL, __version__
from debt_atlas.client import Atlas, AtlasError

_default: Atlas | None = None


def default() -> Atlas:
    """The shared client used by the module-level functions."""
    global _default
    if _default is None:
        _default = Atlas()
    return _default


def _delegate(name: str):
    def call(*args: Any, **kwargs: Any) -> Any:
        return getattr(default(), name)(*args, **kwargs)

    call.__name__ = name
    call.__doc__ = getattr(Atlas, name).__doc__
    return call


indicators = _delegate("indicators")
sources = _delegate("sources")
countries = _delegate("countries")
latest = _delegate("latest")
rank = _delegate("rank")
series = _delegate("series")
wide = _delegate("wide")
compare = _delegate("compare")
spending = _delegate("spending")
profile = _delegate("profile")
bilateral = _delegate("bilateral")
who_owes = _delegate("who_owes")
owed_by = _delegate("owed_by")
download_all = _delegate("download_all")
live = _delegate("live")
resolve_country = _delegate("resolve_country")

__all__ = [
    "Atlas", "AtlasError", "__version__", "REPO_URL", "SITE_URL", "default",
    "indicators", "sources", "countries", "latest", "rank", "series", "wide", "compare", "spending",
    "profile", "bilateral", "who_owes", "owed_by", "download_all", "live", "resolve_country",
]
