"""Form views built on the all-competitions match table."""
from __future__ import annotations

import pandas as pd

from .projection import load_fixtures, load_matches


def last_ucl_md_end() -> pd.Timestamp | None:
    fx = load_fixtures().dropna(subset=["home_score"])
    if fx.empty:
        return None
    return pd.Timestamp(fx["kickoff"].max()).tz_localize("Europe/Paris").tz_convert("UTC")


def since_last_md(matches: pd.DataFrame | None = None) -> pd.DataFrame:
    """Domestic minutes / starts / goals / assists since the last UCL matchday."""
    m = matches if matches is not None else load_matches()
    cut = last_ucl_md_end()
    d = m[(m["competition"] != "UCL")]
    if cut is not None:
        d = d[d["kickoff"] > cut]
    g = d.groupby("player_id").agg(
        dom_apps=("mins", lambda s: int((s > 0).sum())), dom_games=("mins", "size"),
        dom_starts60=("mins", lambda s: int((s >= 60).sum())), dom_mins=("mins", "sum"),
        dom_goals=("goals", "sum"), dom_assists=("assists", "sum"),
    )
    return g.reset_index()


def timeline(player_id: str, matches: pd.DataFrame | None = None) -> pd.DataFrame:
    m = matches if matches is not None else load_matches()
    t = m[m["player_id"] == player_id].sort_values("kickoff").copy()
    t["is_ucl"] = t["competition"] == "UCL"
    t["g_a"] = t["goals"].fillna(0) + t["assists"].fillna(0)
    return t
