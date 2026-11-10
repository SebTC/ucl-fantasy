"""Expected UCL Fantasy points (xPts) per player per upcoming matchday.

Built for long gaps between UCL matchdays: every competitive match (domestic
league, domestic cup, UCL) is used, weighted by *calendar* age with an
exponential half-life. Domestic numbers are converted to UCL-equivalent with
per-league strength factors (config/model.yaml).

Components (scored with config/rules.yaml):
  appearance      p(plays) and p(60+ mins) from recent starts + injury status
  goals/assists   shrunk per-90 rates x opponent defence x home/away
  clean sheet     Poisson from own defence vs opponent attack
  conceded        GK/DEF penalty, Poisson expectation of floor(GC/2)
  saves/recov.    per-90 from UCL data (domestic feed lacks them), shrunk
  POTM, cards     per-90 rates, shrunk
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
import pandas as pd
import yaml

from ..config import CONFIG_DIR, POS, rules
from ..store import DB_PATH, read
from . import xg as xgmod
from .ratings import team_ratings as xg_team_ratings

AVAIL_ZERO = {"I", "S", "NIS"}


@lru_cache
def model_params() -> dict:
    return yaml.safe_load((CONFIG_DIR / "model.yaml").read_text())


def league_factor(comp: str | None, p: dict) -> float:
    if comp == "UCL":
        return 1.0
    return float(p["league_factor"].get(comp or "", p["league_factor"]["default"]))


def is_cup(comp: str | None, p: dict) -> bool:
    return bool(comp) and comp != "UCL" and any(k.lower() in comp.lower() for k in p["cup_keywords"])


def poisson_floor_div(lam: float, k: int) -> float:
    """E[floor(X/k)] for X ~ Poisson(lam)."""
    if lam <= 0:
        return 0.0
    n_max = int(lam + 10 * math.sqrt(lam) + 20)
    tot, pk = 0.0, math.exp(-lam)
    for n in range(n_max):
        if n > 0:
            pk *= lam / n
        tot += pk * (n // k)
    return tot


# ---------------------------------------------------------------- data access
def load_matches(as_of: datetime | None = None, db=DB_PATH) -> pd.DataFrame:
    """All player matches (domestic + UCL) in one frame, as of a point in time."""
    dom = read("""SELECT d.player_id, d.kickoff, d.competition, d.team_id, d.mins, d.goals, d.assists,
                         d.cs, d.yc, d.rc, d.opp_score AS gc, d.team_score AS gf_team, NULL AS recoveries, NULL AS saves,
                         NULL AS mom, NULL AS gob, NULL AS points
                  FROM domestic_player_match d""", path=db)
    dom["kickoff"] = pd.to_datetime(dom["kickoff"], utc=True)
    ucl = read("""SELECT u.player_id, f.kickoff, 'UCL' AS competition, u.mins, u.goals,
                         u.assists, u.cs, u.yc, u.rc, u.gc, u.recoveries, u.saves, u.mom, u.gob, u.points, u.md_id,
                         u.match_id
                  FROM ucl_player_md u JOIN fixtures f ON f.match_id = u.match_id""", path=db)
    # fixture times are Europe/Paris local
    ucl["kickoff"] = pd.to_datetime(ucl["kickoff"]).dt.tz_localize("Europe/Paris").dt.tz_convert("UTC")
    now = None
    if as_of is not None:
        now = pd.Timestamp(as_of)
        now = now.tz_convert("UTC") if now.tzinfo else now.tz_localize("UTC")
    # xG inputs: domestic team xG (football-data) and UCL shot xG (UEFA match stats)
    dom = xgmod.attach_domestic_xg(dom, xgmod.domestic_team_xg(now, db=db))
    shots = xgmod.ucl_shot_table(now, db=db)
    fx = read("SELECT match_id, home_id, away_id, home_score, away_score FROM fixtures", path=db)
    ucl = ucl.merge(fx, on="match_id", how="left")
    if len(shots):
        sh = shots[["player_id", "match_id", "team_id", "xg", "xa"]]
        ucl = ucl.merge(sh, on=["player_id", "match_id"], how="left")
        team_x = shots.groupby(["match_id", "team_id"])["xg"].sum().rename("team_xg").reset_index()
        ucl = ucl.merge(team_x, on=["match_id", "team_id"], how="left")
    else:
        ucl[["team_id", "xg", "xa", "team_xg"]] = np.nan
    is_home = ucl["team_id"].astype(str) == ucl["home_id"].astype(str)
    ucl["gf_team"] = np.where(ucl["team_id"].isna(), np.nan, np.where(is_home, ucl["home_score"], ucl["away_score"]))
    ucl = ucl.drop(columns=["home_id", "away_id", "home_score", "away_score"])
    dom["xg"] = np.nan
    dom["xa"] = np.nan
    m = pd.concat([dom, ucl], ignore_index=True)
    num = ["mins", "goals", "assists", "cs", "yc", "rc", "gc", "gf_team", "recoveries", "saves", "mom", "gob",
           "points", "xg", "xa", "team_xg", "opp_xg"]
    m[num] = m[num].apply(pd.to_numeric, errors="coerce")
    if now is not None:
        m = m[m["kickoff"] < now]
    return m


def load_fixtures(db=DB_PATH) -> pd.DataFrame:
    f = read("SELECT * FROM fixtures", path=db)
    f["kickoff"] = pd.to_datetime(f["kickoff"])
    return f


# ---------------------------------------------------------------- team ratings
def team_ratings(fixtures: pd.DataFrame, matches: pd.DataFrame, as_of: datetime, p: dict) -> pd.DataFrame:
    """Attack/defence multipliers per club (1.0 = average UCL side), UCL-equivalent.

    Uses UCL results (weighted x2) plus domestic team scorelines seen in the
    domestic feed, converted with league factors, all time-decayed.
    """
    now = pd.Timestamp(as_of).tz_convert("UTC") if pd.Timestamp(as_of).tzinfo else pd.Timestamp(as_of, tz="UTC")
    rows = []
    played = fixtures.dropna(subset=["home_score"])
    for r in played.itertuples():
        ko = pd.Timestamp(r.kickoff).tz_localize("Europe/Paris").tz_convert("UTC")
        if ko >= now:
            continue
        rows += [(r.home_id, ko, r.home_score, r.away_score, 1.0, 2.0),
                 (r.away_id, ko, r.away_score, r.home_score, 1.0, 2.0)]
    dom = matches[(matches["competition"] != "UCL") & matches["team_id"].notna()]
    dom = dom.drop_duplicates(subset=["team_id", "kickoff"])
    for r in dom.itertuples():
        if r.kickoff >= now or pd.isna(r.gc):
            continue
        rows.append((r.team_id, r.kickoff, r.gf_team, r.gc, league_factor(r.competition, p), 1.0))
    df = pd.DataFrame(rows, columns=["club_id", "kickoff", "gf", "ga", "lf", "w"])
    if df.empty:
        return pd.DataFrame(columns=["club_id", "att", "def"])
    age = (now - df["kickoff"]).dt.total_seconds() / 86400
    df["w"] = df["w"] * 0.5 ** (age / p["half_life_days"])
    df["gf_adj"] = df["gf"] * df["lf"]
    df["ga_adj"] = df["ga"] / df["lf"]
    k = 3.0  # shrink toward average with k pseudo-matches
    base_gf = np.nansum(df["gf_adj"] * df["w"]) / df.loc[df["gf_adj"].notna(), "w"].sum()
    base_ga = np.nansum(df["ga_adj"] * df["w"]) / df["w"].sum()
    out = []
    for cid, g in df.groupby("club_id"):
        gfw = g.loc[g["gf_adj"].notna(), "w"]
        att = (np.nansum(g["gf_adj"] * g["w"]) + k * base_gf) / (gfw.sum() + k) / base_gf
        dfn = (np.nansum(g["ga_adj"] * g["w"]) + k * base_ga) / (g["w"].sum() + k) / base_ga
        out.append((str(cid), att, dfn))
    r = pd.DataFrame(out, columns=["club_id", "att", "def"])
    r.attrs["base_goals"] = float(base_gf)
    return r


def club_ratings(fixtures: pd.DataFrame, matches: pd.DataFrame, now: pd.Timestamp, db=DB_PATH) -> pd.DataFrame:
    """Team ratings with the configured model ("xg": league xG fits, "goals": UCL clubs' scorelines)."""
    p = model_params()
    if p.get("team_model", "xg") == "xg":
        return xg_team_ratings(fixtures, matches, now, p, league_xg=xgmod.domestic_team_xg(now, db=db),
                               ucl_team_xg=xgmod.ucl_shot_table(now, db=db), league_factor=league_factor,
                               euro=xgmod.euro_matches_xg(now, db=db))
    return team_ratings(fixtures, matches, now, p)


# ---------------------------------------------------------------- player model
@dataclass
class PlayerRates:
    p_play: float
    p60: float
    mins: float
    g90: float
    a90: float
    rec90: float
    sav90: float
    yc90: float
    mom_rate: float
    gob_share: float
    n90: float
    n_recent: int


def _priors(m: pd.DataFrame, pos_of: dict) -> dict:
    m = m[m["mins"] > 0].copy()
    m["pos"] = m["player_id"].map(pos_of)
    pri = {}
    for pos in (1, 2, 3, 4):
        g = m[m["pos"] == pos]
        n90 = g["mins"].sum() / 90 or 1
        u = g[g["competition"] == "UCL"]
        u90 = u["mins"].sum() / 90 or 1
        pri[pos] = dict(
            g90=g["goals"].sum() / n90, a90=g["assists"].sum() / n90, yc90=g["yc"].sum() / n90,
            rec90=u["recoveries"].sum() / u90, sav90=u["saves"].sum() / u90,
            mom=u["mom"].sum() / max(len(u), 1),
        )
    return pri


def price_scaled(pr: dict, pos: int, price: float, mean_price: dict, p: dict) -> dict:
    """Position prior scaled by price: UEFA prices encode expected quality."""
    k = (price / mean_price[pos]) ** p.get("price_prior_exponent", 2.0) if price and mean_price.get(pos) else 1.0
    k = min(max(k, 0.3), p.get("price_prior_cap", 2.5))
    out = dict(pr[pos])
    for key in ("g90", "a90", "mom"):
        out[key] = out[key] * k
    return out


def player_rates(pm: pd.DataFrame, pos: int, status: str, trained: str, now, pri: dict, p: dict) -> PlayerRates:
    """pri: this player's priors (already price-scaled). Pass status='' and trained='' to ignore
    the live injury feed (backtests)."""
    pm = pm[(now - pm["kickoff"]).dt.days <= p["lookback_days"]]
    if pm.empty:
        base = 0.0 if status in AVAIL_ZERO else 0.15
        pr = pri
        return PlayerRates(base, base * 0.5, 45, pr["g90"], pr["a90"], pr["rec90"], pr["sav90"], pr["yc90"],
                           pr["mom"] * 0.5, 0.1, 0.0, 0)
    age = (now - pm["kickoff"]).dt.total_seconds() / 86400
    w = 0.5 ** (age / p["half_life_days"])
    comp = pm["competition"]
    w = w * np.where(comp == "UCL", p["ucl_weight"], 1.0)
    w_av = w * np.where([is_cup(c, p) for c in comp], p["cup_availability_weight"], 1.0)
    lf = np.array([league_factor(c, p) for c in comp])

    played = pm["mins"] > 0
    # shrink toward a bench-player prior: one or two recent starts is weak evidence
    a = p.get("avail_pseudo", 0.0)
    p_play = float(((w_av * played).sum() + a * p.get("avail_prior", 0.5)) / (w_av.sum() + a))
    p60 = float(((w_av * (pm["mins"] >= 60)).sum() + a * p.get("avail_prior_60", 0.3)) / (w_av.sum() + a))
    cap = p.get("avail_cap", 1.0)
    p_play, p60 = min(p_play, cap), min(p60, cap * p.get("avail_cap_60", 1.0))
    mins = float((w * pm["mins"])[played].sum() / max(w[played].sum(), 1e-9)) if played.any() else 0.0

    n90 = float((w * pm["mins"]).sum() / 90)
    k = p["shrink_90s"]
    pr = pri

    def rate(col, prior, scale=None, mask=None):
        x = pm[col].fillna(0).to_numpy(dtype=float)
        ww = w.to_numpy() if mask is None else (w * mask).to_numpy()
        n = float((ww * pm["mins"].to_numpy()).sum() / 90)
        s = float((ww * x * (scale if scale is not None else 1)).sum())
        return (s + k * prior) / (n + k)

    is_ucl = (comp == "UCL").astype(float)
    pm = pm.assign(sig_g=pm["goals"], sig_a=pm["assists"])
    alpha = p.get("goal_weight", 1.0)
    if alpha < 1.0:
        # xG estimate per match: UCL from the player's own shots; domestic from team xG x his share
        on = pm["mins"].fillna(0) / 90
        team_on = pm["gf_team"] * on
        known = team_on.notna()
        m0 = p.get("share_pseudo_goals", 4.0)
        tg90 = p.get("team_goals_per90", 1.6)
        wk = w[known]
        tot = float((wk * team_on[known]).sum())
        share_g = (float((wk * pm.loc[known, "goals"].fillna(0)).sum()) + m0 * pr["g90"] / tg90) / (tot + m0)
        share_a = (float((wk * pm.loc[known, "assists"].fillna(0)).sum()) + m0 * pr["a90"] / tg90) / (tot + m0)
        dom_xg = pm["team_xg"] * on
        est_g = pm["xg"].where(pm["xg"].notna(), dom_xg * share_g)
        est_a = pm["xa"].where(pm["xa"].notna(), dom_xg * share_a)
        pm = pm.assign(sig_g=np.where(est_g.notna(), alpha * pm["goals"].fillna(0) + (1 - alpha) * est_g, pm["goals"]),
                       sig_a=np.where(est_a.notna(), alpha * pm["assists"].fillna(0) + (1 - alpha) * est_a,
                                      pm["assists"]))
    g90 = rate("sig_g", pr["g90"], lf)
    a90 = rate("sig_a", pr["a90"], lf)
    yc90 = rate("yc", pr["yc90"])
    rec90 = rate("recoveries", pr["rec90"], mask=is_ucl)
    sav90 = rate("saves", pr["sav90"], mask=is_ucl)
    u = pm[comp == "UCL"]
    starts = int((u["mins"] >= 60).sum())
    mom_rate = (u["mom"].fillna(0).sum() + 3 * pr["mom"]) / (starts + 3)
    gob_share = (u["gob"].fillna(0).sum() + 0.5) / (u["goals"].sum() + 4)

    # status overrides (official feed)
    if status in AVAIL_ZERO:
        p_play = p60 = 0.0
    elif status == "D":
        p_play, p60 = p_play * 0.5, p60 * 0.4
    if trained.startswith("Unlikely"):
        p60 *= 0.35
        p_play = min(p_play, 0.6)
    p60 = min(p60, p_play)
    n_recent = int(((now - pm["kickoff"]).dt.days <= 35).sum())
    return PlayerRates(p_play, p60, mins or 70.0, g90, a90, rec90, sav90, yc90, mom_rate, gob_share, n90, n_recent)


def expected_points(r: PlayerRates, pos: int, lam_for_ratio: float, lam_against: float, sc: dict,
                    sav_scale: float = 1.0) -> dict:
    """Score one fixture. lam_for_ratio scales attacking output vs an average opponent."""
    P = POS[pos]
    on = r.mins / 90 * r.p_play  # expected share of the match on the pitch
    xg = r.g90 * on * lam_for_ratio
    xa = r.a90 * on * lam_for_ratio
    p_cs = math.exp(-lam_against)
    pts = {
        "pts_appearance": r.p60 * sc["appearance"]["min_60"] + (r.p_play - r.p60) * sc["appearance"]["under_60"],
        "pts_goals": xg * sc["goal"][P] + xg * r.gob_share * sc["goal_outside_box"],
        "pts_assists": xa * sc["assist"],
        "pts_clean_sheet": r.p60 * p_cs * sc["clean_sheet"].get(P, 0),
        "pts_conceded": r.p60 * poisson_floor_div(lam_against, 2) * sc["goals_conceded_per_2"].get(P, 0),
        "pts_saves": (r.p60 * poisson_floor_div(r.sav90 * sav_scale, 3) * sc["saves_per_3"]) if P == "GK" else 0.0,
        "pts_recoveries": (r.p_play * poisson_floor_div(r.rec90 * r.mins / 90, 3) * sc["ball_recoveries_per_3"])
        if P != "GK" else 0.0,
        "pts_potm": r.p60 * r.mom_rate * sc["player_of_match"],
        "pts_cards": on * r.yc90 * sc["yellow"],
    }
    pts["xpts"] = sum(pts.values())
    pts.update(x_goals=xg, x_assists=xa, p_cs=p_cs)
    return pts


def confidence(r: PlayerRates, has_domestic: bool) -> str:
    if r.n_recent >= 4 and has_domestic:
        return "high"
    if r.n_recent >= 2:
        return "medium"
    return "low"


def project(horizon: int = 3, as_of: datetime | None = None, db=DB_PATH, md_from: int | None = None,
            use_status: bool = True) -> pd.DataFrame:
    """xPts per player for matchdays md_from .. md_from+horizon-1 (one row per player x MD)."""
    p = model_params()
    sc = rules()["scoring"]
    now = pd.Timestamp(as_of or datetime.now(timezone.utc))
    now = now.tz_convert("UTC") if now.tzinfo else now.tz_localize("UTC")

    players = read("""SELECT * FROM player_snapshots WHERE fetched_on = (SELECT MAX(fetched_on) FROM player_snapshots)""",
                   path=db)
    matches = load_matches(as_of=now, db=db)
    fixtures = load_fixtures(db=db)
    if md_from is None:
        md_from = int(read("SELECT v FROM meta WHERE k='current_md'", path=db)["v"].iloc[0])
    ratings = club_ratings(fixtures, matches, now, db=db).set_index("club_id")
    base = ratings.attrs.get("base_goals", 1.5) if len(ratings) else 1.5
    pos_of = dict(zip(players["player_id"], players["pos"]))
    pri = _priors(matches, pos_of)
    regulars = players[players["mins"] > 0]
    mean_price = regulars.groupby("pos")["price"].mean().to_dict()
    by_player = {pid: g for pid, g in matches.groupby("player_id")}
    has_dom = set(matches.loc[matches["competition"] != "UCL", "player_id"])

    upcoming = fixtures[(fixtures["md_id"] >= md_from) & (fixtures["md_id"] < md_from + horizon)]
    fx_by_club: dict[str, list] = {}
    for f in upcoming.itertuples():
        fx_by_club.setdefault(f.home_id, []).append((f.md_id, f.away_id, True, f.kickoff))
        fx_by_club.setdefault(f.away_id, []).append((f.md_id, f.home_id, False, f.kickoff))

    def rating(cid, col):
        return float(ratings[col].get(cid, 1.0)) if len(ratings) else 1.0

    out = []
    for pl in players.itertuples():
        if not pl.active:
            continue
        pm = by_player.get(pl.player_id, matches.iloc[0:0])
        st, tr = (pl.status or "", pl.trained or "") if use_status else ("", "")
        my_pri = price_scaled(pri, int(pl.pos), pl.price, mean_price, p)
        r = player_rates(pm, int(pl.pos), st, tr, now, my_pri, p)
        conf = confidence(r, pl.player_id in has_dom)
        for md, opp, home, ko in fx_by_club.get(pl.club_id, []):
            ha = p["home_advantage"] if home else 1 / p["home_advantage"]
            lam_for_ratio = rating(opp, "def") * ha
            lam_against = base * rating(opp, "att") * rating(pl.club_id, "def") / ha
            e = expected_points(r, int(pl.pos), lam_for_ratio, lam_against, sc,
                                sav_scale=rating(opp, "att") / ha)
            out.append(dict(player_id=pl.player_id, md_id=md, opp_id=opp, home=home, kickoff=ko,
                            p_play=r.p_play, p60=r.p60, exp_mins=r.mins, confidence=conf, **e))
    return pd.DataFrame(out)


def summary(proj: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """One row per player: next-MD xPts and horizon total, joined to player info."""
    if proj.empty:
        return players.assign(xpts_next=0.0, xpts_horizon=0.0)
    first = proj["md_id"].min()
    nxt = proj[proj["md_id"] == first].groupby("player_id")["xpts"].sum().rename("xpts_next")
    hor = proj.groupby("player_id")["xpts"].sum().rename("xpts_horizon")
    meta = proj[proj["md_id"] == first].drop_duplicates("player_id").set_index("player_id")[
        ["p_play", "p60", "exp_mins", "confidence", "opp_id", "home", "kickoff", "x_goals", "x_assists", "p_cs"]]
    df = players.set_index("player_id").join([nxt, hor, meta]).reset_index()
    df[["xpts_next", "xpts_horizon"]] = df[["xpts_next", "xpts_horizon"]].fillna(0.0)
    df["xpts_per_m"] = df["xpts_horizon"] / df["price"]
    return df
