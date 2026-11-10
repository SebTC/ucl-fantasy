"""New data sources: UEFA match stats, football-data.co.uk, league fits, trends."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ucl import parse
from ucl.analytics.ratings import fit_league
from ucl.analytics.xg import shot_xg

FX = Path(__file__).parent / "fixtures"


def test_match_player_stats():
    rows = parse.match_player_stats(json.loads((FX / "matchstats_2049624.json").read_text()), "2049624")
    assert rows and all(r["mins"] > 0 for r in rows)
    r = next(r for r in rows if r["player_id"] == "250051338")
    assert (r["goals"], r["shots_box"], r["shots_out"], r["pens"]) == (1, 3, 2, 1)


def test_football_data_parse():
    rows = parse.football_data((FX / "football_data_E0.csv").read_text(encoding="utf-8-sig"), "E0")
    assert len(rows) == 5
    r = rows[0]
    assert r["date"].count("-") == 2 and r["hxg"] is not None
    assert abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1) < 1e-9


def test_shot_xg_values():
    df = pd.DataFrame(dict(shots_box=[3, 0], shots_out=[2, 0], big_chances=[1, 0], pens=[1, 0]))
    x = shot_xg(df)
    assert x[1] == 0 and 1.0 < x[0] < 1.5


def test_fit_league_recovers_strength():
    rng = np.random.default_rng(0)
    teams = [f"T{i}" for i in range(10)]
    att = dict(zip(teams, np.linspace(0.6, 1.6, 10)))
    rows, now = [], pd.Timestamp("2026-11-01", tz="UTC")
    for k in range(6):
        for h in teams:
            for a in teams:
                if h != a:
                    lam_h, lam_a = 1.4 * att[h], 1.4 * att[a]
                    rows.append(dict(date=now - pd.Timedelta(days=k * 30 + 1), home=h, away=a,
                                     hg=rng.poisson(lam_h), ag=rng.poisson(lam_a), hxg=lam_h, axg=lam_a))
    r, mu, home = fit_league(pd.DataFrame(rows), now, dict(xg_weight_team=0.7, team_lookback_days=400))
    fitted = r.set_index("team")["att"]
    assert fitted.corr(pd.Series(att)) > 0.97
    assert 1.2 < mu < 1.6


def test_football_data_extra_parse():
    from ucl.parse import football_data_extra
    rows = football_data_extra((FX / "football_data_new_NOR.csv").read_text(encoding="utf-8-sig"), "NOR", "2000-01-01")
    assert rows and rows[0]["hxg"] is None and rows[0]["p_home"] is not None
    assert football_data_extra((FX / "football_data_new_NOR.csv").read_text(encoding="utf-8-sig"), "NOR", "2099-01-01") == []


def test_uefa_match_list():
    rows = parse.uefa_match_list(json.loads((FX / "uefa_matches.json").read_text()), 1, 2027)
    assert rows and all(r["home_score"] is not None and r["home_country"] for r in rows)


def test_odds_goals_round_trip():
    from math import exp, factorial

    from ucl.analytics.ratings import odds_goals

    def pmf(lam):
        return [exp(-lam) * lam ** k / factorial(k) for k in range(11)]

    lh, la = 1.9, 0.8
    ph_, pa_ = pmf(lh), pmf(la)
    p_home = sum(ph_[i] * pa_[j] for i in range(11) for j in range(11) if i > j)
    p_away = sum(ph_[i] * pa_[j] for i in range(11) for j in range(11) if i < j)
    gh, ga = odds_goals([p_home, np.nan], [p_away, np.nan], total=lh + la)
    assert abs(gh[0] - lh) < 0.03 and abs(ga[0] - la) < 0.03
    assert np.isnan(gh[1])


def test_european_update_direction():
    from ucl.analytics.ratings import _update

    p = dict(home_advantage=1.1, team_half_life_days=90)
    now = pd.Timestamp("2026-11-01", tz="UTC")
    ko = now - pd.Timedelta(days=10)
    # club A scores far more than expected against an average opponent and concedes nothing
    games = [("A", "X", 1.0, 1.0, 4.0, 0.0, True, ko)] * 3
    att, dfn, n = _update({"A": 1.0}, {"A": 1.0}, ["A"], games, 1.5, p, 6, now)
    assert att["A"] > 1.0 and dfn["A"] < 1.0 and n["A"] == 3
