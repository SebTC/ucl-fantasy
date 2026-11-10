"""Backtest the projection model on past UCL matchdays.

For each finished matchday, project using only matches played before its
first kickoff (live injury flags ignored, since we only have today's), then
compare with the real fantasy points.

    python -m ucl.backtest
"""
from __future__ import annotations

import argparse

import pandas as pd

from .analytics.projection import load_fixtures, project
from .store import read


def run(verbose: bool = True) -> pd.DataFrame:
    fx = load_fixtures()
    done = fx.dropna(subset=["home_score"]).groupby("md_id")["kickoff"].min()
    actual = read("SELECT player_id, md_id, points, mins, goals AS g, assists AS a FROM ucl_player_md")
    res = []
    for md, first_ko in done.items():
        if md < 2:  # need some history
            continue
        as_of = pd.Timestamp(first_ko).tz_localize("Europe/Paris") - pd.Timedelta(hours=1)
        pr = project(horizon=1, as_of=as_of, md_from=int(md), use_status=False)
        # players absent from the UCL feed for that MD didn't play: 0 points
        m = pr.merge(actual[actual.md_id == md], on=["player_id", "md_id"], how="left")
        m[["points", "g", "a", "mins"]] = m[["points", "g", "a", "mins"]].fillna(0)
        # model-independent evaluation set: anyone with UCL minutes this season up to this MD
        pool = set(actual.loc[(actual.md_id <= md) & (actual.mins > 0), "player_id"])
        m = m[m["player_id"].isin(pool)]
        rho = m["xpts"].rank().corr(m["points"].rank())
        top = m.nlargest(10, "xpts")
        best = m.nlargest(10, "points")
        pl = m[m["mins"] > 0]
        rho_played = pl["xpts"].rank().corr(pl["points"].rank())
        res.append(dict(md=md, n=len(m), spearman=round(rho, 3), rho_played=round(rho_played, 3),
                        top30_proj_avg_pts=round(m.nlargest(30, "xpts")["points"].mean(), 2),
                        top10_proj_avg_pts=round(top["points"].mean(), 2),
                        top10_actual_avg_pts=round(best["points"].mean(), 2),
                        all_avg_pts=round(m["points"].mean(), 2),
                        mae=round((m["xpts"] - m["points"]).abs().mean(), 2),
                        # calibration: 1.0 = projected totals match reality
                        cal_pts=round(m["xpts"].sum() / m["points"].sum(), 2),
                        cal_goals=round(m["x_goals"].sum() / max(m["g"].sum(), 1), 2),
                        cal_assists=round(m["x_assists"].sum() / max(m["a"].sum(), 1), 2)))
    df = pd.DataFrame(res)
    if verbose:
        print(df.to_string(index=False))
        if len(df):
            print(f"\nmean spearman {df.spearman.mean():.3f} (among players who played {df.rho_played.mean():.3f})"
                  f" | MAE {df.mae.mean():.3f} | top-30 avg {df.top30_proj_avg_pts.mean():.2f} | captain pool (top10 xPts) avg "
                  f"{df.top10_proj_avg_pts.mean():.2f} pts vs field avg {df.all_avg_pts.mean():.2f}")
    return df


if __name__ == "__main__":
    argparse.ArgumentParser().parse_args()
    run()
