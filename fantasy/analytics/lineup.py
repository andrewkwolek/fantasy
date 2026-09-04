"""Exact best-possible-lineup solver.

"Points left on the bench" is only meaningful if the benchmark is the *true*
optimum. A greedy fill (best QB, best RB, ... then flex) is not: it can strand a
high-scoring flex-eligible player behind a slot decision made earlier. So we
solve it properly as a max-weight bipartite matching between lineup slots and
rostered players, via the Hungarian algorithm. Rosters are ~20 players against
~10 slots, so an O(n^3) exact method costs microseconds.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from ..espn.constants import BENCH_SLOTS, FLEX_ELIGIBILITY

INELIGIBLE = 1e7


def slot_instances(slot_counts: dict) -> list[int]:
    """Expand {slot_id: count} into a flat list of startable slot IDs."""
    instances: list[int] = []
    for raw_slot, count in (slot_counts or {}).items():
        slot_id = int(raw_slot)
        if slot_id in BENCH_SLOTS:
            continue
        instances.extend([slot_id] * int(count or 0))
    return sorted(instances)


def _eligible(player: dict, slot_id: int) -> bool:
    """Can this player legally start in this slot?"""
    slots = player.get("eligible_slots")
    if slots:
        return slot_id in slots
    # Very old seasons omit eligibleSlots; fall back to position rules.
    allowed = FLEX_ELIGIBILITY.get(slot_id)
    return bool(allowed and player.get("position") in allowed)


def _hungarian(cost: Sequence[Sequence[float]]) -> list[int]:
    """Minimum-cost assignment for a rows x cols matrix with rows <= cols.

    Returns `assignment[row] = col` (or -1). Standard O(n^3) JV formulation with
    potentials; `p` tracks which row currently owns each column.
    """
    n, m = len(cost), len(cost[0])
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = -1
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    assignment = [-1] * n
    for j in range(1, m + 1):
        if p[j] > 0:
            assignment[p[j] - 1] = j - 1
    return assignment


def optimal_lineup(
    players: Iterable[dict], slot_counts: dict
) -> tuple[float, list[dict]]:
    """Return (best_possible_points, chosen_players_with_slot).

    `players` is a team's full roster for one week (starters *and* bench), each
    with `points` and ideally `eligible_slots`.
    """
    roster = [p for p in players if p.get("points") is not None]
    slots = slot_instances(slot_counts)
    if not roster or not slots:
        return 0.0, []

    # Pad the player side so the matrix is never wider-than-tall in reverse.
    n_slots, n_players = len(slots), len(roster)
    width = max(n_players, n_slots)

    cost: list[list[float]] = []
    for slot_id in slots:
        row = []
        for idx in range(width):
            if idx >= n_players or not _eligible(roster[idx], slot_id):
                row.append(INELIGIBLE)
            else:
                row.append(-float(roster[idx].get("points") or 0.0))
        cost.append(row)

    assignment = _hungarian(cost)

    total = 0.0
    chosen = []
    for slot_idx, player_idx in enumerate(assignment):
        if player_idx < 0 or player_idx >= n_players:
            continue
        if cost[slot_idx][player_idx] >= INELIGIBLE:
            continue  # solver only used this pair as filler
        player = roster[player_idx]
        total += float(player.get("points") or 0.0)
        chosen.append({**player, "optimal_slot_id": slots[slot_idx]})
    return round(total, 2), chosen


def efficiency(actual: float, optimal: float) -> float:
    """Fraction of the achievable score the manager actually started."""
    return round(actual / optimal, 4) if optimal > 0 else 0.0
