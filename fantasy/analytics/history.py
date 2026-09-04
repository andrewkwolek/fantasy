"""All-time league history: franchise careers, head-to-head, and the record book."""
from __future__ import annotations

import statistics
from collections import defaultdict

from .core import LeagueData, summarize
from .standings import final_rank_map, season_standings


def _title_seasons(data: LeagueData, franchise_id: str) -> list[int]:
    """Seasons this franchise finished first, by the league's own placings."""
    out = []
    for season in data.season_years:
        ranks = final_rank_map(data, season)
        for (s, team_id), team in data.teams.items():
            if s != season or team.get("franchise_id") != franchise_id:
                continue
            # A placing only exists once ESPN has settled the bracket.
            if (team.get("final_rank") or 0) and ranks.get(team_id) == 1:
                out.append(season)
    return sorted(out)


def franchise_career(data: LeagueData, franchise_id: str) -> dict:
    weeks = data.team_weeks_for(franchise_id=franchise_id)
    regular = [w for w in weeks if not w.is_playoff]
    franchise = data.franchises.get(franchise_id, {})

    seasons_played = sorted({w.season for w in weeks})
    points = [w.points for w in regular]
    stats = summarize(points)

    wins = sum(1 for w in regular if w.result == "W")
    losses = sum(1 for w in regular if w.result == "L")
    ties = sum(1 for w in regular if w.result == "T")
    games = len(regular)

    ap_w = sum(w.all_play_wins for w in regular)
    ap_l = sum(w.all_play_losses for w in regular)
    ap_t = sum(w.all_play_ties for w in regular)
    ap_total = ap_w + ap_l + ap_t
    ap_pct = (ap_w + 0.5 * ap_t) / ap_total if ap_total else 0.0

    playoff_weeks = [w for w in weeks if w.is_playoff and not w.is_consolation]
    playoff_wins = sum(1 for w in playoff_weeks if w.result == "W")

    finishes = []
    playoff_appearances = 0
    for season in seasons_played:
        row = next(
            (r for r in season_standings(data, season)
             if r["franchise_id"] == franchise_id), None
        )
        if not row:
            continue
        # Only completed seasons have a placing; ESPN leaves it 0 until then.
        if row["espn_final_rank"]:
            finishes.append(row["final_rank"])
        if row["made_playoffs"]:
            playoff_appearances += 1

    scored = [w for w in weeks if w.optimal_points > 0 and not w.forfeit]
    titles = _title_seasons(data, franchise_id)

    return {
        "franchise_id": franchise_id,
        "name": franchise.get("display_name", "Unknown"),
        "owner": franchise.get("owner_name", "Unknown"),
        "logo": franchise.get("logo", ""),
        "seasons": len(seasons_played),
        "season_years": seasons_played,
        "first_season": min(seasons_played) if seasons_played else 0,
        "last_season": max(seasons_played) if seasons_played else 0,
        "wins": wins, "losses": losses, "ties": ties, "games": games,
        "record": f"{wins}-{losses}" + (f"-{ties}" if ties else ""),
        "win_pct": round((wins + 0.5 * ties) / games, 4) if games else 0.0,
        "points_for": round(sum(points), 2),
        "points_against": round(sum(w.opponent_points for w in regular), 2),
        "avg": stats["avg"], "stdev": stats["stdev"], "cv": stats["cv"],
        "high": stats["high"], "low": stats["low"],
        "all_play_record": f"{ap_w}-{ap_l}" + (f"-{ap_t}" if ap_t else ""),
        "all_play_pct": round(ap_pct, 4),
        "expected_wins": round(ap_pct * games, 2),
        "luck": round(wins + 0.5 * ties - ap_pct * games, 2),
        "titles": len(titles),
        "title_years": titles,
        "playoff_appearances": playoff_appearances,
        "playoff_record": f"{playoff_wins}-{len(playoff_weeks) - playoff_wins}",
        "best_finish": min(finishes) if finishes else 0,
        "worst_finish": max(finishes) if finishes else 0,
        "avg_finish": round(statistics.fmean(finishes), 2) if finishes else 0.0,
        "efficiency": round(statistics.fmean([w.efficiency for w in scored]), 4) if scored else 0.0,
        "bench_points": round(sum(w.bench_points for w in weeks), 2),
    }


def all_time_standings(data: LeagueData) -> list[dict]:
    rows = [franchise_career(data, fid) for fid in data.franchises]
    rows = [r for r in rows if r["games"] > 0]
    rows.sort(key=lambda r: (-r["win_pct"], -r["points_for"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def head_to_head(data: LeagueData) -> dict:
    """All-time franchise-vs-franchise grid, consolation games excluded."""
    grid: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0, "games": 0}
    )
    for tw in data.team_weeks:
        # Consolation games decide nothing in this league, so they do not
        # belong in an all-time head-to-head record either.
        if tw.is_consolation:
            continue
        me = tw.franchise_id
        them = data.franchise_of(tw.season, tw.opponent_id)
        if not me or not them or me == them:
            continue
        cell = grid[(me, them)]
        cell["games"] += 1
        cell["pf"] += tw.points
        cell["pa"] += tw.opponent_points
        if tw.result == "W":
            cell["wins"] += 1
        elif tw.result == "L":
            cell["losses"] += 1
        else:
            cell["ties"] += 1

    order = [
        f["franchise_id"]
        for f in sorted(
            all_time_standings(data), key=lambda r: (-r["win_pct"], r["name"])
        )
    ]
    cells = {}
    for (me, them), cell in grid.items():
        cell["pf"] = round(cell["pf"], 1)
        cell["pa"] = round(cell["pa"], 1)
        cell["record"] = f"{cell['wins']}-{cell['losses']}" + (
            f"-{cell['ties']}" if cell["ties"] else ""
        )
        cell["win_pct"] = round(
            (cell["wins"] + 0.5 * cell["ties"]) / cell["games"], 3
        ) if cell["games"] else 0.0
        cells[f"{me}|{them}"] = cell

    return {
        "order": order,
        "names": {fid: data.franchise_name(fid) for fid in order},
        "owners": {fid: data.owner_name(fid) for fid in order},
        "cells": cells,
    }


def record_book(data: LeagueData, *, top: int = 10) -> dict:
    """Superlatives across every game that counted.

    Consolation ("toilet bowl") games are excluded: this league does not use
    them for anything, so a score put up in one is not a league record.
    """
    weeks = [w for w in data.team_weeks if not w.is_consolation]
    if not weeks:
        return {}

    def label(tw):
        return {
            "season": tw.season,
            "period": tw.period,
            "team": data.team_name(tw.season, tw.team_id),
            "owner": data.owner_name(tw.franchise_id),
            "opponent": data.team_name(tw.season, tw.opponent_id),
            "points": tw.points,
            "opponent_points": tw.opponent_points,
            "margin": tw.margin,
            "result": tw.result,
            "is_playoff": tw.is_playoff,
        }

    # Forfeits are excluded from the bench-management board: a zeroed week is
    # not a lineup mistake. They remain in the scoring and margin boards, where
    # the result genuinely stands.
    scored = [w for w in weeks if w.optimal_points > 0 and not w.forfeit]
    losses = [w for w in weeks if w.result == "L"]
    wins = [w for w in weeks if w.result == "W"]

    # Season-level aggregates for the "best season" boards.
    season_rows = []
    for season in data.season_years:
        season_rows.extend(season_standings(data, season))

    return {
        "highest_score": [label(w) for w in sorted(weeks, key=lambda w: -w.points)[:top]],
        "lowest_score": [
            {**label(w), "forfeit": w.forfeit}
            for w in sorted(weeks, key=lambda w: w.points)[:top]
        ],
        "biggest_blowout": [label(w) for w in sorted(wins, key=lambda w: -w.margin)[:top]],
        "closest_game": [
            label(w) for w in sorted(wins, key=lambda w: w.margin)[:top]
        ],
        "highest_losing_score": [label(w) for w in sorted(losses, key=lambda w: -w.points)[:top]],
        "lowest_winning_score": [label(w) for w in sorted(wins, key=lambda w: w.points)[:top]],
        "highest_combined": [
            {**label(w), "combined": round(w.points + w.opponent_points, 2)}
            for w in sorted(weeks, key=lambda w: -(w.points + w.opponent_points))[:top]
        ],
        "most_bench_points": [
            {**label(w), "bench_points": w.bench_points, "optimal": w.optimal_points,
             "misses": w.slot_misses}
            for w in sorted(scored, key=lambda w: -w.bench_points)[:top]
        ],
        "perfect_weeks": [
            {**label(w), "efficiency": w.efficiency}
            for w in sorted(
                [w for w in scored if w.efficiency >= 0.999], key=lambda w: -w.points
            )[:top]
        ],
        "best_season_pf": sorted(season_rows, key=lambda r: -r["points_for"])[:top],
        "best_season_avg": sorted(season_rows, key=lambda r: -r["avg"])[:top],
        "luckiest_seasons": sorted(season_rows, key=lambda r: -r["luck"])[:top],
        "unluckiest_seasons": sorted(season_rows, key=lambda r: r["luck"])[:top],
    }


def champions(data: LeagueData) -> list[dict]:
    """One row per completed season: champion, runner-up, and regular-season best."""
    rows = []
    for season in data.season_years:
        teams = [t for (s, _), t in data.teams.items() if s == season]
        standings = season_standings(data, season)
        settled = any(r["espn_final_rank"] for r in standings)
        champ = next((r for r in standings if settled and r["final_rank"] == 1), None)
        runner = next((r for r in standings if settled and r["final_rank"] == 2), None)
        top_seed = standings[0] if standings else None
        scoring_leader = max(standings, key=lambda r: r["points_for"]) if standings else None

        rows.append(
            {
                "season": season,
                "name": data.seasons.get(season, {}).get("name", ""),
                "teams": len(teams),
                "champion": champ["name"] if champ else None,
                "champion_owner": champ["owner"] if champ else None,
                "champion_franchise": champ["franchise_id"] if champ else None,
                "runner_up": runner["name"] if runner else None,
                "regular_season_best": top_seed["name"] if top_seed else None,
                "regular_season_record": top_seed["record"] if top_seed else None,
                "scoring_leader": scoring_leader["name"] if scoring_leader else None,
                "scoring_leader_points": scoring_leader["points_for"] if scoring_leader else 0,
                "complete": bool(champ),
            }
        )
    return rows
