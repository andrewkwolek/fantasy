"""Render the whole site to static files for GitHub Pages (or any static host).

Rather than duplicate the rendering logic, this calls the real FastAPI route
functions with a synthetic Request and writes what they return. That keeps the
static build and the live server permanently in step -- there is one set of
templates and one set of analytics.

Two details make the output work under GitHub Pages:

* **Base path.** A project site is served from `https://<user>.github.io/<repo>/`,
  so every absolute `/...` link has to be rewritten to `/<repo>/...`. Pass
  `--base-path /<repo>`. A user/organisation site (`<user>.github.io`) or a
  custom domain is served from the root and needs no prefix.
* **Directory pages.** `/season/2025` is written as `season/2025/index.html`,
  which is what a static host serves for that URL.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Callable, Iterable

from starlette.requests import Request

from .analytics.core import LeagueData
from .analytics.draft import draft_board
from .config import load_config
from .store import db
from .web import app as webapp

# Absolute href/src that need the base-path prefix. Skips protocol-relative
# ("//cdn...") and any already-prefixed link.
_ABS_URL = re.compile(r'(href|src)="/(?!/)')

NOINDEX = (
    '<meta name="robots" content="noindex, nofollow">\n'
    '<meta name="referrer" content="no-referrer">'
)


def _request(path: str) -> Request:
    """Minimal ASGI scope -- templates only read `request.url.path`."""
    return Request(
        {
            "type": "http", "http_version": "1.1", "method": "GET",
            "scheme": "https", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "headers": [],
            "server": ("localhost", 443), "client": ("127.0.0.1", 0),
            "app": webapp.app,
        }
    )


def _render(fn: Callable, path: str, **kwargs) -> str:
    response = fn(request=_request(path), **kwargs)
    return response.body.decode("utf-8")


# Any attribute holding a site-absolute URL. Used only to *detect* leftovers --
# the rewriter itself is deliberately limited to href/src.
_ANY_ABS = re.compile(r'(?:href|src|action|value|data-[a-z-]+)="(/(?!/)[^"]*)"')


def _finalize(html: str, base_path: str, *, noindex: bool) -> str:
    if base_path:
        html = _ABS_URL.sub(rf'\1="{base_path}/', html)
        # JS navigation cannot use a rewritten attribute, so it reads this.
        html = html.replace(
            '<meta name="base-path" content="">',
            f'<meta name="base-path" content="{base_path}">', 1,
        )
    if noindex:
        html = html.replace("<head>", "<head>\n" + NOINDEX, 1)
    return html


def _unprefixed(html: str, base_path: str) -> list[str]:
    """Site-absolute URLs that never got the base-path prefix.

    Attribute rewriting only covers href/src, so anything else carrying a path
    (an <option value>, a form action) would silently point off-site. Catch it
    at build time rather than in someone's browser.
    """
    if not base_path:
        return []
    return sorted({
        url for url in _ANY_ABS.findall(html)
        if not url.startswith(base_path + "/") and url != base_path
    })


def _scrub(value, slugs: dict[str, str]):
    """Replace ESPN member GUIDs with slugs anywhere in an exported structure.

    `franchise_id` is the manager's ESPN member GUID, which is also their SWID
    cookie -- half of the API credential pair and a permanent account handle. It
    must not appear in files that get published, so the JSON carries the same
    name slug the URLs use.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in ("franchise_id", "winner_franchise", "champion_franchise") \
                    and isinstance(item, str):
                out[key] = slugs.get(item, "")
            else:
                out[key] = _scrub(item, slugs)
        return out
    if isinstance(value, list):
        return [_scrub(v, slugs) for v in value]
    if isinstance(value, str) and value in slugs:
        return slugs[value]
    return value


def _write_json(path: Path, payload, slugs: dict[str, str]) -> None:
    path.write_text(json.dumps(_scrub(payload, slugs), indent=1))


def _write(out: Path, url_path: str, html: str) -> None:
    rel = url_path.strip("/")
    target = (out / rel / "index.html") if rel else (out / "index.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")


def _pages(data: LeagueData) -> Iterable[tuple[str, Callable[[], str]]]:
    """Every URL the static site should contain."""
    yield "/", lambda: _render(webapp.home, "/", data=data)
    yield "/history", lambda: _render(webapp.history_page, "/history", data=data)
    yield "/records", lambda: _render(webapp.records_page, "/records", data=data)
    yield "/trades", lambda: _render(webapp.trades_page, "/trades", data=data)

    for season in data.season_years:
        yield (f"/season/{season}",
               lambda s=season: _render(webapp.season_page, f"/season/{s}", season=s, data=data))
        yield (f"/power/{season}",
               lambda s=season: _render(webapp.power_page, f"/power/{s}", season=s, data=data))
        if draft_board(data, season):
            yield (f"/draft/{season}",
                   lambda s=season: _render(webapp.draft_page, f"/draft/{s}", season=s, data=data))

        for team_id in sorted({t for (s, t) in data.teams if s == season}):
            yield (f"/team/{season}/{team_id}",
                   lambda s=season, t=team_id: _render(
                       webapp.team_page, f"/team/{s}/{t}", season=s, team_id=t, data=data))

        for period in range(1, data.latest_completed_period(season) + 1):
            yield (f"/week/{season}/{period}",
                   lambda s=season, p=period: _render(
                       webapp.week_page, f"/week/{s}/{p}", season=s, period=p, data=data))

    for franchise_id in data.franchises:
        slug = data.slug_for(franchise_id)
        yield (f"/franchise/{slug}",
               lambda f=slug: _render(
                   webapp.franchise_page, f"/franchise/{f}", franchise_id=f, data=data))


def build(
    out_dir: Path,
    *,
    base_path: str = "",
    noindex: bool = True,
    clean: bool = True,
    cname: str | None = None,
) -> dict:
    """Render the site into `out_dir`. Returns a summary."""
    base_path = "/" + base_path.strip("/") if base_path.strip("/") else ""
    out = Path(out_dir)
    if clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    try:
        median_from = load_config().median_scoring_from
    except SystemExit:
        median_from = None

    written, failed = 0, []
    with db.session() as conn:
        data = LeagueData(conn, median_scoring_from=median_from)
        if not data.season_years:
            raise SystemExit("No data to export. Run: python3 -m fantasy.cli scrape")

        stray: dict[str, list[str]] = {}
        for url_path, render in _pages(data):
            try:
                html = _finalize(render(), base_path, noindex=noindex)
                missed = _unprefixed(html, base_path)
                if missed:
                    stray[url_path] = missed
                _write(out, url_path, html)
                written += 1
            except Exception as exc:  # one bad page must not lose the build
                failed.append((url_path, f"{type(exc).__name__}: {exc}"))

        # A static host has no error handler; GitHub Pages serves /404.html.
        try:
            ctx = webapp.base_context(data, _request("/404"))
            ctx.update(status=404, detail="That page is not part of this site.")
            html = webapp.templates.TemplateResponse(
                _request("/404"), "error.html", ctx
            ).body.decode()
            (out / "404.html").write_text(
                _finalize(html, base_path, noindex=noindex), encoding="utf-8"
            )
        except Exception as exc:
            failed.append(("/404.html", str(exc)))

        # Machine-readable copies alongside the pages.
        api = out / "api"
        api.mkdir(exist_ok=True)
        from .analytics.history import record_book
        from .analytics.power import power_rankings
        from .analytics.projections import playoff_odds
        from .analytics.standings import season_standings
        from .analytics.trades import analyze_trades

        slugs = data.franchise_slugs
        _write_json(api / "records.json", record_book(data), slugs)
        _write_json(api / "trades.json", analyze_trades(data), slugs)
        for season in data.season_years:
            _write_json(api / f"standings-{season}.json",
                        season_standings(data, season), slugs)
            _write_json(api / f"power-{season}.json",
                        power_rankings(data, season), slugs)
            odds = playoff_odds(data, season)
            if odds:
                _write_json(api / f"odds-{season}.json", odds, slugs)

    shutil.copytree(webapp.HERE / "static", out / "static", dirs_exist_ok=True)
    # Stop GitHub Pages running the output through Jekyll.
    (out / ".nojekyll").write_text("")

    # GitHub Pages keeps a custom domain in a CNAME file inside the published
    # branch. Every build wipes the output directory, so the file has to be
    # rewritten here or the domain silently detaches on the next publish.
    if cname:
        (out / "CNAME").write_text(cname.strip() + "\n")

    return {
        "pages": written,
        "failed": failed,
        "unprefixed": stray,
        "out": str(out.resolve()),
        "base_path": base_path or "/",
        "bytes": sum(f.stat().st_size for f in out.rglob("*") if f.is_file()),
        "cname": cname or "",
    }
