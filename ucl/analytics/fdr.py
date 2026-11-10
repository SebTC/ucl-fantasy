"""Fixture difficulty: expected goals for/against per club per upcoming matchday."""
from __future__ import annotations

import math

import pandas as pd

from .projection import club_ratings, load_fixtures, load_matches, model_params


def fixture_grid(md_from: int, horizon: int = 4, as_of=None) -> pd.DataFrame:
    p = model_params()
    now = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC")
    fx = load_fixtures()
    r = club_ratings(fx, load_matches(as_of=now), now).set_index("club_id")
    base = r.attrs.get("base_goals", 1.5)
    up = fx[(fx["md_id"] >= md_from) & (fx["md_id"] < md_from + horizon)]
    rows = []
    for f in up.itertuples():
        for club, opp, home in ((f.home_id, f.away_id, True), (f.away_id, f.home_id, False)):
            ha = p["home_advantage"] if home else 1 / p["home_advantage"]
            att = lambda c: float(r["att"].get(c, 1.0))
            dfn = lambda c: float(r["def"].get(c, 1.0))
            xgf = base * att(club) * dfn(opp) * ha
            xga = base * att(opp) * dfn(club) / ha
            rows.append(dict(club_id=club, md_id=f.md_id, opp_id=opp, home=home, kickoff=f.kickoff,
                             xgf=xgf, xga=xga, p_cs=math.exp(-xga)))
    return pd.DataFrame(rows)


def ratings_table(as_of=None) -> pd.DataFrame:
    now = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC")
    fx = load_fixtures()
    return club_ratings(fx, load_matches(as_of=now), now)
