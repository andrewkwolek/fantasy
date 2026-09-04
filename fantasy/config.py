"""Runtime configuration, loaded from .env / environment."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "league.sqlite3"
RAW_DIR = DATA_DIR / "raw"

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Config:
    league_id: int
    espn_s2: str | None
    swid: str | None
    first_season: int | None
    # First season that awards a second win for beating the weekly median.
    median_scoring_from: int | None = None

    @property
    def is_private(self) -> bool:
        return bool(self.espn_s2 and self.swid)

    @property
    def cookies(self) -> dict[str, str]:
        if not self.is_private:
            return {}
        swid = self.swid or ""
        # ESPN wants SWID wrapped in braces; tolerate either form in .env.
        if not swid.startswith("{"):
            swid = "{" + swid.strip("{}") + "}"
        return {"espn_s2": self.espn_s2 or "", "SWID": swid}


def _int_or_none(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def load_config() -> Config:
    league_id = _int_or_none(os.getenv("LEAGUE_ID"))
    if league_id is None:
        raise SystemExit(
            "LEAGUE_ID is not set.\n"
            "  cp .env.example .env   and fill in your league ID "
            "(and ESPN_S2 / SWID if the league is private)."
        )
    return Config(
        league_id=league_id,
        espn_s2=(os.getenv("ESPN_S2") or "").strip() or None,
        swid=(os.getenv("SWID") or "").strip() or None,
        first_season=_int_or_none(os.getenv("FIRST_SEASON")),
        median_scoring_from=_int_or_none(os.getenv("MEDIAN_SCORING_FROM")),
    )


DATA_DIR.mkdir(exist_ok=True)
RAW_DIR.mkdir(exist_ok=True)
