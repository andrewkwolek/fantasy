"""Trade forensics: who actually won each deal.

The honest way to grade a trade is to ask what the pieces did *afterward*, so
every trade is scored on two axes:

  production  -- total points the acquired players scored from the trade week
                 onward, regardless of whether they were started. Measures the
                 asset acquired.
  contribution -- points the acquired players scored while actually in the new
                 team's starting lineup. Measures the value realised.

Both are reported, because a trade can win on paper and lose in practice.
"""
from __future__ import annotations

from collections import defaultdict

from ..store import db
from .core import LeagueData


def _player_production(data: LeagueData) -> dict:
    """Indexes over player_weeks for fast post-trade lookups."""
    total: dict[tuple[int, int, int], float] = defaultdict(float)   # season,player,week
    by_team: dict[tuple[int, int, int, int], dict] = {}             # +team
    for (season, week, team_id), rows in data.player_weeks.items():
        for row in rows:
            key = (season, row["player_id"], week)
            total[key] = max(total[key], row["points"])
            by_team[(season, row["player_id"], week, team_id)] = row
    return {"total": total, "by_team": by_team}


def _sum_after(index: dict, season: int, player_id: int, from_week: int,
               team_id: int | None = None, started_only: bool = False) -> float:
    points = 0.0
    if team_id is None:
        for (s, pid, week), value in index["total"].items():
            if s == season and pid == player_id and week >= from_week:
                points += value
    else:
        for (s, pid, week, tid), row in index["by_team"].items():
            if s != season or pid != player_id or tid != team_id or week < from_week:
                continue
            if started_only and not row["is_starter"]:
                continue
            points += row["points"]
    return round(points, 2)


def _roster_timeline(data: LeagueData, season: int) -> tuple[list[int], dict[int, dict[int, int]]]:
    """scoring period -> {player_id: team_id} for one season."""
    byweek: dict[int, dict[int, int]] = defaultdict(dict)
    for (s, week, team_id), rows in data.player_weeks.items():
        if s != season:
            continue
        for row in rows:
            byweek[week][row["player_id"]] = team_id
    return sorted(byweek), byweek


def infer_transactions(data: LeagueData, season: int) -> dict[str, list[dict]]:
    """Reconstruct roster moves by diffing consecutive weeks' rosters.

    ESPN only serves its transaction feed for the *current* season -- for any
    completed year `mTransactions2` omits the array entirely and the activity
    endpoint 404s. But the weekly rosters we already store encode the same
    history: a player on team A in week N and team B in week N+1 changed hands.

    A pair of teams exchanging players in *both* directions in the same week is
    a trade; everything else is a waiver/free-agent add or a drop. That test can
    in principle be fooled by two managers swapping each other's drops in the
    same week, so results are labelled as inferred rather than reported as fact.
    """
    weeks, byweek = _roster_timeline(data, season)
    trades: list[dict] = []
    adds: list[dict] = []

    for prev_week, week in zip(weeks, weeks[1:]):
        prev, cur = byweek[prev_week], byweek[week]
        moves: list[tuple[int, int, int]] = []
        for pid, team in cur.items():
            if pid not in prev:
                adds.append({"player_id": pid, "team_id": team, "week": week})
            elif prev[pid] != team:
                moves.append((pid, prev[pid], team))

        # Group this week's moves by the unordered pair of teams involved.
        pairs: dict[frozenset, dict[tuple[int, int], list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for pid, src, dst in moves:
            pairs[frozenset((src, dst))][(src, dst)].append(pid)

        for teams, directions in pairs.items():
            if len(directions) < 2:
                continue  # one-way movement is a waiver claim, not a trade
            trades.append(
                {
                    "season": season,
                    "trade_id": f"inferred-{season}-{week}-{'-'.join(str(t) for t in sorted(teams))}",
                    "week": week,
                    "date": 0,
                    "moves": [
                        {"player_id": pid, "from_team_id": src, "to_team_id": dst,
                         "season": season}
                        for (src, dst), pids in directions.items()
                        for pid in pids
                    ],
                    "source": "inferred",
                }
            )
    return {"trades": trades, "adds": adds}


def _load_trades(data: LeagueData) -> list[dict]:
    """Assemble trades per season: reported where ESPN has them, inferred elsewhere."""
    reported: dict[int, list[dict]] = defaultdict(list)

    items_by_txn: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for item in db.query(data.conn, "SELECT * FROM transaction_items"):
        items_by_txn[(item["season"], item["txn_id"])].append(item)

    for txn in db.query(
        data.conn,
        "SELECT * FROM transactions WHERE type LIKE 'TRADE%' ORDER BY season, execution_date",
    ):
        moves = [
            i for i in items_by_txn.get((txn["season"], txn["txn_id"]), [])
            if i["from_team_id"] and i["to_team_id"]
        ]
        if moves:
            reported[txn["season"]].append(
                {
                    "season": txn["season"],
                    "trade_id": txn["txn_id"],
                    "week": txn["scoring_period"] or 1,
                    "date": txn["execution_date"],
                    "moves": moves,
                    "source": "reported",
                }
            )

    # The activity feed is a second reported source where it exists.
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for act in db.query(data.conn, "SELECT * FROM activity WHERE action = 'TRADED'"):
        grouped[(act["season"], act["topic_id"])].append(act)
    for (season, topic), acts in grouped.items():
        moves = [
            {"player_id": a["player_id"], "from_team_id": a["from_team_id"],
             "to_team_id": a["to_team_id"], "season": season}
            for a in acts if a["player_id"] and a["to_team_id"]
        ]
        if moves and not reported[season]:
            reported[season].append(
                {"season": season, "trade_id": topic, "week": 1,
                 "date": acts[0]["date"], "moves": moves, "source": "reported"}
            )

    trades: list[dict] = []
    for season in data.season_years:
        if reported.get(season):
            trades.extend(reported[season])
        else:
            trades.extend(infer_transactions(data, season)["trades"])
    return trades


def analyze_trades(data: LeagueData) -> list[dict]:
    index = _player_production(data)
    results = []

    for trade in _load_trades(data):
        season, week = trade["season"], trade["week"]
        sides: dict[int, dict] = defaultdict(
            lambda: {"received": [], "sent": [], "production": 0.0, "contribution": 0.0}
        )

        for move in trade["moves"]:
            pid = move["player_id"]
            to_team, from_team = move["to_team_id"], move["from_team_id"]
            player = data.players.get(pid, {})
            name = player.get("name") or f"Player {pid}"

            production = _sum_after(index, season, pid, week)
            contribution = _sum_after(index, season, pid, week, team_id=to_team, started_only=True)
            asset = {
                "player_id": pid,
                "name": name,
                "position": player.get("position", "?"),
                "production": production,
                "contribution": contribution,
            }
            if to_team:
                sides[to_team]["received"].append(asset)
                sides[to_team]["production"] += production
                sides[to_team]["contribution"] += contribution
            if from_team:
                sides[from_team]["sent"].append(asset)

        if len(sides) < 2:
            continue

        for team_id, side in sides.items():
            side["team_id"] = team_id
            side["team"] = data.team_name(season, team_id)
            side["owner"] = data.owner_name(data.franchise_of(season, team_id))
            side["franchise_id"] = data.franchise_of(season, team_id)
            side["given_production"] = round(sum(a["production"] for a in side["sent"]), 2)
            side["production"] = round(side["production"], 2)
            side["contribution"] = round(side["contribution"], 2)
            side["net"] = round(side["production"] - side["given_production"], 2)

        ordered = sorted(sides.values(), key=lambda s: -s["net"])
        best, worst = ordered[0], ordered[-1]
        results.append(
            {
                "season": season,
                "trade_id": trade["trade_id"],
                "source": trade.get("source", "reported"),
                "week": week,
                "date": trade["date"],
                "sides": ordered,
                "player_count": len(trade["moves"]),
                "winner": best["team"] if best["net"] > worst["net"] else None,
                "winner_franchise": best["franchise_id"] if best["net"] > worst["net"] else None,
                "verdict_margin": round(best["net"] - worst["net"], 2),
                "lopsided": best["net"] - worst["net"] > 100,
            }
        )

    results.sort(key=lambda t: (-t["season"], -t["week"]))
    return results


def trade_leaderboard(data: LeagueData, trades: list[dict]) -> list[dict]:
    """Per-franchise trading record across league history."""
    tally: dict[str, dict] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "net": 0.0, "acquired": 0.0}
    )
    for trade in trades:
        for side in trade["sides"]:
            fid = side["franchise_id"]
            if not fid:
                continue
            row = tally[fid]
            row["trades"] += 1
            row["net"] += side["net"]
            row["acquired"] += side["production"]
            if trade["winner_franchise"] == fid:
                row["wins"] += 1
            elif trade["winner_franchise"]:
                row["losses"] += 1

    rows = []
    for fid, row in tally.items():
        rows.append(
            {
                "franchise_id": fid,
                "name": data.franchise_name(fid),
                "owner": data.owner_name(fid),
                "trades": row["trades"],
                "wins": row["wins"],
                "losses": row["losses"],
                "net": round(row["net"], 2),
                "avg_net": round(row["net"] / row["trades"], 2) if row["trades"] else 0.0,
                "acquired": round(row["acquired"], 2),
            }
        )
    rows.sort(key=lambda r: -r["net"])
    return rows
