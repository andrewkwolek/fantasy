"""Command line entry point: scrape, serve, report."""
from __future__ import annotations

import argparse
import logging
import sys

from . import config as cfg


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_scrape(args) -> int:
    from .scrape import scrape_league

    conf = cfg.load_config()
    seasons = args.seasons or None
    print(f"Scraping league {conf.league_id} "
          f"({'private' if conf.is_private else 'public'})...")
    reports = scrape_league(conf, seasons=seasons, refresh=args.refresh)

    print("\n" + "=" * 62)
    for report in reports:
        print("  " + report.summary())
        for warning in report.warnings[:3]:
            print(f"      ! {warning[:100]}")
    print("=" * 62)
    print(f"\nDatabase: {cfg.DB_PATH}")
    print("Next:  python3 -m fantasy.cli serve")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    if not cfg.DB_PATH.exists():
        print("No database yet. Run:  python3 -m fantasy.cli scrape", file=sys.stderr)
        return 1
    print(f"Serving http://{args.host}:{args.port}")
    uvicorn.run(
        "fantasy.web.app:app", host=args.host, port=args.port,
        reload=args.reload, log_level="warning",
    )
    return 0


def cmd_report(args) -> int:
    from .analytics.core import LeagueData
    from .analytics.reports import week_report
    from .store import db

    with db.session() as conn:
        try:
            median_from = cfg.load_config().median_scoring_from
        except SystemExit:
            median_from = None
        data = LeagueData(conn, median_scoring_from=median_from)
        season = args.season or data.current_season
        if season is None:
            print("No data. Run scrape first.", file=sys.stderr)
            return 1
        period = args.week or data.latest_completed_period(season)
        report = week_report(data, season, period)
        if not report:
            print(f"No completed games for {season} week {period}.", file=sys.stderr)
            return 1

        print(f"\n  {season} — Week {period}")
        print("  " + "-" * 50)
        for line in report["headlines"]:
            print(f"  • {line}")
        print(f"\n  League average: {report['average_score']}")
        print("\n  Scores")
        # report["games"] holds one row per team; print each matchup once.
        shown: set[str] = set()
        for game in report["games"]:
            if game["team"] in shown:
                continue
            shown.add(game["team"])
            shown.add(game["opponent"])
            print(f"    {game['team'][:24]:<24}{game['points']:>7.1f}   "
                  f"{game['opponent'][:24]:<24}{game['opponent_points']:>7.1f}")
    return 0


def cmd_export(args) -> int:
    """Render the whole site to static files."""
    from .export import build

    result = build(
        args.out,
        base_path=args.base_path,
        noindex=not args.allow_indexing,
        cname=args.cname,
    )
    print(f"\n  {result['pages']} pages -> {result['out']}")
    print(f"  base path: {result['base_path']}   size: {result['bytes'] / 1_000_000:.1f} MB")
    if result["cname"]:
        print(f"  custom domain: {result['cname']}  (CNAME written)")
    if result.get("unprefixed"):
        print("\n  Links that would leave the site (missing the base path):")
        for path, urls in list(result["unprefixed"].items())[:5]:
            print(f"    {path}: {', '.join(urls[:4])}")
        return 1
    if result["failed"]:
        print(f"\n  {len(result['failed'])} page(s) failed:")
        for path, err in result["failed"][:10]:
            print(f"    {path}: {err[:110]}")
        return 1
    print("\n  Publish to GitHub Pages:")
    print(f"    cd {result['out']} && git init -b main && git add -A")
    print('    git commit -m "Publish league site"')
    print("    git remote add origin git@github.com:<you>/<repo>.git && git push -f origin main")
    print("  then Settings -> Pages -> Source: Deploy from a branch -> main / (root)")
    return 0


def cmd_demo(args) -> int:
    """Populate the database with a synthetic league (for offline testing)."""
    from .demo import build_demo_league

    seasons = build_demo_league(years=args.years, teams=args.teams)
    print(f"Built demo league: {seasons} seasons -> {cfg.DB_PATH}")
    print("Run:  python3 -m fantasy.cli serve")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fantasy", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scrape = sub.add_parser("scrape", help="Pull league data from ESPN")
    p_scrape.add_argument("--seasons", type=int, nargs="*",
                          help="Specific years (default: auto-discover all)")
    p_scrape.add_argument("--refresh", action="store_true",
                          help="Ignore the local raw cache and re-fetch")
    p_scrape.set_defaults(func=cmd_scrape)

    p_serve = sub.add_parser("serve", help="Run the website")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_report = sub.add_parser("report", help="Print a weekly recap")
    p_report.add_argument("--season", type=int)
    p_report.add_argument("--week", type=int)
    p_report.set_defaults(func=cmd_report)

    p_export = sub.add_parser("export", help="Render the site to static HTML")
    p_export.add_argument("--out", default="dist", help="Output directory (default: dist)")
    p_export.add_argument(
        "--base-path", default="",
        help="Sub-path the site is served from, e.g. /my-league for "
             "https://user.github.io/my-league/. Omit for a root/custom domain.")
    p_export.add_argument(
        "--cname", default=None,
        help="Custom domain, e.g. league.example.com. Writes the CNAME file "
             "GitHub Pages needs; the build would otherwise delete it. With a "
             "custom domain the site is served from the root, so omit --base-path.")
    p_export.add_argument(
        "--allow-indexing", action="store_true",
        help="Drop the noindex tag. GitHub Pages sites are public; by default "
             "the export asks search engines not to index it.")
    p_export.set_defaults(func=cmd_export)

    p_demo = sub.add_parser("demo", help="Generate a synthetic league for testing")
    p_demo.add_argument("--years", type=int, default=6)
    p_demo.add_argument("--teams", type=int, default=10)
    p_demo.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
