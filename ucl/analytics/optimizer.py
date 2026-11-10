"""Transfer optimiser (integer programme, solved with HiGHS via PuLP).

Maximises expected points over a horizon of matchdays:
  * picks a 15-man squad (2 GK / 5 DEF / 5 MID / 3 FWD), max N per club, within budget
  * for each matchday picks a legal starting XI and a captain (points doubled)
  * bench players count a little (bench_weight) as injury cover
  * each transfer beyond the free ones costs `extra_transfer_cost` points
Wildcard / Limitless: pass max_transfers=15 and free_transfers=15 (Limitless also budget=None).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pulp

from ..config import POS, rules


@dataclass
class Plan:
    status: str
    squad: list[str]
    transfers_in: list[str]
    transfers_out: list[str]
    lineups: dict[int, list[str]]
    captains: dict[int, str]
    objective: float
    hit_cost: int
    cost: float


def optimise(
    proj: pd.DataFrame,            # rows: player_id, md_id, xpts
    players: pd.DataFrame,         # player_id, pos, price, club_id, active
    current_squad: list[str],
    bank: float,
    free_transfers: int = 2,
    max_transfers: int | None = 2,
    horizon_mds: list[int] | None = None,
    max_per_club: int | None = None,
    budget: float | None = 0.0,    # None = unlimited (Limitless)
    bench_weight: float = 0.1,
    md_decay: float = 0.9,         # later matchdays are less certain
    exclude: set[str] | None = None,
    force_in: set[str] | None = None,
) -> Plan:
    R = rules()
    max_per_club = max_per_club or R["max_per_club"]
    mds = horizon_mds or sorted(proj["md_id"].unique())
    pl = players[(players["active"] == 1) | players["player_id"].isin(current_squad)].copy()
    if exclude:
        pl = pl[~pl["player_id"].isin(exclude - set(current_squad))]
    pl = pl.set_index("player_id")
    ids = list(pl.index)
    x = proj.pivot_table(index="player_id", columns="md_id", values="xpts", aggfunc="sum").reindex(ids).fillna(0.0)

    prob = pulp.LpProblem("ucl", pulp.LpMaximize)
    s = pulp.LpVariable.dicts("s", ids, cat="Binary")
    st = {(i, m): pulp.LpVariable(f"st_{i}_{m}", cat="Binary") for i in ids for m in mds}
    cp = {(i, m): pulp.LpVariable(f"c_{i}_{m}", cat="Binary") for i in ids for m in mds}
    cur = set(current_squad)
    t_in = {i: s[i] for i in ids if i not in cur}
    n_in = pulp.lpSum(t_in.values())
    extra = pulp.LpVariable("extra", lowBound=0)

    prob += (
        pulp.lpSum(
            (md_decay ** k) * x.at[i, m] * (st[i, m] + cp[i, m] + bench_weight * (s[i] - st[i, m]))
            for k, m in enumerate(mds) for i in ids
        )
        - R["extra_transfer_cost"] * extra
    )
    for code, n in R["squad"].items():
        pos_id = {v: k for k, v in POS.items()}[code]
        prob += pulp.lpSum(s[i] for i in ids if pl.at[i, "pos"] == pos_id) == n
    for club, grp in pl.groupby("club_id"):
        prob += pulp.lpSum(s[i] for i in grp.index) <= max_per_club
    if budget is not None:
        sell_value = float(pl.loc[[i for i in current_squad if i in pl.index], "price"].sum())
        prob += pulp.lpSum(s[i] * float(pl.at[i, "price"]) for i in ids) <= sell_value + bank + budget
    for m in mds:
        prob += pulp.lpSum(st[i, m] for i in ids) == 11
        prob += pulp.lpSum(cp[i, m] for i in ids) == 1
        for code, (lo, hi) in R["starting_xi"].items():
            pos_id = {v: k for k, v in POS.items()}[code]
            grp = [i for i in ids if pl.at[i, "pos"] == pos_id]
            prob += pulp.lpSum(st[i, m] for i in grp) >= lo
            prob += pulp.lpSum(st[i, m] for i in grp) <= hi
        for i in ids:
            prob += st[i, m] <= s[i]
            prob += cp[i, m] <= st[i, m]
    prob += extra >= n_in - free_transfers
    if max_transfers is not None:
        prob += n_in <= max_transfers
    for i in force_in or ():
        if i in s:
            prob += s[i] == 1

    prob.solve(pulp.HiGHS(msg=False, timeLimit=60))
    squad = [i for i in ids if s[i].value() and s[i].value() > 0.5]
    lineups = {m: [i for i in ids if st[i, m].value() > 0.5] for m in mds}
    caps = {m: next((i for i in ids if cp[i, m].value() > 0.5), None) for m in mds}
    ins = [i for i in squad if i not in cur]
    outs = [i for i in current_squad if i not in squad]
    return Plan(
        status=pulp.LpStatus[prob.status], squad=squad, transfers_in=ins, transfers_out=outs,
        lineups=lineups, captains=caps, objective=float(pulp.value(prob.objective) or 0),
        hit_cost=int(round(extra.value() or 0)) * R["extra_transfer_cost"],
        cost=float(pl.loc[squad, "price"].sum()),
    )


def best_lineup(xpts: pd.Series, pos: pd.Series) -> tuple[list[str], str]:
    """Best legal XI and captain for one matchday from a 15-man squad."""
    R = rules()
    ids = list(xpts.index)
    prob = pulp.LpProblem("xi", pulp.LpMaximize)
    st = pulp.LpVariable.dicts("st", ids, cat="Binary")
    cp = pulp.LpVariable.dicts("cp", ids, cat="Binary")
    prob += pulp.lpSum(float(xpts[i]) * (st[i] + cp[i]) for i in ids)
    prob += pulp.lpSum(st.values()) == 11
    prob += pulp.lpSum(cp.values()) == 1
    for code, (lo, hi) in R["starting_xi"].items():
        pid = {v: k for k, v in POS.items()}[code]
        grp = [i for i in ids if pos[i] == pid]
        prob += pulp.lpSum(st[i] for i in grp) >= lo
        prob += pulp.lpSum(st[i] for i in grp) <= hi
    for i in ids:
        prob += cp[i] <= st[i]
    prob.solve(pulp.HiGHS(msg=False))
    return [i for i in ids if st[i].value() > 0.5], next(i for i in ids if cp[i].value() > 0.5)
