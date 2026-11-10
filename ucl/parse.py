"""Turn raw feed JSON into flat rows for the store. Pure functions: easy to test."""
from __future__ import annotations

from datetime import datetime


def _dt(s: str | None) -> str | None:
    """'11/24/2026 21:00:00' or '09/10/26 06:45:00 PM' (CET local) -> ISO string."""
    if not s:
        return None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%y %I:%M:%S %p", "%m/%d/%Y %I:%M:%S %p"):
        try:
            return datetime.strptime(s.strip(), fmt).isoformat()
        except ValueError:
            pass
    return None


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def clubs(raw: list) -> list[dict]:
    return [
        dict(club_id=str(t["id"]), name=t.get("webName"), short=t.get("shortName"),
             code=t.get("countryCode"), pot=t.get("htPtName"), eliminated=_int(t.get("isEliminated")))
        for t in raw
    ]


def fixtures(raw: list) -> tuple[list[dict], list[dict]]:
    mds, matches = [], []
    for md in raw:
        mds.append(dict(md_id=md["mdId"], deadline=_dt(md.get("deadline")), is_locked=md.get("mdIsLocked"),
                        is_current=md.get("mdIsCurrent"), subs_allowed=md.get("subsAllowed")))
        for m in md.get("match", []):
            played = str(m.get("matchStatus")) == "2"
            matches.append(dict(
                match_id=str(m["mId"]), md_id=md["mdId"], kickoff=_dt(m.get("dateTime")),
                home_id=str(m["htId"]), away_id=str(m["atId"]),
                home_score=_int(m.get("htScore")) if played else None,
                away_score=_int(m.get("atScore")) if played else None,
                status=_int(m.get("matchStatus")),
            ))
    return mds, matches


def players(raw: list, fetched_on: str, md_id: int) -> list[dict]:
    return [
        dict(fetched_on=fetched_on, md_id=md_id, player_id=str(p["id"]), name=p.get("pDName"),
             full_name=p.get("pFName"), club_id=str(p.get("tId")), pos=p.get("skill"), price=p.get("value"),
             sel_pct=p.get("selPer"), tot_pts=p.get("totPts"), mins=p.get("minsPlyd"),
             status=p.get("pStatus") or "", trained=p.get("trained") or "", active=p.get("isActive"),
             transfers_in=p.get("mTransferIn"), transfers_out=p.get("mTransferOut"))
        for p in raw
    ]


def popupstats(raw: dict, player_id: str) -> list[dict]:
    if not raw:
        return []
    pts = {p["mdId"]: p for p in raw.get("matchdayPoints") or []}
    # matchdayStats carries mId=0; the parallel 'stats' list has the real match ids
    mid = {s["mdId"]: s.get("mId") for s in raw.get("stats") or [] if s.get("mId")}
    mid.update({f["mdId"]: f.get("mId") for f in raw.get("fixtures") or []
                if str(f.get("selfTId")) in (str(f.get("htId")), str(f.get("atId"))) and f["mdId"] not in mid})
    rows = []
    for s in raw.get("matchdayStats") or []:
        md = s["mdId"]
        rows.append(dict(
            player_id=player_id, md_id=md, match_id=str(s.get("mId") or mid.get(md) or ""), mins=s.get("oF") or 0,
            goals=s.get("gS") or 0, assists=s.get("gA") or 0, cs=s.get("cS") or 0, gc=s.get("gC") or 0,
            yc=s.get("yC") or 0, rc=s.get("rC") or 0, og=s.get("oG") or 0, pen_saved=s.get("pS") or 0,
            pen_missed=s.get("pM") or 0, pen_earned=s.get("pE") or 0, recoveries=s.get("bR") or 0,
            gob=s.get("gOB") or 0, mom=s.get("mOM") or 0, saves=s.get("sS") or s.get("saves") or 0,
            points=(pts.get(md) or {}).get("tPoints"),
        ))
    return rows


def domestic_form(raw: list, player_id: str) -> list[dict]:
    rows = []
    for g in raw or []:
        m = g.get("match") or {}
        team_id = str(g.get("teamId"))
        home, away = m.get("homeTeam") or {}, m.get("awayTeam") or {}
        is_home = str(home.get("id")) == team_id
        opp = away if is_home else home
        tot = ((m.get("score") or {}).get("total")) or {}
        ts, os_ = (tot.get("home"), tot.get("away")) if is_home else (tot.get("away"), tot.get("home"))
        rows.append(dict(
            player_id=player_id, kickoff=(m.get("kickOffTime") or {}).get("dateTime"),
            competition=((m.get("competition") or {}).get("metaData") or {}).get("name"),
            team_id=team_id, opp_id=str(opp.get("id")), opp_name=opp.get("internationalName"),
            home=int(is_home), team_score=ts, opp_score=os_, mins=g.get("minutesPlayedOfficial") or 0,
            goals=g.get("goals") or 0, assists=g.get("assists") or 0, cs=g.get("cleanSheets") or 0,
            yc=g.get("yellowCards") or 0, rc=g.get("redCards") or 0,
        ))
    return [r for r in rows if r["kickoff"]]


# ------------------------------------------------------------- extra sources
MATCHSTAT_KEYS = dict(
    mins="minutes_played_official", goals="goals", shots="attempts", shots_box="shot_in_penalty_area",
    shots_out="shot_outside_penalty_area", on_target="attempts_on_target", big_chances="big_chance",
    pens="penalties", key_passes="key_passes", chances_created="chance_created",
    box_touches="total_ball_touches_performed_in_penalty_area", recoveries="recovered_ball", saves="saves",
)


def match_player_stats(raw: list | None, match_id: str) -> list[dict]:
    """matchstats.uefa.com player-statistics -> one row per player who played."""
    rows = []
    for p in raw or []:
        s = {t["name"]: t.get("value") for t in p.get("statistics") or []}
        row = dict(player_id=str(p.get("playerId")), match_id=str(match_id), team_id=str(p.get("teamId")))
        row.update({k: _int(s.get(v)) or 0 for k, v in MATCHSTAT_KEYS.items()})
        if row["mins"] > 0:
            rows.append(row)
    return rows


def _odds_probs(r: dict) -> tuple:
    """Bookmaker-implied 1X2 probabilities, overround removed (closing odds preferred)."""
    for h, d, a in (("AvgCH", "AvgCD", "AvgCA"), ("AvgH", "AvgD", "AvgA"), ("B365H", "B365D", "B365A")):
        try:
            inv = [1 / float(r[h]), 1 / float(r[d]), 1 / float(r[a])]
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        t = sum(inv)
        return tuple(x / t for x in inv)
    return (None, None, None)


def football_data(csv_text: str | None, div: str) -> list[dict]:
    import csv
    import io

    def f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    rows = []
    for r in csv.DictReader(io.StringIO(csv_text or "")):
        if not r.get("HomeTeam") or r.get("FTHG") in (None, ""):
            continue
        try:
            d = datetime.strptime(r["Date"], "%d/%m/%Y" if len(r["Date"]) == 10 else "%d/%m/%y").date()
        except ValueError:
            continue
        ph, pd_, pa = _odds_probs(r)
        rows.append(dict(div=div, date=d.isoformat(), home=r["HomeTeam"], away=r["AwayTeam"],
                         hg=_int(r["FTHG"]), ag=_int(r["FTAG"]), hxg=f(r.get("HxG")), axg=f(r.get("AxG")),
                         p_home=ph, p_draw=pd_, p_away=pa))
    return rows


def md_snapshot(raw: list, md_id: int) -> list[dict]:
    return [dict(md_id=md_id, player_id=str(p["id"]), price=p.get("value"), sel_pct=p.get("selPer"),
                 transfers_in=p.get("mTransferIn"), transfers_out=p.get("mTransferOut")) for p in raw]


def football_data_extra(csv_text: str | None, div: str, since: str) -> list[dict]:
    """football-data.co.uk new/{country}.csv (Home/Away/HG/AG columns). Keeps matches on/after `since` (ISO)."""
    import csv
    import io

    rows = []
    for r in csv.DictReader(io.StringIO(csv_text or "")):
        if not r.get("Home") or r.get("HG") in (None, ""):
            continue
        try:
            d = datetime.strptime(r["Date"], "%d/%m/%Y" if len(r["Date"]) == 10 else "%d/%m/%y").date()
        except (KeyError, ValueError):
            continue
        if d.isoformat() < since:
            continue
        ph, pd_, pa = _odds_probs(r)
        rows.append(dict(div=div, date=d.isoformat(), home=r["Home"], away=r["Away"], hg=_int(r["HG"]),
                         ag=_int(r["AG"]), hxg=None, axg=None, p_home=ph, p_draw=pd_, p_away=pa))
    return rows


def uefa_match_list(raw: list | None, competition: int, season_year: int) -> list[dict]:
    """match.uefa.com/v5/matches -> finished matches with scores."""
    rows = []
    for m in raw or []:
        if m.get("status") != "FINISHED":
            continue
        sc = (m.get("score") or {}).get("regular") or (m.get("score") or {}).get("total") or {}
        h, a = m.get("homeTeam") or {}, m.get("awayTeam") or {}
        rows.append(dict(
            match_id=str(m["id"]), competition=competition, season_year=season_year,
            round=((m.get("round") or {}).get("metaData") or {}).get("name"),
            kickoff=(m.get("kickOffTime") or {}).get("dateTime"),
            home_id=str(h.get("id")), away_id=str(a.get("id")), home_country=h.get("countryCode"),
            away_country=a.get("countryCode"), home_name=h.get("internationalName"),
            away_name=a.get("internationalName"), home_score=_int(sc.get("home")), away_score=_int(sc.get("away")),
        ))
    return rows
