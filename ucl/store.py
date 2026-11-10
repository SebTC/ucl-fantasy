"""SQLite storage. Everything is upserted, so ingest can run as often as you like."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pandas as pd

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS clubs (
  club_id TEXT PRIMARY KEY, name TEXT, short TEXT, code TEXT, pot TEXT, eliminated INTEGER
);
CREATE TABLE IF NOT EXISTS fixtures (
  match_id TEXT PRIMARY KEY, md_id INTEGER, kickoff TEXT,  -- kickoff: ISO, Europe/Paris local
  home_id TEXT, away_id TEXT, home_score INTEGER, away_score INTEGER, status INTEGER
);
CREATE TABLE IF NOT EXISTS matchdays (
  md_id INTEGER PRIMARY KEY, deadline TEXT, is_locked INTEGER, is_current INTEGER, subs_allowed INTEGER
);
CREATE TABLE IF NOT EXISTS player_snapshots (
  fetched_on TEXT, md_id INTEGER, player_id TEXT, name TEXT, full_name TEXT, club_id TEXT,
  pos INTEGER, price REAL, sel_pct REAL, tot_pts INTEGER, mins INTEGER, status TEXT, trained TEXT,
  active INTEGER, transfers_in INTEGER, transfers_out INTEGER,
  PRIMARY KEY (fetched_on, player_id)
);
CREATE TABLE IF NOT EXISTS ucl_player_md (
  player_id TEXT, md_id INTEGER, match_id TEXT, mins INTEGER, goals INTEGER, assists INTEGER,
  cs INTEGER, gc INTEGER, yc INTEGER, rc INTEGER, og INTEGER, pen_saved INTEGER, pen_missed INTEGER,
  pen_earned INTEGER, recoveries INTEGER, gob INTEGER, mom INTEGER, saves INTEGER, points INTEGER,
  PRIMARY KEY (player_id, md_id)
);
CREATE TABLE IF NOT EXISTS domestic_player_match (
  player_id TEXT, kickoff TEXT, competition TEXT, team_id TEXT, opp_id TEXT, opp_name TEXT,
  home INTEGER, team_score INTEGER, opp_score INTEGER, mins INTEGER, goals INTEGER,
  assists INTEGER, cs INTEGER, yc INTEGER, rc INTEGER,
  PRIMARY KEY (player_id, kickoff)
);
CREATE TABLE IF NOT EXISTS projections (
  computed_at TEXT, player_id TEXT, md_id INTEGER, xpts REAL, p_play REAL, p60 REAL,
  x_goals REAL, x_assists REAL, p_cs REAL, confidence TEXT,
  PRIMARY KEY (computed_at, player_id, md_id)
);
CREATE TABLE IF NOT EXISTS player_md_snapshot (   -- price/ownership as of each matchday's feed
  md_id INTEGER, player_id TEXT, price REAL, sel_pct REAL, transfers_in INTEGER, transfers_out INTEGER,
  PRIMARY KEY (md_id, player_id)
);
CREATE TABLE IF NOT EXISTS ucl_player_match_stats (  -- UEFA match stats: shot quality inputs
  player_id TEXT, match_id TEXT, team_id TEXT, mins INTEGER, goals INTEGER, shots INTEGER,
  shots_box INTEGER, shots_out INTEGER, on_target INTEGER, big_chances INTEGER, pens INTEGER,
  key_passes INTEGER, chances_created INTEGER, box_touches INTEGER, recoveries INTEGER, saves INTEGER,
  PRIMARY KEY (player_id, match_id)
);
CREATE TABLE IF NOT EXISTS team_match_xg (  -- football-data.co.uk: domestic results with team xG
  div TEXT, date TEXT, home TEXT, away TEXT, hg INTEGER, ag INTEGER, hxg REAL, axg REAL,
  p_home REAL, p_draw REAL, p_away REAL,
  PRIMARY KEY (div, date, home, away)
);
CREATE TABLE IF NOT EXISTS euro_matches (  -- European matches outside the fantasy league phase
  match_id TEXT PRIMARY KEY, competition INTEGER, season_year INTEGER, round TEXT, kickoff TEXT,  -- UTC
  home_id TEXT, away_id TEXT, home_country TEXT, away_country TEXT, home_name TEXT, away_name TEXT,
  home_score INTEGER, away_score INTEGER
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


@contextmanager
def connect(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def upsert(con: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    con.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
    return len(rows)


def set_meta(con, k: str, v: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))


def get_meta(con, k: str, default=None):
    row = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else default


def read(sql: str, params=(), path=DB_PATH) -> pd.DataFrame:
    with connect(path) as con:
        return pd.read_sql_query(sql, con, params=params)


def latest_players(path=DB_PATH) -> pd.DataFrame:
    return read(
        """SELECT p.*, c.name AS club, c.short AS club_short FROM player_snapshots p
           LEFT JOIN clubs c ON c.club_id = p.club_id
           WHERE fetched_on = (SELECT MAX(fetched_on) FROM player_snapshots)""",
        path=path,
    )
