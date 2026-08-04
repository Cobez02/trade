"""Override study — "if there's confidence in a run, go over what the bot
wants." PRE-REGISTERED 2026-08-04 (Connor-approved) before running.

Question: the production sim blocks entries when the timing-edge gate says
the option is priced against us (implied rich vs forecast). Connor proposes
overriding that gate when the market is visibly running. This study replays
EXACTLY those blocked trades — same signals, same strike selection, same
spread/premium/cap gates, same pessimistic fills and production exits — and
takes ONLY the entries the edge gate refused:

  Cell A: every edge-blocked entry (the full override).
  Cell B: edge-blocked entries on "run" days — underlying moved >= 1.0% from
          prior close in the signal's direction on entry day (the proposal).
  Cell C: same with a >= 1.5% run threshold.

All three declared before any result existed. Success bar to even DISCUSS
shipping: PF > 1 AND total > 0 AND n >= 30 in a run-day cell; an actual
ship would then need its own confirmation pass. Universe: the 14 validated
watchlist names, 2020-10..2026-07. Exits: production fixed (take +45% /
stop -30% / 2-DTE time). One position per symbol at a time, as live.
"""
import json
import datetime as dt
import pandas as pd
import backtest as B
from engine import rsi, macd_signal, trend_up
import execution

NAMES = ["SPY", "QQQ", "TSLA", "AAPL", "MSFT", "NVDA", "AMD", "META",
         "AMZN", "GOOGL", "NFLX", "IWM", "COIN", "PLTR"]
RUN_THRESH_B, RUN_THRESH_C = 0.010, 0.015


def sim_blocked(symbol, bars, exps):
    """Production entry pipeline, but ENTER only what the edge gate blocked.
    Returns trades tagged with the entry day's signed run size."""
    trades, open_pos = [], None
    days = [d for d in bars.index if B.START <= d.date() <= B.END]
    closes = bars["close"]
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
                    exit_fill = q["bid"]
                    pnl = (exit_fill - open_pos["entry_fill"]) * 100 - 2 * B.FEE_PER_CONTRACT_SIDE
                    trades.append({"symbol": symbol, "run": open_pos["run"],
                                   "entry_date": open_pos["entry_date"], "exit_date": iso,
                                   "exit_reason": reason, "pnl": round(pnl, 2),
                                   "pnl_pct": round((exit_fill - open_pos["entry_fill"])
                                                    / open_pos["entry_fill"], 4),
                                   "flagged": False})
                    open_pos = None
            elif dte_left <= 0:
                pnl = -open_pos["entry_fill"] * 100 - B.FEE_PER_CONTRACT_SIDE
                trades.append({"symbol": symbol, "run": open_pos["run"],
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
        if row["ask"] * 100 > B.MAX_PREMIUM_PER_TRADE:
            continue
        te = execution.timing_edge(spot, k, dte_e / 365.0, fc, row["bid"], row["ask"],
                                   is_call=(right == "CALL"), side="buy")
        if te.get("favorable"):
            continue                       # <-- INVERTED: we take only the BLOCKED
        # signed run: entry-day close vs prior close, positive = in signal direction
        idx = list(bars.index).index(day)
        if idx == 0:
            continue
        day_ret = float(closes.iloc[idx] / closes.iloc[idx - 1] - 1)
        run = day_ret if direction == "bull" else -day_ret
        open_pos = {"symbol": symbol, "exp": exp, "entry_fill": row["ask"],
                    "entry_date": iso, "run": round(run, 4),
                    "q": B.contract_series(symbol, exp, k, right, iso)}
    return trades


if __name__ == "__main__":
    allt = []
    for name in NAMES:
        try:
            tr = sim_blocked(name, B.stock_bars(name), B.expirations(name))
            allt += tr
            print(f"{name}: {len(tr)} blocked trades", flush=True)
        except Exception as e:
            print(name, "ERROR", str(e)[:100], flush=True)
    cells = {
        "A_all_blocked": allt,
        "B_run_1pct": [t for t in allt if t["run"] >= RUN_THRESH_B],
        "C_run_1p5pct": [t for t in allt if t["run"] >= RUN_THRESH_C],
    }
    out = {}
    for name, tr in cells.items():
        s = B.score(tr, name)
        s["mean_pnl_pct"] = (round(sum(t["pnl_pct"] for t in tr) / len(tr), 4)
                             if tr else None)
        out[name] = s
        print(f"{name}: n={s['n']} total={s.get('total')} pf={s.get('profit_factor')} "
              f"wr={s.get('win_rate')} mean_ret={s.get('mean_pnl_pct')}", flush=True)
    out["trades_sample"] = allt[:50]
    json.dump(out, open("override_result.json", "w"), indent=1)
    print("override study done", flush=True)
