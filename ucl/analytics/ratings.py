"""Team attack/defence ratings on a UCL scale (1.0 = average UCL side).

1. Each covered domestic league is fitted on *every* match in it (not just the
   UCL clubs' games), so schedule strength is accounted for: a goal signal that
   blends team xG and real goals, time-decayed, home advantage per league.
2. League ratings are converted to UCL-equivalent with the league factors.
   Clubs in leagues without xG data fall back to their own domestic scorelines.
   Norway/Austria have no xG: their leagues are fitted on odds-implied goals.
3. Other European matches (qualifiers, last season's UCL/UEL/UECL) update the
   ratings with shot xG vs European opposition: this is the main evidence for
   clubs from Czechia, Slovakia, Ukraine and Azerbaijan.
4. The UCL league-phase results so far update the ratings (shot xG + goals).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .xg import team_map


def _decay(age_days, half_life):
    return 0.5 ** (np.asarray(age_days, dtype=float) / half_life)


def odds_goals(p_home, p_away, total: float, max_goals: int = 10) -> tuple[np.ndarray, np.ndarray]:
    """Expected goals (home, away) whose Poisson 1X2 matches the bookmaker's P(home) - P(away),
    with the match total fixed at `total`. NaN where odds are missing."""
    ph = np.asarray(p_home, dtype=float)
    pa = np.asarray(p_away, dtype=float)
    s = np.linspace(-0.97, 0.97, 195) * total
    lh, la = (total + s) / 2, (total - s) / 2
    k = np.arange(max_goals + 1)
    fact = np.array([math.factorial(int(i)) for i in k], dtype=float)
    ph_k = np.exp(-lh[:, None]) * lh[:, None] ** k / fact
    pa_k = np.exp(-la[:, None]) * la[:, None] ** k / fact
    joint = ph_k[:, :, None] * pa_k[:, None, :]
    diff = np.tril(np.ones((len(k), len(k))), -1)  # home > away
    curve = (joint * diff).sum((1, 2)) - (joint * diff.T).sum((1, 2))  # increasing in s
    sup = np.interp(ph - pa, curve, s)
    gh, ga = (total + sup) / 2, (total - sup) / 2
    bad = np.isnan(ph) | np.isnan(pa)
    gh[bad] = np.nan
    ga[bad] = np.nan
    return gh, ga


def _blend(parts: list[tuple[np.ndarray, float]]) -> np.ndarray:
    """Weighted mean of the signals that are available (non-NaN) per match."""
    num = np.zeros(len(parts[0][0]))
    den = np.zeros(len(parts[0][0]))
    for x, w in parts:
        ok = ~np.isnan(x)
        num[ok] += w * x[ok]
        den[ok] += w
    return num / np.where(den > 0, den, 1)


def fit_league(m: pd.DataFrame, now: pd.Timestamp, p: dict, iters: int = 25) -> tuple[pd.DataFrame, float, float]:
    """m: div rows (date, home, away, hg, ag, hxg, axg[, p_home, p_away]).
    Returns (ratings, mean goals/team/match, home factor)."""
    beta = p.get("xg_weight_team", 0.7)
    w_odds = p.get("odds_weight_team", 0.0)
    hl = p.get("team_half_life_days", 90)
    m = m[(now - m["date"]).dt.days <= p.get("team_lookback_days", 365)]
    if m.empty:
        return pd.DataFrame(columns=["team", "att", "def"]), 1.4, 1.0
    w = _decay((now - m["date"]).dt.days, hl)
    hg, ag = m["hg"].to_numpy(float), m["ag"].to_numpy(float)
    parts_h, parts_a = [(hg, 1 - beta)], [(ag, 1 - beta)]
    hx = m["hxg"].to_numpy(float) if "hxg" in m else np.full(len(m), np.nan)
    ax = m["axg"].to_numpy(float) if "axg" in m else np.full(len(m), np.nan)
    parts_h.append((hx, beta))
    parts_a.append((ax, beta))
    has_xg = bool(np.isfinite(hx).any())
    if w_odds > 0 and "p_home" in m and (not has_xg or p.get("odds_with_xg", False)):
        total = float((w * (hg + ag)).sum() / w.sum())
        oh, oa = odds_goals(m["p_home"].to_numpy(float), m["p_away"].to_numpy(float), total)
        parts_h.append((oh, w_odds))
        parts_a.append((oa, w_odds))
    yh, ya = _blend(parts_h), _blend(parts_a)
    mu = float((w * (yh + ya)).sum() / (2 * w.sum()))
    home = float(np.sqrt((w * yh).sum() / max((w * ya).sum(), 1e-9)))  # sqrt: split between home/away
    teams = sorted(set(m["home"]) | set(m["away"]))
    ix = {t: i for i, t in enumerate(teams)}
    hi, ai = m["home"].map(ix).to_numpy(), m["away"].map(ix).to_numpy()
    att, dfn = np.ones(len(teams)), np.ones(len(teams))
    k = 2.0  # pseudo-matches at league average
    for _ in range(iters):
        # expected for each side given opponent; solve for att, then def
        e_h = mu * home * dfn[ai]
        e_a = mu / home * dfn[hi]
        num = np.bincount(hi, w * yh, len(teams)) + np.bincount(ai, w * ya, len(teams)) + k
        den = np.bincount(hi, w * e_h, len(teams)) + np.bincount(ai, w * e_a, len(teams)) + k
        att = num / den
        c_h = mu / home * att[ai]  # goals conceded at home come from the away attack
        c_a = mu * home * att[hi]
        num = np.bincount(hi, w * ya, len(teams)) + np.bincount(ai, w * yh, len(teams)) + k
        den = np.bincount(hi, w * c_h, len(teams)) + np.bincount(ai, w * c_a, len(teams)) + k
        dfn = num / den
        att /= np.exp(np.mean(np.log(att)))
        dfn /= np.exp(np.mean(np.log(dfn)))
    return pd.DataFrame(dict(team=teams, att=att, **{"def": dfn})), mu, home


def _update(att, dfn, clubs, games, base, p, k, now, half_life=None):
    """Multiplicative update of att/def from games: iterable of
    (club, opp, opp_att, opp_def, goals_signal, home, kickoff). opp may be None (non-UCL opponent)."""
    ha = p["home_advantage"]
    num_a, den_a, num_d, den_d = ({c: 0.0 for c in clubs} for _ in range(4))
    for me, opp, o_att, o_def, y_for, y_against, home, ko in games:
        wt = float(_decay((now - ko).days, half_life or p.get("team_half_life_days", 90)))
        h = ha if home else 1 / ha
        if me in att:
            num_a[me] += wt * y_for
            den_a[me] += wt * base * att[me] * o_def * h
            num_d[me] += wt * y_against
            den_d[me] += wt * base * o_att * dfn[me] / h
    new_att = {c: att[c] * (num_a[c] + k) / (den_a[c] + k) for c in clubs}
    new_def = {c: dfn[c] * (num_d[c] + k) / (den_d[c] + k) for c in clubs}
    n = {c: 0 for c in clubs}
    for g in games:
        if g[0] in n:
            n[g[0]] += 1
    return new_att, new_def, n


def team_ratings(fixtures: pd.DataFrame, matches: pd.DataFrame, now: pd.Timestamp, p: dict,
                 league_xg: pd.DataFrame | None = None, ucl_team_xg: pd.DataFrame | None = None,
                 league_factor=None, euro: pd.DataFrame | None = None) -> pd.DataFrame:
    tm = team_map()
    divs = {**tm["divisions"], **tm.get("extra_divisions", {})}
    club_map = {**tm["clubs"], **(tm.get("extra_clubs", {}) if p.get("use_extra_leagues", True) else {})}
    clubs = sorted(set(fixtures["home_id"]) | set(fixtures["away_id"]))
    raw = {}  # club -> (UCL-eq goals for per match, against per match, evidence weight)
    source = {}

    # 1+2: covered leagues (team xG, or odds for leagues without xG)
    if league_xg is not None and len(league_xg):
        fits = {}
        for div in {d for d, _ in club_map.values()}:
            fits[div] = fit_league(league_xg[league_xg["div"] == div], now, p)
        for cid, (div, name) in club_map.items():
            r, mu, _ = fits.get(div, (pd.DataFrame(), 1.4, 1.0))
            row = r[r["team"] == name] if len(r) else r
            if row.empty:
                continue
            lf = league_factor(divs[div], p)
            raw[cid] = (float(row["att"].iloc[0]) * mu * lf, float(row["def"].iloc[0]) * mu / lf, 10.0)
            source[cid] = "league xG" if div in tm["divisions"] else "league odds"

    # fallback: own domestic scorelines from the UEFA domestic-form feed
    dom = matches[(matches["competition"] != "UCL") & matches["team_id"].notna()]
    dom = dom.drop_duplicates(subset=["team_id", "kickoff"])
    dom = dom[(dom["kickoff"] < now) & dom["gc"].notna()]
    for cid, g in dom.groupby("team_id"):
        cid = str(cid)
        if cid in raw:
            continue
        w = _decay((now - g["kickoff"]).dt.total_seconds() / 86400, p.get("team_half_life_days", 90))
        lf = np.array([league_factor(c, p) for c in g["competition"]])
        gf = float((w * g["gf_team"].fillna(0) * lf).sum() / w.sum())
        ga = float((w * g["gc"] / lf).sum() / w.sum())
        # no opponent adjustment here and a crude league factor: trust it less
        raw[cid] = (gf, ga, min(float(w.sum()), p.get("fallback_evidence_cap", 3.0)))
        source[cid] = "domestic goals"

    # normalise to the UCL field (shrunk toward the field mean by evidence)
    if raw:
        gf_mean = np.mean([v[0] for v in raw.values()])
        ga_mean = np.mean([v[1] for v in raw.values()])
    else:
        gf_mean = ga_mean = 1.0
    k = 3.0
    att = {c: ((raw[c][0] * raw[c][2] + gf_mean * k) / (raw[c][2] + k)) / gf_mean if c in raw else 1.0 for c in clubs}
    dfn = {c: ((raw[c][1] * raw[c][2] + ga_mean * k) / (raw[c][2] + k)) / ga_mean if c in raw else 1.0 for c in clubs}
    dom_att, dom_def = dict(att), dict(dfn)

    played = fixtures.dropna(subset=["home_score"]).copy()
    played["ko"] = pd.to_datetime(played["kickoff"]).dt.tz_localize("Europe/Paris").dt.tz_convert("UTC")
    played = played[played["ko"] < now]
    base = float((played["home_score"].sum() + played["away_score"].sum()) / max(2 * len(played), 1)) \
        if len(played) else 1.5
    base = (base * 2 * len(played) + 1.5 * 20) / (2 * len(played) + 20)  # shrink early-season base
    beta = p.get("xg_weight_team", 0.7)

    def sig(x, g):
        return beta * x + (1 - beta) * g if x is not None and not pd.isna(x) else g

    # 3: other European matches (qualifiers, last season), opponents rated by country if not in the field
    n_euro = {c: 0 for c in clubs}
    ke = p.get("euro_rating_pseudo_goals", 0)
    if euro is not None and len(euro) and ke:
        cf = p.get("country_factor", {})
        ref = p.get("country_factor_ref", 0.85)

        def opp_strength(oid, country):
            if oid in att:
                return att[oid], dfn[oid]
            f = float(cf.get(country, cf.get("default", 0.5)))
            return f / ref, ref / f

        e = euro[euro["kickoff"] < now]
        games = []
        for r in e.itertuples():
            for me, opp, oc, gf, ga, xf, xa, home in (
                    (r.home_id, r.away_id, r.away_country, r.home_score, r.away_score, r.home_xg, r.away_xg, True),
                    (r.away_id, r.home_id, r.home_country, r.away_score, r.home_score, r.away_xg, r.home_xg, False)):
                if me not in att:
                    continue
                oa, od = opp_strength(opp, oc)
                games.append((me, opp, oa, od, sig(xf, gf), sig(xa, ga), home, r.kickoff))
        att, dfn, n_euro = _update(att, dfn, clubs, games, base, p, ke, now,
                                   half_life=p.get("euro_half_life_days", 180))

    # 4: UCL league phase
    txg = {}
    if ucl_team_xg is not None and len(ucl_team_xg):
        txg = ucl_team_xg.groupby(["match_id", "team_id"])["xg"].sum().to_dict()
    games = []
    for r in played.itertuples():
        hx, ax = txg.get((r.match_id, r.home_id)), txg.get((r.match_id, r.away_id))
        games.append((r.home_id, r.away_id, att[r.away_id], dfn[r.away_id], sig(hx, r.home_score),
                      sig(ax, r.away_score), True, r.ko))
        games.append((r.away_id, r.home_id, att[r.home_id], dfn[r.home_id], sig(ax, r.away_score),
                      sig(hx, r.home_score), False, r.ko))
    att, dfn, _ = _update(att, dfn, clubs, games, base, p, p.get("ucl_rating_pseudo_goals", 4.0), now)

    out = pd.DataFrame(dict(
        club_id=clubs, att=[att[c] for c in clubs], **{"def": [dfn[c] for c in clubs]},
        dom_att=[dom_att[c] for c in clubs], dom_def=[dom_def[c] for c in clubs],
        source=[source.get(c, "none") for c in clubs], n_euro=[n_euro[c] for c in clubs],
    ))
    out.attrs["base_goals"] = base
    return out
