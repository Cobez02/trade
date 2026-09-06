"""
Run:  python -m tests.test_core   (from the package root)
No network. Synthetic bars. Every test states what it is checking.
"""
from __future__ import annotations
import datetime as dt
import math
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config as C
from data import sessions as S
from strategy.noise_area import build_sessions, NoiseAreaStrategy
from risk.engine import RiskEngine, DayState, AccountState
from risk import rules as R
from backtest.simulate import Backtester

PASS = 0; FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}  {detail}")


def synth_days(days: list[dt.date], seed=1, drift=0.0, vol_per_min=0.0003, start=5000.0,
               shape=None) -> pd.DataFrame:
    """1-minute RTH bars (UTC index) for the given days. `shape(day, minute_i, n)`
    can inject a deterministic path (returns price) for scenario tests."""
    rng = np.random.default_rng(seed)
    parts = []; px = start
    for d in days:
        o, c, _, _ = S.session_bounds(d)
        idx = pd.date_range(o, c - pd.Timedelta(minutes=1), freq="1min")
        n = len(idx)
        if shape:
            closes = np.array([shape(d, i, n, px) for i in range(n)])
        else:
            rets = rng.normal(drift, vol_per_min, n)
            closes = px * np.cumprod(1 + rets)
        opens = np.r_[px, closes[:-1]]
        highs = np.maximum(opens, closes) * (1 + 0.0001); lows = np.minimum(opens, closes) * (1 - 0.0001)
        df = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                           "volume": rng.integers(100, 1000, n)}, index=idx.tz_convert("UTC"))
        parts.append(df); px = closes[-1]
    out = pd.concat(parts); out.index.name = "ts"
    return out


def weekdays(start: dt.date, n: int) -> list[dt.date]:
    out = []; d = start
    while len(out) < n:
        if S.is_trading_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
print("SESSIONS / DST")
o, c, f, e = S.session_bounds(dt.date(2026, 7, 1))
check("July: flatten 14:52 CT == 19:52 UTC", f.astimezone(dt.timezone.utc).strftime("%H:%M") == "19:52",
      f.astimezone(dt.timezone.utc))
o, c, f, e = S.session_bounds(dt.date(2026, 12, 1))
check("December: flatten 14:52 CT == 20:52 UTC (the bug that would have killed the options bot)",
      f.astimezone(dt.timezone.utc).strftime("%H:%M") == "20:52", f.astimezone(dt.timezone.utc))
o, c, f, e = S.session_bounds(dt.date(2026, 11, 27))
check("Black Friday early close: close 12:00 CT, flatten 11:52, no entries after 11:00",
      (c.strftime("%H:%M"), f.strftime("%H:%M"), e.strftime("%H:%M")) == ("12:00", "11:52", "11:00"),
      (c, f, e))
check("holiday detection", not S.is_trading_day(dt.date(2026, 11, 26)) and S.is_trading_day(dt.date(2026, 11, 30)))

days = weekdays(dt.date(2026, 6, 1), 25)
bars = synth_days(days)
rth = S.rth_only(bars)
check("rth_only keeps 390 minutes/day", all(len(g) == 390 for _, g in rth.groupby(rth.index.date)))
b30 = S.resample_bars(rth[rth.index.date == days[0]], 30)
check("30-min resample gives 13 bars anchored at 08:30 CT", len(b30) == 13 and b30.index[0].strftime("%H:%M") == "08:30",
      (len(b30), b30.index[0]))
early = synth_days([dt.date(2026, 11, 27)])
b30e = S.resample_bars(S.rth_only(early), 30)
check("early-close day gives 7 decision bars", len(b30e) == 7, len(b30e))
check("bar_slot: 10:00 CT is slot 3", S.bar_slot(pd.Timestamp("2026-06-01 10:00", tz="America/Chicago"), 30) == 3)

# ---------------------------------------------------------------------------
print("NOISE AREA BANDS")
ctxs = build_sessions(bars, 30, 14, 1.0)
check("one context per session", len(ctxs) == 25, len(ctxs))
check("first session has no band (no lookback)", not ctxs[0].ready())
check("session 3 has bands (>=3 lookback obs)", ctxs[3].ready())
c14 = ctxs[20]
check("upper >= max(open, prev_close) and lower <= min(...)",
      (c14.upper[3] >= max(c14.open_px, c14.prev_close) - 1e-9) and (c14.lower[3] <= min(c14.open_px, c14.prev_close) + 1e-9))
check("sigma grows through the day (noise widens)", np.nanmean(c14.sigma[8:]) > np.nanmean(c14.sigma[:4]),
      c14.sigma.round(5))
# no look-ahead: bands of session k must not change if session k+1 data changes
bars2 = bars.copy(); last = bars2.index.date == days[-1]
bars2.loc[last, ["open", "high", "low", "close"]] *= 1.05
ctxs2 = build_sessions(bars2, 30, 14, 1.0)
check("no look-ahead: session -2 bands unchanged when session -1 data changes",
      np.allclose(ctxs[-2].upper, ctxs2[-2].upper) and np.allclose(ctxs[-2].lower, ctxs2[-2].lower))

# ---------------------------------------------------------------------------
print("STRATEGY DECISIONS (scenario day)")
# 20 quiet days, then a day that breaks out at 10:30, holds, and falls back at 13:00
def shape_last_day(base: pd.DataFrame, path):
    """Overwrite the last session with a deterministic price path (function of minute index)."""
    out = base.copy()
    m = out.index.tz_convert("America/Chicago").date == days[-1]
    idx = out.index[m]; px0 = float(out.loc[idx[0], "open"])
    closes = np.array([path(i, len(idx), px0) for i in range(len(idx))])
    opens = np.r_[px0, closes[:-1]]
    out.loc[m, "open"] = opens; out.loc[m, "close"] = closes
    out.loc[m, "high"] = np.maximum(opens, closes); out.loc[m, "low"] = np.minimum(opens, closes)
    return out
def breakout(i, n, px):
    if i < 120:   return px * (1 + 0.012 * i / 120)               # ramps to +1.2% by 10:30
    if i < 270:   return px * 1.012                               # holds: well outside noise
    return px * 1.002                                             # back inside the noise, above the stop
scen = shape_last_day(bars, breakout)
sctx = build_sessions(scen, 30, 14, 1.0)
last_ctx = sctx[-1]
strat = NoiseAreaStrategy(trail="band", allow_reversal=True, max_entries=2, instrument=C.MES)
d3 = strat.on_bar_close(last_ctx, 3, 0, 0, last_ctx.open_ts + pd.Timedelta(minutes=120))
check("bar 3 (close 10:30) breakout -> target long", d3.target == 1, d3)
d5 = strat.on_bar_close(last_ctx, 5, 1, 1, last_ctx.open_ts + pd.Timedelta(minutes=180))
check("bar 5 still outside noise -> hold long", d5.target == 1, d5)
d9 = strat.on_bar_close(last_ctx, 9, 1, 1, last_ctx.open_ts + pd.Timedelta(minutes=300))
check("bar 9 back inside noise -> exit", d9.target == 0, d9)
late = strat.on_bar_close(last_ctx, 12, 0, 0, last_ctx.close_ts)
check("no new entry after the entry cutoff", late.target == 0, late)
check("stop distance = noise width at the slot, clamped to the instrument's [min, max] points",
      C.MES.min_stop_pts <= strat.stop_distance(last_ctx, 3) <= C.MES.max_stop_pts
      and strat.stop_distance(last_ctx, 3) >= C.STOP_RANGE_MULT * float(last_ctx.upper[3] - last_ctx.lower[3]) - 1e-9, strat.stop_distance(last_ctx, 3))

# ---------------------------------------------------------------------------
print("RISK ENGINE")
re_ = RiskEngine(instrument=C.MES, risk_per_trade=200, max_contracts=5, daily_kill=700, daily_cap=1200, max_drawdown=2000)
acct = RiskEngine.fresh_account(2000)
s = re_.size(4.0, DayState(), acct)             # 4 pts * $5 = $20 + $4.50 costs = $24.5/contract
check("sizing floors: $200 / $24.5 -> 8 -> capped at 5", s.qty == 5, s)
s = re_.size(20.0, DayState(), acct)            # $100 + 4.5 -> 1 contract
check("sizing: 20-pt stop -> 1 contract", s.qty == 1, s)
s = re_.size(60.0, DayState(), acct)            # $300 > $200 -> 0, never rounded up
check("too-wide stop -> 0 contracts (never round up)", s.qty == 0, s)
dsx = DayState(realized=-650)
s = re_.size(20.0, dsx, acct)
check("risk shrinks to the room left before the kill ($50 -> 0 contracts)", s.qty == 0, s)
dsk = DayState(realized=-700)
s = re_.size(20.0, dsk, acct)
check("daily kill halts entries", s.qty == 0 and dsk.halted, s)
dsc = DayState(realized=1250)
s = re_.size(20.0, dsc, acct)
check("daily profit cap halts entries (consistency protection)", s.qty == 0 and "cap" in s.reason, s)
thin = AccountState(-1800, 0, -2000)
s = re_.size(20.0, DayState(), thin)
check("buffer gate: $200 buffer < 1.5x risk -> no entry", s.qty == 0 and "buffer" in s.reason, s)
a = RiskEngine.fresh_account(2000)
re_.end_of_day(a, 500); re_.end_of_day(a, 800)
check("EOD trailing floor follows the peak", math.isclose(a.floor, -700) and a.balance == 1300, a)
re_.end_of_day(a, 1200)
check("floor locks at the start balance", a.floor == 0.0 and a.balance == 2500, a)

# ---------------------------------------------------------------------------
print("PROP RULE SIMULATOR")
rules = R.PRESETS["topstep_50k"]
o = R.simulate_path(rules, [1000, 1000, 1000, 250, 250])
check("+3,000 by day 3 but the 5-winning-day rule delays the pass to day 5", o.result == "pass" and o.days == 5, o)
o = R.simulate_path(rules, [2000, 1000, 250, 250])
check("a +$2,000 day raises the target to $4,000 (consistency)", o.result == "open" and o.balance == 3500, o)
o = R.simulate_path(rules, [2000, 1000, 250, 250, 600])
check("...and it passes at $4,000+ with 5 winning days", o.result == "pass" and o.days == 5, o)
o = R.simulate_path(rules, [-900, -900, -300])
check("cumulative -$2,100 fails on the drawdown at the close", o.result == "fail" and o.days == 3, o)
o = R.simulate_path(rules, [500, -1500, -1100])
check("EOD trailing: peak 500 -> floor -1500; day 3 close -2100 fails", o.result == "fail" and o.days == 3, o)
o = R.simulate_path(rules, [200, 300], intraday_lows=[-2100, 0])
check("intraday touch of the floor fails even though the day closed green", o.result == "fail" and o.days == 1, o)
o = R.simulate_path(rules, [-1500, 400, 600, 700, 700, 700, 700, 700])
check("daily loss limit caps a day at -$1,000 (session stop, not a fail)", o.result == "pass", o)
stat = R.RuleSet("static", 50_000, 3000, 2000, "static")
o = R.simulate_path(stat, [1500, -1400, -1400])
check("static floor does not trail: balance -1300 survives", o.result == "open" and o.balance == -1300, o)
mc = R.monte_carlo(rules, [50] * 10 + [-40] * 10, n=500)
check("monte carlo returns probabilities that sum to 1", math.isclose(mc["pass"] + mc["fail"] + mc["open"], 1.0), mc)
mc_zero = R.monte_carlo(rules, [300, -300] * 10, n=2000)
check("zero-edge sample: P(pass) well below 50%", mc_zero["pass"] < 0.5, mc_zero["pass"])

# ---------------------------------------------------------------------------
print("BACKTESTER (scenario day)")
bt = Backtester(strat, re_, C.MES, 30)
rep = bt.run(sctx)
tr = rep.trades
tr_last = [t for t in tr if t.day == str(days[-1])]
check("scenario produced exactly one trade on the breakout day", len(tr_last) == 1, [(t.day, t.reason_in) for t in tr])
if tr_last:
    t = tr_last[0]
    check("entered long at the open of the bar after the first decision close (09:00 CT, +slippage)",
          t.side == 1 and t.entry_ts.endswith("09:00:00-05:00") and t.entry_px > float(last_ctx.bars_1m["open"].iloc[30]), t)
    check("exited on the band exit or flatten, not the stop", t.reason_out in ("flatten",) or t.reason_out.startswith("exit long"), t.reason_out)
    expected_costs = 2 * t.qty * (C.MES.commission_side + C.MES.slippage_cost)
    gross = t.points * t.qty * C.MES.point_value
    check("P&L = points x qty x $5 - commissions (slippage already in fills)",
          math.isclose(t.pnl, gross - 2 * t.qty * C.MES.commission_side, abs_tol=0.02), (t.pnl, gross))
summ = rep.summary()
check("summary has the prop-relevant fields", all(k in summ for k in ("worst_intraday_low", "days_below_-700", "sharpe_daily_ann")))
sc = rep.prop_score(rules, n=300)
check("prop_score runs on a report", "pass" in sc and "this_history" in sc)

# stop scenario: breakout then crash through the stop
def crash(i, n, px):
    if i < 150:   return px * (1 + 0.012 * min(i, 120) / 120)     # breakout, then...
    return px * 0.97                                              # ...a 4% gap down through the stop
scen2 = shape_last_day(bars, crash)
sctx2 = build_sessions(scen2, 30, 14, 1.0)
bt2 = Backtester(NoiseAreaStrategy(trail="band", instrument=C.MES), RiskEngine(C.MES, 200, 5, 700, 1200, 2000), C.MES, 30)
rep2 = bt2.run(sctx2)
t2 = [t for t in rep2.trades if t.day == str(days[-1])]
check("crash day: the resting stop closes the trade", bool(t2) and t2[0].reason_out == "stop", [t.reason_out for t in t2])
if t2:
    check("stop loss <= risk per trade + slippage/commissions", t2[0].pnl >= -(200 + 5 * 2 * (C.MES.slippage_cost + C.MES.commission_side)) - 1, t2[0].pnl)
    check("gap-through-stop fills at the bar open (worse than the stop)", t2[0].exit_px <= t2[0].stop_px, (t2[0].exit_px, t2[0].stop_px))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
