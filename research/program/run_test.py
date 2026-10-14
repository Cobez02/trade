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


def score(test_id: str, hypothesis: str, data_desc: str, res: dict, notes: str = ""):
    d = res["daily"].astype(float)
    n = trial_count() + 1
    sr_d = float(d.mean() / d.std(ddof=0)) if d.std(ddof=0) > 0 else 0.0
    T = len(d); sk = float(((d - d.mean()) ** 3).mean() / (d.std(ddof=0) ** 3 + 1e-12)); ku = float(((d - d.mean()) ** 4).mean() / (d.std(ddof=0) ** 4 + 1e-12))
    dsr = deflated_sharpe(sr_d, T, n, sk, ku)
    pf = float(d[d > 0].sum() / -d[d < 0].sum()) if (d < 0).any() else float("inf")
    gross = res.get("gross", float("nan")); costs = res.get("costs", float("nan"))
    cost_share = (costs / gross) if gross and gross > 0 else float("nan")
    pb = pbo(res["params_grid"], 10)["pbo"] if isinstance(res.get("params_grid"), np.ndarray) else float("nan")
    gate = (dsr >= 0.95) and (pf >= 1.15) and (not (cost_share == cost_share) or cost_share < 0.40)
    verdict = "PASS->phase3" if gate else "fail"
    line = (f"| {n} | {test_id} | {dt.date.today()} | {hypothesis} | {data_desc} | {sharpe_ann(d.values):.2f} | {pf:.2f} | "
            f"{cost_share:.0%} | {dsr:.3f} | {verdict} | trades {res.get('trades','?')}, worst day {d.min():.0f}, PBO {pb if pb == pb else 'n/a'}; {notes} |\n")
    with open(SCORE, "a") as f:
        f.write(line)
    print(line)
    return verdict


# ------------------------------------------------------------------ registered tests
def test_3A_1(symbols=("ES", "NQ", "RTY", "YM"), stop_frac=0.01, start="2010-01-01", end="2023-12-31") -> dict:
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


TESTS = {"3A-1": test_3A_1}


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
        score(tid, TESTS[tid].__doc__.splitlines()[0], "see prereg", res)


if __name__ == "__main__":
    main()
