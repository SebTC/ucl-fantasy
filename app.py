"""UCL Fantasy toolkit - local dashboard.   Run:  .venv/bin/streamlit run app.py"""
from __future__ import annotations

import os

import altair as alt
import pandas as pd
import streamlit as st

from ucl import ingest
from ucl.analytics import fdr, form, optimizer, projection, trends
from ucl.config import POS, STATUS_LABEL, load_my_team, rules, save_my_team
from ucl.store import DB_PATH, latest_players, read

st.set_page_config(page_title="UCL Fantasy", page_icon="⚽", layout="wide")


# ------------------------------------------------------------------ data
def _stamp() -> str:
    if not DB_PATH.exists():
        return ""
    r = read("SELECT v FROM meta WHERE k='last_ingest'")
    return r["v"].iloc[0] if len(r) else ""


@st.cache_data(show_spinner="Computing projections...")
def load(stamp: str, horizon: int):
    players = latest_players()
    meta = dict(read("SELECT k, v FROM meta").values)
    md = int(meta["current_md"])
    proj = projection.project(horizon=horizon, md_from=md)
    summ = projection.summary(proj, players)
    summ = summ.merge(form.since_last_md(), on="player_id", how="left")
    dom_cols = ["dom_apps", "dom_games", "dom_starts60", "dom_mins", "dom_goals", "dom_assists"]
    summ[dom_cols] = summ[dom_cols].fillna(0).astype(int)
    clubs = read("SELECT club_id, short, name FROM clubs")
    short = dict(zip(clubs.club_id, clubs.short))
    summ["opp"] = summ["opp_id"].map(short)
    summ["pos_name"] = summ["pos"].map(POS)
    summ["status_label"] = summ["status"].map(STATUS_LABEL).fillna(summ["status"])
    tr = trends.summary()
    if len(tr):
        summ = summ.merge(tr.drop(columns=["price_now", "own_now"]), on="player_id", how="left")
    for c in ("d_price_season", "d_price_md", "d_own_md", "d_own_season", "net_transfers"):
        if c not in summ:
            summ[c] = 0.0
        summ[c] = summ[c].fillna(0)
    proj["opp"] = proj["opp_id"].map(short)
    return players, proj, summ, meta, short


if not DB_PATH.exists():
    st.title("UCL Fantasy")
    st.info("No data yet.")
    if st.button("Download data now (takes ~8 minutes the first time)"):
        with st.status("Ingesting...") as s:
            ingest.run(log=s.write)
        st.rerun()
    st.stop()

with st.sidebar:
    horizon = st.slider("Projection horizon (matchdays)", 1, 4, 3)
    stamp = _stamp()
    st.caption(f"Data updated: {stamp.replace('T', ' ')}")
    if st.button("Refresh data"):
        with st.status("Refreshing...") as s:
            ingest.run(log=s.write)
        st.cache_data.clear()
        st.rerun()

players, proj, summ, meta, club_short = load(stamp, horizon)
CUR_MD = int(meta["current_md"])
name_of = dict(zip(summ.player_id, summ.name))
label_of = {r.player_id: f"{r.name} ({r.club_short}, {r.pos_name}, €{r.price}m)" for r in summ.itertuples()}


OWN_CAPS = {0: "Anyone", 20: "< 20%", 10: "< 10%", 5: "< 5%", 2: "< 2%", 1: "< 1%"}


def fmt_ko(ts) -> str:
    return pd.Timestamp(ts).strftime("%a %d %b %H:%M") if pd.notna(ts) else ""


# ------------------------------------------------------------------ pages
def page_players():
    st.header("Players")
    st.caption(f"Projections for MD{CUR_MD}–MD{CUR_MD + horizon - 1}. Form uses every competition, "
               "weighted by date, so domestic games between matchdays count.")
    c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 3, 2, 2, 2])
    pos = c1.multiselect("Position", ["GK", "DEF", "MID", "FWD"])
    clubs = c2.multiselect("Club", sorted(summ.club_short.dropna().unique()))
    pmin, pmax = c3.slider("Price (€m)", 3.5, 13.0, (3.5, 13.0), 0.1)
    own_cap = c4.selectbox("Owned by", list(OWN_CAPS), format_func=OWN_CAPS.get,
                           help="Differentials: players few managers own")
    avail = c5.checkbox("Hide injured / suspended / not in squad", True)
    q = c6.text_input("Search name")
    df = summ[summ.active == 1].copy()
    if pos:
        df = df[df.pos_name.isin(pos)]
    if clubs:
        df = df[df.club_short.isin(clubs)]
    df = df[(df.price >= pmin) & (df.price <= pmax)]
    if avail:
        df = df[~df.status.isin(["I", "S", "NIS"])]
    if own_cap:
        df = df[df.sel_pct < own_cap]
    if q:
        df = df[df.name.str.contains(q, case=False) | df.full_name.str.contains(q, case=False)]
    df["next"] = df.apply(lambda r: f"{r.opp} ({'H' if r.home else 'A'})" if isinstance(r.opp, str) else "", axis=1)
    show = df.sort_values("xpts_next", ascending=False)[[
        "name", "club_short", "pos_name", "price", "status_label", "next", "xpts_next", "xpts_horizon",
        "xpts_per_m", "p60", "x_goals", "x_assists", "tot_pts", "sel_pct", "d_own_md", "net_transfers",
        "d_price_md", "d_price_season", "dom_apps", "dom_starts60", "dom_goals", "dom_assists", "confidence"]]
    st.dataframe(
        show, hide_index=True, height=650, use_container_width=True,
        column_config={
            "name": "Player", "club_short": "Club", "pos_name": "Pos",
            "price": st.column_config.NumberColumn("€m", format="%.1f"),
            "status_label": "Status", "next": "Next",
            "xpts_next": st.column_config.NumberColumn(f"xPts MD{CUR_MD}", format="%.1f"),
            "xpts_horizon": st.column_config.NumberColumn(f"xPts {horizon} MDs", format="%.1f"),
            "xpts_per_m": st.column_config.NumberColumn("xPts/€m", format="%.2f"),
            "p60": st.column_config.ProgressColumn("P(60+ min)", min_value=0, max_value=1, format="percent"),
            "x_goals": st.column_config.NumberColumn("xG", format="%.2f", help=f"Expected goals MD{CUR_MD}"),
            "x_assists": st.column_config.NumberColumn("xA", format="%.2f", help=f"Expected assists MD{CUR_MD}"),
            "tot_pts": "UCL pts", "sel_pct": st.column_config.NumberColumn("Owned %", format="%.1f"),
            "d_own_md": st.column_config.NumberColumn("Δ own", format="%+.1f",
                                                      help="Ownership change (points) since last matchday's feed"),
            "net_transfers": st.column_config.NumberColumn("Net transfers", format="%+d",
                                                           help="Transfers in minus out this matchday"),
            "d_price_md": st.column_config.NumberColumn("Δ€ MD", format="%+.1f", help="Price change at this matchday"),
            "d_price_season": st.column_config.NumberColumn("Δ€ season", format="%+.1f"),
            "dom_apps": st.column_config.NumberColumn("Dom apps*", help="Domestic appearances since last UCL MD"),
            "dom_starts60": st.column_config.NumberColumn("Dom 60+*"),
            "dom_goals": st.column_config.NumberColumn("Dom G*"), "dom_assists": st.column_config.NumberColumn("Dom A*"),
            "confidence": "Data",
        },
    )
    st.caption(f"{len(show)} players. * since the last UCL matchday. xPts = expected fantasy points; "
               "xG/xA blend real goals with shot-quality and team xG.")


def page_player():
    st.header("Player detail")
    pid = st.selectbox("Player", summ.sort_values("xpts_horizon", ascending=False).player_id,
                       format_func=lambda i: label_of.get(i, i))
    r = summ.set_index("player_id").loc[pid]
    c = st.columns(6)
    c[0].metric("Price", f"€{r.price}m")
    c[1].metric("UCL points", int(r.tot_pts or 0))
    c[2].metric(f"xPts MD{CUR_MD}", f"{r.xpts_next:.1f}")
    c[3].metric("P(60+ min)", f"{(r.p60 or 0):.0%}")
    c[4].metric("Owned", f"{r.sel_pct}%")
    c[5].metric("Status", r.status_label or "Available")
    if r.trained:
        st.caption(f"Official note: {r.trained}")

    t = form.timeline(pid)
    if t.empty:
        st.info("No match data yet for this player.")
    else:
        t["Competition"] = t["competition"].where(~t["is_ucl"], "Champions League")
        t["date"] = t["kickoff"].dt.tz_convert("Europe/Paris").dt.tz_localize(None)
        base = alt.Chart(t).encode(x=alt.X("date:T", title=None))
        mins = base.mark_bar(opacity=0.35, size=10).encode(
            y=alt.Y("mins:Q", title="Minutes", scale=alt.Scale(domain=[0, 95])),
            color=alt.Color("is_ucl:N", legend=None, scale=alt.Scale(domain=[True, False], range=["#1f4fd8", "#9aa4b2"])),
            tooltip=["Competition", "date:T", "mins", "goals", "assists"])
        ga = base.mark_circle(size=120, color="#e4572e").encode(
            y=alt.Y("g_a:Q", title="Goals + assists", axis=alt.Axis(orient="right")),
            tooltip=["Competition", "date:T", "goals", "assists"])
        st.altair_chart(alt.layer(mins, ga).resolve_scale(y="independent").properties(height=260),
                        use_container_width=True)
        st.caption("Bars: minutes (blue = UCL, grey = domestic). Dots: goals + assists.")
        st.dataframe(t.sort_values("kickoff", ascending=False)[
            ["date", "Competition", "mins", "goals", "xg", "assists", "xa", "team_xg", "cs", "yc", "recoveries",
             "points"]],
            hide_index=True, use_container_width=True,
            column_config={"xg": st.column_config.NumberColumn("xG", format="%.2f", help="UCL: from his shots"),
                           "xa": st.column_config.NumberColumn("xA", format="%.2f", help="UCL: from chances created"),
                           "team_xg": st.column_config.NumberColumn("Team xG", format="%.2f",
                                                                    help="His team's xG in the match")})

    h = trends.history()
    h = h[h.player_id == pid]
    if len(h) > 1:
        st.subheader("Price & ownership")
        pc = alt.Chart(h).encode(x=alt.X("date:T", title=None))
        price = pc.mark_line(point=True, color="#1f4fd8", interpolate="step-after").encode(
            y=alt.Y("price:Q", title="Price €m", scale=alt.Scale(zero=False)), tooltip=["label", "price", "sel_pct"])
        own = pc.mark_line(point=True, color="#e4572e").encode(
            y=alt.Y("sel_pct:Q", title="Owned %", axis=alt.Axis(orient="right")), tooltip=["label", "price", "sel_pct"])
        st.altair_chart(alt.layer(price, own).resolve_scale(y="independent").properties(height=220),
                        use_container_width=True)
        st.caption("Blue: price. Orange: ownership. One point per matchday feed plus each daily refresh.")

    pr = proj[proj.player_id == pid].sort_values("md_id")
    if len(pr):
        st.subheader("Projection breakdown")
        comp_cols = [c for c in pr.columns if c.startswith("pts_")]
        b = pr[["md_id", "opp", "home"] + comp_cols + ["xpts"]].copy()
        b.columns = [c.replace("pts_", "") for c in b.columns]
        st.dataframe(b.round(2), hide_index=True, use_container_width=True)


def page_fixtures():
    st.header("Fixture difficulty")
    g = fdr.fixture_grid(CUR_MD, horizon=max(horizon, 3))
    if g.empty:
        st.info("No upcoming fixtures in the feed yet (knockout draw pending?).")
        return
    g["club"] = g.club_id.map(club_short)
    g["label"] = g.apply(lambda r: f"{club_short.get(r.opp_id, '?')} ({'H' if r.home else 'A'})", axis=1)
    g["net"] = g.xgf - g.xga
    metric = st.radio("Colour by", ["Net (attack - defence)", "Attack: expected goals for", "Defence: clean sheet %"],
                      horizontal=True)
    col, title, scheme = {"Net (attack - defence)": ("net", "xGF - xGA", "redyellowgreen"),
                          "Attack: expected goals for": ("xgf", "xGF", "greens"),
                          "Defence: clean sheet %": ("p_cs", "P(CS)", "blues")}[metric]
    order = g.groupby("club")[col].sum().sort_values(ascending=False).index.tolist()
    ch = alt.Chart(g).mark_rect().encode(
        x=alt.X("md_id:O", title="Matchday"), y=alt.Y("club:N", sort=order, title=None),
        color=alt.Color(f"{col}:Q", title=title, scale=alt.Scale(scheme=scheme)),
        tooltip=["club", "md_id", "label", alt.Tooltip("xgf:Q", format=".2f"), alt.Tooltip("xga:Q", format=".2f"),
                 alt.Tooltip("p_cs:Q", format=".0%")])
    txt = alt.Chart(g).mark_text(fontSize=11).encode(x="md_id:O", y=alt.Y("club:N", sort=order), text="label")
    st.altair_chart((ch + txt).properties(height=max(400, 22 * len(order))), use_container_width=True)
    st.caption("Clubs sorted by the best run over the selected window. Ratings are fitted on every match in "
               "each domestic league (team xG or betting odds + goals, opponent-adjusted), scaled by league "
               "strength, then updated with other European games (qualifiers, last season) and this season's "
               "UCL results.")
    with st.expander("Team ratings"):
        r = fdr.ratings_table()
        r["club"] = r.club_id.map(club_short)
        st.dataframe(r.sort_values("att", ascending=False)[["club", "att", "def", "dom_att", "dom_def", "source",
                                                            "n_euro"]],
                     hide_index=True, use_container_width=True,
                     column_config={"att": st.column_config.NumberColumn("Attack", format="%.2f",
                                                                         help="1.0 = average UCL side; higher is better"),
                                    "def": st.column_config.NumberColumn("Defence", format="%.2f",
                                                                         help="Goals allowed vs average; lower is better"),
                                    "dom_att": st.column_config.NumberColumn("Attack (domestic only)", format="%.2f"),
                                    "dom_def": st.column_config.NumberColumn("Defence (domestic only)", format="%.2f"),
                                    "source": st.column_config.TextColumn(
                                        "Domestic data", help="league xG: whole league fitted on team xG; league odds: "
                                        "fitted on betting-odds-implied goals (Norway, Austria); domestic goals: own "
                                        "scorelines only (Czechia, Slovakia, Ukraine, Azerbaijan)"),
                                    "n_euro": st.column_config.NumberColumn(
                                        "Other Euro games", help="Qualifiers and last season's UCL/UEL/UECL games "
                                        "(shot xG vs European opposition) used in the rating")})


def squad_table(team: dict) -> pd.DataFrame:
    ids = list(team["squad"])
    s = summ[summ.player_id.isin(ids)].copy()
    s["bench"] = s.player_id.map(team["squad"])
    per_md = proj[proj.player_id.isin(ids)].pivot_table(index="player_id", columns="md_id", values="xpts", aggfunc="sum")
    per_md.columns = [f"MD{c}" for c in per_md.columns]
    return s.merge(per_md, left_on="player_id", right_index=True, how="left")


def page_my_team():
    st.header("My team")
    team = load_my_team()
    if len(team["squad"]) != 15:
        st.warning("Set your 15-player squad below.")
    s = squad_table(team) if team["squad"] else pd.DataFrame()
    if len(s):
        xi, cap = optimizer.best_lineup(s.set_index("player_id")["xpts_next"], s.set_index("player_id")["pos"])
        s["best_xi"] = s.player_id.isin(xi)
        s["role"] = s.player_id.map(lambda i: "C" if i == cap else "")
        c = st.columns(4)
        c[0].metric("Squad value", f"€{s.price.sum():.1f}m")
        c[1].metric("Bank", f"€{team['bank']:.1f}m")
        c[2].metric("Free transfers", team["free_transfers"])
        best = s[s.best_xi].xpts_next.sum() + s.loc[s.player_id == cap, "xpts_next"].sum()
        c[3].metric(f"Best XI xPts MD{CUR_MD}", f"{best:.1f}")
        md_cols = [c for c in s.columns if c.startswith("MD")]
        s["next"] = s.apply(lambda r: f"{r.opp} ({'H' if r.home else 'A'}) {fmt_ko(r.kickoff)}"
                            if isinstance(r.opp, str) else "-", axis=1)
        st.dataframe(
            s.sort_values(["best_xi", "pos", "xpts_next"], ascending=[False, True, False])[
                ["role", "best_xi", "name", "club_short", "pos_name", "price", "status_label", "next", "p60"] + md_cols],
            hide_index=True, use_container_width=True, height=36 * 16,
            column_config={"best_xi": st.column_config.CheckboxColumn("Start"), "role": "",
                           "name": "Player", "club_short": "Club", "pos_name": "Pos",
                           "price": st.column_config.NumberColumn("€m", format="%.1f"),
                           "status_label": "Status", "next": "Next fixture",
                           "p60": st.column_config.ProgressColumn("P(60+)", min_value=0, max_value=1, format="percent"),
                           **{m: st.column_config.NumberColumn(m, format="%.1f") for m in md_cols}})
        flagged = s[s.status.isin(["I", "S", "NIS", "D"]) | s.trained.str.startswith("Unlikely")]
        for r in flagged.itertuples():
            st.warning(f"{r.name}: {r.status_label or ''} {('- ' + r.trained) if r.trained else ''}")

    with st.expander("Edit squad", expanded=len(team["squad"]) != 15):
        all_ids = summ.sort_values(["pos", "price"], ascending=[True, False]).player_id.tolist()
        chosen = st.multiselect("15 players", all_ids, default=[i for i in team["squad"] if i in label_of],
                                format_func=lambda i: label_of.get(i, i), max_selections=15)
        c1, c2, c3 = st.columns(3)
        bank = c1.number_input("Bank (€m)", 0.0, 100.0, float(team["bank"]), 0.1)
        ft = c2.number_input("Free transfers", 0, 15, int(team["free_transfers"]))
        cap = c3.selectbox("Captain (official)", [None] + chosen, format_func=lambda i: label_of.get(i, "-") if i else "-",
                           index=([None] + chosen).index(team["captain"]) if team.get("captain") in chosen else 0)
        if st.button("Save squad", disabled=len(chosen) != 15):
            team.update(squad={i: team["squad"].get(i, 0) for i in chosen}, bank=bank, free_transfers=ft,
                        captain=cap, matchday=CUR_MD)
            save_my_team(team)
            st.success("Saved")
            st.rerun()


def page_transfers():
    st.header("Transfer planner")
    team = load_my_team()
    if len(team["squad"]) != 15:
        st.warning("Set your squad on the My team page first.")
        return
    c1, c2, c3, c4 = st.columns(4)
    mode = c1.selectbox("Mode", ["Normal", "Wildcard", "Limitless (1 MD, unlimited budget)"])
    max_t = c2.slider("Max transfers", 0, 6, min(2, team["free_transfers"] + 1), disabled=mode != "Normal")
    ft = c3.number_input("Free transfers", 0, 15, int(team["free_transfers"]), disabled=mode != "Normal")
    bank = c4.number_input("Bank (€m)", 0.0, 50.0, float(team["bank"]), 0.1)
    excl = st.multiselect("Never pick", summ.player_id, format_func=lambda i: label_of.get(i, i))
    own_cap = st.selectbox("Only buy players owned by", list(OWN_CAPS), format_func=OWN_CAPS.get,
                           help="Differential mode: incoming players must be below this ownership")
    force = st.multiselect("Must include", summ.player_id, format_func=lambda i: label_of.get(i, i))
    if not st.button("Optimise", type="primary"):
        st.caption("Maximises expected points over the horizon (later matchdays discounted 10%), choosing the best "
                   "XI and captain each matchday. Extra transfers cost "
                   f"{rules()['extra_transfer_cost']} points each.")
        return
    p = proj
    kw = dict(free_transfers=ft, max_transfers=max_t)
    if mode == "Wildcard":
        kw = dict(free_transfers=15, max_transfers=15)
    elif mode.startswith("Limitless"):
        kw = dict(free_transfers=15, max_transfers=15, budget=None)
        p = proj[proj.md_id == CUR_MD]
    with st.spinner("Solving..."):
        base = optimizer.optimise(p, players, list(team["squad"]), bank, free_transfers=15, max_transfers=0)
        if own_cap:
            excl = set(excl) | set(summ.loc[(summ.sel_pct >= own_cap) & ~summ.player_id.isin(team["squad"])
                                            & ~summ.player_id.isin(force), "player_id"])
        plan = optimizer.optimise(p, players, list(team["squad"]), bank, exclude=set(excl), force_in=set(force), **kw)
    if plan.status != "Optimal":
        st.error(f"Solver status: {plan.status}")
        return
    gain = plan.objective - base.objective
    c = st.columns(3)
    c[0].metric("Expected gain vs no transfers", f"{gain:+.1f} pts")
    c[1].metric("Points hit", f"-{plan.hit_cost}" if plan.hit_cost else "0")
    c[2].metric("Squad cost", f"€{plan.cost:.1f}m")
    if not plan.transfers_in:
        st.success("Best move: keep your squad (roll the transfer).")
    out_df = summ[summ.player_id.isin(plan.transfers_out)][["name", "club_short", "pos_name", "price", "xpts_horizon"]]
    in_df = summ[summ.player_id.isin(plan.transfers_in)][["name", "club_short", "pos_name", "price", "xpts_horizon"]]
    a, b = st.columns(2)
    a.subheader("Out")
    a.dataframe(out_df, hide_index=True, use_container_width=True)
    b.subheader("In")
    b.dataframe(in_df, hide_index=True, use_container_width=True)
    st.subheader("Captain & lineup by matchday")
    for md, xi in plan.lineups.items():
        st.markdown(f"**MD{md}** captain: **{name_of.get(plan.captains[md])}** - XI: "
                    + ", ".join(name_of.get(i, i) for i in xi))


def page_captain():
    st.header("Captain picker")
    team = load_my_team()
    scope = st.radio("Pool", ["My squad", "All players"], horizontal=True)
    df = summ[summ.player_id.isin(team["squad"])] if scope == "My squad" else summ[summ.active == 1]
    df = df.sort_values("xpts_next", ascending=False).head(15).copy()
    df["kickoff_s"] = df.kickoff.map(fmt_ko)
    df["fixture"] = df.apply(lambda r: f"{r.opp} ({'H' if r.home else 'A'})" if isinstance(r.opp, str) else "-", axis=1)
    df["captain_xpts"] = df.xpts_next * 2
    st.dataframe(df[["name", "club_short", "fixture", "kickoff_s", "xpts_next", "captain_xpts", "p60", "x_goals",
                     "x_assists", "status_label"]], hide_index=True, use_container_width=True,
                 column_config={"kickoff_s": "Kick-off (CET)",
                                "xpts_next": st.column_config.NumberColumn("xPts", format="%.1f"),
                                "captain_xpts": st.column_config.NumberColumn("As captain", format="%.1f"),
                                "p60": st.column_config.ProgressColumn("P(60+)", min_value=0, max_value=1, format="percent"),
                                "x_goals": st.column_config.NumberColumn("xG", format="%.2f"),
                                "x_assists": st.column_config.NumberColumn("xA", format="%.2f")})
    early = df[df.kickoff == df.kickoff.min()]
    if len(early) and len(df):
        st.info("Captain switch tip: UCL lets you move the armband after early games. A strong pick from the "
                f"earliest slot ({fmt_ko(df.kickoff.min())}) keeps the option to switch: "
                + ", ".join(early.name.head(3)))


def page_trends():
    st.header("Price & ownership trends")
    tr = summ[summ.active == 1]
    md = CUR_MD
    st.caption(f"Changes at MD{md} compare the MD{md} player feed with MD{md - 1}'s. Prices move between "
               "matchdays; net transfers are this matchday's window so far.")
    c1, c2, c3 = st.columns(3)
    pos = c1.multiselect("Position", ["GK", "DEF", "MID", "FWD"], key="tr_pos")
    own_cap = c2.selectbox("Owned by", list(OWN_CAPS), format_func=OWN_CAPS.get, key="tr_own")
    min_price = c3.slider("Min price (€m)", 3.5, 13.0, 3.5, 0.5)
    if pos:
        tr = tr[tr.pos_name.isin(pos)]
    if own_cap:
        tr = tr[tr.sel_pct < own_cap]
    tr = tr[tr.price >= min_price]
    cols = ["name", "club_short", "pos_name", "price", "d_price_md", "d_price_season", "sel_pct", "d_own_md",
            "d_own_season", "net_transfers", "xpts_horizon"]
    cfg = {"name": "Player", "club_short": "Club", "pos_name": "Pos",
           "price": st.column_config.NumberColumn("€m", format="%.1f"),
           "d_price_md": st.column_config.NumberColumn("Δ€ MD", format="%+.1f"),
           "d_price_season": st.column_config.NumberColumn("Δ€ season", format="%+.1f"),
           "sel_pct": st.column_config.NumberColumn("Owned %", format="%.1f"),
           "d_own_md": st.column_config.NumberColumn("Δ own MD", format="%+.1f"),
           "d_own_season": st.column_config.NumberColumn("Δ own season", format="%+.1f"),
           "net_transfers": st.column_config.NumberColumn("Net transfers", format="%+d"),
           "xpts_horizon": st.column_config.NumberColumn(f"xPts {horizon} MDs", format="%.1f")}
    a, b = st.columns(2)
    a.subheader("Most transferred in")
    a.dataframe(tr.nlargest(15, "net_transfers")[cols], hide_index=True, use_container_width=True, column_config=cfg)
    b.subheader("Most transferred out")
    b.dataframe(tr.nsmallest(15, "net_transfers")[cols], hide_index=True, use_container_width=True, column_config=cfg)
    a, b = st.columns(2)
    a.subheader("Price risers (season)")
    a.dataframe(tr[tr.d_price_season > 0].sort_values(["d_price_season", "d_price_md"], ascending=False).head(15)[cols],
                hide_index=True, use_container_width=True, column_config=cfg)
    b.subheader("Price fallers (season)")
    b.dataframe(tr[tr.d_price_season < 0].sort_values(["d_price_season", "price"], ascending=[True, False]).head(15)[cols],
                hide_index=True, use_container_width=True, column_config=cfg)
    st.subheader("Under-owned for their projection")
    st.caption("High expected points, low ownership: the differentials the template misses.")
    dif = tr[tr.p60 > 0.5].assign(edge=lambda d: d.xpts_horizon * (1 - d.sel_pct / 100))
    st.dataframe(dif[dif.sel_pct < (own_cap or 10)].nlargest(20, "xpts_horizon")[cols],
                 hide_index=True, use_container_width=True, column_config=cfg)

    st.subheader("Compare players")
    default = tr.nlargest(3, "net_transfers").player_id.tolist()
    pick = st.multiselect("Players", summ.player_id, default=default, format_func=lambda i: label_of.get(i, i))
    if pick:
        h = trends.history()
        h = h[h.player_id.isin(pick)].assign(player=lambda d: d.player_id.map(name_of))
        metric = st.radio("Show", ["Ownership %", "Price"], horizontal=True)
        y = "sel_pct" if metric.startswith("Own") else "price"
        st.altair_chart(alt.Chart(h).mark_line(point=True, interpolate="step-after" if y == "price" else "linear")
                        .encode(x=alt.X("date:T", title=None), y=alt.Y(f"{y}:Q", title=metric, scale=alt.Scale(zero=False)),
                                color=alt.Color("player:N", title=None), tooltip=["player", "label", "price", "sel_pct"])
                        .properties(height=300), use_container_width=True)


def page_model():
    st.header("Model check")
    st.caption("Backtest: each past matchday is projected using only matches played before it, "
               "then compared with the real fantasy points.")
    if st.button("Run backtest"):
        from ucl import backtest
        with st.spinner("Running..."):
            st.dataframe(backtest.run(verbose=False), hide_index=True)
        st.caption("spearman: rank agreement over everyone who has played UCL minutes this season (1 = perfect); "
                   "rho_played: the same among players who actually played that matchday. "
                   "top30: real points of the 30 highest-projected players. cal_*: projected / actual totals "
                   "(1 = calibrated).")


_PAGES = [
    (page_players, "Players", "📋", None),
    (page_player, "Player detail", "🔎", "player"),
    (page_fixtures, "Fixtures", "📅", "fixtures"),
    (page_my_team, "My team", "👕", "my-team"),
    (page_transfers, "Transfers", "🔁", "transfers"),
    (page_captain, "Captain", "©️", "captain"),
    (page_trends, "Trends", "📈", "trends"),
    (page_model, "Model check", "🧪", "model"),
]
_start = os.environ.get("UCL_START_PAGE", "Players")  # used by tests/test_app.py
pg = st.navigation([st.Page(fn, title=t, icon=i, url_path=u, default=(t == _start)) for fn, t, i, u in _PAGES])
pg.run()
