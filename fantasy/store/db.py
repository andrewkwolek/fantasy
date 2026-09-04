"""SQLite persistence for scraped league data.

The schema is deliberately denormalised-ish and fully re-derivable: every table
is keyed so that re-scraping a season is an idempotent upsert rather than an
append. `franchises` is the one derived table -- it stitches per-season team IDs
into owner-based identities that survive team renames.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..config import DB_PATH

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS seasons (
    season INTEGER PRIMARY KEY,
    name TEXT, size INTEGER,
    reg_season_weeks INTEGER, playoff_teams INTEGER, playoff_matchup_len INTEGER,
    draft_type TEXT, auction_budget REAL, keeper_count INTEGER,
    lineup_slots TEXT,
    current_matchup_period INTEGER, latest_scoring_period INTEGER,
    final_scoring_period INTEGER, first_scoring_period INTEGER,
    is_active INTEGER, scoring_type TEXT
);

CREATE TABLE IF NOT EXISTS members (
    guid TEXT, season INTEGER,
    display_name TEXT, first_name TEXT, last_name TEXT, is_manager INTEGER,
    PRIMARY KEY (guid, season)
);

CREATE TABLE IF NOT EXISTS teams (
    season INTEGER, team_id INTEGER,
    name TEXT, abbrev TEXT, owner_guid TEXT, all_owner_guids TEXT, logo TEXT,
    division_id INTEGER,
    wins INTEGER, losses INTEGER, ties INTEGER,
    points_for REAL, points_against REAL,
    playoff_seed INTEGER, final_rank INTEGER,
    streak_length INTEGER, streak_type TEXT,
    acquisitions INTEGER, drops INTEGER, trades INTEGER, waiver_rank INTEGER,
    franchise_id TEXT,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS franchises (
    franchise_id TEXT PRIMARY KEY,
    display_name TEXT, owner_name TEXT, logo TEXT,
    first_season INTEGER, last_season INTEGER, seasons_played INTEGER
);

CREATE TABLE IF NOT EXISTS matchups (
    season INTEGER, matchup_id INTEGER,
    matchup_period INTEGER,
    home_team_id INTEGER, away_team_id INTEGER,
    home_points REAL, away_points REAL,
    winner_team_id INTEGER, playoff_tier TEXT,
    is_playoff INTEGER, is_consolation INTEGER,
    margin REAL, total REAL,
    PRIMARY KEY (season, matchup_id)
);

CREATE TABLE IF NOT EXISTS player_weeks (
    season INTEGER, week INTEGER, team_id INTEGER, player_id INTEGER,
    player_name TEXT, position TEXT, pro_team TEXT,
    slot_id INTEGER, slot TEXT, is_starter INTEGER,
    points REAL, projected REAL, injury_status TEXT, eligible_slots TEXT,
    PRIMARY KEY (season, week, team_id, player_id, slot_id)
);

CREATE TABLE IF NOT EXISTS players (
    player_id INTEGER PRIMARY KEY,
    name TEXT, position TEXT, pro_team TEXT
);

CREATE TABLE IF NOT EXISTS draft_picks (
    season INTEGER, overall_pick INTEGER,
    round INTEGER, round_pick INTEGER,
    team_id INTEGER, player_id INTEGER,
    bid_amount REAL, is_keeper INTEGER, auto_drafted INTEGER,
    PRIMARY KEY (season, overall_pick)
);

CREATE TABLE IF NOT EXISTS transactions (
    season INTEGER, txn_id TEXT,
    type TEXT, status TEXT, team_id INTEGER, member_guid TEXT,
    scoring_period INTEGER, bid_amount REAL,
    proposed_date INTEGER, execution_date INTEGER,
    PRIMARY KEY (season, txn_id)
);

CREATE TABLE IF NOT EXISTS transaction_items (
    season INTEGER, txn_id TEXT, player_id INTEGER, item_type TEXT,
    from_team_id INTEGER, to_team_id INTEGER, scoring_period INTEGER,
    PRIMARY KEY (season, txn_id, player_id, item_type, from_team_id, to_team_id)
);

CREATE TABLE IF NOT EXISTS activity (
    season INTEGER, message_id TEXT,
    topic_id TEXT, date INTEGER, action TEXT, player_id INTEGER,
    to_team_id INTEGER, from_team_id INTEGER,
    PRIMARY KEY (season, message_id)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE INDEX IF NOT EXISTS ix_matchups_season_period ON matchups(season, matchup_period);
CREATE INDEX IF NOT EXISTS ix_pw_season_week ON player_weeks(season, week);
CREATE INDEX IF NOT EXISTS ix_pw_team ON player_weeks(season, team_id);
CREATE INDEX IF NOT EXISTS ix_pw_player ON player_weeks(player_id);
CREATE INDEX IF NOT EXISTS ix_teams_franchise ON teams(franchise_id);
CREATE INDEX IF NOT EXISTS ix_txn_items ON transaction_items(season, txn_id);
"""

# Columns that hold JSON-ish values and must be serialised on write.
JSON_COLUMNS = {"lineup_slots", "eligible_slots"}


def connect(path: Path | None = None, *, same_thread: bool = True) -> sqlite3.Connection:
    """Open the database.

    `same_thread=False` is needed by the web app: FastAPI runs sync endpoints on
    a threadpool, so a cached connection is reached from several threads. Callers
    that do this MUST serialise their access (the app holds a lock per request).
    """
    conn = sqlite3.connect(path or DB_PATH, check_same_thread=same_thread is True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def session(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        init_db(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _encode(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if key in JSON_COLUMNS or isinstance(value, (dict, list)):
            out[key] = json.dumps(value)
        elif isinstance(value, bool):
            out[key] = int(value)
        else:
            out[key] = value
    return out


def upsert(conn: sqlite3.Connection, table: str, rows: Sequence[dict]) -> int:
    """REPLACE-style bulk insert. Returns the number of rows written."""
    if not rows:
        return 0
    encoded = [_encode(r) for r in rows]
    columns = list(encoded[0].keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    sql = (
        f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    )
    conn.executemany(sql, encoded)
    return len(encoded)


def query(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (key, json.dumps(value)),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


# ------------------------------------------------------------- franchise glue


def rebuild_franchises(conn: sqlite3.Connection) -> int:
    """Fold per-season teams into owner-based franchises.

    A franchise is identified by its owner GUID, which ESPN keeps stable across
    seasons even as team names and team IDs churn. Teams with no recorded owner
    (rare, mostly in very old seasons) fall back to a synthetic per-team key so
    they still appear in history rather than vanishing.
    """
    teams = query(conn, "SELECT * FROM teams ORDER BY season, team_id")
    if not teams:
        return 0

    members = {
        (m["guid"], m["season"]): m["display_name"]
        for m in query(conn, "SELECT guid, season, display_name FROM members")
    }
    latest_member_name: dict[str, tuple[int, str]] = {}
    for (guid, season), name in members.items():
        prior = latest_member_name.get(guid)
        if prior is None or season > prior[0]:
            latest_member_name[guid] = (season, name)

    grouped: dict[str, list[dict]] = {}
    for team in teams:
        guid = (team["owner_guid"] or "").strip()
        key = guid or f"team:{team['team_id']}"
        grouped.setdefault(key, []).append(team)

    franchise_rows = []
    for key, group in grouped.items():
        group.sort(key=lambda t: t["season"])
        newest = group[-1]
        owner = latest_member_name.get(key, (0, ""))[1]
        franchise_rows.append(
            {
                "franchise_id": key,
                "display_name": newest["name"] or owner or key[:8],
                "owner_name": owner or newest["name"] or "Unknown",
                "logo": newest["logo"] or "",
                "first_season": group[0]["season"],
                "last_season": newest["season"],
                "seasons_played": len(group),
            }
        )
        conn.executemany(
            "UPDATE teams SET franchise_id = ? WHERE season = ? AND team_id = ?",
            [(key, t["season"], t["team_id"]) for t in group],
        )

    conn.execute("DELETE FROM franchises")
    upsert(conn, "franchises", franchise_rows)
    conn.commit()
    return len(franchise_rows)
