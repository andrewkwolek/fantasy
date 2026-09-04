"""FastAPI application serving the league site."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..analytics.core import LeagueData
from ..analytics.draft import draft_grades, steals_and_busts, waiver_analysis, draft_board
from ..analytics.history import (
    all_time_standings, champions, franchise_career, head_to_head, record_book,
)
from ..analytics.power import power_rankings
from ..analytics.projections import playoff_odds
from ..analytics.reports import power_history, week_report
from ..analytics.standings import season_standings, weekly_scores
from ..analytics.trades import analyze_trades, trade_leaderboard
from ..config import DB_PATH, load_config
from ..store import db
from . import charts

HERE = Path(__file__).parent
app = FastAPI(title="Fantasy League HQ", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=str(HERE / "templates"))

_lock = threading.Lock()
_cache: dict = {"mtime": None, "conn": None, "data": None}


def _build_data() -> LeagueData:
    """Cached LeagueData, rebuilt whenever the database file changes."""
    if not DB_PATH.exists():
        raise HTTPException(503, "No data yet. Run: python3 -m fantasy.cli scrape")
    mtime = DB_PATH.stat().st_mtime
    if _cache["mtime"] != mtime or _cache["data"] is None:
        if _cache["conn"] is not None:
            _cache["conn"].close()
        conn = db.connect(same_thread=False)
        db.init_db(conn)
        try:
            median_from = load_config().median_scoring_from
        except SystemExit:      # no .env yet (demo data); rule simply off
            median_from = None
        _cache.update(
            mtime=mtime, conn=conn,
            data=LeagueData(conn, median_scoring_from=median_from),
        )
    return _cache["data"]


def get_data() -> Iterator[LeagueData]:
    """Request-scoped handle on the shared LeagueData.

    The underlying SQLite connection and the memoised analytics are shared across
    requests, and FastAPI dispatches sync endpoints onto a threadpool, so the
    whole request is serialised behind one lock. The work is read-only and
    sub-second, and this is a single-household site, so a lock is the right
    trade against per-thread connections and duplicated caches.
    """
    with _lock:
        yield _build_data()


def newest_played_season(data: LeagueData) -> int | None:
    """Newest season with completed games.

    ESPN publishes next season's league (and its draft) months before Week 1, so
    `current_season` is regularly a season with an empty schedule. Landing the
    site there shows a page of blank tables.
    """
    for season in reversed(data.season_years):
        if data.latest_completed_period(season):
            return season
    return data.current_season


def newest_drafted_season(data: LeagueData) -> int | None:
    rows = db.query(
        data.conn, "SELECT DISTINCT season FROM draft_picks ORDER BY season DESC LIMIT 1"
    )
    return rows[0]["season"] if rows else data.current_season


def base_context(data: LeagueData, request: Request) -> dict:
    played = newest_played_season(data)
    return {
        "request": request,
        "data": data,
        "seasons": list(reversed(data.season_years)),
        "current_season": data.current_season,
        "display_season": played,
        "draft_season": newest_drafted_season(data),
        "preseason": (
            data.current_season
            if data.current_season and data.current_season != played
            else None
        ),
        "league_name": (
            data.seasons.get(data.current_season, {}).get("name", "Fantasy League")
            if data.current_season else "Fantasy League"
        ),
        "charts": charts,
        "is_demo": db.get_meta(data.conn, "demo", False),
    }


def _resolve_season(data: LeagueData, season: int | None) -> int:
    if season is None:
        if data.current_season is None:
            raise HTTPException(503, "No seasons in the database yet.")
        return data.current_season
    if season not in data.seasons:
        raise HTTPException(404, f"No data for season {season}")
    return season


# --------------------------------------------------------------------- pages


@app.get("/", response_class=HTMLResponse)
def home(request: Request, data: LeagueData = Depends(get_data)):
    ctx = base_context(data, request)
    season = ctx["display_season"] or _resolve_season(data, None)
    period = data.latest_completed_period(season)

    standings = season_standings(data, season)
    power = power_rankings(data, season)
    odds = playoff_odds(data, season)
    report = week_report(data, season, period) if period else {}
    season_row = data.seasons.get(season, {})

    league_avg = (
        round(sum(r["points_for"] for r in standings) / max(1, sum(r["games"] for r in standings)), 1)
        if standings else 0
    )

    ctx.update(
        season=season, period=period, standings=standings, power=power,
        odds=odds, report=report, season_row=season_row, league_avg=league_avg,
        champions=champions(data),
        all_time=all_time_standings(data)[:5],
        records=record_book(data, top=3),
    )
    return templates.TemplateResponse(request, "home.html", ctx)


@app.get("/season/{season}", response_class=HTMLResponse)
def season_page(request: Request, season: int, data: LeagueData = Depends(get_data)):
    season = _resolve_season(data, season)
    ctx = base_context(data, request)
    standings = season_standings(data, season)
    scores = weekly_scores(data, season)

    periods = sorted(scores)
    team_ids = [r["team_id"] for r in standings]
    names = {r["team_id"]: r["name"] for r in standings}
    grid, tips = [], []
    for tid in team_ids:
        row, tiprow = [], []
        for period in periods:
            entry = next((s for s in scores[period] if s["team_id"] == tid), None)
            row.append(entry["points"] if entry else None)
            tiprow.append(
                f"{names[tid]} · Wk {period}: {entry['points']} "
                f"({entry['result']} vs {entry['opponent']} {entry['opponent_points']})"
                if entry else ""
            )
        grid.append(row)
        tips.append(tiprow)

    flat = [v for row in grid for v in row if v is not None]
    # Label only the teams furthest from the break-even diagonal; the rest are
    # identified on hover. Labelling all ten just produces a pile of text.
    ranked = sorted(standings, key=lambda r: -abs(r["points_for"] - r["points_against"]))
    notable = {r["team_id"] for r in ranked[:4]}
    pf_pa_points = [
        {
            "x": r["points_against"], "y": r["points_for"],
            "label": (r["abbrev"] or r["name"][:10]) if r["team_id"] in notable else "",
            "tip": f"{r['name']} — PF {r['points_for']} / PA {r['points_against']} "
                   f"({r['record']})",
        }
        for r in standings
    ]
    effs = [r["efficiency"] for r in standings if r["efficiency"]]
    avg_efficiency = round(100 * sum(effs) / len(effs), 1) if effs else None
    ctx.update(
        pf_pa_points=pf_pa_points,
        avg_efficiency=avg_efficiency,
        season=season, standings=standings, scores=scores, periods=periods,
        heat_rows=[names[t] for t in team_ids], heat_cols=[f"W{p}" for p in periods],
        heat_values=grid, heat_tips=tips,
        heat_center=round(sum(flat) / len(flat), 1) if flat else 0,
        season_row=data.seasons.get(season, {}),
        power=power_rankings(data, season),
        odds=playoff_odds(data, season),
    )
    return templates.TemplateResponse(request, "season.html", ctx)


@app.get("/power/{season}", response_class=HTMLResponse)
def power_page(request: Request, season: int, data: LeagueData = Depends(get_data)):
    season = _resolve_season(data, season)
    ctx = base_context(data, request)
    history = power_history(data, season)
    series = []
    if history:
        for team in history[-1]["teams"]:
            values = []
            for snap in history:
                match = next((t for t in snap["teams"] if t["team_id"] == team["team_id"]), None)
                values.append(match["rank"] if match else None)
            series.append({"name": team["name"], "key": f"t{team['team_id']}", "values": values})
    ctx.update(
        season=season,
        power=power_rankings(data, season),
        history=history,
        series=series,
        x_labels=[f"W{s['period']}" for s in history],
        odds=playoff_odds(data, season),
    )
    return templates.TemplateResponse(request, "power.html", ctx)


@app.get("/week/{season}/{period}", response_class=HTMLResponse)
def week_page(request: Request, season: int, period: int, data: LeagueData = Depends(get_data)):
    season = _resolve_season(data, season)
    ctx = base_context(data, request)
    report = week_report(data, season, period)
    if not report:
        raise HTTPException(404, f"No completed games for {season} week {period}")
    latest = data.latest_completed_period(season)
    pairs = []
    seen = set()
    for game in report["games"]:
        if game["team_id"] in seen:
            continue
        seen.add(game["team_id"])
        opponent = next(
            (g for g in report["games"]
             if g["team"] == game["opponent"] and g["team_id"] not in seen), None
        )
        if opponent:
            seen.add(opponent["team_id"])
            pairs.append((game, opponent))
    score_rows = [
        {
            "label": g["team"], "value": g["points"],
            "tip": f"{g['team']} {g['points']} ({g['result']}) vs "
                   f"{g['opponent']} {g['opponent_points']}",
        }
        for g in report["games"]
    ]
    ctx.update(
        season=season, period=period, report=report, pairs=pairs,
        latest=latest, score_rows=score_rows,
        prev_period=period - 1 if period > 1 else None,
        next_period=period + 1 if period < latest else None,
    )
    return templates.TemplateResponse(request, "week.html", ctx)


@app.get("/team/{season}/{team_id}", response_class=HTMLResponse)
def team_page(request: Request, season: int, team_id: int, data: LeagueData = Depends(get_data)):
    season = _resolve_season(data, season)
    ctx = base_context(data, request)
    if (season, team_id) not in data.teams:
        raise HTTPException(404, "Unknown team")
    weeks = sorted(
        data.team_weeks_for(season=season, team_id=team_id), key=lambda w: w.period
    )
    rows = [
        {
            "period": w.period,
            "opponent": data.team_name(season, w.opponent_id),
            "opponent_id": w.opponent_id,
            "points": w.points,
            "opponent_points": w.opponent_points,
            "result": w.result,
            "margin": w.margin,
            "optimal": w.optimal_points,
            "efficiency": w.efficiency,
            "bench": w.bench_points,
            "all_play": f"{w.all_play_wins}-{w.all_play_losses}",
            "misses": w.slot_misses,
            "is_playoff": w.is_playoff,
            "is_consolation": w.is_consolation,
            "forfeit": w.forfeit,
        }
        for w in weeks
    ]
    team = data.team(season, team_id)
    standing = next(
        (r for r in season_standings(data, season) if r["team_id"] == team_id), {}
    )
    week_rows = [
        {
            "label": f"Wk {r['period']}", "value": r["points"],
            "tip": f"Week {r['period']}: {r['points']} ({r['result']}) vs "
                   f"{r['opponent']} {r['opponent_points']}",
        }
        for r in rows
    ]
    ctx.update(
        season=season, team=team, rows=rows, standing=standing,
        week_rows=week_rows,
        career=franchise_career(data, team["franchise_id"]) if team.get("franchise_id") else None,
        draft=[p for p in draft_board(data, season) if p["team_id"] == team_id],
    )
    return templates.TemplateResponse(request, "team.html", ctx)


@app.get("/franchise/{franchise_id}", response_class=HTMLResponse)
def franchise_page(request: Request, franchise_id: str, data: LeagueData = Depends(get_data)):
    resolved = data.franchise_by_slug(franchise_id)
    if resolved is None:
        raise HTTPException(404, "Unknown franchise")
    franchise_id = resolved
    ctx = base_context(data, request)
    career = franchise_career(data, franchise_id)
    by_season = []
    for season in career["season_years"]:
        row = next(
            (r for r in season_standings(data, season) if r["franchise_id"] == franchise_id),
            None,
        )
        if row:
            by_season.append(row)
    ctx.update(career=career, by_season=by_season, franchise_id=franchise_id)
    return templates.TemplateResponse(request, "franchise.html", ctx)


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request, data: LeagueData = Depends(get_data)):
    ctx = base_context(data, request)
    ctx.update(
        all_time=all_time_standings(data),
        champions=list(reversed(champions(data))),
        h2h=head_to_head(data),
    )
    return templates.TemplateResponse(request, "history.html", ctx)


@app.get("/records", response_class=HTMLResponse)
def records_page(request: Request, data: LeagueData = Depends(get_data)):
    ctx = base_context(data, request)
    ctx.update(records=record_book(data, top=10))
    return templates.TemplateResponse(request, "records.html", ctx)


@app.get("/trades", response_class=HTMLResponse)
def trades_page(request: Request, data: LeagueData = Depends(get_data)):
    ctx = base_context(data, request)
    trades = analyze_trades(data)
    ctx.update(trades=trades, leaderboard=trade_leaderboard(data, trades))
    return templates.TemplateResponse(request, "trades.html", ctx)


@app.get("/draft/{season}", response_class=HTMLResponse)
def draft_page(request: Request, season: int, data: LeagueData = Depends(get_data)):
    season = _resolve_season(data, season)
    ctx = base_context(data, request)
    board = draft_board(data, season)
    draft_points = [
        {
            "x": p["overall_pick"], "y": p["points_rank"], "label": "",
            "color": p["value"],
            "tip": f"Pick {p['overall_pick']}: {p['player']} ({p['position']}) — "
                   f"finished #{p['points_rank']}, {p['points']} pts · {p['team']}",
        }
        for p in board
    ]
    ctx.update(
        season=season,
        grades=draft_grades(data, season),
        board=board,
        draft_points=draft_points,
        waivers=waiver_analysis(data, season),
        **steals_and_busts(data, season),
    )
    return templates.TemplateResponse(request, "draft.html", ctx)



# ----------------------------------------------------------------------- api


@app.get("/api/standings/{season}")
def api_standings(season: int, data: LeagueData = Depends(get_data)):
    return JSONResponse(season_standings(data, _resolve_season(data, season)))


@app.get("/api/power/{season}")
def api_power(season: int, data: LeagueData = Depends(get_data)):
    return JSONResponse(power_rankings(data, _resolve_season(data, season)))


@app.get("/api/odds/{season}")
def api_odds(season: int, data: LeagueData = Depends(get_data)):
    return JSONResponse(playoff_odds(data, _resolve_season(data, season)))


@app.get("/api/records")
def api_records(data: LeagueData = Depends(get_data)):
    return JSONResponse(record_book(data))


@app.get("/api/trades")
def api_trades(data: LeagueData = Depends(get_data)):
    return JSONResponse(analyze_trades(data))


@app.exception_handler(HTTPException)
def http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    try:
        with _lock:
            ctx = base_context(_build_data(), request)
    except HTTPException:
        ctx = {"request": request, "seasons": [], "league_name": "Fantasy League",
               "current_season": None, "is_demo": False}
    ctx.update(status=exc.status_code, detail=exc.detail)
    return templates.TemplateResponse(request, "error.html", ctx, status_code=exc.status_code)
