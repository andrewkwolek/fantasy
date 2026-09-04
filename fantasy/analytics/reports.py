"""Weekly recap: the story of a week, assembled from the fact table."""
from __future__ import annotations

from .core import LeagueData
from .power import power_rankings


def _fmt(name: str, points: float) -> str:
    return f"{name} ({points:.1f})"


def week_report(data: LeagueData, season: int, period: int) -> dict:
    weeks = [tw for tw in data.team_weeks if tw.season == season and tw.period == period]
    if not weeks:
        return {}

    def named(tw, **extra):
        return {
            "team": data.team_name(season, tw.team_id),
            "team_id": tw.team_id,
            "owner": data.owner_name(tw.franchise_id),
            "opponent": data.team_name(season, tw.opponent_id),
            "points": tw.points,
            "opponent_points": tw.opponent_points,
            "margin": tw.margin,
            "result": tw.result,
            **extra,
        }

    scored = [w for w in weeks if w.optimal_points > 0 and not w.forfeit]
    wins = [w for w in weeks if w.result == "W"]
    losses = [w for w in weeks if w.result == "L"]

    high = max(weeks, key=lambda w: w.points)
    low = min(weeks, key=lambda w: w.points)
    blowout = max(wins, key=lambda w: w.margin) if wins else None
    nail_biter = min(wins, key=lambda w: w.margin) if wins else None

    # "Lucky" = won despite a bottom-half score; "unlucky" = lost with a top score.
    ordered = sorted(weeks, key=lambda w: -w.points)
    rank_of = {w.team_id: i for i, w in enumerate(ordered, 1)}
    lucky = max(
        (w for w in wins if rank_of[w.team_id] > len(weeks) / 2),
        key=lambda w: rank_of[w.team_id], default=None,
    )
    unlucky = min(
        (w for w in losses if rank_of[w.team_id] <= len(weeks) / 2),
        key=lambda w: rank_of[w.team_id], default=None,
    )

    best_manager = max(scored, key=lambda w: w.efficiency) if scored else None
    worst_manager = min(scored, key=lambda w: w.efficiency) if scored else None
    most_left = max(scored, key=lambda w: w.bench_points) if scored else None

    # Top individual performances across the whole league this week.
    performers = []
    for tw in weeks:
        for row in data.roster(season, period, tw.team_id):
            if not row["is_starter"]:
                continue
            performers.append(
                {
                    "player": row["player_name"],
                    "position": row["position"],
                    "points": row["points"],
                    "team": data.team_name(season, tw.team_id),
                }
            )
    performers.sort(key=lambda p: -p["points"])

    headlines = []
    headlines.append(f"{_fmt(data.team_name(season, high.team_id), high.points)} led the league.")
    if blowout and blowout.margin >= 30:
        headlines.append(
            f"{data.team_name(season, blowout.team_id)} steamrolled "
            f"{data.team_name(season, blowout.opponent_id)} by {blowout.margin:.1f}."
        )
    if nail_biter and nail_biter.margin <= 5:
        headlines.append(
            f"{data.team_name(season, nail_biter.team_id)} survived by "
            f"{nail_biter.margin:.1f} over {data.team_name(season, nail_biter.opponent_id)}."
        )
    if unlucky:
        headlines.append(
            f"{data.team_name(season, unlucky.team_id)} scored "
            f"{unlucky.points:.1f} and still lost."
        )
    if most_left and most_left.bench_points > 20:
        misses = ", ".join(
            f"{m['benched']} ({m['benched_points']:.1f})" for m in most_left.slot_misses[:2]
        )
        headlines.append(
            f"{data.team_name(season, most_left.team_id)} left "
            f"{most_left.bench_points:.1f} on the bench" + (f" — {misses}." if misses else ".")
        )

    return {
        "season": season,
        "period": period,
        "games": sorted(
            [named(w) for w in weeks], key=lambda g: -g["points"]
        ),
        "high": named(high),
        "low": named(low),
        "blowout": named(blowout) if blowout else None,
        "nail_biter": named(nail_biter) if nail_biter else None,
        "lucky": named(lucky) if lucky else None,
        "unlucky": named(unlucky) if unlucky else None,
        "best_manager": named(best_manager, efficiency=best_manager.efficiency)
        if best_manager else None,
        "worst_manager": named(
            worst_manager, efficiency=worst_manager.efficiency,
            bench_points=worst_manager.bench_points, misses=worst_manager.slot_misses,
        ) if worst_manager else None,
        "most_left_on_bench": named(
            most_left, bench_points=most_left.bench_points,
            optimal=most_left.optimal_points, misses=most_left.slot_misses,
        ) if most_left else None,
        "top_performers": performers[:10],
        "headlines": headlines,
        "average_score": round(sum(w.points for w in weeks) / len(weeks), 2),
    }


def power_history(data: LeagueData, season: int) -> list[dict]:
    """Power score for every team at every point in the season."""
    latest = data.latest_completed_period(season)
    out = []
    for period in range(1, latest + 1):
        ranks = power_rankings(data, season, through_period=period)
        out.append(
            {
                "period": period,
                "teams": [
                    {"team_id": r["team_id"], "name": r["name"],
                     "score": r["score"], "rank": r["rank"]}
                    for r in ranks
                ],
            }
        )
    return out
