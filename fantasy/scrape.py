"""Pull a whole league -- every season, every week -- into the local database."""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field

from .config import Config
from .espn import parse
from .espn.client import ESPNClient, ESPNError, NotFound
from .store import db

log = logging.getLogger(__name__)


@dataclass
class SeasonReport:
    season: int
    teams: int = 0
    matchups: int = 0
    player_weeks: int = 0
    draft_picks: int = 0
    transactions: int = 0
    activity: int = 0
    weeks_scanned: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.season}: {self.teams} teams, {self.matchups} matchups, "
            f"{self.player_weeks} player-weeks ({self.weeks_scanned} wks), "
            f"{self.draft_picks} picks, {self.transactions} txns"
            + (f"  [{len(self.warnings)} warn]" if self.warnings else "")
        )


def _weeks_to_scan(season_row: dict) -> list[int]:
    """Which scoring periods actually have games worth fetching."""
    first = season_row.get("first_scoring_period") or 1
    final = season_row.get("final_scoring_period") or 0
    latest = season_row.get("latest_scoring_period") or 0

    # An in-progress season only has data up to the latest scored week.
    end = latest if season_row.get("is_active") and latest else final
    if not end:
        # Fall back to the schedule length when status is unhelpful.
        end = (season_row.get("reg_season_weeks") or 14) + 3
    return list(range(first, end + 1))


def scrape_season(
    client: ESPNClient, conn: sqlite3.Connection, season: int
) -> SeasonReport:
    report = SeasonReport(season=season)

    # 1. Settings, members, teams, standings -----------------------------
    core = client.fetch(season, ["mSettings", "mTeam", "mStandings", "mRoster"])
    season_row = parse.parse_season(core, season)
    db.upsert(conn, "seasons", [season_row])
    db.upsert(conn, "members", parse.parse_members(core, season))
    teams = parse.parse_teams(core, season)
    report.teams = db.upsert(conn, "teams", teams)

    if not teams:
        report.warnings.append("no teams returned")
        return report

    # 2. Full schedule ---------------------------------------------------
    try:
        sched = client.fetch(season, ["mMatchup", "mMatchupScore"], cache_key="schedule")
        report.matchups = db.upsert(conn, "matchups", parse.parse_matchups(sched, season))
    except ESPNError as exc:
        report.warnings.append(f"schedule: {exc}")

    # 3. Per-week player detail -----------------------------------------
    all_player_rows: list[dict] = []
    for week in _weeks_to_scan(season_row):
        try:
            payload = client.fetch(
                season,
                ["mMatchup", "mMatchupScore", "mBoxscore"],
                params={"scoringPeriodId": week},
                cache_key=f"week-{week:02d}",
            )
        except NotFound:
            break  # ran past the end of the season
        except ESPNError as exc:
            report.warnings.append(f"week {week}: {exc}")
            continue

        rows = parse.parse_player_weeks(payload, season, week)
        if not rows:
            continue
        report.weeks_scanned += 1
        all_player_rows.extend(rows)
        report.player_weeks += db.upsert(conn, "player_weeks", rows)

    if all_player_rows:
        db.upsert(conn, "players", list(parse.collect_players(all_player_rows).values()))

    # 4. Draft -----------------------------------------------------------
    try:
        draft = client.fetch(season, ["mDraftDetail"])
        report.draft_picks = db.upsert(conn, "draft_picks", parse.parse_draft(draft, season))
    except ESPNError as exc:
        report.warnings.append(f"draft: {exc}")

    # 5. Transactions ----------------------------------------------------
    try:
        txn_payload = client.fetch(season, ["mTransactions2"])
        txns, items = parse.parse_transactions(txn_payload, season)
        report.transactions = db.upsert(conn, "transactions", txns)
        db.upsert(conn, "transaction_items", items)
    except ESPNError as exc:
        report.warnings.append(f"transactions: {exc}")

    # 6. Activity feed (adds/drops/trades; the only source for old seasons)
    try:
        offset, total = 0, 0
        while offset < 500:  # ESPN stops returning topics well before this
            payload = client.fetch_activity(season, offset=offset, limit=25)
            rows = parse.parse_activity(payload, season)
            if not rows:
                break
            total += db.upsert(conn, "activity", rows)
            offset += 25
        report.activity = total
    except ESPNError as exc:
        report.warnings.append(f"activity: {exc}")

    conn.commit()
    return report


def scrape_league(
    config: Config,
    *,
    seasons: list[int] | None = None,
    refresh: bool = False,
) -> list[SeasonReport]:
    client = ESPNClient(config, refresh=refresh)

    with db.session() as conn:
        if seasons is None:
            log.info("discovering seasons for league %s ...", config.league_id)
            seasons = client.discover_seasons()
            log.info("found %d seasons: %s", len(seasons), seasons)

        reports = []
        for season in seasons:
            started = time.monotonic()
            try:
                report = scrape_season(client, conn, season)
            except ESPNError as exc:
                report = SeasonReport(season=season, warnings=[str(exc)])
                log.error("season %s failed: %s", season, exc)
            reports.append(report)
            log.info("%s  (%.1fs)", report.summary(), time.monotonic() - started)

        count = db.rebuild_franchises(conn)
        log.info("stitched %d franchises", count)
        db.set_meta(conn, "league_id", config.league_id)
        db.set_meta(conn, "seasons", seasons)
        db.set_meta(conn, "last_scrape", time.time())

    return reports
