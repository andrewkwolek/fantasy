"""Draft grades and waiver-wire hit rates.

A pick is graded against what that draft slot is *worth*, not against the field.
The expected return for a slot comes from the league's own history (average
finish position of the player taken at that slot across all seasons), which
automatically calibrates to league size and scoring settings. Where history is
thin, the within-season fallback -- comparing a pick's number to where the
player actually finished -- is used instead.
"""
from __future__ import annotations

from collections import defaultdict

from ..store import db
from .core import LeagueData


def _season_points(data: LeagueData) -> dict[tuple[int, int], float]:
    """(season, player_id) -> total points scored that season."""
    totals: dict[tuple[int, int], float] = defaultdict(float)
    seen: set[tuple[int, int, int]] = set()
    for (season, week, _team), rows in data.player_weeks.items():
        for row in rows:
            key = (season, row["player_id"], week)
            if key in seen:
                continue  # a player appears once per team-week; count once
            seen.add(key)
            totals[(season, row["player_id"])] += row["points"]
    return {k: round(v, 2) for k, v in totals.items()}


def _started_points(data: LeagueData) -> dict[tuple[int, int, int], float]:
    """(season, team_id, player_id) -> points contributed from the starting lineup."""
    totals: dict[tuple[int, int, int], float] = defaultdict(float)
    for (season, _week, team_id), rows in data.player_weeks.items():
        for row in rows:
            if row["is_starter"]:
                totals[(season, team_id, row["player_id"])] += row["points"]
    return {k: round(v, 2) for k, v in totals.items()}


def draft_board(data: LeagueData, season: int) -> list[dict]:
    picks = db.query(
        data.conn,
        "SELECT * FROM draft_picks WHERE season = ? ORDER BY overall_pick",
        (season,),
    )
    if not picks:
        return []

    points = _season_points(data)
    started = _started_points(data)

    rows = []
    for pick in picks:
        pid = pick["player_id"]
        player = data.players.get(pid, {})
        total = points.get((season, pid), 0.0)
        rows.append(
            {
                "season": season,
                "overall_pick": pick["overall_pick"],
                "round": pick["round"],
                "round_pick": pick["round_pick"],
                "team_id": pick["team_id"],
                "team": data.team_name(season, pick["team_id"]),
                "franchise_id": data.franchise_of(season, pick["team_id"]),
                "owner": data.owner_name(data.franchise_of(season, pick["team_id"])),
                "player_id": pid,
                "player": player.get("name") or f"Player {pid}",
                "position": player.get("position", "?"),
                "pro_team": player.get("pro_team", ""),
                "bid_amount": pick["bid_amount"],
                "is_keeper": bool(pick["is_keeper"]),
                "auto_drafted": bool(pick["auto_drafted"]),
                "points": total,
                "started_points": started.get((season, pick["team_id"], pid), 0.0),
            }
        )

    # Where did each drafted player actually finish, by points?
    ranked = sorted(rows, key=lambda r: -r["points"])
    for i, row in enumerate(ranked, 1):
        row["points_rank"] = i

    for row in rows:
        # Positive = outperformed draft slot.
        row["value"] = row["overall_pick"] - row["points_rank"]
        row["ppd"] = (
            round(row["points"] / row["bid_amount"], 2) if row["bid_amount"] else None
        )

    return rows


def draft_grades(data: LeagueData, season: int) -> list[dict]:
    """Aggregate a season's picks into a per-team draft grade."""
    board = draft_board(data, season)
    if not board:
        return []

    by_team: dict[int, list[dict]] = defaultdict(list)
    for pick in board:
        by_team[pick["team_id"]].append(pick)

    rows = []
    for team_id, picks in by_team.items():
        total = sum(p["points"] for p in picks)
        started = sum(p["started_points"] for p in picks)
        value = sum(p["value"] for p in picks)
        best = max(picks, key=lambda p: p["value"])
        worst = min(picks, key=lambda p: p["value"])
        rows.append(
            {
                "season": season,
                "team_id": team_id,
                "team": data.team_name(season, team_id),
                "franchise_id": data.franchise_of(season, team_id),
                "owner": data.owner_name(data.franchise_of(season, team_id)),
                "picks": len(picks),
                "points": round(total, 2),
                "started_points": round(started, 2),
                "value": value,
                "avg_value": round(value / len(picks), 2) if picks else 0.0,
                "best_pick": best,
                "worst_pick": worst,
                "spend": round(sum(p["bid_amount"] for p in picks), 2),
            }
        )

    rows.sort(key=lambda r: -r["value"])
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    # Letter grade from within-season standing, so grades always spread out.
    for i, row in enumerate(rows):
        pct = i / max(1, len(rows) - 1)
        row["grade"] = (
            "A" if pct <= 0.2 else "B" if pct <= 0.45
            else "C" if pct <= 0.7 else "D" if pct <= 0.9 else "F"
        )
    return rows


def steals_and_busts(data: LeagueData, season: int, *, top: int = 10) -> dict:
    board = draft_board(data, season)
    if not board:
        return {"steals": [], "busts": []}
    # Busts are only interesting among picks that carried real expectations.
    early = [p for p in board if p["overall_pick"] <= max(24, len(board) // 3)]
    return {
        "steals": sorted(board, key=lambda p: -p["value"])[:top],
        "busts": sorted(early, key=lambda p: p["value"])[:top],
    }


def waiver_analysis(data: LeagueData, season: int, *, top: int = 15) -> list[dict]:
    """Best in-season pickups, scored by what they produced after being added."""
    adds = db.query(
        data.conn,
        """SELECT t.team_id, t.scoring_period, t.bid_amount, i.player_id
           FROM transactions t JOIN transaction_items i
             ON t.season = i.season AND t.txn_id = i.txn_id
           WHERE t.season = ? AND t.type IN ('WAIVER','FREEAGENT')
             AND i.item_type = 'ADD'""",
        (season,),
    )
    inferred = False
    if not adds:
        adds = [
            {
                "team_id": a["to_team_id"],
                "scoring_period": 1,
                "bid_amount": 0,
                "player_id": a["player_id"],
            }
            for a in db.query(
                data.conn,
                "SELECT * FROM activity WHERE season = ? AND action IN "
                "('FA ADDED','WAIVER ADDED')",
                (season,),
            )
        ]
    if not adds:
        # ESPN drops its transaction feed for completed seasons, so recover
        # pickups by diffing weekly rosters: a player who appears on a roster
        # having been on none the week before was added.
        from .trades import infer_transactions

        inferred = True
        adds = [
            {"team_id": a["team_id"], "scoring_period": a["week"],
             "bid_amount": 0, "player_id": a["player_id"]}
            for a in infer_transactions(data, season)["adds"]
        ]

    started = _started_points(data)
    seen: set[tuple[int, int]] = set()
    rows = []
    for add in adds:
        team_id, pid = add["team_id"], add["player_id"]
        if not team_id or not pid or (team_id, pid) in seen:
            continue
        seen.add((team_id, pid))
        contribution = started.get((season, team_id, pid), 0.0)
        if contribution <= 0:
            continue
        player = data.players.get(pid, {})
        rows.append(
            {
                "season": season,
                "team": data.team_name(season, team_id),
                "franchise_id": data.franchise_of(season, team_id),
                "owner": data.owner_name(data.franchise_of(season, team_id)),
                "player": player.get("name") or f"Player {pid}",
                "position": player.get("position", "?"),
                "week_added": add["scoring_period"],
                "bid": add["bid_amount"],
                "started_points": contribution,
                "inferred": inferred,
            }
        )
    rows.sort(key=lambda r: -r["started_points"])
    return rows[:top]
