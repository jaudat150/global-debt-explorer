"""Command line: ``debt-atlas latest gov_debt_gdp --countries Japan USA``."""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from debt_atlas import Atlas, AtlasError, __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="debt-atlas", description="Global Debt & Fiscal Atlas data from the command line.")
    parser.add_argument("--version", action="version", version=f"debt-atlas {__version__}")
    parser.add_argument("--source", help="Base URL or folder of the export files")
    parser.add_argument("--csv", action="store_true", help="Print CSV instead of a table")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("indicators", help="List indicators")
    p = sub.add_parser("latest", help="Latest value per country"); p.add_argument("indicator"); p.add_argument("--countries", nargs="*")
    p = sub.add_parser("rank", help="Top countries"); p.add_argument("indicator"); p.add_argument("-n", type=int, default=10); p.add_argument("--lowest", action="store_true")
    p = sub.add_parser("series", help="History"); p.add_argument("indicator"); p.add_argument("countries", nargs="+"); p.add_argument("--start", type=int)
    p = sub.add_parser("spending", help="Where the money goes (US$)"); p.add_argument("country")
    p = sub.add_parser("who-owes", help="A country's lenders"); p.add_argument("country")
    p = sub.add_parser("owed-by", help="Who owes a country"); p.add_argument("country")
    p = sub.add_parser("profile", help="National debt profile"); p.add_argument("country")
    p = sub.add_parser("download", help="Save every file"); p.add_argument("folder")
    args = parser.parse_args(argv)

    atlas = Atlas(args.source) if args.source else Atlas()
    try:
        if args.command == "indicators":
            result = atlas.indicators()[["id", "category", "unit", "name"]]
        elif args.command == "latest":
            result = atlas.latest(args.indicator, args.countries)[["iso3", "country", "value", "year", "is_estimate", "source"]]
        elif args.command == "rank":
            result = atlas.rank(args.indicator, args.n, lowest=args.lowest)[["iso3", "country", "value", "year", "source"]]
        elif args.command == "series":
            result = atlas.series(args.indicator, args.countries, start=args.start)[["iso3", "year", "value", "series_code"]]
        elif args.command == "spending":
            result = atlas.spending(args.country)[["item", "usd", "pct_gdp", "usd_per_person", "year", "scope"]]
        elif args.command == "who-owes":
            result = atlas.who_owes(args.country)
        elif args.command == "owed-by":
            result = atlas.owed_by(args.country)
        elif args.command == "profile":
            result = pd.Series(atlas.profile(args.country)).to_frame("value")
        else:
            print(f"Saved to {atlas.download_all(args.folder)}")
            return 0
    except AtlasError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.csv:
        result.to_csv(sys.stdout, index=False)
    else:
        with pd.option_context("display.max_rows", 300, "display.width", 160, "display.max_colwidth", 60):
            print(result.to_string(index=args.command == "profile"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
