"""Edge-proportional sizing study — PRE-REGISTERED 2026-08-05 (Connor-approved).

Proposal: "use more of it on high-confidence days." Operationalized the only
way that survived this week's override study: confidence = the SYSTEM'S
measured vol edge at entry (forecast minus implied, in vol points — the same
"+4.1pts" the live notes print), never the market's mood. Position budget
becomes a function of that number; entry GATES are unchanged.

Cells (declared before running):
  BASE  flat $600 budget                       (production geometry)
  TIER  $600 / $900 / $1200 at edge <3 / 3-5 / >=5 pts
  PROP  budget = clamp(600 * edge_pts/2, 600, 1500)

All cells size qty = budget // (ask*100), min 1, P/L scales with qty — note
the published per-name sims booked qty=1, so BASE here is the apples-to-
apples baseline, not the published numbers. Raising a budget also ADMITS
contracts the flat cap rejected (ask in ($600, budget]); that is part of the
proposal being tested. Success bar to DISCUSS shipping: a sizing cell must
beat BASE on total P/L AND not worsen max drawdown by more than its P/L
gain (drawdown-adjusted dominance), across the 14-name pool. Ship would be
paper-first regardless. Pessimistic fills, production exits, 6 years.
"""
import json
import datetime as dt
import pandas as pd
import backtest as B
from engine import rsi, macd_signal, trend_up
import execution

NAMES = ["SPY", "QQQ", "TSLA", "AAPL", "MSFT", "NVDA", "AMD", "META",
         "AMZN", "GOOGL", "NFLX", "IWM", "COIN", "PLTR"]


def budget_base(edge_pts):
    return 600.0

def budget_tier(edge_pts):
    if edge_pts >= 5.0:
        return 1200.0
    if edge_pts >= 3.0:
        return 900.0
    return 600.0

def budget_prop(edge_pts):
    return max(600.0, min(1500.0, 600.0 * edge_pts / 2.0))

CELLS = [("BASE_flat600", budget_base), ("TIER_600_900_1200", budget_tier),
         ("PROP_x_edge", budget_prop)]


def sim_sized(symbol, bars, exps, budget_fn):
    trades, open_pos = [], None
    days = [d for d in bars.index if B.START <= d.date() <= B.END]
    for day in days:
        iso = day.date().isoformat()
        if open_pos is not None:
            q = open_pos["q"].get(iso)
            dte_left = (dt.date.fromisoformat(open_pos["exp"]) - day.date()).days
            reason = None
            if q:
                plpc = (q["mid"] - open_pos["entry_fill"]) / open_pos["entry_fill"]
                if plpc >= B.TAKE_PROFIT_PCT:
                    reason = f"take-profit {plpc:+.0%}"
                elif plpc <= B.STOP_LOSS_PCT:
                    reason = f"stop-loss {plpc:+.0%}"
                elif dte_left <= B.TIME_STOP_DTE:
                    reason = f"time-stop ({dte_left} DTE)"
                if reason:
                    qty = open_pos["qty"]
                    pnl = (q["bid"] - open_pos["entry_fill"]) * 100 * qty \
                          - 2 * B.FEE_PER_CONTRACT_SIDE * qty
                    trades.append({"symbol": symbol, "qty": qty,
                                   "edge_pts": open_pos["edge_pts"],
                                   "cost": round(open_pos["entry_fill"] * 100 * qty, 2),
                                   "entry_date": open_pos["entry_date"], "exit_date": iso,
                                   "exit_reason": reason, "pnl": round(pnl, 2),
                                   "pnl_pct": round((q["bid"] - open_pos["entry_fill"])
                                                    / open_pos["entry_fill"], 4),
                                   "flagged": False})
                    open_pos = None
            elif dte_left <= 0:
                qty = open_pos["qty"]
                pnl = -open_pos["entry_fill"] * 100 * qty - B.FEE_PER_CONTRACT_SIDE * qty
                trades.append({"symbol": symbol, "qty": qty,
                               "edge_pts": open_pos["edge_pts"],
                               "cost": round(open_pos["entry_fill"] * 100 * qty, 2),
                               "entry_date": open_pos["entry_date"], "exit_date": iso,
                               "exit_reason": "expired no quote", "pnl": round(pnl, 2),
                               "pnl_pct": -1.0, "flagged": True})
                open_pos = None
        if open_pos is not None:
            continue
        ctx = B.day_context(bars, day)
        if ctx is None:
            continue
        spot, fc, closes_l = ctx
        cs = pd.Series(closes_l)
        r = rsi(cs)
        hist_now, hist_prev = macd_signal(cs)
        up = trend_up(cs)
        direction = None
        if r is not None and hist_now is not None:
            if r < 35 and hist_now > hist_prev:
                direction = "bull"
            elif up and hist_prev <= 0 < hist_now:
                direction = "bull"
            elif r > 70 and hist_now < hist_prev:
                direction = "bear"
        if direction is None:
            continue
        pe = B.pick_for_day(exps, day.date())
        if pe is None:
            continue
        exp, dte_e = pe
        snap = B.day_snapshot(symbol, exp, iso)
        right = "CALL" if direction == "bull" else "PUT"
        ks_all = B.snap_strikes(snap, right)
        k_target = spot * (1 + B.TARGET_OTM_PCT) if right == "CALL" else spot * (1 - B.TARGET_OTM_PCT)
        k = B.nearest(ks_all, k_target)
        if k is None:
            continue
        row = snap.get((k, right), {}).get(iso)
        if not row:
            continue
        spread_pct = (row["ask"] - row["bid"]) / row["mid"] if row["mid"] else 9
        if spread_pct > B.MAX_SPREAD_PCT:
            continue
        # LIVE-PARITY FLOOR (screens.MIN_CONTRACT_PRICE = 0.50): the production
        # screens refuse sub-$0.50 contracts outright; the qty=1 sims never
        # needed this gate, but budget-scaled sizing puts 10-20x on exactly
        # that bucket. Added after cell BASE ran once WITHOUT it (n=851,
        # -$4,042) - that variant is preserved in the report as the
        # what-if-no-floor disclosure, not discarded.
        if row["ask"] < 0.50:
            continue
        te = execution.timing_edge(spot, k, dte_e / 365.0, fc, row["bid"], row["ask"],
                                   is_call=(right == "CALL"), side="buy")
        if not te.get("favorable"):
            continue
        iv = execution.implied_vol(row["mid"], spot, k, dte_e / 365.0,
                                   is_call=(right == "CALL"))
        edge_pts = (fc - iv) * 100 if iv is not None else 0.0
        budget = budget_fn(edge_pts)
        cost1 = row["ask"] * 100
        if cost1 > budget:
            continue
        qty = max(1, int(budget // cost1))
        open_pos = {"symbol": symbol, "exp": exp, "entry_fill": row["ask"],
                    "entry_date": iso, "qty": qty, "edge_pts": round(edge_pts, 2),
                    "q": B.contract_series(symbol, exp, k, right, iso)}
    return trades


if __name__ == "__main__":
    out = {}
    for cell_name, fn in CELLS:
        allt = []
        for name in NAMES:
            try:
                allt += sim_sized(name, B.stock_bars(name), B.expirations(name), fn)
            except Exception as e:
                print(name, cell_name, "ERROR", str(e)[:90], flush=True)
        s = B.score(allt, cell_name)
        s["total_deployed"] = round(sum(t["cost"] for t in allt), 2)
        s["ret_on_deployed"] = (round(sum(t["pnl"] for t in allt)
                                      / s["total_deployed"], 4)
                                if s["total_deployed"] else None)
        out[cell_name] = {"score": s}
        print(f"{cell_name}: n={s['n']} total={s.get('total')} "
              f"pf={s.get('profit_factor')} maxDD={s.get('max_dd')} "
              f"deployed={s['total_deployed']} ret={s['ret_on_deployed']}", flush=True)
        json.dump(out, open("sizing_result.json", "w"), indent=1)
    print("sizing study done", flush=True)
