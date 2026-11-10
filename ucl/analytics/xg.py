"""Expected-goals inputs without Understat.

Two free, public sources stand in for player xG:

* UCL matches: UEFA's own match stats give every player's shots inside/outside
  the box, big chances, penalties and chances created. We turn those into a
  shot-quality xG (and chance-based xA) and calibrate the totals so they match
  the goals/assists actually scored in this season's UCL.
* Domestic matches: football-data.co.uk publishes *team* xG for 11 of the
  leagues UCL clubs play in. A player's domestic xG is estimated as his team's
  xG while he was on the pitch times his (shrunk) share of the team's goals.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
import yaml

from ..config import CONFIG_DIR
from ..store import DB_PATH, read

# Shot values (goals per shot). Big chances get a bonus on top of the box value.
XG_BOX, XG_OUT, XG_BIG_BONUS, XG_PEN = 0.10, 0.035, 0.25, 0.76
XG_UNKNOWN = 0.095  # shots without a location split (Europa/Conference League data): average shot value
XA_PER_CHANCE = 0.11  # before calibration


@lru_cache
def team_map() -> dict:
    return yaml.safe_load((CONFIG_DIR / "team_map.yaml").read_text())


def shot_xg(df: pd.DataFrame) -> pd.Series:
    box_np = (df["shots_box"] - df["pens"]).clip(lower=0)
    unknown = (df["shots"] - df["shots_box"] - df["shots_out"]).clip(lower=0) if "shots" in df else 0
    return (XG_BOX * box_np + XG_OUT * df["shots_out"] + XG_BIG_BONUS * df["big_chances"] + XG_PEN * df["pens"]
            + XG_UNKNOWN * unknown)


def ucl_shot_table(as_of=None, db=DB_PATH) -> pd.DataFrame:
    """Per player per UCL match: shot xG and chance xA, calibrated to real totals before as_of."""
    s = read("""SELECT s.*, f.kickoff FROM ucl_player_match_stats s
                JOIN fixtures f ON f.match_id = s.match_id""", path=db)
    if s.empty:
        return s.assign(xg=[], xa=[])
    s["kickoff"] = pd.to_datetime(s["kickoff"]).dt.tz_localize("Europe/Paris").dt.tz_convert("UTC")
    if as_of is not None:
        s = s[s["kickoff"] < as_of]
    num = [c for c in s.columns if c not in ("player_id", "match_id", "team_id", "kickoff")]
    s[num] = s[num].apply(pd.to_numeric, errors="coerce").fillna(0)
    s["xg"] = shot_xg(s)
    s["xa"] = XA_PER_CHANCE * s["chances_created"]
    # calibrate with a little shrinkage toward 1 (80 pseudo-goals)
    assists = read("SELECT player_id, match_id, assists FROM ucl_player_md", path=db)
    a = s.merge(assists, on=["player_id", "match_id"], how="left")["assists"].fillna(0).sum()
    cal_g = (s["goals"].sum() + 80) / (s["xg"].sum() + 80)
    cal_a = (a + 80) / (s["xa"].sum() + 80)
    s["xg"] *= cal_g
    s["xa"] *= cal_a
    s.attrs.update(cal_g=cal_g, cal_a=cal_a)
    return s


def domestic_team_xg(as_of=None, db=DB_PATH) -> pd.DataFrame:
    """Domestic league results with team xG (football-data.co.uk), one row per match."""
    x = read("SELECT * FROM team_match_xg", path=db)
    x["date"] = pd.to_datetime(x["date"]).dt.tz_localize("UTC")
    if as_of is not None:
        x = x[x["date"] < as_of.normalize()]
    return x


def club_match_xg(xg: pd.DataFrame) -> pd.DataFrame:
    """Rows (club_id, date, team_xg, opp_xg, team_goals) for mapped UCL clubs."""
    tm = team_map()
    out = []
    for cid, (div, name) in tm["clubs"].items():
        d = xg[xg["div"] == div]
        h = d[d["home"] == name]
        a = d[d["away"] == name]
        out.append(pd.DataFrame(dict(club_id=cid, date=h["date"], team_xg=h["hxg"], opp_xg=h["axg"])))
        out.append(pd.DataFrame(dict(club_id=cid, date=a["date"], team_xg=a["axg"], opp_xg=a["hxg"])))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["club_id", "date", "team_xg", "opp_xg"])


def attach_domestic_xg(dom: pd.DataFrame, xg: pd.DataFrame) -> pd.DataFrame:
    """Add team_xg/opp_xg to domestic player-match rows (matching on club and local match date)."""
    cm = club_match_xg(xg).dropna(subset=["team_xg"])
    dom = dom.copy()
    dom["date"] = dom["kickoff"].dt.normalize()
    if cm.empty:
        dom["team_xg"] = np.nan
        dom["opp_xg"] = np.nan
        return dom.drop(columns="date")
    cm = cm.drop_duplicates(["club_id", "date"])
    key = cm.set_index(["club_id", "date"])
    res = []
    for shift in (0, -1, 1):  # late kick-offs can fall on the next UTC day
        idx = pd.MultiIndex.from_arrays([dom["team_id"].astype(str), dom["date"] + pd.Timedelta(days=shift)])
        res.append(key.reindex(idx)[["team_xg", "opp_xg"]].to_numpy())
    vals = res[0]
    for r in res[1:]:
        vals = np.where(np.isnan(vals), r, vals)
    dom["team_xg"], dom["opp_xg"] = vals[:, 0], vals[:, 1]
    return dom.drop(columns="date")


def euro_matches_xg(as_of=None, db=DB_PATH) -> pd.DataFrame:
    """Other European matches (qualifiers, previous seasons) with each side's shot xG."""
    e = read("SELECT * FROM euro_matches", path=db)
    if e.empty:
        return e
    e["kickoff"] = pd.to_datetime(e["kickoff"], utc=True)
    if as_of is not None:
        e = e[e["kickoff"] < as_of]
    s = read("""SELECT s.* FROM ucl_player_match_stats s JOIN euro_matches e ON e.match_id = s.match_id""", path=db)
    num = ["shots", "shots_box", "shots_out", "big_chances", "pens", "goals"]
    s[num] = s[num].apply(pd.to_numeric, errors="coerce").fillna(0)
    s["xg"] = shot_xg(s)
    cal = (s["goals"].sum() + 80) / (s["xg"].sum() + 80)
    g = s.groupby(["match_id", "team_id"]).agg(xg=("xg", "sum"), shots=("shots", "sum"))
    g = g[g["shots"] > 0]  # no shots recorded at all = missing data, not zero xG
    t = (g["xg"] * cal).to_dict()
    e["home_xg"] = [t.get((m, h)) for m, h in zip(e["match_id"], e["home_id"])]
    e["away_xg"] = [t.get((m, a)) for m, a in zip(e["match_id"], e["away_id"])]
    return e
