"""Season standings, enriched with the metrics ESPN does not show you."""
from __future__ import annotations

import statistics
from collections import defaultdict

from .core import LeagueData, TeamWeek, summarize


def _record(weeks: list[TeamWeek]) -> tuple[int, int, int]:
    wins = sum(1 for w in weeks if w.result == "W")
    losses = sum(1 for w in weeks if w.result == "L")
    ties = sum(1 for w in weeks if w.result == "T")
    return wins, losses, ties


def team_season_row(data: LeagueData, season: int, team_id: int, *, regular_only: bool = True) -> dict:
    weeks = data.team_weeks_for(season=season, team_id=team_id, regular_only=regular_only)
    team = data.team(season, team_id)
    points = [w.points for w in weeks]
    stats = summarize(points)
    wins, losses, ties = _record(weeks)
    games = len(weeks)

    ap_w = sum(w.all_play_wins for w in weeks)
    ap_l = sum(w.all_play_losses for w in weeks)
    ap_t = sum(w.all_play_ties for w in weeks)
    ap_games = ap_w + ap_l + ap_t
    ap_pct = (ap_w + 0.5 * ap_t) / ap_games if ap_games else 0.0

    # Expected wins: how many games this scoring profile "should" have won
    # against a random schedule. The gap to actual wins is schedule luck.
    expected_wins = round(ap_pct * games, 2)

    scored = [w for w in weeks if w.optimal_points > 0 and not w.forfeit]
    avg_eff = statistics.fmean([w.efficiency for w in scored]) if scored else 0.0
    bench = round(sum(w.bench_points for w in weeks), 2)

    # Median scoring: a second win each week for outscoring half the league.
    median_weeks = [w for w in weeks if w.median_win is not None]
    median_wins = sum(1 for w in median_weeks if w.median_win == 1.0)
    median_ties = sum(1 for w in median_weeks if w.median_win == 0.5)
    median_losses = len(median_weeks) - median_wins - median_ties
    uses_median = bool(median_weeks)

    close = [w for w in weeks if abs(w.margin) <= 10]
    blowouts_won = sum(1 for w in weeks if w.margin >= 40)
    blowouts_lost = sum(1 for w in weeks if w.margin <= -40)

    return {
        "season": season,
        "team_id": team_id,
        "franchise_id": team.get("franchise_id") or "",
        "name": team.get("name", f"Team {team_id}"),
        "abbrev": team.get("abbrev", ""),
        "logo": team.get("logo", ""),
        "owner": data.owner_name(team.get("franchise_id") or ""),
        "wins": wins, "losses": losses, "ties": ties, "games": games,
        "win_pct": round((wins + 0.5 * ties) / games, 4) if games else 0.0,
        "record": f"{wins}-{losses}" + (f"-{ties}" if ties else ""),
        "points_for": round(sum(points), 2),
        "points_against": round(sum(w.opponent_points for w in weeks), 2),
        "avg": stats["avg"], "stdev": stats["stdev"], "cv": stats["cv"],
        "high": stats["high"], "low": stats["low"],
        "all_play_wins": ap_w, "all_play_losses": ap_l, "all_play_ties": ap_t,
        "all_play_pct": round(ap_pct, 4),
        "all_play_record": f"{ap_w}-{ap_l}" + (f"-{ap_t}" if ap_t else ""),
        "expected_wins": expected_wins,
        "luck": round(wins + 0.5 * ties - expected_wins, 2),
        "efficiency": round(avg_eff, 4),
        "bench_points": bench,
        "optimal_points": round(sum(w.optimal_points for w in weeks), 2),
        "close_games": len(close),
        "close_wins": sum(1 for w in close if w.result == "W"),
        "blowout_wins": blowouts_won,
        "blowout_losses": blowouts_lost,
        # Strength of schedule: what your opponents averaged against you.
        "sos": round(statistics.fmean([w.opponent_points for w in weeks]), 2) if weeks else 0.0,
        "uses_median": uses_median,
        "median_wins": median_wins,
        "median_losses": median_losses,
        "median_ties": median_ties,
        "median_record": (
            f"{median_wins}-{median_losses}" + (f"-{median_ties}" if median_ties else "")
            if uses_median else ""
        ),
        # Total wins under the league's two-points-a-week format.
        "total_wins": round(wins + 0.5 * ties + median_wins + 0.5 * median_ties, 1),
        "total_games": (games * 2) if uses_median else games,
        "combined_record": (
            f"{wins + median_wins}-{losses + median_losses}"
            + (f"-{ties + median_ties}" if (ties + median_ties) else "")
            if uses_median else ""
        ),
        "playoff_seed": team.get("playoff_seed") or 0,
        # ESPN's own final placing, which reorders non-playoff teams by the
        # consolation bracket. Corrected in season_standings below.
        "espn_final_rank": team.get("final_rank") or 0,
        "acquisitions": team.get("acquisitions") or 0,
        "trades": team.get("trades") or 0,
    }


def season_standings(data: LeagueData, season: int, *, regular_only: bool = True) -> list[dict]:
    """Standings memoised per LeagueData -- the record book asks for every season."""
    memo = getattr(data, "_standings_memo", None)
    if memo is None:
        memo = data._standings_memo = {}
    key = (season, regular_only)
    if key not in memo:
        memo[key] = _season_standings(data, season, regular_only=regular_only)
    return memo[key]


def _season_standings(data: LeagueData, season: int, *, regular_only: bool = True) -> list[dict]:
    team_ids = sorted({tid for (s, tid) in data.teams if s == season})
    rows = [team_season_row(data, season, tid, regular_only=regular_only) for tid in team_ids]
    rows = [r for r in rows if r["games"] > 0]

    # Under median scoring the standings are ordered by total wins (matchup win
    # plus median win), which is what the league actually seeds off.
    if rows and rows[0]["uses_median"]:
        rows.sort(key=lambda r: (-r["total_wins"], -r["points_for"]))
    else:
        rows.sort(key=lambda r: (-r["win_pct"], -r["points_for"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i

    # Final placings. Only the playoff bracket can move a team: everyone who
    # misses the playoffs is fixed at their regular-season place, because the
    # consolation ("toilet bowl") games decide nothing in this league.
    playoff_teams = (data.seasons.get(season) or {}).get("playoff_teams") or 0
    for row in rows:
        made_playoffs = bool(playoff_teams) and row["rank"] <= playoff_teams
        row["made_playoffs"] = made_playoffs
        if made_playoffs:
            row["final_rank"] = row["espn_final_rank"] or row["rank"]
        else:
            row["final_rank"] = row["rank"]
    # Separate ranks for the "who is actually good" columns.
    for key, field in (("points_for", "pf_rank"), ("all_play_pct", "ap_rank"),
                       ("efficiency", "eff_rank"), ("luck", "luck_rank")):
        for i, row in enumerate(sorted(rows, key=lambda r: -r[key]), 1):
            row[field] = i
    return rows


def weekly_scores(data: LeagueData, season: int) -> dict[int, list[dict]]:
    """period -> per-team scores, for heatmaps and week-by-week views."""
    out: dict[int, list[dict]] = defaultdict(list)
    for tw in data.team_weeks_for(season=season):
        out[tw.period].append(
            {
                "team_id": tw.team_id,
                "name": data.team_name(season, tw.team_id),
                "points": tw.points,
                "opponent": data.team_name(season, tw.opponent_id),
                "opponent_points": tw.opponent_points,
                "result": tw.result,
                "margin": tw.margin,
                "efficiency": tw.efficiency,
                "optimal_points": tw.optimal_points,
                "all_play_wins": tw.all_play_wins,
                "is_playoff": tw.is_playoff,
            }
        )
    for period in out:
        out[period].sort(key=lambda r: -r["points"])
    return dict(out)


def final_rank_map(data: LeagueData, season: int) -> dict[int, int]:
    """team_id -> the league's final placing (consolation results ignored)."""
    return {r["team_id"]: r["final_rank"] for r in season_standings(data, season)}
