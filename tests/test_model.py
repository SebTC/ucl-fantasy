import pandas as pd

from ucl.analytics.optimizer import best_lineup, optimise
from ucl.analytics.projection import poisson_floor_div
from ucl.config import rules


def test_poisson_floor_div():
    assert poisson_floor_div(0, 3) == 0
    assert 0.2 < poisson_floor_div(1.5, 2) < 0.6
    assert abs(poisson_floor_div(30, 3) - 9.67) < 0.2  # large lambda ~ (lam-1)/3


def _pool():
    rows, pid = [], 0
    for pos, n in ((1, 4), (2, 10), (3, 10), (4, 6)):
        for k in range(n):
            pid += 1
            rows.append(dict(player_id=str(pid), pos=pos, price=4.0 + k * 0.5, club_id=str(pid % 6), active=1))
    pl = pd.DataFrame(rows)
    proj = pd.DataFrame([dict(player_id=r.player_id, md_id=md, xpts=r.price * (1 + 0.1 * r.pos)) for r in pl.itertuples()
                         for md in (5, 6)])
    return pl, proj


def _legal(squad, pl, budget):
    s = pl.set_index("player_id").loc[squad]
    R = rules()
    assert len(squad) == 15
    counts = s["pos"].value_counts().to_dict()
    assert counts == {1: R["squad"]["GK"], 2: R["squad"]["DEF"], 3: R["squad"]["MID"], 4: R["squad"]["FWD"]}
    assert s["club_id"].value_counts().max() <= R["max_per_club"]
    assert s["price"].sum() <= budget + 1e-6


def test_optimiser_respects_rules():
    pl, proj = _pool()
    cheapest = []
    for pos, n in ((1, 2), (2, 5), (3, 5), (4, 3)):
        cheapest += pl[pl.pos == pos].sort_values("price").player_id.head(n).tolist()
    plan = optimise(proj, pl, cheapest, bank=10.0, free_transfers=2, max_transfers=3, max_per_club=3)
    assert plan.status == "Optimal"
    budget = pl.set_index("player_id").loc[cheapest, "price"].sum() + 10.0
    _legal(plan.squad, pl, budget)
    assert len(plan.transfers_in) <= 3
    assert plan.hit_cost == 4 * max(0, len(plan.transfers_in) - 2)
    for md, xi in plan.lineups.items():
        assert len(xi) == 11 and plan.captains[md] in xi


def test_best_lineup():
    pl, proj = _pool()
    squad = pl.groupby("pos").head(5)
    squad = pd.concat([squad[squad.pos == 1].head(2), squad[squad.pos == 2], squad[squad.pos == 3], squad[squad.pos == 4].head(3)])
    x = proj[proj.md_id == 5].set_index("player_id").loc[squad.player_id, "xpts"]
    xi, cap = best_lineup(x, squad.set_index("player_id")["pos"])
    assert len(xi) == 11 and cap in xi
    assert (squad.set_index("player_id").loc[xi, "pos"] == 1).sum() == 1
