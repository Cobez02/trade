"""Live-exit replay — post-hoc DIAGNOSTIC on the watchlist drop call, 8/1.

The watchlist study graded names under the sim's simplified exits (fixed
+45% take / -30% stop / 2-DTE time). The LIVE watcher has no fixed take:
it trails (arms at peak>=+25%, exits on a 20-point giveback from peak),
hard-stops at -30%, time-stops at <=1 DTE (exitrules.decide). If trailing
lets big winners run, names whose wins are outsized (NFLX's live +183%)
could grade differently. This replays the IDENTICAL entries under the
live exit rules, EOD-conservatively: pnl% measured on each day's CLOSING
BID (no intraday peaks), so the trail benefit is UNDERSTATED — a name
that flips positive here flips despite the handicap.

Entry block copied verbatim from backtest.sim_singles (same gates, same
pessimistic ask fill). All data comes from the existing disk cache.
"""
import json, datetime as dt
import pandas as pd
import backtest as B
from engine import rsi, macd_signal, trend_up
import execution

TRAIL_ARM, TRAIL_GIVEBACK, HARD_STOP = 0.25, 0.20, -0.30

NAMES = ["SPY", "QQQ", "TSLA", "AAPL", "MSFT", "NVDA", "AMD", "META",
         "AMZN", "GOOGL", "NFLX", "IWM", "COIN", "PLTR"]


def sim_live_exits(symbol, bars, exps):
    trades, open_pos = [], None
    days = [d for d in bars.index if B.START <= d.date() <= B.END]
    for day in days:
        iso = day.date().isoformat()
        if open_pos is not None:
            q = open_pos["q"].get(iso)
            dte_left = (dt.date.fromisoformat(open_pos["exp"]) - day.date()).days
            if q:
                cur = (q["bid"] - open_pos["entry_fill"]) / open_pos["entry_fill"]
                peak = max(open_pos["peak"], cur)
                open_pos["peak"] = peak
                reason = None
                if dte_left <= 1:
                    reason = f"time-stop ({dte_left} DTE)"
                elif peak >= TRAIL_ARM and (peak - cur) >= TRAIL_GIVEBACK:
                    reason = f"trail: peaked {peak:+.0%}, now {cur:+.0%}"
                elif cur <= HARD_STOP:
                    reason = f"stop {cur:+.0%}"
                if reason:
                    pnl = (q["bid"] - open_pos["entry_fill"]) * 100 * open_pos.get("qty", 1) \
                          - 2 * B.FEE_PER_CONTRACT_SIDE
                    trades.append({"symbol": symbol, "pnl": round(pnl, 2),
                                   "pnl_pct": round(cur, 4),
                                   "exit_date": iso, "exit_reason": reason,
                                   "flagged": False})
                    open_pos = None
            elif dte_left <= 0:
                pnl = -open_pos["entry_fill"] * 100 - B.FEE_PER_CONTRACT_SIDE
                trades.append({"symbol": symbol, "pnl": round(pnl, 2), "pnl_pct": -1.0,
                               "exit_date": iso, "exit_reason": "expired no quote",
                               "flagged": True})
                open_pos = None
        if open_pos is not None:
            continue
        # ---- entry block: verbatim logic from backtest.sim_singles ----
        ctx = B.day_context(bars, day)
        if ctx is None:
            continue
        spot, fc, closes = ctx
        cs = pd.Series(closes)
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
        if not te.get("favorable"):
            continue
        open_pos = {"symbol": symbol, "exp": exp, "entry_fill": row["ask"],
                    "peak": (row["bid"] - row["ask"]) / row["ask"],
                    "q": B.contract_series(symbol, exp, k, right, iso)}
    return trades


if __name__ == "__main__":
    out = {}
    for name in NAMES:
        try:
            bars = B.stock_bars(name)
            exps = B.expirations(name)
            tr = sim_live_exits(name, bars, exps)
            s = B.score(tr, name)
            out[name] = s
            print(f"{name}: n={s['n']} total={s['total']} pf={s['profit_factor']} "
                  f"wr={s['win_rate']}", flush=True)
        except Exception as e:
            out[name] = {"error": str(e)[:120]}
            print(name, "ERROR", e, flush=True)
        json.dump(out, open("liveexit_result.json", "w"), indent=1)
    print("live-exit replay done", flush=True)
