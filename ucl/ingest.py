"""Pull everything into SQLite.

    python -m ucl.ingest            # normal daily run (cached feeds reused for 12h)
    python -m ucl.ingest --quick    # game state + players only (no per-player feeds)
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from . import parse
from .client import FeedClient
import yaml

from .config import CONFIG_DIR, rules
from .store import connect, set_meta, upsert


def season_codes(today: date | None = None) -> list[str]:
    """football-data season codes: current and previous, e.g. ['2627', '2526']."""
    d = today or date.today()
    y = d.year % 100 if d.month >= 7 else d.year % 100 - 1
    return [f"{y:02d}{y + 1:02d}", f"{y - 1:02d}{y:02d}"]


EURO_COMPS = (1, 14, 2019)  # Champions League (incl. qualifiers), Europa League, Conference League


def euro_matches(c: FeedClient, log=print) -> dict:
    """European matches of the 36 clubs outside the fantasy league phase (qualifiers, last season)
    with their shot data. Used to rate clubs whose domestic league has no xG."""
    with connect() as con:
        clubs = {r[0] for r in con.execute("SELECT club_id FROM clubs")}
        known = {r[0] for r in con.execute("SELECT match_id FROM fixtures")}
    season = season_codes()[0]
    this_year = 2000 + int(season[2:])  # UEFA seasonYear = year the season ends
    found = []
    for year in (this_year, this_year - 1):
        for comp in EURO_COMPS:
            old = year < this_year
            for off in range(0, 2000, 100):
                raw = c.uefa_matches(comp, year, off, max_age=30 * 86400 if old else 12 * 3600)
                if not raw:
                    break
                found += parse.uefa_match_list(raw, comp, year)
                if len(raw) < 100:
                    break
    mine = [m for m in found if (m["home_id"] in clubs or m["away_id"] in clubs) and m["match_id"] not in known]
    shots = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for part in ex.map(lambda m: parse.match_player_stats(c.match_player_stats(m["match_id"]), m["match_id"]), mine):
            shots += part
    return {"matches": mine, "shots": shots}


def ingest_extras(c: FeedClient, md: int, raw_players: list, log=print) -> dict:
    """Price/ownership history per matchday, UEFA shot data, domestic team xG."""
    stats = {}
    snap = parse.md_snapshot(raw_players, md)
    for past in range(1, md):
        # past matchday feeds never change; cache for a month
        v = c.get(f"players/players_{c.tour}_en_{past}.json", max_age=30 * 86400)
        v = v["playerList"] if isinstance(v, dict) else v
        snap += parse.md_snapshot(v or [], past)
    with connect() as con:
        stats["md_snapshots"] = upsert(con, "player_md_snapshot", snap)
        done = [r[0] for r in con.execute("SELECT match_id FROM fixtures WHERE home_score IS NOT NULL")]
    rows = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for part in ex.map(lambda m: parse.match_player_stats(c.match_player_stats(m), m), done):
            rows += part
    divs = yaml.safe_load((CONFIG_DIR / "team_map.yaml").read_text())["divisions"]
    xg_rows = []
    for i, code in enumerate(season_codes()):
        for div in divs:
            xg_rows += parse.football_data(c.football_data_csv(code, div, max_age=12 * 3600 if i == 0 else 30 * 86400), div)
    tm = yaml.safe_load((CONFIG_DIR / "team_map.yaml").read_text())
    since = (date.today() - timedelta(days=420)).isoformat()
    for country in tm.get("extra_divisions", {}):
        xg_rows += parse.football_data_extra(c.football_data_extra(country), country, since)
    euro = euro_matches(c, log=log)
    rows += euro["shots"]
    with connect() as con:
        stats["euro_matches"] = upsert(con, "euro_matches", euro["matches"])
        stats["ucl_shot_rows"] = upsert(con, "ucl_player_match_stats", rows)
        stats["team_xg_rows"] = upsert(con, "team_match_xg", xg_rows)
    log(f"Extras: {stats['md_snapshots']} price/ownership rows, {stats['ucl_shot_rows']} UCL shot rows "
        f"({len(done)} matches), {stats['team_xg_rows']} domestic team-xG/odds matches, "
        f"{stats['euro_matches']} other European matches")
    return stats


def run(quick: bool = False, workers: int = 4, log=print) -> dict:
    c = FeedClient(tour_id=rules()["season_tour_id"])
    cons = c.constraints()
    if not cons:
        raise SystemExit("Could not read constraints feed - is the site up?")
    md = int(cons["matchdayId"])
    stats = {"matchday": md}
    with connect() as con:
        set_meta(con, "current_md", str(md))
        set_meta(con, "deadline", parse._dt(cons.get("deadline")) or "")
        set_meta(con, "max_per_club", str(cons.get("maxTeamPlayers")))
        set_meta(con, "budget", str(cons.get("maxTeamValue")))
        stats["clubs"] = upsert(con, "clubs", parse.clubs(c.teams() or []))
        mds, matches = parse.fixtures(c.fixtures() or [])
        upsert(con, "matchdays", mds)
        stats["fixtures"] = upsert(con, "fixtures", matches)
        raw_players = c.players(md) or []
        stats["players"] = upsert(con, "player_snapshots", parse.players(raw_players, date.today().isoformat(), md))
        set_meta(con, "last_ingest", datetime.now().isoformat(timespec="seconds"))
    log(f"MD{md}: {stats['clubs']} clubs, {stats['fixtures']} fixtures, {stats['players']} players")
    with connect() as con:
        upsert(con, "player_md_snapshot", parse.md_snapshot(raw_players, md))
    if quick:
        return stats
    stats.update(ingest_extras(c, md, raw_players, log=log))

    active = [str(p["id"]) for p in raw_players if p.get("isActive") == 1]
    played = {str(p["id"]) for p in raw_players if (p.get("minsPlyd") or 0) > 0}
    jobs = [("dom", pid) for pid in active] + [("ucl", pid) for pid in active if pid in played]
    log(f"Fetching {len(jobs)} per-player feeds ({len(active)} domestic, {len(played)} UCL)...")

    def work(job):
        kind, pid = job
        if kind == "dom":
            return kind, parse.domestic_form(c.domestic_form(pid), pid)
        return kind, parse.popupstats(c.popupstats(pid), pid)

    dom_rows, ucl_rows, done = [], [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed(ex.submit(work, j) for j in jobs):
            kind, rows = fut.result()
            (dom_rows if kind == "dom" else ucl_rows).extend(rows)
            done += 1
            if done % 200 == 0:
                log(f"  {done}/{len(jobs)}")
    with connect() as con:
        stats["domestic_rows"] = upsert(con, "domestic_player_match", dom_rows)
        stats["ucl_rows"] = upsert(con, "ucl_player_md", ucl_rows)
    log(f"Stored {stats['domestic_rows']} domestic match rows, {stats['ucl_rows']} UCL matchday rows")
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    run(quick=a.quick, workers=a.workers)


if __name__ == "__main__":
    sys.exit(main())
