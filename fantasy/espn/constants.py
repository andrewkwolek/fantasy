"""ESPN fantasy football magic numbers.

ESPN's v3 API speaks entirely in integer IDs. These maps are the decoder ring.
"""
from __future__ import annotations

# lineupSlotId -> human label
LINEUP_SLOTS: dict[int, str] = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
    14: "DB", 15: "DP", 16: "D/ST", 17: "K", 18: "P", 19: "HC",
    20: "BE", 21: "IR", 22: "", 23: "FLEX", 24: "ER",
}

# Slots that do not count toward the weekly score.
BENCH_SLOTS: frozenset[int] = frozenset({20, 21, 24})

# defaultPositionId -> position
POSITIONS: dict[int, str] = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 7: "P",
    9: "DT", 10: "DE", 11: "LB", 12: "CB", 13: "S", 14: "DB",
    16: "D/ST", 17: "K",
}

# proTeamId -> NFL team abbreviation (0 = free agent)
PRO_TEAMS: dict[int, str] = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG",
    20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF",
    26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL",
    34: "HOU",
}

# Message type IDs on the league "communication" (activity) feed.
ACTIVITY_TYPES: dict[int, str] = {
    178: "FA ADDED", 179: "DROPPED", 180: "WAIVER ADDED", 181: "DROPPED",
    239: "DROPPED", 244: "TRADED", 224: "TRADED", 226: "TRADED",
}

# Transaction `type` values from the mTransactions2 view.
TRANSACTION_TYPES: frozenset[str] = frozenset(
    {"TRADE_ACCEPT", "TRADE_PROPOSAL", "WAIVER", "FREEAGENT", "ROSTER", "DRAFT"}
)

PLAYOFF_SEED_WINNER = 1

# Slot groups used when solving for a team's best-possible lineup. Each entry is
# (slot_id, set_of_positions_eligible). Order matters: the solver fills the most
# constrained slots first.
FLEX_ELIGIBILITY: dict[int, frozenset[str]] = {
    0: frozenset({"QB"}),
    2: frozenset({"RB"}),
    4: frozenset({"WR"}),
    6: frozenset({"TE"}),
    17: frozenset({"K"}),
    16: frozenset({"D/ST"}),
    3: frozenset({"RB", "WR"}),
    5: frozenset({"WR", "TE"}),
    23: frozenset({"RB", "WR", "TE"}),
    7: frozenset({"QB", "RB", "WR", "TE"}),
}


def slot_name(slot_id: int) -> str:
    return LINEUP_SLOTS.get(slot_id, f"S{slot_id}")


def position_name(position_id: int) -> str:
    return POSITIONS.get(position_id, "?")


def pro_team(team_id: int) -> str:
    return PRO_TEAMS.get(team_id, "FA")
