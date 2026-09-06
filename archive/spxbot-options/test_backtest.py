"""
Backtest harness tests — pure logic only, no network. Run: python3 test_backtest.py

Pins the three ways a backtest lies: config drift (testing rules production
does not run), optimistic fills, and scoring arithmetic.
"""
from __future__ import annotations
import sys, types, datetime as dt

import backtest as B
import engine as E

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")

print("=" * 74)
print("1. Config mirror — the backtest tests THE SHIPPED RULES, not a cousin")
print("=" * 74)
pairs = [
    ("SPREADS_RICH_PTS", B.SPREADS_RICH_PTS, E.SPREADS_RICH_PTS),
    ("SPREADS_MIN_CREDIT_FRAC", B.SPREADS_MIN_CREDIT_FRAC, E.SPREADS_MIN_CREDIT_FRAC),
    ("SPREADS_NET_COST_FRAC", B.SPREADS_NET_COST_FRAC, E.SPREADS_NET_COST_FRAC),
    ("SPREADS_MAX_LOSS", B.SPREADS_MAX_LOSS, E.SPREADS_MAX_LOSS),
    ("SPREADS_DTE_MIN", B.SPREADS_DTE_MIN, E.SPREADS_DTE_MIN),
    ("SPREADS_DTE_MAX", B.SPREADS_DTE_MAX, E.SPREADS_DTE_MAX),
    ("SPREADS_TAKE_FRAC", B.SPREADS_TAKE_FRAC, E.SPREADS_TAKE_FRAC),
    ("SPREADS_STOP_MULT", B.SPREADS_STOP_MULT, E.SPREADS_STOP_MULT),
    ("SPREADS_TIME_DTE", B.SPREADS_TIME_DTE, E.SPREADS_TIME_DTE),
    ("MAX_SPREAD_PCT", B.MAX_SPREAD_PCT, E.MAX_SPREAD_PCT),
    ("MAX_PREMIUM_PER_TRADE", B.MAX_PREMIUM_PER_TRADE, E.MAX_PREMIUM_PER_TRADE),
    ("TAKE_PROFIT_PCT", B.TAKE_PROFIT_PCT, E.TAKE_PROFIT_PCT),
    ("STOP_LOSS_PCT", B.STOP_LOSS_PCT, E.STOP_LOSS_PCT),
    ("TARGET_OTM_PCT", B.TARGET_OTM_PCT, E.TARGET_OTM_PCT),
]
drift = [f"{n} bt={a} live={b}" for n, a, b in pairs if a != b]
check("every mirrored constant equals the live engine value", not drift, "; ".join(drift))
check("TIME_STOP_DTE mirrored", B.TIME_STOP_DTE == E.TIME_STOP_DTE,
      f"bt={B.TIME_STOP_DTE} live={E.TIME_STOP_DTE}")

print()
print("=" * 74)
print("2. Selection helpers")
print("=" * 74)
exps = ["2022-06-03", "2022-06-10", "2022-06-17", "2022-07-15"]
pe = B.pick_expiration(exps, dt.date(2022, 6, 1), 5, 10)
check("expiration in [5,10] DTE nearest 7 chosen", pe == ("2022-06-10", 9), str(pe))
check("no expiration in window -> None",
      B.pick_expiration(exps, dt.date(2022, 8, 1), 5, 10) is None)
check("nearest strike", B.nearest([1, 2, 3.5], 3.2) == 3.5)
check("nearest of empty -> None", B.nearest([], 1) is None)

print()
print("=" * 74)
print("3. Scoring arithmetic")
print("=" * 74)
trades = [{"pnl": 30.0, "exit_date": "2021-01-05", "flagged": False},
          {"pnl": 30.0, "exit_date": "2021-06-05", "flagged": False},
          {"pnl": -60.0, "exit_date": "2022-01-05", "flagged": True}]
s = B.score(trades, "t")
check("total", s["total"] == 0.0)
check("win rate (rounded to 3dp by score)", s["win_rate"] == 0.667)
check("profit factor", s["profit_factor"] == 1.0)
check("max drawdown is the worst equity giveback", s["max_dd"] == -60.0)
check("flag count carried", s["flagged"] == 1)
check("empty trades -> n=0 stub", B.score([], "e")["n"] == 0)

print()
print("=" * 74)
print("4. Stress re-books flagged spread exits at 2.5x credit, never better")
print("=" * 74)
tr = [{"pnl": 25.0, "credit_fill": 0.60, "flagged": False},
      {"pnl": -50.0, "credit_fill": 0.60, "flagged": True},
      {"pnl": -120.0, "credit_fill": 0.60, "flagged": True}]
st = B.stress_spreads(tr)
check("unflagged untouched", st[0]["pnl"] == 25.0)
check("flagged mild loss re-booked to -1.5x-credit bound (-90.4)",
      abs(st[1]["pnl"] - (-90.4)) < 1e-6, str(st[1]["pnl"]))
check("flagged loss already worse than bound is NOT improved",
      st[2]["pnl"] == -120.0)

print()
print("=" * 74)
print("5. option_eod row hygiene (offline: parser rejects junk quotes)")
print("=" * 74)
# emulate the parse loop on crafted rows
rows = [
    {"bid": "1.00", "ask": "1.10", "created": "2022-06-01T17:00:00", "high": "1.2", "low": "0.9"},
    {"bid": "0", "ask": "1.10", "created": "2022-06-02T17:00:00"},        # zero bid
    {"bid": "1.20", "ask": "1.10", "created": "2022-06-03T17:00:00"},     # crossed
    {"bid": "x", "ask": "1.10", "created": "2022-06-04T17:00:00"},        # garbage
]
out = {}
for r in rows:
    try:
        b, a = float(r["bid"]), float(r["ask"])
        if b <= 0 or a <= 0 or a < b:
            continue
        d = str(r["created"])[:10]
        out[d] = {"bid": b, "ask": a}
    except (KeyError, TypeError, ValueError):
        continue
check("only the clean two-sided quote survives", list(out) == ["2022-06-01"])

print()
print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 74)
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
