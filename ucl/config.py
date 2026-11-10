"""Paths and rule loading."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "ucl.sqlite"
CACHE_DIR = DATA_DIR / "cache"
MY_TEAM_PATH = CONFIG_DIR / "my_team.yaml"

POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
STATUS_LABEL = {"": "Available", "I": "Injured", "D": "Doubtful", "S": "Suspended", "NIS": "Not in squad"}


@lru_cache
def rules() -> dict:
    return yaml.safe_load((CONFIG_DIR / "rules.yaml").read_text())


def load_my_team() -> dict:
    if not MY_TEAM_PATH.exists():
        return {"squad": {}, "bank": 0.0, "free_transfers": 2, "captain": None, "chips_used": []}
    data = yaml.safe_load(MY_TEAM_PATH.read_text()) or {}
    data["squad"] = {str(k): int(v) for k, v in (data.get("squad") or {}).items()}
    if data.get("captain") is not None:
        data["captain"] = str(data["captain"])
    return data


def save_my_team(team: dict) -> None:
    out = dict(team)
    out["squad"] = {int(k): int(v) for k, v in team["squad"].items()}
    if out.get("captain") is not None:
        out["captain"] = int(out["captain"])
    header = "# Your UCL Fantasy squad. id: bench position (0 = starting XI, 1..4 = bench order)\n"
    MY_TEAM_PATH.write_text(header + yaml.safe_dump(out, sort_keys=False))
