"""Monte Carlo playoff odds.

Each team's weekly score is modelled as a normal draw from its own observed mean
and standard deviation, shrunk toward the league mean so that a team with two
games played is not treated as a known quantity. The remaining schedule is then
replayed thousands of times and final standings tallied under ESPN's tiebreak
(wins, then points for).
"""
from __future__ import annotations

import random
import statistics
from collections import defaultdict

from .core import LeagueData

DEFAULT_SIMS = 10_000
# Games of league-average performance blended into every team's estimate.
SHRINKAGE_GAMES = 4


def _team_profiles(data: LeagueData, season: int) -> dict[int, tuple[float, float]]:
    """team_id -> (mean, stdev), shrunk toward the league baseline."""
    by_team: dict[int, list[float]] = defaultdict(list)
    for tw in data.team_weeks_for(season=season, regular_only=True):
        by_team[tw.team_id].append(tw.points)
    if not by_team:
        return {}

    everything = [p for points in by_team.values() for p in points]
    league_mean = statistics.fmean(everything)
    league_sd = statistics.pstdev(everything) if len(everything) > 1 else 15.0

    profiles = {}
    for team_id, points in by_team.items():
        n = len(points)
        mean = (sum(points) + league_mean * SHRINKAGE_GAMES) / (n + SHRINKAGE_GAMES)
        sd = statistics.pstdev(points) if n > 2 else league_sd
        # Blend in the league spread so a fluky-quiet team is not modelled as
        # having no variance at all.
        sd = (sd * n + league_sd * SHRINKAGE_GAMES) / (n + SHRINKAGE_GAMES)
        profiles[team_id] = (mean, max(sd, 5.0))
    return profiles


def playoff_odds(
    data: LeagueData, season: int, *, sims: int = DEFAULT_SIMS, seed: int = 7
) -> list[dict]:
    season_row = data.seasons.get(season) or {}
    playoff_teams = season_row.get("playoff_teams") or 0
    reg_weeks = season_row.get("reg_season_weeks") or 0
    if not playoff_teams or not reg_weeks:
        return []

    profiles = _team_profiles(data, season)
    if not profiles:
        return []

    played = data.latest_completed_period(season)
    if played >= reg_weeks:
        return []  # regular season is over; odds are decided

    # Current state.
    standing: dict[int, dict] = {
        tid: {"wins": 0.0, "points": 0.0} for tid in profiles
    }
    for tw in data.team_weeks_for(season=season, regular_only=True):
        state = standing[tw.team_id]
        state["points"] += tw.points
        state["wins"] += 1 if tw.result == "W" else (0.5 if tw.result == "T" else 0)

    remaining = [
        m for m in data.matchups
        if m["season"] == season
        and m["matchup_period"] > played
        and m["matchup_period"] <= reg_weeks
        and m["home_team_id"] in profiles
        and m["away_team_id"] in profiles
    ]

    rng = random.Random(seed)
    made = defaultdict(int)
    top_seed = defaultdict(int)
    byes = defaultdict(int)
    seed_sum = defaultdict(float)
    win_sum = defaultdict(float)
    bye_count = max(0, playoff_teams - 4) if playoff_teams > 4 else 0

    for _ in range(sims):
        wins = {tid: standing[tid]["wins"] for tid in profiles}
        points = {tid: standing[tid]["points"] for tid in profiles}

        for game in remaining:
            home, away = game["home_team_id"], game["away_team_id"]
            hm, hsd = profiles[home]
            am, asd = profiles[away]
            hs = rng.gauss(hm, hsd)
            as_ = rng.gauss(am, asd)
            points[home] += hs
            points[away] += as_
            if hs > as_:
                wins[home] += 1
            elif as_ > hs:
                wins[away] += 1
            else:
                wins[home] += 0.5
                wins[away] += 0.5

        order = sorted(profiles, key=lambda t: (-wins[t], -points[t]))
        for rank, tid in enumerate(order, 1):
            seed_sum[tid] += rank
            win_sum[tid] += wins[tid]
            if rank <= playoff_teams:
                made[tid] += 1
            if rank == 1:
                top_seed[tid] += 1
            if bye_count and rank <= bye_count:
                byes[tid] += 1

    rows = []
    for tid in profiles:
        rows.append(
            {
                "team_id": tid,
                "team": data.team_name(season, tid),
                "franchise_id": data.franchise_of(season, tid),
                "playoff_odds": round(100 * made[tid] / sims, 1),
                "top_seed_odds": round(100 * top_seed[tid] / sims, 1),
                "bye_odds": round(100 * byes[tid] / sims, 1) if bye_count else None,
                "avg_seed": round(seed_sum[tid] / sims, 2),
                "proj_wins": round(win_sum[tid] / sims, 2),
                "proj_avg": round(profiles[tid][0], 2),
                "volatility": round(profiles[tid][1], 2),
            }
        )
    rows.sort(key=lambda r: (-r["playoff_odds"], r["avg_seed"]))
    return rows
