"""Generate a synthetic league in ESPN's exact data shape.

Purpose is verification, not entertainment: it exercises multi-season franchise
stitching, renamed teams, mid-season trades that actually move players between
rosters, deliberately imperfect lineup decisions (so efficiency is < 100%), and
playoff brackets. Anything the analytics layer can compute on real data, it can
compute on this.
"""
from __future__ import annotations

import random

from .store import db

FIRST_NAMES = ["Alex", "Sam", "Jordan", "Casey", "Riley", "Morgan", "Taylor",
               "Jamie", "Avery", "Quinn", "Reese", "Rowan"]
LAST_NAMES = ["Nguyen", "Okafor", "Rivera", "Kowalski", "Bianchi", "Haddad",
              "Lindqvist", "Moreau", "Tanaka", "Delgado", "Fischer", "Ahmed"]
TEAM_WORDS = [
    ("Gridiron", "Goblins"), ("Turf", "Tyrants"), ("Blitz", "Brigade"),
    ("Hail Mary", "Hooligans"), ("Pylon", "Pirates"), ("Audible", "Anarchy"),
    ("Red Zone", "Rhinos"), ("Play Action", "Pandas"), ("Sack", "Sultans"),
    ("Two Minute", "Titans"), ("Bootleg", "Badgers"), ("Screen Pass", "Sharks"),
]

# position -> (weekly mean, weekly stdev, roster count)
POSITION_MODEL = {
    "QB":   (18.0, 6.5, 2),
    "RB":   (11.0, 6.0, 4),
    "WR":   (10.5, 6.5, 5),
    "TE":   (8.0, 5.0, 2),
    "K":    (8.0, 3.0, 1),
    "D/ST": (7.5, 4.5, 1),
}
ELIGIBLE = {
    "QB": [0, 7, 20], "RB": [2, 3, 23, 7, 20], "WR": [4, 3, 5, 23, 7, 20],
    "TE": [6, 5, 23, 7, 20], "K": [17, 20], "D/ST": [16, 20],
}
LINEUP_SLOTS = {"0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1, "17": 1, "20": 7}
STARTABLE = 9


def _player_pool(rng: random.Random, count: int) -> list[dict]:
    pool, pid = [], 1000
    for position, (mean, sd, _) in POSITION_MODEL.items():
        for i in range(count):
            pid += 1
            # Per-player talent multiplier gives the pool a realistic spread.
            pool.append({
                "player_id": pid,
                "name": f"{position}{i + 1} {rng.choice(LAST_NAMES)}",
                "position": position,
                "pro_team": rng.choice(["KC", "SF", "BUF", "DAL", "PHI", "BAL", "DET"]),
                "talent": rng.gauss(1.0, 0.28),
                "mean": mean, "sd": sd,
            })
    return pool


def _pick_starters(rng: random.Random, roster: list[dict], scores: dict) -> set[int]:
    """Choose a lineup the way a human does: mostly right, sometimes not.

    Managers set lineups on projections, not results, so we rank by a noisy
    estimate of each player's score. That reliably produces efficiency in the
    85-99% band, which is what real leagues look like.
    """
    guess = {
        p["player_id"]: scores[p["player_id"]] + rng.gauss(0, 5.5) for p in roster
    }
    by_pos: dict[str, list[dict]] = {}
    for p in roster:
        by_pos.setdefault(p["position"], []).append(p)
    for players in by_pos.values():
        players.sort(key=lambda p: -guess[p["player_id"]])

    starters: list[dict] = []
    for position, need in (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1),
                           ("D/ST", 1), ("K", 1)):
        starters.extend(by_pos.get(position, [])[:need])
    used = {p["player_id"] for p in starters}
    flex = [p for p in roster if p["position"] in ("RB", "WR", "TE")
            and p["player_id"] not in used]
    if flex:
        starters.append(max(flex, key=lambda p: guess[p["player_id"]]))
    return {p["player_id"] for p in starters[:STARTABLE]}


def build_demo_league(*, years: int = 6, teams: int = 10, seed: int = 42) -> int:
    rng = random.Random(seed)
    end_year = 2026
    seasons = list(range(end_year - years + 1, end_year + 1))

    guids = [f"{{DEMO-{i:04d}}}" for i in range(teams)]
    owners = [
        {"guid": guids[i],
         "first": FIRST_NAMES[i % len(FIRST_NAMES)],
         "last": LAST_NAMES[i % len(LAST_NAMES)]}
        for i in range(teams)
    ]
    # Persistent manager skill -- drives realistic multi-year dynasties.
    skill = {g["guid"]: rng.gauss(1.0, 0.07) for g in owners}

    with db.session() as conn:
        for table in ("seasons", "members", "teams", "matchups", "player_weeks",
                      "players", "draft_picks", "transactions",
                      "transaction_items", "activity", "franchises"):
            conn.execute(f"DELETE FROM {table}")

        all_players: dict[int, dict] = {}

        for si, season in enumerate(seasons):
            is_current = season == seasons[-1]
            reg_weeks = 14
            pool = _player_pool(rng, count=teams * 3)
            all_players.update({p["player_id"]: p for p in pool})

            # ---- teams (ids shuffle year to year; names drift) -------------
            team_ids = list(range(1, teams + 1))
            rng.shuffle(team_ids)
            assign = dict(zip(guids, team_ids))
            team_meta = {}
            for i, guid in enumerate(guids):
                words = TEAM_WORDS[(i + si) % len(TEAM_WORDS)]
                team_meta[assign[guid]] = {
                    "guid": guid,
                    "name": f"{words[0]} {words[1]}",
                    "abbrev": (words[1][:3]).upper(),
                }

            db.upsert(conn, "seasons", [{
                "season": season, "name": "The Demo Dynasty League", "size": teams,
                "reg_season_weeks": reg_weeks, "playoff_teams": 6,
                "playoff_matchup_len": 1, "draft_type": "SNAKE",
                "auction_budget": 0, "keeper_count": 0,
                "lineup_slots": LINEUP_SLOTS,
                "current_matchup_period": 9 if is_current else reg_weeks + 3,
                "latest_scoring_period": 8 if is_current else reg_weeks + 3,
                "final_scoring_period": reg_weeks + 3,
                "first_scoring_period": 1,
                "is_active": is_current, "scoring_type": "H2H_POINTS",
            }])
            db.upsert(conn, "members", [{
                "guid": o["guid"], "season": season,
                "display_name": f"{o['first']} {o['last']}",
                "first_name": o["first"], "last_name": o["last"],
                "is_manager": o is owners[0],
            } for o in owners])

            # ---- draft: snake order over the talent-sorted pool -------------
            order = list(team_ids)
            rng.shuffle(order)
            ranked = sorted(pool, key=lambda p: -p["talent"] * p["mean"])
            rosters: dict[int, list[dict]] = {t: [] for t in team_ids}
            need = {t: dict((k, v[2]) for k, v in POSITION_MODEL.items()) for t in team_ids}
            picks, overall, undrafted = [], 0, list(ranked)

            for rnd in range(1, sum(v[2] for v in POSITION_MODEL.values()) + 1):
                seq = order if rnd % 2 else order[::-1]
                for slot, team_id in enumerate(seq, 1):
                    choice = next(
                        (p for p in undrafted if need[team_id][p["position"]] > 0), None
                    )
                    if choice is None:
                        continue
                    undrafted.remove(choice)
                    need[team_id][choice["position"]] -= 1
                    rosters[team_id].append(choice)
                    overall += 1
                    picks.append({
                        "season": season, "overall_pick": overall, "round": rnd,
                        "round_pick": slot, "team_id": team_id,
                        "player_id": choice["player_id"], "bid_amount": 0,
                        "is_keeper": False, "auto_drafted": False,
                    })
            db.upsert(conn, "draft_picks", picks)

            # ---- mid-season trades actually move the players ---------------
            trade_weeks = sorted(rng.sample(range(3, 11), k=rng.randint(1, 3)))
            trades = []
            for n, week in enumerate(trade_weeks):
                a, b = rng.sample(team_ids, 2)
                if len(rosters[a]) < 3 or len(rosters[b]) < 3:
                    continue
                give = rng.choice([p for p in rosters[a] if p["position"] != "K"])
                get = rng.choice([p for p in rosters[b] if p["position"] != "K"])
                trades.append((week, a, b, give, get))

            # ---- weekly simulation -----------------------------------------
            weeks_played = 8 if is_current else reg_weeks + 3
            roster_now = {t: list(r) for t, r in rosters.items()}
            week_scores: dict[int, dict[int, float]] = {}
            player_rows: list[dict] = []

            for week in range(1, weeks_played + 1):
                for tw, a, b, give, get in trades:
                    if tw != week:
                        continue
                    if give in roster_now[a] and get in roster_now[b]:
                        roster_now[a].remove(give); roster_now[a].append(get)
                        roster_now[b].remove(get); roster_now[b].append(give)

                week_scores[week] = {}
                for team_id, roster in roster_now.items():
                    boost = skill[team_meta[team_id]["guid"]]
                    scores = {
                        p["player_id"]: max(
                            0.0, round(rng.gauss(p["mean"] * p["talent"] * boost, p["sd"]), 1)
                        )
                        for p in roster
                    }
                    starters = _pick_starters(rng, roster, scores)
                    total = 0.0
                    for p in roster:
                        started = p["player_id"] in starters
                        slot = (ELIGIBLE[p["position"]][0] if started else 20)
                        if started:
                            total += scores[p["player_id"]]
                        player_rows.append({
                            "season": season, "week": week, "team_id": team_id,
                            "player_id": p["player_id"], "player_name": p["name"],
                            "position": p["position"], "pro_team": p["pro_team"],
                            "slot_id": slot,
                            "slot": "BE" if not started else p["position"],
                            "is_starter": started,
                            "points": scores[p["player_id"]],
                            "projected": round(p["mean"] * p["talent"], 1),
                            "injury_status": "ACTIVE",
                            "eligible_slots": ELIGIBLE[p["position"]],
                        })
                    week_scores[week][team_id] = round(total, 2)

            db.upsert(conn, "player_weeks", player_rows)
            db.upsert(conn, "players", [{
                "player_id": p["player_id"], "name": p["name"],
                "position": p["position"], "pro_team": p["pro_team"],
            } for p in all_players.values()])

            # ---- schedule: rotating round robin ----------------------------
            # The full regular season is always scheduled. Weeks beyond the
            # current point carry no result, exactly as ESPN reports them, so
            # the projection code has real future games to simulate.
            matchups, mid = [], 0
            rotation = team_ids[:]
            for week in range(1, reg_weeks + 1):
                played = week <= weeks_played
                half = len(rotation) // 2
                pairs = list(zip(rotation[:half], rotation[half:][::-1]))
                for home, away in pairs:
                    mid += 1
                    hp = week_scores[week][home] if played else 0.0
                    ap = week_scores[week][away] if played else 0.0
                    winner = None
                    if played:
                        winner = home if hp > ap else (away if ap > hp else 0)
                    matchups.append({
                        "season": season, "matchup_id": mid, "matchup_period": week,
                        "home_team_id": home, "away_team_id": away,
                        "home_points": hp, "away_points": ap,
                        "winner_team_id": winner,
                        "playoff_tier": "NONE", "is_playoff": False,
                        "is_consolation": False,
                        "margin": round(abs(hp - ap), 2), "total": round(hp + ap, 2),
                    })
                rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]

            # ---- playoffs for completed seasons ----------------------------
            final_rank: dict[int, int] = {}
            if not is_current:
                tally = {t: {"w": 0.0, "pf": 0.0} for t in team_ids}
                for m in [x for x in matchups if x["winner_team_id"] is not None]:
                    tally[m["home_team_id"]]["pf"] += m["home_points"]
                    tally[m["away_team_id"]]["pf"] += m["away_points"]
                    if m["winner_team_id"] == 0:
                        tally[m["home_team_id"]]["w"] += 0.5
                        tally[m["away_team_id"]]["w"] += 0.5
                    else:
                        tally[m["winner_team_id"]]["w"] += 1
                seeded = sorted(team_ids, key=lambda t: (-tally[t]["w"], -tally[t]["pf"]))
                bracket = seeded[:6]

                # Round 1: seeds 1-2 bye; 3v6, 4v5.
                def play(a: int, b: int, week: int) -> tuple[int, int]:
                    nonlocal mid
                    mid += 1
                    ap, bp = week_scores[week][a], week_scores[week][b]
                    win, lose = (a, b) if ap >= bp else (b, a)
                    matchups.append({
                        "season": season, "matchup_id": mid, "matchup_period": week,
                        "home_team_id": a, "away_team_id": b,
                        "home_points": ap, "away_points": bp,
                        "winner_team_id": win, "playoff_tier": "WINNERS_BRACKET",
                        "is_playoff": True, "is_consolation": False,
                        "margin": round(abs(ap - bp), 2), "total": round(ap + bp, 2),
                    })
                    return win, lose

                w1, _ = play(bracket[2], bracket[5], reg_weeks + 1)
                w2, _ = play(bracket[3], bracket[4], reg_weeks + 1)
                f1, s1 = play(bracket[0], w2, reg_weeks + 2)
                f2, s2 = play(bracket[1], w1, reg_weeks + 2)
                champ, runner = play(f1, f2, reg_weeks + 3)
                third, fourth = play(s1, s2, reg_weeks + 3)

                final_rank = {champ: 1, runner: 2, third: 3, fourth: 4}
                for i, t in enumerate(
                    [t for t in seeded if t not in final_rank], start=5
                ):
                    final_rank[t] = i

            db.upsert(conn, "matchups", matchups)

            # ---- team standings rows ---------------------------------------
            rows = []
            for team_id in team_ids:
                w = l = t = 0
                pf = pa = 0.0
                for m in matchups:
                    if m["is_playoff"] or m["winner_team_id"] is None:
                        continue
                    for side, other in (("home", "away"), ("away", "home")):
                        if m[f"{side}_team_id"] != team_id:
                            continue
                        pf += m[f"{side}_points"]
                        pa += m[f"{other}_points"]
                        if m["winner_team_id"] == 0:
                            t += 1
                        elif m["winner_team_id"] == team_id:
                            w += 1
                        else:
                            l += 1
                meta = team_meta[team_id]
                rows.append({
                    "season": season, "team_id": team_id, "name": meta["name"],
                    "abbrev": meta["abbrev"], "owner_guid": meta["guid"],
                    "all_owner_guids": meta["guid"], "logo": "", "division_id": 0,
                    "wins": w, "losses": l, "ties": t,
                    "points_for": round(pf, 2), "points_against": round(pa, 2),
                    "playoff_seed": 0, "final_rank": final_rank.get(team_id, 0),
                    "streak_length": 0, "streak_type": "",
                    "acquisitions": rng.randint(4, 30), "drops": rng.randint(4, 30),
                    "trades": sum(1 for tr in trades if team_id in (tr[1], tr[2])),
                    "waiver_rank": 0, "franchise_id": None,
                })
            db.upsert(conn, "teams", rows)

            # ---- transaction records for the trades ------------------------
            txns, items = [], []
            for n, (week, a, b, give, get) in enumerate(trades):
                txn_id = f"{season}-trade-{n}"
                txns.append({
                    "season": season, "txn_id": txn_id, "type": "TRADE_ACCEPT",
                    "status": "EXECUTED", "team_id": a,
                    "member_guid": team_meta[a]["guid"], "scoring_period": week,
                    "bid_amount": 0, "proposed_date": 0, "execution_date": week,
                })
                items.append({
                    "season": season, "txn_id": txn_id, "player_id": give["player_id"],
                    "item_type": "TRADE", "from_team_id": a, "to_team_id": b,
                    "scoring_period": week,
                })
                items.append({
                    "season": season, "txn_id": txn_id, "player_id": get["player_id"],
                    "item_type": "TRADE", "from_team_id": b, "to_team_id": a,
                    "scoring_period": week,
                })
            db.upsert(conn, "transactions", txns)
            db.upsert(conn, "transaction_items", items)

        db.rebuild_franchises(conn)
        db.set_meta(conn, "league_id", 0)
        db.set_meta(conn, "seasons", seasons)
        db.set_meta(conn, "demo", True)

    return len(seasons)
