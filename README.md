# UCL Fantasy toolkit (personal)

A local, paywall-free analysis dashboard for UEFA Champions League Fantasy.

## Use
```bash
cd ucl-fantasy
.venv/bin/streamlit run app.py        # dashboard at http://localhost:8501
.venv/bin/python -m ucl.ingest        # refresh data (~8 min first run, cached after)
.venv/bin/python -m ucl.backtest      # check projection accuracy on past matchdays
.venv/bin/python -m pytest -q         # tests
```

Pages: Players · Player detail · Fixtures · My team · Transfers · Captain · Trends · Model check

Ownership filters (< 20/10/5/2/1 %) are on Players, Trends and Transfers (differential mode).

## Daily auto-refresh (recommended)
UEFA's domestic-form feed only returns each player's **last 5** domestic matches, so the
history only grows if ingest runs regularly. Install the launchd job (07:00 and 19:00):
```bash
sed "s#/path/to/ucl-fantasy#$PWD#g" scripts/com.st.uclfantasy.ingest.plist \
  > ~/Library/LaunchAgents/com.st.uclfantasy.ingest.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.st.uclfantasy.ingest.plist
# remove: launchctl bootout gui/$(id -u)/com.st.uclfantasy.ingest
```

## How projections work
`ucl/analytics/projection.py`. Every competitive match (domestic league, cup, UCL) is weighted by
calendar age (half-life 21 days), so the domestic games between UCL matchdays drive form.
Domestic output is converted to UCL level via league factors (`config/model.yaml`).
Rates are shrunk toward a price-based prior (UEFA prices encode quality).

xG (no Understat: it blocks non-browser clients), `ucl/analytics/xg.py`:
- UCL matches: player xG from UEFA shot data (box/outside, big chances, penalties), xA from
  chances created, both calibrated to real season totals.
- Domestic matches: team xG from football-data.co.uk × the player's shrunk share of team goals.
- Player rates blend 40% real goals/assists, 60% xG estimate (`goal_weight`).

Team ratings (`ucl/analytics/ratings.py`): each covered league is fitted on all its matches
(70% xG + 30% goals, opponent-adjusted, 90-day half-life), scaled by league factor, then
updated with UCL results. Norway and Austria (no xG) are fitted on betting-odds-implied goals;
Czechia, Slovakia, Ukraine and Azerbaijan use the club's own scorelines. Every club's rating is
also updated with its other European games (qualifiers, last season's UCL/UEL/UECL) using UEFA
shot data, which is the main evidence for those four leagues.

Start probability is shrunk toward a bench prior and capped at 95% (the backtest showed
"certain starters" only played 82% of the time).
Scoring rules: `config/rules.yaml` (derived from official point breakdowns).

Backtest (MD2–4, 2026/27, all players with UCL minutes): v1 MAE 2.18, top-30 projected avg 4.72 pts
→ v2 MAE 2.07, top-30 avg 5.17 pts.

## Keeping your squad in sync
`config/my_team.yaml`, editable on the My team page. Update it after you make transfers on
the official site (the private team API needs the site's own app headers, so we don't call it).

## Data sources
See `docs/endpoints.md`. Public UEFA feeds and football-data.co.uk only, cached and rate-limited.
No data is included in the repo apart from small test fixtures.

Personal, non-commercial project. Not affiliated with or endorsed by UEFA.
