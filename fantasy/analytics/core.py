"""Load the database once and derive the central fact table: team-weeks.

Almost every advanced metric on the site is an aggregation over `team_weeks`,
so it is computed once here and cached.
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from ..store import db
from .lineup import efficiency, optimal_lineup


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value if value is not None else default


def scoring_periods_for(season_row: dict, matchup_period: int) -> list[int]:
    """Map a matchup period to the scoring period(s) it covers.

    Regular-season matchups are 1:1 with scoring periods. Playoff rounds can be
    multi-week, in which case ESPN lays them out consecutively after the regular
    season.
    """
    reg = season_row.get("reg_season_weeks") or 0
    length = max(1, season_row.get("playoff_matchup_len") or 1)
    if matchup_period <= reg or length == 1:
        return [matchup_period]
    start = reg + (matchup_period - reg - 1) * length + 1
    return list(range(start, start + length))


@dataclass
class TeamWeek:
    """One team's result in one matchup period."""

    season: int
    period: int
    team_id: int
    franchise_id: str
    opponent_id: int
    points: float
    opponent_points: float
    result: str  # "W" | "L" | "T"
    is_playoff: bool
    is_consolation: bool
    margin: float
    # Filled in by the all-play pass.
    all_play_wins: int = 0
    all_play_losses: int = 0
    all_play_ties: int = 0
    # Filled in by the lineup pass (0 when player detail is unavailable).
    optimal_points: float = 0.0
    bench_points: float = 0.0
    efficiency: float = 0.0
    slot_misses: list[dict] = field(default_factory=list)
    # True when ESPN reports the team's whole starting lineup as 0.00 -- a
    # forfeit or commissioner zero-out, not a lineup decision.
    forfeit: bool = False
    # Under median scoring: 1.0 for beating the league's median score that week,
    # 0.5 for matching it, 0.0 for missing it. None when the season predates the
    # rule. This is the second of the two wins available each week.
    median_win: float | None = None

    @property
    def won(self) -> int:
        return int(self.result == "W")

    @property
    def all_play_pct(self) -> float:
        played = self.all_play_wins + self.all_play_losses + self.all_play_ties
        if not played:
            return 0.0
        return (self.all_play_wins + 0.5 * self.all_play_ties) / played


class LeagueData:
    """In-memory view of the scraped league."""

    def __init__(self, conn, *, median_scoring_from: int | None = None):
        self.conn = conn
        self.median_scoring_from = median_scoring_from

    def uses_median_scoring(self, season: int) -> bool:
        """Does this season award a second win for beating the weekly median?"""
        return (
            self.median_scoring_from is not None
            and season >= self.median_scoring_from
        )

    # ------------------------------------------------------------- raw tables

    @cached_property
    def seasons(self) -> dict[int, dict]:
        rows = db.query(self.conn, "SELECT * FROM seasons ORDER BY season")
        for row in rows:
            row["lineup_slots"] = _loads(row.get("lineup_slots"), {})
        return {r["season"]: r for r in rows}

    @cached_property
    def season_years(self) -> list[int]:
        return sorted(self.seasons)

    @cached_property
    def current_season(self) -> int | None:
        return self.season_years[-1] if self.season_years else None

    @cached_property
    def teams(self) -> dict[tuple[int, int], dict]:
        rows = db.query(self.conn, "SELECT * FROM teams")
        return {(r["season"], r["team_id"]): r for r in rows}

    @cached_property
    def franchises(self) -> dict[str, dict]:
        rows = db.query(self.conn, "SELECT * FROM franchises ORDER BY display_name")
        return {r["franchise_id"]: r for r in rows}

    @cached_property
    def matchups(self) -> list[dict]:
        return db.query(
            self.conn, "SELECT * FROM matchups ORDER BY season, matchup_period, matchup_id"
        )

    @cached_property
    def players(self) -> dict[int, dict]:
        return {r["player_id"]: r for r in db.query(self.conn, "SELECT * FROM players")}

    def team(self, season: int, team_id: int) -> dict:
        return self.teams.get(
            (season, team_id),
            {"season": season, "team_id": team_id, "name": f"Team {team_id}",
             "franchise_id": "", "abbrev": "", "logo": ""},
        )

    def team_name(self, season: int, team_id: int) -> str:
        return self.team(season, team_id)["name"]

    def franchise_of(self, season: int, team_id: int) -> str:
        return self.team(season, team_id).get("franchise_id") or ""

    @cached_property
    def franchise_slugs(self) -> dict[str, str]:
        """franchise_id -> URL-safe slug, derived from the manager's name.

        Deliberately NOT the ESPN member GUID. That GUID is the account's SWID
        cookie -- one half of the API credential pair and a persistent account
        identifier -- so it must never end up in a URL on a page that gets
        published. Names are already displayed on every page, so a name slug
        leaks nothing new.
        """
        taken: set[str] = set()
        slugs: dict[str, str] = {}
        for fid in sorted(self.franchises):
            record = self.franchises[fid]
            label = record.get("owner_name") or record.get("display_name") or ""
            base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
            if not base:
                # Non-Latin or empty names still need a stable, opaque handle.
                base = "manager-" + hashlib.sha256(fid.encode()).hexdigest()[:8]
            slug, n = base, 2
            while slug in taken:
                slug, n = f"{base}-{n}", n + 1
            taken.add(slug)
            slugs[fid] = slug
        return slugs

    def slug_for(self, franchise_id: str) -> str:
        return self.franchise_slugs.get(franchise_id, "unknown")

    def franchise_by_slug(self, slug: str) -> str | None:
        """Resolve a slug back to a franchise id."""
        if slug in self.franchises:
            return slug          # tolerate a raw id from an old link
        for fid, candidate in self.franchise_slugs.items():
            if candidate == slug:
                return fid
        return None

    def franchise_name(self, franchise_id: str) -> str:
        fr = self.franchises.get(franchise_id)
        return fr["display_name"] if fr else "Unknown"

    def owner_name(self, franchise_id: str) -> str:
        fr = self.franchises.get(franchise_id)
        return fr["owner_name"] if fr else "Unknown"

    # ------------------------------------------------------- player-week data

    @cached_property
    def player_weeks(self) -> dict[tuple[int, int, int], list[dict]]:
        """(season, week, team_id) -> roster rows."""
        grouped: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
        for row in db.query(self.conn, "SELECT * FROM player_weeks"):
            row["eligible_slots"] = _loads(row.get("eligible_slots"), [])
            row["is_starter"] = bool(row["is_starter"])
            grouped[(row["season"], row["week"], row["team_id"])].append(row)
        return grouped

    def roster(self, season: int, period: int, team_id: int) -> list[dict]:
        """A team's roster for a matchup period, merged across scoring periods."""
        season_row = self.seasons.get(season, {})
        rows: list[dict] = []
        for week in scoring_periods_for(season_row, period):
            rows.extend(self.player_weeks.get((season, week, team_id), []))
        return rows

    # --------------------------------------------------------- the fact table

    @cached_property
    def team_weeks(self) -> list[TeamWeek]:
        weeks: list[TeamWeek] = []
        for game in self.matchups:
            if game["winner_team_id"] is None:
                continue  # not yet played
            season = game["season"]
            for side, other in (("home", "away"), ("away", "home")):
                team_id = game[f"{side}_team_id"]
                opp_id = game[f"{other}_team_id"]
                points = game[f"{side}_points"]
                opp_points = game[f"{other}_points"]
                winner = game["winner_team_id"]
                result = "T" if winner == 0 else ("W" if winner == team_id else "L")
                weeks.append(
                    TeamWeek(
                        season=season,
                        period=game["matchup_period"],
                        team_id=team_id,
                        franchise_id=self.franchise_of(season, team_id),
                        opponent_id=opp_id,
                        points=points,
                        opponent_points=opp_points,
                        result=result,
                        is_playoff=bool(game["is_playoff"]),
                        is_consolation=bool(game["is_consolation"]),
                        margin=round(points - opp_points, 2),
                    )
                )

        self._apply_all_play(weeks)
        self._apply_median_scoring(weeks)
        self._apply_lineup_efficiency(weeks)
        return weeks

    @staticmethod
    def _apply_all_play(weeks: list[TeamWeek]) -> None:
        """Score every team against every *other* team in the same week.

        All-play strips out schedule luck: it asks how you would have done if
        you had played the entire league that week instead of one opponent.
        """
        buckets: dict[tuple[int, int], list[TeamWeek]] = defaultdict(list)
        for tw in weeks:
            if not tw.is_consolation:
                buckets[(tw.season, tw.period)].append(tw)

        for group in buckets.values():
            if len(group) < 2:
                continue
            scores = [tw.points for tw in group]
            for tw in group:
                for other in scores:
                    if other < tw.points:
                        tw.all_play_wins += 1
                    elif other > tw.points:
                        tw.all_play_losses += 1
                # Exclude self from the tie count.
                tw.all_play_ties = scores.count(tw.points) - 1

    def _apply_median_scoring(self, weeks: list[TeamWeek]) -> None:
        """Award the weekly median win.

        The rule exists to blunt schedule luck: your matchup result still counts,
        but so does whether you outscored half the league, which no opponent
        draw can distort. Only regular-season weeks are scored this way -- the
        playoffs are decided head to head.
        """
        buckets: dict[tuple[int, int], list[TeamWeek]] = defaultdict(list)
        for tw in weeks:
            if self.uses_median_scoring(tw.season) and not tw.is_playoff:
                buckets[(tw.season, tw.period)].append(tw)

        for group in buckets.values():
            if len(group) < 2:
                continue
            median = statistics.median([tw.points for tw in group])
            for tw in group:
                if tw.points > median:
                    tw.median_win = 1.0
                elif tw.points == median:
                    tw.median_win = 0.5
                else:
                    tw.median_win = 0.0

    def _apply_lineup_efficiency(self, weeks: list[TeamWeek]) -> None:
        if not self.player_weeks:
            return
        for tw in weeks:
            roster = self.roster(tw.season, tw.period, tw.team_id)
            if not roster:
                continue
            slot_counts = self.seasons.get(tw.season, {}).get("lineup_slots") or {}
            best, chosen = optimal_lineup(roster, slot_counts)
            if best <= 0:
                continue
            starters = [p for p in roster if p["is_starter"]]
            started_ids = {p["player_id"] for p in starters}
            chosen_ids = {p["player_id"] for p in chosen}

            # A team that scored nothing while every starter reads 0.00, yet had
            # a bench that did score, was zeroed out administratively. Efficiency
            # is a measure of lineup decisions, so such a week is not one.
            tw.forfeit = (
                tw.points == 0
                and bool(starters)
                and all(p["points"] == 0 for p in starters)
                and best > 0
            )

            tw.optimal_points = best
            tw.efficiency = efficiency(tw.points, best)
            tw.bench_points = round(max(0.0, best - tw.points), 2)
            tw.slot_misses = [
                {
                    "benched": p["player_name"],
                    "benched_points": p["points"],
                    "position": p["position"],
                }
                for p in chosen
                if p["player_id"] not in started_ids
            ]
            # Guard against rosters ESPN reports incompletely.
            if not chosen_ids:
                tw.efficiency = 0.0

    # ------------------------------------------------------------- selections

    def team_weeks_for(
        self,
        *,
        season: int | None = None,
        team_id: int | None = None,
        franchise_id: str | None = None,
        regular_only: bool = False,
        through_period: int | None = None,
    ) -> list[TeamWeek]:
        out = self.team_weeks
        if season is not None:
            out = [tw for tw in out if tw.season == season]
        if team_id is not None:
            out = [tw for tw in out if tw.team_id == team_id]
        if franchise_id is not None:
            out = [tw for tw in out if tw.franchise_id == franchise_id]
        if regular_only:
            out = [tw for tw in out if not tw.is_playoff]
        if through_period is not None:
            out = [tw for tw in out if tw.period <= through_period]
        return out

    def latest_completed_period(self, season: int) -> int:
        periods = [tw.period for tw in self.team_weeks if tw.season == season]
        return max(periods) if periods else 0


def summarize(points: list[float]) -> dict:
    """Descriptive stats for a list of weekly scores."""
    if not points:
        return {"n": 0, "avg": 0.0, "stdev": 0.0, "high": 0.0, "low": 0.0, "cv": 0.0}
    avg = statistics.fmean(points)
    stdev = statistics.pstdev(points) if len(points) > 1 else 0.0
    return {
        "n": len(points),
        "avg": round(avg, 2),
        "stdev": round(stdev, 2),
        "high": round(max(points), 2),
        "low": round(min(points), 2),
        # Coefficient of variation: volatility normalised for scoring level, so
        # it is comparable across seasons with different scoring settings.
        "cv": round(stdev / avg, 4) if avg else 0.0,
    }
