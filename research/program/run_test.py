"""
The harness. One pre-registered test per invocation.

    python -m research.program.run_test 3A-1            # runs the registered test for prereg id 3A-1

Rules it enforces, so a session cannot forget them:
  * a prereg file research/program/prereg/<id>.md must exist BEFORE the run (it refuses otherwise)
  * data comes only through research.program.data (budgeted, holdout-sealed)
  * the cost model is fixed here, not in the test
  * every run appends exactly one line to SCOREBOARD.md with the running trial count N and the deflated Sharpe at N
  * a BudgetExceeded stops the run and prints the credit-rule message; nothing is scored

Registered tests return a dict: {"daily": pd.Series (date -> $ P&L), "trades": int, "gross": $, "costs": $, "params_grid": optional T x K matrix of variant daily P&L for PBO}
"""
from __future__ import annotations
import importlib, os, re, sys, math, json
import datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from research.program import data as D
from research.program.stats import deflated_sharpe, pbo, sharpe_ann

HERE = os.path.dirname(os.path.abspath(__file__))
SCORE = os.path.join(HERE, "SCOREBOARD.md")
COST = {"ES": 2.50 + 12.50, "NQ": 2.50 + 5.00, "RTY": 2.50 + 5.00, "YM": 2.50 + 5.00,     # full-size: comm + 1 tick, per side
        "MES": 1.00 + 1.25, "MNQ": 1.00 + 0.50, "M2K": 1.00 + 0.50, "MYM": 1.00 + 0.50,
        "ZN": 2.00 + 15.625, "ZB": 2.00 + 31.25, "GC": 2.50 + 10.00, "CL": 2.50 + 10.00}
POINT = {"ES": 50, "NQ": 20, "RTY": 50, "YM": 5, "MES": 5, "MNQ": 2, "M2K": 5, "MYM": 0.5, "ZN": 1000, "ZB": 1000, "GC": 100, "CL": 1000}


def trial_count() -> int:
    try:
        return sum(1 for l in open(SCORE) if l.startswith("| ") and not l.startswith("| N ") and not l.startswith("|---"))
    except FileNotFoundError:
        return 0


def bp_rule_cell(res: dict, n_trials: int, ppy: float) -> str:
    """PROGRAM.md §0b: Sharpe / PF / cost share / DSR p / verdict with costs in bp at the Oct-2026 level."""
    if "stopped_bp" not in res:
        return "n/a"
    g = np.asarray(res["gross_bp"], float); st = np.asarray(res["stopped_bp"], float); c = float(res["cost_bp"])
    net = st - c
    if len(net) < 2 or net.std(ddof=0) == 0:
        return "n/a"
    sr_d = float(net.mean() / net.std(ddof=0)); T = len(net)
    sk = float(((net - net.mean()) ** 3).mean() / (net.std(ddof=0) ** 3 + 1e-12)); ku = float(((net - net.mean()) ** 4).mean() / (net.std(ddof=0) ** 4 + 1e-12))
    dsr = deflated_sharpe(sr_d, T, n_trials, sk, ku)
    pf = float(net[net > 0].sum() / -net[net < 0].sum()) if (net < 0).any() else float("inf")
    cs = c / g.mean() if g.mean() > 0 else float("nan")
    gate = (dsr >= 0.95) and (pf >= 1.15) and (not (cs == cs) or cs < 0.40)
    return f"Sharpe {sharpe_ann(net, ppy):.2f} / PF {pf:.2f} / cost {cs:.0%} / DSR {dsr:.3f} / {'PASS->phase3' if gate else 'fail'} (net {net.mean():.1f} bp)"


def score(test_id: str, hypothesis: str, data_desc: str, res: dict, notes: str = ""):
    d = res["daily"].astype(float)
    n = trial_count() + 1
    years = max((pd.to_datetime(d.index[-1]) - pd.to_datetime(d.index[0])).days / 365.25, 0.5) if len(d) > 1 else 1.0
    ppy = res.get("ppy") or len(d) / years                       # a subset held 32 nights/yr is annualised by 32, not 252
    if "gross_per_night" in res:
        notes = f"gross/night ${res['gross_per_night']:.2f} vs RT ${res['rt_cost']:.2f} ({res['gross_per_night']/res['rt_cost']:.2f}x), {res.get('bp_per_night', float('nan')):.1f} bp, {ppy:.0f} nights/yr; " + notes
    sr_d = float(d.mean() / d.std(ddof=0)) if d.std(ddof=0) > 0 else 0.0
    T = len(d); sk = float(((d - d.mean()) ** 3).mean() / (d.std(ddof=0) ** 3 + 1e-12)); ku = float(((d - d.mean()) ** 4).mean() / (d.std(ddof=0) ** 4 + 1e-12))
    dsr = deflated_sharpe(sr_d, T, n, sk, ku)
    pf = float(d[d > 0].sum() / -d[d < 0].sum()) if (d < 0).any() else float("inf")
    gross = res.get("gross", float("nan")); costs = res.get("costs", float("nan"))
    cost_share = (costs / gross) if gross and gross > 0 else float("nan")
    pb = pbo(res["params_grid"], 10)["pbo"] if isinstance(res.get("params_grid"), np.ndarray) else float("nan")
    gate = (dsr >= 0.95) and (pf >= 1.15) and (not (cost_share == cost_share) or cost_share < 0.40)
    verdict = "PASS->phase3" if gate else "fail"
    bp_cell = bp_rule_cell(res, n, ppy)
    line = (f"| {n} | {test_id} | {dt.date.today()} | {hypothesis} | {data_desc} | {sharpe_ann(d.values, ppy):.2f} | {pf:.2f} | "
            f"{cost_share:.0%} | {dsr:.3f} | {verdict} | trades {res.get('trades','?')}, worst day {d.min():.0f}, PBO {round(pb, 2) if pb == pb else 'n/a'}; {notes} | {bp_cell} |\n")
    with open(SCORE, "a") as f:
        f.write(line)
    print(line)
    return verdict


# ------------------------------------------------------------------ registered tests
def test_3A_1(symbols=("ES", "NQ", "RTY", "YM"), stop_frac=0.01, start="2010-06-06", end="2023-12-31") -> dict:
    """Globex overnight drift: buy at the 17:00 CT reopen, 1% stop, sell at 08:30 CT. Per-contract P&L in the MICRO.
    Development data only (<= 2023). One result line per symbol is logged by the caller; this returns the ES one
    plus a params_grid over stop levels for PBO."""
    out = {}
    for root in symbols:
        df = D.fetch_cme_minutes(f"{root}.v.0", start, end)
        ct = df.tz_convert("America/Chicago")
        micro = {"ES": "MES", "NQ": "MNQ", "RTY": "M2K", "YM": "MYM"}[root]
        pt, cost_rt = POINT[micro], 2 * COST[micro]
        days = sorted(set(ct.index.date)); by = {d: g for d, g in ct.groupby(ct.index.date)}
        grid_stops = (0.0, 0.0075, 0.01, 0.015, 0.02)
        pnl = {s: {} for s in grid_stops}; gross = 0.0; n = 0
        for i in range(len(days) - 1):
            d0, d1 = days[i], days[i + 1]
            if d0.weekday() == 4 or (d1 - d0).days != 1: continue
            g0, g1 = by[d0], by[d1]
            ent = g0[(g0.index.hour == 17) & (g0.index.minute == 2)]; ex = g1[(g1.index.hour == 8) & (g1.index.minute == 30)]
            if len(ent) != 1 or len(ex) != 1: continue
            entry = float(ent["open"].iloc[0]); exit_px = float(ex["open"].iloc[0])
            path = pd.concat([g0[g0.index >= ent.index[0]], g1[g1.index < ex.index[0]]])[["open", "low"]].to_numpy()
            n += 1; gross += (exit_px - entry) * pt
            for s in grid_stops:
                px = exit_px
                if s:
                    lvl = entry * (1 - s)
                    for o, lo in path:
                        if o <= lvl: px = o; break
                        if lo <= lvl: px = lvl; break
                pnl[s][str(d1)] = (px - entry) * pt - cost_rt
        series = {s: pd.Series(v).sort_index() for s, v in pnl.items()}
        main = series[stop_frac]
        grid = np.column_stack([series[s].reindex(main.index).fillna(0).values for s in grid_stops])
        out[root] = {"daily": main, "trades": n, "gross": gross, "costs": n * cost_rt, "params_grid": grid,
                     "by_stop": {s: round(sharpe_ann(v.values), 2) for s, v in series.items()}}
    return out


# ------------------------------------------------------------------ 3A subset tests (all on the cached nights table)
from research.program import nights as NI
CAL = os.path.join(HERE, "calendars")


def _cal(name, col):
    return set(pd.to_datetime(pd.read_csv(os.path.join(CAL, name))[col]).dt.normalize())


def _grid(all_nights: pd.DataFrame, variants: dict, stop=0.01) -> np.ndarray:
    idx = all_nights["d1"].values
    cols = []
    for v in variants.values():
        ser = pd.Series(v[f"net_{stop}"].values, index=v["d1"].values).reindex(idx).fillna(0.0)
        cols.append(ser.values)
    return np.column_stack(cols)


def _pack(sub, root, all_nights, variants, extra=""):
    r = NI.summarize(sub, root); r["params_grid"] = _grid(all_nights, variants); r["notes"] = extra; r["sub"] = sub
    return r


def _complement_note(name, sub, root):
    s = NI.summarize(sub, root)
    return f"{name}: n {s['trades']}, gross/night ${s['gross_per_night']:.2f}, {s['bp_per_night']:.1f} bp, net sum ${s['daily'].sum():.0f}"


def test_3A_6() -> dict:
    """Scheduled-macro nights (exit date = CPI/NFP/FOMC decision date): hold INTO the release; premium concentrates there."""
    cpi, nfp = _cal("cpi.csv", "release_date"), _cal("nfp.csv", "release_date")
    fomc = set(pd.to_datetime(pd.read_csv(os.path.join(CAL, "fomc.csv")).query("kind == 'scheduled'")["decision_date"]).dt.normalize())
    out = {}
    for root in ("ES", "NQ", "RTY", "YM"):
        n = NI.nights(root)
        is_cpi, is_nfp, is_fomc = n["d1"].isin(cpi), n["d1"].isin(nfp), n["d1"].isin(fomc)
        post_fomc = n["d0"].isin(fomc)
        ev = is_cpi | is_nfp | is_fomc
        variants = {"event": n[ev], "non_event": n[~ev], "cpi": n[is_cpi], "nfp": n[is_nfp], "fomc_into": n[is_fomc],
                    "post_fomc": n[post_fomc], "all": n}
        notes = "; ".join(_complement_note(k, v, root) for k, v in variants.items() if k != "event")
        out[root] = _pack(n[ev], root, n, variants, notes)
    return out


def _fomc_week(d1: pd.Series) -> pd.Series:
    """CMVJ cycle week of each date: t = weekdays since the last scheduled announcement (t=0 on it), t=-1 the weekday before
    the next one; week = floor((t+1)/5)."""
    sched = np.array(sorted(pd.to_datetime(pd.read_csv(os.path.join(CAL, "fomc.csv")).query("kind == 'scheduled'")["decision_date"]).dt.normalize()), dtype="datetime64[D]")
    d = d1.values.astype("datetime64[D]")
    pos = np.searchsorted(sched, d, side="right")            # sched[pos-1] <= d < sched[pos]
    prev = sched[np.clip(pos - 1, 0, len(sched) - 1)]; nxt = sched[np.clip(pos, 0, len(sched) - 1)]
    t = np.busday_count(prev, d).astype(int)
    before_next = (pos < len(sched)) & (np.busday_count(d, nxt) == 1)
    t = np.where(before_next, -1, t)
    t = np.where(pos == 0, 99, t)                            # before the first announcement in the calendar: unknown
    return pd.Series(np.floor((t + 1) / 5).astype(int), index=d1.index)


def test_3A_4() -> dict:
    """FOMC-cycle even weeks (CMVJ 2019): hold the night only when its exit date is in cycle week 0, 2, 4 or 6."""
    out = {}
    for root in ("ES", "NQ", "RTY", "YM"):
        n = NI.nights(root); wk = _fomc_week(n["d1"])
        even = wk.isin([0, 2, 4, 6]); odd = wk.isin([1, 3, 5, 7])
        variants = {"even": n[even], "odd": n[odd], "week0": n[wk == 0], "week2": n[wk == 2], "week4": n[wk == 4], "week6": n[wk == 6], "all": n}
        early = n["d1"] < "2017-01-01"
        notes = "; ".join(_complement_note(k, v, root) for k, v in variants.items() if k != "even")
        notes += "; 2010-16 even/odd bp %.1f/%.1f, 2017-23 %.1f/%.1f" % (n[even & early]["ret"].mean() * 1e4, n[odd & early]["ret"].mean() * 1e4,
                                                                     n[even & ~early]["ret"].mean() * 1e4, n[odd & ~early]["ret"].mean() * 1e4)
        out[root] = _pack(n[even], root, n, variants, notes)
    return out


def test_3A_2() -> dict:
    """Overnight after DOWN day sessions (day_ret < 0; Lou-Polk-Skouras tug of war / end-of-day flow reversal)."""
    out = {}
    for root in ("ES", "NQ", "RTY", "YM"):
        n = NI.nights(root); dr = n["day_ret"]
        down, up = dr < 0, dr >= 0
        terc = pd.qcut(dr, 3, labels=["bottom", "middle", "top"])
        variants = {"down": n[down], "up": n[up], "bottom": n[terc == "bottom"], "middle": n[terc == "middle"], "top": n[terc == "top"], "all": n}
        early = n["d1"] < "2017-01-01"; no2003 = ~n["d1"].dt.strftime("%Y-%m").eq("2020-03")
        notes = "; ".join(_complement_note(k, v, root) for k, v in variants.items() if k != "down")
        notes += "; nan day_ret %d; 2010-16 down/up bp %.1f/%.1f, 2017-23 %.1f/%.1f; down ex-2020-03 %.1f bp" % (
            dr.isna().sum(), n[down & early]["ret"].mean() * 1e4, n[up & early]["ret"].mean() * 1e4,
            n[down & ~early]["ret"].mean() * 1e4, n[up & ~early]["ret"].mean() * 1e4, n[down & no2003]["ret"].mean() * 1e4)
        out[root] = _pack(n[down], root, n, variants, notes)
    return out


def _vix_ratio(d0: pd.Series) -> pd.Series:
    v = pd.read_csv(os.path.join(CAL, "vix_daily.csv"), parse_dates=["date"]).set_index("date")["close"]
    v3 = pd.read_csv(os.path.join(CAL, "vix3m_daily.csv"), parse_dates=["date"]).set_index("date")["close"]
    r = (v / v3).dropna().sort_index()
    # last close dated <= d0 (Friday's for Sunday nights); never a later date
    idx = r.index.values.astype("datetime64[D]"); d = d0.values.astype("datetime64[D]")
    pos = np.searchsorted(idx, d, side="right") - 1
    out = np.where(pos >= 0, r.values[np.clip(pos, 0, None)], np.nan)
    stale = (d - idx[np.clip(pos, 0, None)]).astype(int) > 4
    return pd.Series(np.where(stale, np.nan, out), index=d0.index)


def test_3A_3() -> dict:
    """Overnight only when the VIX term structure is flat/inverted (VIX/VIX3M >= 1 at the prior close)."""
    out = {}
    for root in ("ES", "NQ", "RTY", "YM"):
        n = NI.nights(root); r = _vix_ratio(n["d0"])
        back, cont = r >= 1.0, r < 1.0
        terc = pd.qcut(r, 3, labels=["low", "mid", "high"])
        variants = {"backwardation": n[back], "contango": n[cont], "r_low": n[terc == "low"], "r_mid": n[terc == "mid"], "r_high": n[terc == "high"], "all": n}
        no2020 = ~n["d1"].between("2020-03-01", "2020-05-31")
        notes = "; ".join(_complement_note(k, v, root) for k, v in variants.items() if k != "backwardation")
        notes += "; nan ratio %d; backwardation share %.1f%%; back ex-2020-03..05: n %d, %.1f bp" % (
            r.isna().sum(), back.mean() * 100, (back & no2020).sum(), n[back & no2020]["ret"].mean() * 1e4)
        out[root] = _pack(n[back], root, n, variants, notes)
    return out


def _vix_state(d0: pd.Series, qs=(0.20, 0.33, 0.50, 0.67), min_obs=250) -> pd.DataFrame:
    """r_t and expanding-window percentile thresholds thr_q(t) computed on CBOE dates strictly before t (shift(1))."""
    v = pd.read_csv(os.path.join(CAL, "vix_daily.csv"), parse_dates=["date"]).set_index("date")["close"]
    v3 = pd.read_csv(os.path.join(CAL, "vix3m_daily.csv"), parse_dates=["date"]).set_index("date")["close"]
    r = (v / v3).dropna().sort_index()
    tab = pd.DataFrame({"r": r})
    for q in qs:
        tab[f"thr_{q}"] = r.expanding(min_periods=min_obs).quantile(q).shift(1)      # strictly-prior dates only
    idx = tab.index.values.astype("datetime64[D]"); d = d0.values.astype("datetime64[D]")
    pos = np.searchsorted(idx, d, side="right") - 1
    ok = (pos >= 0) & ((d - idx[np.clip(pos, 0, None)]).astype(int) <= 4)
    out = tab.iloc[np.clip(pos, 0, None)].reset_index(drop=True); out.index = d0.index
    out.loc[~ok, :] = np.nan
    return out


def test_3A_14() -> dict:
    """Vol-state switch: hold the night iff VIX/VIX3M >= its expanding 33rd percentile (prior dates only); size 1 or 0."""
    out = {}
    for root in ("ES", "NQ", "RTY", "YM"):
        n = NI.nights(root); st = _vix_state(n["d0"])
        elig = st["thr_0.33"].notna()
        hold = {q: elig & (st["r"] >= st[f"thr_{q}"]) for q in (0.20, 0.33, 0.50, 0.67)}
        variants = {"q33": n[hold[0.33]], "q20": n[hold[0.20]], "q50": n[hold[0.50]], "q67": n[hold[0.67]], "uncond": n[elig]}
        aside = n[elig & ~hold[0.33]]
        notes = "; ".join(_complement_note(k, v, root) for k, v in variants.items() if k != "q33")
        notes += "; stood aside: " + _complement_note("aside", aside, root).split(": ", 1)[1]
        notes += "; eligible %d of %d nights, held share %.0f%%, first held %s" % (elig.sum(), len(n), hold[0.33].sum() / max(elig.sum(), 1) * 100, n[hold[0.33]]["d1"].min().date())
        out[root] = _pack(n[hold[0.33]], root, n, variants, notes)
    return out


def test_3A_5() -> dict:
    """Pre-FOMC night (exit date = scheduled decision), decisive sub-period 2016-2023; full sample is context."""
    fomc = set(pd.to_datetime(pd.read_csv(os.path.join(CAL, "fomc.csv")).query("kind == 'scheduled'")["decision_date"]).dt.normalize())
    out = {}
    for root in ("ES", "NQ", "RTY", "YM"):
        n = NI.nights(root); into, post = n["d1"].isin(fomc), n["d0"].isin(fomc)
        late = n["d1"] >= "2016-01-01"; no2020 = ~n["d1"].between("2020-03-01", "2020-05-31")
        variants = {"into_2016_23": n[into & late], "into_2010_15": n[into & ~late], "into_full": n[into], "post_2016_23": n[post & late], "all_2016_23": n[late]}
        sub = n[into & late]; t = sub["ret"].mean() / sub["ret"].std() * np.sqrt(len(sub)) if len(sub) > 2 else float("nan")
        notes = "; ".join(_complement_note(k, v, root) for k, v in variants.items() if k != "into_2016_23")
        notes += "; 2016-23 t-stat of mean %.2f; 2016-23 ex-2020-03..05 %.1f bp (n %d); MIN TRACK NOT MET (reported, not gated)" % (
            t, n[into & late & no2020]["ret"].mean() * 1e4, (into & late & no2020).sum())
        out[root] = _pack(sub, root, n, variants, notes)
    return out


def test_3A_10() -> dict:
    """Overnight drift in ZN/ZB/GC, same 17:02->08:30 window, 1% stop, full-size contracts; grid over stops."""
    out = {}
    for root in ("ZN", "ZB", "GC"):
        n = NI.nights(root)
        r = NI.summarize(n, root)
        r["params_grid"] = np.column_stack([n[f"net_{s}"].values for s in NI.STOPS])
        susp = n["maxstep"] > 0.004
        r["notes"] = "Sharpe by stop " + str({s: round(sharpe_ann(n[f"net_{s}"].values), 2) for s in NI.STOPS}) + \
            "; big-jump nights (>40bp/min, rolls+crashes) %d, their gross $%.0f; by year bp %s" % (
            susp.sum(), n[susp]["gross"].sum(), {y: round(v * 1e4, 1) for y, v in n.groupby(n["d1"].dt.year)["ret"].mean().items()})
        out[root] = r
    return out


# ------------------------------------------------------------------ intraday tests (day-session table)
from research.program import days as DY


def _grid_series(series: list) -> np.ndarray:
    idx = sorted(set().union(*[set(s.index) for s in series]))
    return np.column_stack([s.groupby(level=0).sum().reindex(idx).fillna(0.0).values for s in series])


def _pack2(primary: dict, variants: dict, extra: str = "") -> dict:
    primary["params_grid"] = _grid_series([v["daily"] for v in variants.values()]); primary["notes"] = extra
    return primary


def _note(name: str, r: dict) -> str:
    return f"{name}: n {r['trades']}, gross/trade ${r['gross_per_night']:.2f}, {r['bp_per_night']:.1f} bp, net sum ${r['daily'].sum():.0f}"


def test_3A_11() -> dict:
    """CL long 08:00->09:29 CT into the EIA Weekly Petroleum Status Report on regular Wednesday release days."""
    cal = pd.read_csv(os.path.join(CAL, "eia_wpsr.csv"), parse_dates=["release_date"])
    wed = set(cal[~cal["shifted"]]["release_date"].dt.normalize()); thu = set(cal[cal["shifted"]]["release_date"].dt.normalize())
    d = DY.days("CL"); is_w, is_t = d["d"].isin(wed), d["d"].isin(thu)
    pre = lambda sub: DY.trade_result(sub, "CL", "o_0800", "o_0929")
    variants = {"eia_wed_pre": pre(d[is_w]), "non_eia_pre": pre(d[~is_w & ~is_t]), "eia_wed_reaction_0930_1030": DY.trade_result(d[is_w], "CL", "o_0930", "o_1030"),
                "eia_wed_full_0800_1329": DY.trade_result(d[is_w], "CL", "o_0800", "c_1329"), "all_days_pre": pre(d), "shifted_thu_0800_0959": DY.trade_result(d[is_t], "CL", "o_0800", "o_1000")}
    prim = variants["eia_wed_pre"]; sub = prim["sub"]
    early = sub["d"] < "2017-01-01"; ex2004 = ~sub["d"].dt.strftime("%Y-%m").eq("2020-04")
    r = sub["o_0929"] / sub["o_0800"] - 1
    notes = "; ".join(_note(k, v) for k, v in variants.items() if k != "eia_wed_pre")
    notes += "; eia_wed_pre 2010-16 %.1f bp, 2017-23 %.1f bp, ex-2020-04 %.1f bp; reaction |move| mean %.1f bp vs non-EIA 09:30-10:30 |move| %.1f bp" % (
        r[early].mean() * 1e4, r[~early].mean() * 1e4, r[ex2004].mean() * 1e4,
        (d[is_w]["o_1030"] / d[is_w]["o_0930"] - 1).abs().mean() * 1e4, (d[~is_w & ~is_t]["o_1030"] / d[~is_w & ~is_t]["o_0930"] - 1).abs().mean() * 1e4)
    return {"CL": _pack2(prim, variants, notes)}


def _tom_flags(dates: pd.Series):
    """Turn-of-month per McConnell-Xu: T = last trading day of the month, TOM = {T, T+1, T+2, T+3}; pre = {T-4..T-1}."""
    ds = pd.Series(sorted(dates.unique())); ym = ds.dt.to_period("M")
    last = ds[ym != ym.shift(-1)]                       # last trading day of each month
    pos = {d: i for i, d in enumerate(ds)}; tom, pre = set(), set()
    for T in last:
        i = pos[T]
        tom.update(ds.iloc[i:i + 4]); pre.update(ds.iloc[max(i - 4, 0):i])
    return tom, pre


def test_3A_7() -> dict:
    """Turn-of-month nights (exit date in {T, T+1, T+2, T+3}): month-end rebalancing / dash-for-cash reversal."""
    out = {}
    for root in ("ES", "NQ", "RTY", "YM"):
        n = NI.nights(root); dy = DY.days(root); tom, pre = _tom_flags(dy["d"])
        is_tom, is_pre = n["d1"].isin(tom), n["d1"].isin(pre)
        dtom = dy[dy["d"].isin(tom)]
        variants = {"tom_nights": NI.summarize(n[is_tom], root), "non_tom_nights": NI.summarize(n[~is_tom], root), "pre_T4_T1_nights": NI.summarize(n[is_pre], root),
                    "tom_day_0830_1459": DY.trade_result(dtom, root, "o_0830", "c_1459"), "tom_last_hour_1400_1459": DY.trade_result(dtom, root, "o_1400", "c_1459"), "all_nights": NI.summarize(n, root)}
        prim = variants["tom_nights"]; prim["sub"] = n[is_tom]
        byday = {}
        for k, off in enumerate(range(4)):
            pass
        notes = "; ".join(_note(k, v) for k, v in variants.items() if k != "tom_nights")
        early = n["d1"] < "2017-01-01"
        notes += "; tom 2010-16 %.1f bp / 2017-23 %.1f bp; non-tom %.1f / %.1f" % (n[is_tom & early]["ret"].mean() * 1e4, n[is_tom & ~early]["ret"].mean() * 1e4, n[~is_tom & early]["ret"].mean() * 1e4, n[~is_tom & ~early]["ret"].mean() * 1e4)
        out[root] = _pack2(prim, variants, notes)
    return out


def _opex_week(dates: pd.Series) -> pd.Series:
    """True when the date falls Mon-Fri of the week containing the month's third Friday."""
    d = pd.to_datetime(dates)
    first = d.dt.to_period("M").dt.start_time
    third_fri = first + pd.to_timedelta(((4 - first.dt.weekday) % 7) + 14, unit="D")
    monday = third_fri - pd.Timedelta(days=4)
    return (d >= monday) & (d <= third_fri)


def _last30(sub: pd.DataFrame, root: str, side: np.ndarray) -> dict:
    s = sub.copy(); s["side"] = side
    return DY.trade_result(s, root, "o_1430", "c_1459", side_col="side")


def test_3A_8() -> dict:
    """Opex-week last-30-min REVERSAL: fade the 08:30->14:30 move from 14:30 to 14:59 in the third-Friday week (gamma pinning)."""
    out = {}
    for root in ("ES", "NQ", "RTY", "YM"):
        dy = DY.days(root).dropna(subset=["o_0830", "o_1430", "c_1459"]); m = np.sign(dy["o_1430"] / dy["o_0830"] - 1).values
        opex = _opex_week(dy["d"]).values; post = _opex_week(dy["d"] - pd.Timedelta(days=7)).values
        qtr = opex & dy["d"].dt.month.isin([3, 6, 9, 12]).values
        variants = {"opex_fade": _last30(dy[opex], root, -m[opex]), "opex_follow": _last30(dy[opex], root, m[opex]), "post_opex_follow": _last30(dy[post], root, m[post]),
                    "other_weeks_fade": _last30(dy[~opex & ~post], root, -m[~opex & ~post]), "all_follow": _last30(dy, root, m), "quarterly_roll_week_fade": _last30(dy[qtr], root, -m[qtr])}
        prim = variants["opex_fade"]
        notes = "; ".join(_note(k, v) for k, v in variants.items() if k != "opex_fade")
        notes += "; mean |last30| %.1f bp" % ((dy["c_1459"] / dy["o_1430"] - 1).abs().mean() * 1e4)
        out[root] = _pack2(prim, variants, notes)
    return out


def test_3A_9() -> dict:
    """Last-30-min MOMENTUM (follow the 08:30->14:30 sign, 14:30->14:59) only in the high vol state (VIX/VIX3M >= expanding median at the prior close)."""
    out = {}
    for root in ("ES", "NQ", "RTY", "YM"):
        dy = DY.days(root).dropna(subset=["o_0830", "o_1430", "c_1459"]).reset_index(drop=True)
        st = _vix_state(dy["d"] - pd.Timedelta(days=1))            # state from closes strictly before the trading day
        hi = (st["r"] >= st["thr_0.5"]).fillna(False).values; lo = (st["r"] < st["thr_0.5"]).fillna(False).values
        m = np.sign(dy["o_1430"] / dy["o_0830"] - 1).values; big = (dy["o_1430"] / dy["o_0830"] - 1).abs().values > 0.005
        variants = {"high_follow": _last30(dy[hi], root, m[hi]), "low_follow": _last30(dy[lo], root, m[lo]), "high_fade": _last30(dy[hi], root, -m[hi]),
                    "all_follow": _last30(dy, root, m), "high_big_follow": _last30(dy[hi & big], root, m[hi & big])}
        prim = variants["high_follow"]
        notes = "; ".join(_note(k, v) for k, v in variants.items() if k != "high_follow")
        notes += "; high-state share %.0f%%, state nan %d" % (hi.mean() * 100, int(st["r"].isna().sum()))
        out[root] = _pack2(prim, variants, notes)
    return out


def _orb(dy: pd.DataFrame, root: str, n: int, long_only=False) -> dict:
    s = dy[dy[f"orb{n}_dir"] != 0].copy()
    if long_only: s = s[s[f"orb{n}_dir"] > 0]
    s["side"] = s[f"orb{n}_dir"].astype(float)
    return DY.trade_result(s, root, f"orb{n}_entry", f"orb{n}_exit", side_col="side")


def test_3A_12() -> dict:
    """Opening-range breakout on MNQ (Zarattini-Aziz, un-leveraged): first 5-min candle's direction, enter 08:35, stop at the range's far side, exit 14:59."""
    out = {}
    for root in ("NQ", "ES"):
        dy = DY.days(root)
        variants = {"orb5": _orb(dy, root, 5), "orb15": _orb(dy, root, 15), "orb5_long_only": _orb(dy, root, 5, True),
                    "orb5_other": _orb(DY.days("ES" if root == "NQ" else "NQ"), "ES" if root == "NQ" else "NQ", 5)}
        prim = variants["orb5"]; sub = prim["sub"]
        stopped = ((sub["orb5_dir"] > 0) & (sub["orb5_exit"] <= sub["l_0834"])) | ((sub["orb5_dir"] < 0) & (sub["orb5_exit"] >= sub["h_0834"]))
        rng = ((sub["h_0834"] - sub["l_0834"]) / sub["o_0830"]).mean() * 1e4
        notes = "; ".join(_note(k, v) for k, v in variants.items() if k != "orb5")
        yr = sub.assign(r=sub["orb5_dir"] * (sub["orb5_exit"] / sub["orb5_entry"] - 1)).groupby(sub["d"].dt.year)["r"].mean() * 1e4
        notes += "; stop-out rate %.0f%%, mean 5-min range %.1f bp; by year bp %s" % (stopped.mean() * 100, rng, {int(k): round(float(v), 1) for k, v in yr.items()})
        out[root] = _pack2(prim, variants, notes)
    return out


def test_3A_13() -> dict:
    """ES overnight sign -> trade RTY (M2K) in that direction 08:30->09:00 (small-cap catch-up at the open)."""
    es = NI.nights("ES")[["d1", "ret"]].rename(columns={"d1": "d", "ret": "es_on"})
    out = {}
    for root in ("RTY", "YM"):
        dy = DY.days(root).merge(es, on="d", how="inner"); sgn = np.sign(dy["es_on"]).values; big = dy["es_on"].abs().values > 0.003
        def tr(sub, exit_col, side): s = sub.copy(); s["side"] = side; return DY.trade_result(s, root, "o_0830", exit_col, side_col="side")
        variants = {"follow_0900": tr(dy, "o_0900", sgn), "follow_0845": tr(dy, "o_0845", sgn), "follow_0930": tr(dy, "o_0930", sgn), "follow_1000": tr(dy, "o_1000", sgn),
                    "follow_0900_big": tr(dy[big], "o_0900", sgn[big]), "fade_0900": tr(dy, "o_0900", -sgn)}
        prim = variants["follow_0900"]
        notes = "; ".join(_note(k, v) for k, v in variants.items() if k != "follow_0900")
        notes += "; days with ES signal %d, |es_on| > 30bp share %.0f%%" % (len(dy), big.mean() * 100)
        out[root] = _pack2(prim, variants, notes)
    return out


TESTS = {"3A-1": test_3A_1, "3A-6": test_3A_6, "3A-4": test_3A_4, "3A-2": test_3A_2, "3A-3": test_3A_3,
         "3A-14": test_3A_14, "3A-5": test_3A_5, "3A-10": test_3A_10,
         "3A-11": test_3A_11, "3A-7": test_3A_7, "3A-8": test_3A_8, "3A-9": test_3A_9, "3A-12": test_3A_12, "3A-13": test_3A_13}
HYP = {"3A-6": "macro nights (CPI/NFP/FOMC-into), 17:02->08:30, 1% stop",
       "3A-4": "FOMC-cycle even weeks (CMVJ), 17:02->08:30, 1% stop",
       "3A-2": "after down day session, 17:02->08:30, 1% stop",
       "3A-3": "VIX>=VIX3M (backwardation) nights, 17:02->08:30, 1% stop",
       "3A-14": "vol-state switch: hold iff VIX/VIX3M >= expanding p33, 1% stop",
       "3A-5": "pre-FOMC night 2016-2023 (decisive), 1% stop",
       "3A-10": "Globex overnight 17:02->08:30, 1% stop, full-size",
       "3A-11": "CL long 08:00->09:29 into EIA report (regular Wednesdays), no stop",
       "3A-7": "turn-of-month nights (T..T+3), 17:02->08:30, 1% stop",
       "3A-8": "opex-week last-30-min fade of the day's move, 14:30->14:59",
       "3A-9": "last-30-min follow in high vol state (VIX/VIX3M >= expanding median), 14:30->14:59",
       "3A-12": "opening-range breakout 5-min, range stop, exit 14:59",
       "3A-13": "follow ES overnight sign, 08:30->09:00"}


def main(argv=None):
    tid = (argv or sys.argv[1:])[0]
    prereg = os.path.join(HERE, "prereg", f"{tid}.md")
    if not os.path.exists(prereg):
        raise SystemExit(f"no prereg at {prereg}: write it first (PREREG_TEMPLATE.md), then run")
    try:
        res = TESTS[tid]()
    except D.BudgetExceeded as e:
        print(f"\n*** STOPPED: {e}\n*** Nothing scored. Report to Connor; do not raise the budget without his OK.")
        raise SystemExit(2)
    if tid == "3A-1":
        for root, r in res.items():
            score(f"{tid}/{root}", "Globex overnight 17:02->08:30 CT, 1% stop", "Databento GLBX 1m 2010-2023",
                  r, notes=f"Sharpe by stop {r['by_stop']}")
    else:
        for root, r in res.items():
            score(f"{tid}/{root}", HYP.get(tid, TESTS[tid].__doc__.splitlines()[0]), "Databento GLBX 1m 2010-2023" if tid in ("3A-10", "3A-11") else "cached GLBX 1m 2010-2023 days" if tid in ("3A-8", "3A-9", "3A-12", "3A-13") else "cached GLBX 1m 2010-2023 nights", r, notes=r.get("notes", ""))


if __name__ == "__main__":
    main()
