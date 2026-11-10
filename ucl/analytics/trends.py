"""Price and ownership trends.

Two sources, merged into one history:
* player_md_snapshot: the official per-matchday player feeds (MD1..now), which
  carry each player's price and ownership as of that matchday. Backfilled.
* player_snapshots: our own daily snapshots, which show movement between
  matchdays (ownership drifts daily as managers transfer).
"""
from __future__ import annotations

import pandas as pd

from ..store import DB_PATH, read


def history(db=DB_PATH) -> pd.DataFrame:
    """Long frame: player_id, date (naive, local), label, md_id, price, sel_pct."""
    md = read("""SELECT s.md_id, s.player_id, s.price, s.sel_pct, m.deadline
                 FROM player_md_snapshot s LEFT JOIN matchdays m ON m.md_id = s.md_id""", path=db)
    md["date"] = pd.to_datetime(md["deadline"])
    md["label"] = "MD" + md["md_id"].astype(str)
    daily = read("SELECT fetched_on, md_id, player_id, price, sel_pct FROM player_snapshots", path=db)
    daily["date"] = pd.to_datetime(daily["fetched_on"]) + pd.Timedelta(hours=12)
    daily["label"] = daily["fetched_on"]
    cols = ["player_id", "date", "label", "md_id", "price", "sel_pct"]
    h = pd.concat([md[cols], daily[cols]], ignore_index=True).dropna(subset=["date"])
    return h.sort_values(["player_id", "date"]).reset_index(drop=True)


def summary(db=DB_PATH) -> pd.DataFrame:
    """One row per player: price/ownership changes per matchday and this matchday's net transfers."""
    s = read("SELECT * FROM player_md_snapshot", path=db)
    if s.empty:
        return pd.DataFrame(columns=["player_id"])
    cur = int(s["md_id"].max())
    piv_p = s.pivot_table(index="player_id", columns="md_id", values="price")
    piv_o = s.pivot_table(index="player_id", columns="md_id", values="sel_pct")
    first = piv_p.apply(lambda r: r.dropna().iloc[0] if r.notna().any() else None, axis=1)
    out = pd.DataFrame(index=piv_p.index)
    out["price_start"] = first
    out["price_now"] = piv_p[cur]
    out["d_price_season"] = out["price_now"] - out["price_start"]
    prev = cur - 1 if cur - 1 in piv_p.columns else None
    out["d_price_md"] = piv_p[cur] - piv_p[prev] if prev else 0.0
    out["own_now"] = piv_o[cur]
    out["d_own_md"] = piv_o[cur] - piv_o[prev] if prev else 0.0
    out["own_start"] = piv_o.apply(lambda r: r.dropna().iloc[0] if r.notna().any() else None, axis=1)
    out["d_own_season"] = out["own_now"] - out["own_start"]
    now = s[s["md_id"] == cur].set_index("player_id")
    out["t_in"] = now["transfers_in"]
    out["t_out"] = now["transfers_out"]
    out["net_transfers"] = out["t_in"].fillna(0) - out["t_out"].fillna(0)
    # daily drift since the previous snapshot, when we have one
    d = read("SELECT fetched_on, player_id, sel_pct FROM player_snapshots", path=db)
    days = sorted(d["fetched_on"].unique())
    if len(days) >= 2:
        a = d[d.fetched_on == days[-2]].set_index("player_id")["sel_pct"]
        b = d[d.fetched_on == days[-1]].set_index("player_id")["sel_pct"]
        out["d_own_day"] = b - a
    else:
        out["d_own_day"] = float("nan")
    out = out.reset_index()
    out.attrs["md"] = cur
    return out


def price_moves(db=DB_PATH) -> pd.DataFrame:
    """Every price change between consecutive matchdays: player_id, md_id, old, new, delta."""
    s = read("SELECT md_id, player_id, price FROM player_md_snapshot ORDER BY player_id, md_id", path=db)
    s["old"] = s.groupby("player_id")["price"].shift()
    s = s.dropna(subset=["old"])
    s["delta"] = (s["price"] - s["old"]).round(2)
    return s[s["delta"] != 0].rename(columns={"price": "new"})
