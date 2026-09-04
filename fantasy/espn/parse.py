"""Turn raw ESPN JSON into flat, database-shaped rows.

Design note on identity: ESPN team IDs are only stable *within* a season, and
team names change constantly. The durable identity across years is the owner's
member GUID, so every team row carries `owner_guid` and the store layer folds
those into long-lived franchises.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from .constants import BENCH_SLOTS, position_name, pro_team, slot_name

log = logging.getLogger(__name__)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def _team_display(team: dict) -> str:
    """ESPN moved from location+nickname to a single `name` field mid-life."""
    name = (team.get("name") or "").strip()
    if name:
        return name
    combined = f"{team.get('location', '')} {team.get('nickname', '')}".strip()
    return combined or f"Team {team.get('id')}"


# --------------------------------------------------------------------- season


def parse_season(payload: dict, season: int) -> dict:
    settings = payload.get("settings") or {}
    schedule_settings = settings.get("scheduleSettings") or {}
    roster_settings = settings.get("rosterSettings") or {}
    draft_settings = settings.get("draftSettings") or {}
    status = payload.get("status") or {}

    return {
        "season": season,
        "name": settings.get("name") or f"Season {season}",
        "size": settings.get("size") or 0,
        "reg_season_weeks": schedule_settings.get("matchupPeriodCount") or 0,
        "playoff_teams": schedule_settings.get("playoffTeamCount") or 0,
        "playoff_matchup_len": schedule_settings.get("playoffMatchupPeriodLength") or 1,
        "draft_type": draft_settings.get("type") or "",
        "auction_budget": draft_settings.get("auctionBudget") or 0,
        "keeper_count": draft_settings.get("keeperCount") or 0,
        "lineup_slots": roster_settings.get("lineupSlotCounts") or {},
        "current_matchup_period": status.get("currentMatchupPeriod") or 0,
        "latest_scoring_period": status.get("latestScoringPeriod") or 0,
        "final_scoring_period": status.get("finalScoringPeriod") or 0,
        "first_scoring_period": status.get("firstScoringPeriod") or 1,
        "is_active": bool(status.get("isActive")),
        "scoring_type": (settings.get("scoringSettings") or {}).get("scoringType") or "",
    }


# --------------------------------------------------------------- members/teams


def parse_members(payload: dict, season: int) -> list[dict]:
    rows = []
    for member in payload.get("members") or []:
        guid = (member.get("id") or "").strip()
        if not guid:
            continue
        first = (member.get("firstName") or "").strip()
        last = (member.get("lastName") or "").strip()
        display = (member.get("displayName") or "").strip()
        # Most ESPN accounts carry an auto-generated handle (ESPNFAN0337619620)
        # as displayName, so a real first/last name wins when we have one.
        real_name = f"{first} {last}".strip()
        rows.append(
            {
                "guid": guid,
                "season": season,
                "display_name": real_name or display or "Unknown",
                "first_name": first,
                "last_name": last,
                "is_manager": bool(member.get("isLeagueManager")),
            }
        )
    return rows


def parse_teams(payload: dict, season: int) -> list[dict]:
    rows = []
    for team in payload.get("teams") or []:
        record = ((team.get("record") or {}).get("overall")) or {}
        owners = team.get("owners") or []
        counter = team.get("transactionCounter") or {}
        rows.append(
            {
                "season": season,
                "team_id": team.get("id"),
                "name": _team_display(team),
                "abbrev": (team.get("abbrev") or "").strip(),
                "owner_guid": (owners[0] if owners else "") or "",
                "all_owner_guids": ",".join(owners),
                "logo": team.get("logo") or "",
                "division_id": team.get("divisionId"),
                "wins": record.get("wins") or 0,
                "losses": record.get("losses") or 0,
                "ties": record.get("ties") or 0,
                "points_for": _num(record.get("pointsFor")),
                "points_against": _num(record.get("pointsAgainst")),
                "playoff_seed": team.get("playoffSeed") or 0,
                "final_rank": team.get("rankCalculatedFinal") or 0,
                "streak_length": record.get("streakLength") or 0,
                "streak_type": record.get("streakType") or "",
                "acquisitions": counter.get("acquisitions") or 0,
                "drops": counter.get("drops") or 0,
                "trades": counter.get("trades") or 0,
                "waiver_rank": team.get("waiverRank") or 0,
            }
        )
    return rows


# ------------------------------------------------------------------- matchups


def parse_matchups(payload: dict, season: int) -> list[dict]:
    """One row per scheduled matchup.

    `winner` is normalised to the winning team_id (0 for a tie, None if the
    game has not been played). Bye weeks -- an entry with no `away` -- are
    skipped, since they are not real games.
    """
    rows = []
    for game in payload.get("schedule") or []:
        home = game.get("home") or {}
        away = game.get("away")
        if not home or not away:
            continue  # playoff bye

        home_id, away_id = home.get("teamId"), away.get("teamId")
        home_pts = _num(home.get("totalPoints"))
        away_pts = _num(away.get("totalPoints"))
        raw_winner = game.get("winner") or "UNDECIDED"

        if raw_winner == "HOME":
            winner = home_id
        elif raw_winner == "AWAY":
            winner = away_id
        elif raw_winner == "TIE":
            winner = 0
        else:
            winner = None

        tier = game.get("playoffTierType") or "NONE"
        rows.append(
            {
                "season": season,
                "matchup_id": game.get("id"),
                "matchup_period": game.get("matchupPeriodId") or 0,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_points": home_pts,
                "away_points": away_pts,
                "winner_team_id": winner,
                "playoff_tier": tier,
                "is_playoff": tier not in ("NONE", ""),
                "is_consolation": "CONSOLATION" in tier or "LOSERS" in tier,
                "margin": round(abs(home_pts - away_pts), 2),
                "total": round(home_pts + away_pts, 2),
            }
        )
    return rows


# --------------------------------------------------------------- player weeks


def _stat_totals(player: dict, week: int) -> tuple[float, float]:
    """Return (actual, projected) points for `week`.

    statSourceId 0 = actual, 1 = projection. We also require the stat row to be
    for this scoring period, since ESPN ships season totals in the same list.
    """
    actual = projected = 0.0
    for stat in player.get("stats") or []:
        if stat.get("scoringPeriodId") != week:
            continue
        total = _num(stat.get("appliedTotal"))
        if stat.get("statSourceId") == 0:
            actual = total
        elif stat.get("statSourceId") == 1:
            projected = total
    return actual, projected


def parse_player_weeks(payload: dict, season: int, week: int) -> list[dict]:
    """Player-level rows for one scoring period, from a mMatchup fetch."""
    rows = []
    for game in payload.get("schedule") or []:
        if (game.get("matchupPeriodId") or 0) == 0:
            continue
        for side in ("home", "away"):
            entry = game.get(side)
            if not entry:
                continue
            team_id = entry.get("teamId")
            # ESPN puts the week's roster under rosterForCurrentScoringPeriod when
            # the request pins a scoringPeriodId, but some seasons only populate
            # rosterForMatchupPeriod. Take whichever is present.
            roster = entry.get("rosterForCurrentScoringPeriod") or {}
            entries = roster.get("entries") or []
            if not entries:
                entries = (entry.get("rosterForMatchupPeriod") or {}).get("entries") or []
            for slot_entry in entries:
                pool = slot_entry.get("playerPoolEntry") or {}
                player = pool.get("player") or {}
                pid = slot_entry.get("playerId") or player.get("id")
                if pid is None:
                    continue
                slot = slot_entry.get("lineupSlotId")
                actual, projected = _stat_totals(player, week)
                rows.append(
                    {
                        "season": season,
                        "week": week,
                        "team_id": team_id,
                        "player_id": pid,
                        "player_name": player.get("fullName") or f"Player {pid}",
                        "position": position_name(player.get("defaultPositionId") or 0),
                        "pro_team": pro_team(player.get("proTeamId") or 0),
                        "slot_id": slot,
                        "slot": slot_name(slot) if slot is not None else "",
                        "is_starter": slot not in BENCH_SLOTS,
                        "points": actual,
                        "projected": projected,
                        "injury_status": player.get("injuryStatus") or "",
                        "eligible_slots": player.get("eligibleSlots") or [],
                    }
                )
    return rows


# ----------------------------------------------------------------- draft/txn


def parse_draft(payload: dict, season: int) -> list[dict]:
    detail = payload.get("draftDetail") or {}
    if not detail.get("drafted"):
        return []
    rows = []
    for pick in detail.get("picks") or []:
        rows.append(
            {
                "season": season,
                "overall_pick": pick.get("overallPickNumber") or 0,
                "round": pick.get("roundId") or 0,
                "round_pick": pick.get("roundPickNumber") or 0,
                "team_id": pick.get("teamId"),
                "player_id": pick.get("playerId"),
                "bid_amount": _num(pick.get("bidAmount")),
                "is_keeper": bool(pick.get("keeper")),
                "auto_drafted": (pick.get("autoDraftTypeId") or 0) != 0,
            }
        )
    return rows


def parse_transactions(payload: dict, season: int) -> tuple[list[dict], list[dict]]:
    """Split the transaction feed into (transactions, transaction_items)."""
    txns, items = [], []
    for txn in payload.get("transactions") or []:
        txn_id = txn.get("id")
        if txn_id is None:
            continue
        status = txn.get("status") or ""
        if status not in ("EXECUTED", ""):
            continue  # skip pending/vetoed proposals
        txns.append(
            {
                "season": season,
                "txn_id": str(txn_id),
                "type": txn.get("type") or "",
                "status": status,
                "team_id": txn.get("teamId"),
                "member_guid": txn.get("memberId") or "",
                "scoring_period": txn.get("scoringPeriodId") or 0,
                "bid_amount": _num(txn.get("bidAmount")),
                "proposed_date": txn.get("proposedDate") or 0,
                "execution_date": txn.get("processDate") or txn.get("proposedDate") or 0,
            }
        )
        for item in txn.get("items") or []:
            items.append(
                {
                    "season": season,
                    "txn_id": str(txn_id),
                    "player_id": item.get("playerId"),
                    "item_type": item.get("type") or "",
                    "from_team_id": item.get("fromTeamId") or 0,
                    "to_team_id": item.get("toTeamId") or 0,
                    "scoring_period": txn.get("scoringPeriodId") or 0,
                }
            )
    return txns, items


def parse_activity(payload: dict, season: int) -> list[dict]:
    """Fallback transaction source: the league communication feed."""
    from .constants import ACTIVITY_TYPES

    rows = []
    for topic in payload.get("topics") or []:
        when = topic.get("date") or 0
        for msg in topic.get("messages") or []:
            type_id = msg.get("messageTypeId")
            if type_id not in ACTIVITY_TYPES:
                continue
            rows.append(
                {
                    "season": season,
                    "topic_id": str(topic.get("id")),
                    "message_id": str(msg.get("id")),
                    "date": when,
                    "action": ACTIVITY_TYPES[type_id],
                    "player_id": msg.get("targetId"),
                    "to_team_id": msg.get("to") or 0,
                    "from_team_id": msg.get("for") or 0,
                }
            )
    return rows


def collect_players(player_weeks: Iterable[dict]) -> dict[int, dict]:
    """Build an id -> player identity map from the weekly rows we already have.

    Saves us from a separate (and heavily rate-limited) player-universe fetch.
    """
    players: dict[int, dict] = {}
    for row in player_weeks:
        pid = row["player_id"]
        current = players.get(pid)
        if current is None:
            players[pid] = {
                "player_id": pid,
                "name": row["player_name"],
                "position": row["position"],
                "pro_team": row["pro_team"],
            }
        elif row["player_name"] and not row["player_name"].startswith("Player "):
            current["name"] = row["player_name"]
            current["position"] = row["position"] or current["position"]
            current["pro_team"] = row["pro_team"] or current["pro_team"]
    return players
