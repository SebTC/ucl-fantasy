import json
from pathlib import Path

from ucl import parse

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text())["data"]["value"]


def test_players_parse():
    rows = parse.players(load("players_90_en_5.json")["playerList"], "2026-11-08", 5)
    assert len(rows) > 1000
    h = next(r for r in rows if r["player_id"] == "250103758")
    assert h["pos"] == 4 and h["price"] == 11.2 and h["club_id"] == "52919"


def test_fixtures_parse():
    mds, matches = parse.fixtures(load("fixtures_90_en.json"))
    assert len(mds) == 8
    assert len(matches) == 8 * 18
    played = [m for m in matches if m["home_score"] is not None]
    assert played and all(m["kickoff"] for m in matches)


def test_popupstats_match_ids_filled():
    rows = parse.popupstats(load("popupstats_250103758.json"), "250103758")
    assert rows and all(r["match_id"] not in ("", "0") for r in rows)
    assert sum(r["goals"] for r in rows) == 5


def test_domestic_form():
    rows = parse.domestic_form(load("domesticform_250103758.json"), "250103758")
    assert len(rows) == 5
    r = rows[0]
    assert r["competition"] == "English Premier League" and r["home"] == 0
    assert r["team_score"] == 1 and r["opp_score"] == 3


def test_dates():
    assert parse._dt("11/24/2026 21:00:00") == "2026-11-24T21:00:00"
    assert parse._dt("09/10/26 06:45:00 PM") == "2026-09-10T18:45:00"
