"""Power rankings.

Win-loss record is a noisy signal in fantasy: a team can go 4-0 on the strength
of the four lowest opposing scores in the league. This model deliberately
under-weights record and leans on scoring, all-play performance, and recent
form, each converted to a within-league percentile before weighting so that no
single component's scale dominates.
"""
from __future__ import annotations

import statistics

from .core import LeagueData
from .standings import season_standings

RECENT_WINDOW = 3

WEIGHTS = {
    "season_scoring": 0.30,   # season-long scoring average
    "recent_form": 0.25,      # last few weeks, where current strength shows
    "all_play": 0.30,         # record vs the whole league, schedule-independent
    "consistency": 0.10,      # low volatility = reliable floor
    "efficiency": 0.05,       # roster management, a small real edge
}


def _percentiles(values: list[float]) -> list[float]:
    """Rank-based 0..1 scaling. Ties share the mean rank; robust to outliers."""
    if not values:
        return []
    if len(values) == 1:
        return [0.5]
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean_rank = (i + j) / 2
        for k in range(i, j + 1):
            ranks[order[k]] = mean_rank
        i = j + 1
    return [r / (len(values) - 1) for r in ranks]


def power_rankings(
    data: LeagueData, season: int, *, through_period: int | None = None
) -> list[dict]:
    if through_period is None:
        through_period = data.latest_completed_period(season)
    if not through_period:
        return []

    team_ids = sorted({tid for (s, tid) in data.teams if s == season})
    base = {r["team_id"]: r for r in season_standings(data, season)}

    raw: list[dict] = []
    for team_id in team_ids:
        weeks = sorted(
            data.team_weeks_for(season=season, team_id=team_id, through_period=through_period),
            key=lambda w: w.period,
        )
        if not weeks:
            continue
        points = [w.points for w in weeks]
        recent = [w.points for w in weeks[-RECENT_WINDOW:]]
        ap_games = sum(w.all_play_wins + w.all_play_losses + w.all_play_ties for w in weeks)
        ap_wins = sum(w.all_play_wins + 0.5 * w.all_play_ties for w in weeks)
        scored = [w for w in weeks if w.optimal_points > 0 and not w.forfeit]

        raw.append(
            {
                "team_id": team_id,
                "name": data.team_name(season, team_id),
                "logo": data.team(season, team_id).get("logo", ""),
                "franchise_id": data.franchise_of(season, team_id),
                "record": base.get(team_id, {}).get("record", ""),
                "season_scoring": statistics.fmean(points),
                "recent_form": statistics.fmean(recent),
                "all_play": ap_wins / ap_games if ap_games else 0.0,
                # Negated: lower volatility should score higher.
                "consistency": -(statistics.pstdev(points) if len(points) > 1 else 0.0),
                "efficiency": statistics.fmean([w.efficiency for w in scored]) if scored else 0.0,
                "last_points": points[-1],
                "streak": _streak(weeks),
            }
        )

    if not raw:
        return []

    scaled = {
        key: _percentiles([r[key] for r in raw]) for key in WEIGHTS
    }
    for idx, row in enumerate(raw):
        row["score"] = round(
            100 * sum(WEIGHTS[key] * scaled[key][idx] for key in WEIGHTS), 1
        )
        row["components"] = {key: round(scaled[key][idx] * 100, 1) for key in WEIGHTS}

    raw.sort(key=lambda r: -r["score"])
    for i, row in enumerate(raw, 1):
        row["rank"] = i

    # Movement vs the same model run one week earlier.
    if through_period > 1:
        prior = {
            r["team_id"]: r["rank"]
            for r in power_rankings(data, season, through_period=through_period - 1)
        }
        for row in raw:
            was = prior.get(row["team_id"])
            row["delta"] = (was - row["rank"]) if was else 0
    else:
        for row in raw:
            row["delta"] = 0
    return raw


def _streak(weeks: list) -> str:
    if not weeks:
        return ""
    last = weeks[-1].result
    count = 0
    for week in reversed(weeks):
        if week.result != last:
            break
        count += 1
    return f"{last}{count}"
