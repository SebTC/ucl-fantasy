# UCL Fantasy data feeds (verified 2026-11-08)

Public, unauthenticated feeds only, fetched politely (cached, rate-limited, descriptive
User-Agent). This is a personal, non-commercial project, not affiliated with or endorsed by UEFA.

Base: `https://gaming.uefa.com/en/uclfantasy/services/feeds/`
Season tour id: **90** (2026/27). Public, no auth, plain GET works from curl.
Envelope: `{"data": {"value": ...}, "meta": {...}}`

| Feed | Path | Notes |
|---|---|---|
| Config | `config/web.json` | URL templates for every other feed |
| Game state | `constraints/constraints_90.json` | current `matchdayId`, `gamedayId`, `deadline` (MM/DD/YYYY HH:MM:SS, CET), `maxTeamPlayers` (per-club cap), `maxTeamValue` |
| Players | `players/players_90_en_{matchdayId}.json` | ~1170 players, ~3.7 MB. `skill` 1=GK 2=DEF 3=MID 4=FWD, `value` price, `selPer` ownership, season stats, `trained` (fitness text), `upcomingMatchesList` |
| Clubs | `teams/teams_90_en.json` | 36 clubs |
| Fixtures | `fixtures/fixtures_90_en.json` | 8 matchdays (league phase) |
| Player UCL detail | `popupstats/popupstats_90_{playerId}.json` | per-matchday stats (`matchdayStats`) and fantasy point breakdown (`matchdayPoints`) |
| **Domestic form** | `domesticform/details_90_{playerId}.json` | last 5 domestic matches: competition, date, opponent, score, minutes, goals, assists, CS, cards. Snapshot it daily to build longer history |
| Player of match | `playerofmatch/stats_90_en_{mdId}.json` | |
| Season stats | `eot/eotstats_90_en.json` | |
| Live points | `scoring/live-players-points_90.json` | 403 outside live windows |

Stat key glossary: oF minutes, gS goals, gA/assist assists, cS clean sheets, gC goals conceded,
yC/rC cards, oG own goals, pS/pM/pE/pC penalties, bR ball recoveries, gOB goal outside box,
mOM player of the match, saves.

## Other public sources (verified 2026-11-08)
| Source | URL | Used for |
|---|---|---|
| UEFA match stats | `https://matchstats.uefa.com/v2/player-statistics/{matchId}` | per-player UCL shots (in/out of box), big chances, penalties, chances created. Same player/club/match ids as the fantasy feeds |
| UEFA comp stats | `https://compstats.uefa.com/v1/player-ranking?competitionId=1&seasonYear=2027&stats=...` | season rankings (not used yet). No xG stat exposed |
| football-data.co.uk | `https://www.football-data.co.uk/mmz4281/{2627}/{E0,SP1,I1,D1,F1,P1,N1,B1,T1,G1,SC0}.csv` | every domestic result with **team xG** (`HxG`/`AxG`, from 2026/27) and closing odds |
| football-data.co.uk extra | `https://www.football-data.co.uk/new/{NOR,AUT}.csv` | Norway + Austria, all teams, results + closing odds (no xG). CZE/SVK/UKR/AZE: 404 |
| UEFA match list | `https://match.uefa.com/v5/matches?competitionId={1,14,2019}&seasonYear=YYYY&offset=&limit=100` | qualifiers + last season's UCL/UEL/UECL. UEL/UECL shot data has total attempts only (no box split) |
| Past player feeds | `players/players_90_en_{md}.json` for earlier MDs | price + ownership as of that MD → trends backfill |

Understat (player xG) returns an error page to non-browser clients, so it is **not** used.
Player xG is estimated instead (see `ucl/analytics/xg.py`). Clubs without domestic xG:
Norway/Austria are fitted on odds-implied goals; Czechia/Slovakia/Ukraine/Azerbaijan use their
own domestic scorelines plus shot xG from their other European games (`euro_matches`).

## Logged-in data: not used
Anything behind a login (your own squad, bank, leagues) is not fetched. The squad lives in
`config/my_team.yaml` and is edited in the dashboard.

## Gotchas
- `popupstats.matchdayStats[].mId` is always 0 — take match ids from `stats[]` / `fixtures[]`.
- Python's default urllib User-Agent gets 403 from Akamai; send a descriptive UA.
- Fixture/deadline times are CET local; domestic kick-offs are UTC (`...Z`).
