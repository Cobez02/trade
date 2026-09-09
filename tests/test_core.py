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

print("BACKTESTER: research replay never freezes")
def losing(i, n, px):
    return px * (1 + 0.012 * min(i, 60) / 60) if i < 60 else px * (1 - 0.006 * (i - 60) / 330)   # breakout then bleed
bars_l = bars.copy()
for dd in days[3::2]:          # every other day: the band keeps learning from quiet days, so entries never dry up
    m = bars_l.index.tz_convert("America/Chicago").date == dd
    idx = bars_l.index[m]; px0 = float(bars_l.loc[idx[0], "open"])
    closes = np.array([losing(i, len(idx), px0) for i in range(len(idx))]); opens = np.r_[px0, closes[:-1]]
    bars_l.loc[m, "open"] = opens; bars_l.loc[m, "close"] = closes
    bars_l.loc[m, "high"] = np.maximum(opens, closes); bars_l.loc[m, "low"] = np.minimum(opens, closes)
ctx_l = build_sessions(bars_l, 30, 14, 1.0)
rep_l = Backtester(NoiseAreaStrategy(trail="band", instrument=C.MES), RiskEngine(C.MES, 200, 5, 700, 1200, 2000), C.MES, 30).run(ctx_l)
days_traded = sorted({t.day for t in rep_l.trades})
check("keeps trading through a long losing stretch (no buffer-gate freeze)", len(days_traded) >= 8 and days_traded[-1] >= str(days[-3]),
      (len(days_traded), days_traded[-1:], str(days[-3])))
check("...and the cumulative loss is well past a $2,000 floor (so the freeze would have bitten)", sum(rep_l.daily) < -2000, sum(rep_l.daily))


# ---------------------------------------------------------------------------
print("SLEEVE 2 — last half hour")
from strategy.lasthalf import LastHalfHourStrategy, vol_regime
lh = LastHalfHourStrategy(min_move=0.0005, confirm=True, instrument=C.MES)
ctxL = ctxs[20]
dec_early = lh.on_bar_close(ctxL, 3, 0, 0, ctxL.open_ts + pd.Timedelta(minutes=120))
check("no decision before the second-to-last bar", dec_early.target == 0 and "not the decision" in dec_early.reason, dec_early)
slotd = ctxL.n_slots - 2
dec = lh.on_bar_close(ctxL, slotd, 0, 0, ctxL.open_ts + pd.Timedelta(minutes=30 * (slotd + 1)))
r_first = float(ctxL.bars_n["close"].iloc[0]) / ctxL.prev_close - 1
r_late = float(ctxL.bars_n["close"].iloc[slotd]) / float(ctxL.bars_n["close"].iloc[slotd - 1]) - 1
expected = (1 if (r_first > 0.0005 and r_late >= 0) else (-1 if (r_first < -0.0005 and r_late <= 0) else 0))
check("decision at 14:30 follows sign(r_first) with r_late confirmation", dec.target == expected, (dec, r_first, r_late))
hold = lh.on_bar_close(ctxL, slotd + 1, 1, 1, ctxL.close_ts)
check("holds to the close once in", hold.target == 1)
mask = vol_regime(ctxs, lookback=5, min_obs=3)
check("regime mask covers every session and is boolean", len(mask) == len(ctxs) and all(isinstance(v, bool) for v in mask.values()))
ctxs_alt = build_sessions(bars2, 30, 14, 1.0)
m1 = vol_regime(ctxs, lookback=5, min_obs=3); m2 = vol_regime(ctxs_alt, lookback=5, min_obs=3)
check("regime has no look-ahead: changing the last session cannot change earlier flags",
      all(m1[c.day] == m2[c.day] for c in ctxs[:-1]))
# the leak that batch 4 had: session i's OWN close must not move session i's flag
bars3 = bars.copy(); mid = days[18]; mm = bars3.index.tz_convert("America/Chicago").date == mid
bars3.loc[mm, ["open", "high", "low", "close"]] *= 1.03                       # +3% day, huge |return| into and out of it
m3 = vol_regime(build_sessions(bars3, 30, 14, 1.0), lookback=5, min_obs=3)
check("regime: a session's own move cannot change its own flag", m1[mid] == m3[mid], (m1[mid], m3[mid]))
check("regime: ...but it does change the NEXT sessions' flags (the statistic is alive)", any(m1[c.day] != m3[c.day] for c in ctxs[19:24]))

print("SLEEVE 3 — pair spread")
from backtest.pair_simulate import PairBacktester
# leg B = an independent random walk (so the spread has a real sigma) with a 1% divergence on the last day that reverts
bars_b = synth_days(days, seed=7)
lastm = bars_b.index.tz_convert("America/Chicago").date == days[-1]
idxl = bars_b.index[lastm]; k = len(idxl)
bump = np.array([1 + 0.012 * min(i, 60) / 60 if i < 90 else 1 + 0.012 * max(0, (150 - i)) / 60 for i in range(k)])   # +1.2% by 09:30, back to 0 by 11:00
for col in ("open", "high", "low", "close"):
    bars_b.loc[lastm, col] = bars_b.loc[lastm, col].to_numpy() * bump
pb = PairBacktester(z_entry=1.5, z_exit=0.5, stop_sigma=1.0, risk=200, slip_a=0.045, slip_b=0.019)
repp = pb.run(bars, bars_b)
tl = [t for t in repp.trades if t.day == str(days[-1])]
check("pair: divergence day produced one spread trade", len(tl) == 1, [(t.day, t.side, t.reason_out) for t in repp.trades][-3:])
if tl:
    check("pair: faded the outperformer (short QQQ / long SPY when QQQ ran up)", tl[0].side == -1, tl[0])
    check("pair: closed on reversion or flatten and made money net of both legs' costs", tl[0].reason_out in ("reverted", "flatten") and tl[0].pnl > 0, tl[0])
check("pair: summary and prop score work", "net_pnl" in repp.summary() and "pass" in repp.prop_score(R.PRESETS["mffu_core_50k"], n=200))

print("PORTFOLIO")
import subprocess, tempfile, json as _json
tmp = tempfile.mkdtemp()
pd.DataFrame([d.__dict__ for d in rep.days]).to_csv(f"{tmp}/a.csv", index=False)
pd.DataFrame([d.__dict__ for d in repp.days]).to_csv(f"{tmp}/b.csv", index=False)
r = subprocess.run([sys.executable, "-m", "scripts.portfolio", f"A={tmp}/a.csv", f"B={tmp}/b.csv", "--holdout-from", str(days[15]),
                    "--mc", "200", "--out", f"{tmp}/out"], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ok = r.returncode == 0 and os.path.exists(f"{tmp}/out/portfolio_summary.json")
check("portfolio script runs and writes the summary with gates", ok, r.stderr[-400:])
if ok:
    j = _json.load(open(f"{tmp}/out/portfolio_summary.json")); check("portfolio: six gates evaluated", len(j["gates"]) == 6, j["gates"])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
