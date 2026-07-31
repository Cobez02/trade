"""
Credit-spreads sleeve test harness. No network, no broker. Run: python3 test_spreads.py

What must never regress, in order of how expensive it would be live:

  * the SIGN CONVENTION — Alpaca mleg treats a negative limit as a credit;
    flipping it turns "collect $60" into "pay $60" on every open
  * leg pairing — mis-grouping the book turns defined risk into naked shorts
  * the exemptions — a flatten or a stop that touches ONE leg of a spread
    unmakes its loss bound at the worst possible moment
"""
from __future__ import annotations
import sys, types, datetime as dt

import engine as E
import exitrules as R
import main as M

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")

def P(sym, qty):
    return types.SimpleNamespace(symbol=sym, qty=str(qty), avg_entry_price="1.0")

EXP = (dt.date.today() + dt.timedelta(days=8)).strftime("%y%m%d")
EXP2 = (dt.date.today() + dt.timedelta(days=15)).strftime("%y%m%d")

# --------------------------------------------------------------------------
print("=" * 74)
print("1. detect_spreads — pairing is exact or it is nothing")
print("=" * 74)
pk, mem = E.detect_spreads([P(f"SPY{EXP}P00690000", -1), P(f"SPY{EXP}P00687000", 1)])
check("short put + lower long put pair into one package",
      len(pk) == 1 and pk[0]["width"] == 3.0 and pk[0]["qty"] == 1)
check("both member symbols captured", len(mem) == 2)

pk, mem = E.detect_spreads([P(f"SPY{EXP}P00690000", -1), P(f"SPY{EXP2}P00687000", 1)])
check("different expiries NEVER pair", len(pk) == 0 and len(mem) == 0)

pk, mem = E.detect_spreads([P(f"SPY{EXP}P00690000", -1), P(f"QQQ{EXP}P00600000", 1)])
check("different underlyings NEVER pair", len(pk) == 0)

pk, mem = E.detect_spreads([P(f"SPY{EXP}C00690000", -1), P(f"SPY{EXP}C00687000", 1)])
check("calls are ignored by v1 put-spread detection", len(pk) == 0)

pk, mem = E.detect_spreads([P(f"SPY{EXP}P00687000", -1), P(f"SPY{EXP}P00690000", 1)])
check("a long ABOVE the short never pairs (that is not a credit spread)",
      len(pk) == 0)

pk, mem = E.detect_spreads([P(f"SPY{EXP}P00690000", -2), P(f"SPY{EXP}P00687000", 1)])
check("size-mismatched legs (short 2 vs long 1) never pair", len(pk) == 0)

pk, mem = E.detect_spreads([
    P(f"SPY{EXP}P00690000", -1), P(f"SPY{EXP}P00687000", 1),
    P(f"SPY{EXP}P00680000", 1)])
check("nearest lower long wins when several could pair",
      len(pk) == 1 and pk[0]["long_strike"] == 687.0)
check("the further long stays OUT of members (it is a single)",
      f"SPY{EXP}P00680000" not in mem)

pk, mem = E.detect_spreads([
    P(f"SPY{EXP}P00690000", -1), P(f"SPY{EXP}P00687000", 1),
    P(f"QQQ{EXP}P00600000", -1), P(f"QQQ{EXP}P00596000", 1),
    P(f"NFLX{EXP}C00071000", 1)])
check("two packages + one single detected together",
      len(pk) == 2 and len(mem) == 4)

pk, mem = E.detect_spreads([P(f"SPY{EXP}P00690000", -1)])
check("an orphan short is NOT a package (assignment debris path)", len(pk) == 0)

for garbage in (None, [], [None], [types.SimpleNamespace()],
                [types.SimpleNamespace(symbol=None, qty=None)],
                [types.SimpleNamespace(symbol="garbage!!", qty="x")]):
    try:
        pk, mem = E.detect_spreads(garbage)
        ok = pk == [] and mem == set()
    except Exception as ex:
        ok = False
    if not ok:
        break
check("garbage inputs -> empty result, never a raise", ok)

# --------------------------------------------------------------------------
print()
print("=" * 74)
print("2. spread_dte")
print("=" * 74)
check("8-days-out reads 8", E.spread_dte(EXP) == 8)
check("garbage ymd -> None (never raises)",
      all(E.spread_dte(x) is None for x in (None, "", "xxx", "99")))

# --------------------------------------------------------------------------
print()
print("=" * 74)
print("3. spread_decide — the three documented exits, in priority order")
print("=" * 74)
d = R.spread_decide(0.60, 0.29, 6)
check("value at 48% of credit -> take-profit", d["action"] == "exit" and "take-profit" in d["reason"])
d = R.spread_decide(0.60, 0.31, 6)
check("value at 52% of credit -> hold", d["action"] == "hold")
d = R.spread_decide(0.60, 1.20, 6)
check("value at 2.0x credit -> stop", d["action"] == "exit" and "stop" in d["reason"])
d = R.spread_decide(0.60, 1.19, 6)
check("value just under 2x -> hold", d["action"] == "hold")
d = R.spread_decide(0.60, 0.55, 2)
check("2 DTE -> time exit even at a middling value", d["action"] == "exit" and "time-exit" in d["reason"])
d = R.spread_decide(0.60, 1.30, 1)
check("stop OUTRANKS time when both apply", "stop" in d["reason"])
d = R.spread_decide(0.60, 0.30, 8)
check("pnl_frac reports fraction of credit", abs(d["pnl_frac"] - 0.5) < 1e-9)
bad = 0
for c, n, t in ((None, 1, 1), ("x", 1, 1), (0.6, None, 1), (0.6, "y", 1),
                (0, 1, 1), (-1, 1, 1), (0.6, -0.1, 1), (float("nan"), 1, 1),
                (0.6, float("nan"), 1), (0.6, 0.5, "z")):
    try:
        d = R.spread_decide(c, n, t)
        if d["action"] == "exit" and "unreadable" in d.get("reason", ""):
            bad += 1
    except Exception:
        bad += 1
check("garbage battery: never raises, never exits blind", bad == 0)

# --------------------------------------------------------------------------
print()
print("=" * 74)
print("4. THE SIGN CONVENTION — a credit is a NEGATIVE mleg limit, exactly once")
print("=" * 74)
class RecTrading:
    def __init__(self): self.reqs = []
    def submit_order(self, req):
        self.reqs.append(req); return types.SimpleNamespace(id="ord-1")

t = RecTrading()
E.submit_spread_order(t, f"SPY{EXP}P00690000", f"SPY{EXP}P00687000", 1, 0.62, "SPXS-62-300-1")
r = t.reqs[-1]
check("OPEN: limit is NEGATIVE (credit received)", r.limit_price == -0.62, str(r.limit_price))
check("OPEN: order_class is mleg", str(r.order_class).endswith("MLEG") or "mleg" in str(r.order_class).lower())
check("OPEN: short leg SELL_TO_OPEN, long leg BUY_TO_OPEN",
      "sell_to_open" in str(r.legs[0].position_intent).lower()
      and "buy_to_open" in str(r.legs[1].position_intent).lower())
check("OPEN: tag rides the PARENT order", r.client_order_id == "SPXS-62-300-1")
E.submit_spread_order(t, "A", "B", 1, -0.62)          # even a wrong-signed input
check("OPEN: caller sign mistakes are absorbed (abs applied)",
      t.reqs[-1].limit_price == -0.62)

E.close_spread_order(t, f"SPY{EXP}P00690000", f"SPY{EXP}P00687000", 1, 0.31)
r = t.reqs[-1]
check("CLOSE: limit is POSITIVE (debit paid)", r.limit_price == 0.31)
check("CLOSE: short leg BUY_TO_CLOSE, long leg SELL_TO_CLOSE",
      "buy_to_close" in str(r.legs[0].position_intent).lower()
      and "sell_to_close" in str(r.legs[1].position_intent).lower())

# --------------------------------------------------------------------------
print()
print("=" * 74)
print("5. rebuild_from_alpaca — spread orders can NEVER poison the singles journal")
print("=" * 74)
def O(sym, side, coid="", oclass="", filled="1", price="1.0", at="2026-07-30T15:00:00Z"):
    return types.SimpleNamespace(symbol=sym, side=side, client_order_id=coid,
                                 order_class=oclass, filled_qty=filled,
                                 filled_avg_price=price, filled_at=at)
class FakeBroker:
    def __init__(self, orders): self._o = orders
    def closed_orders(self, limit=500): return self._o

orders = [
    O(f"SPY{EXP}P00690000", "OrderSide.SELL", coid="SPXS-62-300-99", oclass="mleg"),
    O(f"SPY{EXP}P00690000", "OrderSide.BUY", oclass="mleg"),
    O(f"QQQ{EXP}C00600000", "OrderSide.SELL", coid=""),
    O(f"QQQ{EXP}C00600000", "OrderSide.BUY", coid="SPXB-tech-b-33-6-1-40-1-1"),
]
journal, pos_feat = M.rebuild_from_alpaca(FakeBroker(orders))
syms = [j["symbol"] for j in journal]
check("mleg orders excluded from the journal",
      all("P00690000" not in s for s in syms), str(syms))
check("ordinary singles still journal normally",
      any("QQQ" in s for s in syms))

# --------------------------------------------------------------------------
print()


print()
print("=" * 74)
print("6. Spreads kill switch — entries gated, management never")
print("=" * 74)
import os as _os, importlib as _imp
_os.environ["SPXBOT_SPREADS"] = "0"
_imp.reload(M)
try:
    M.try_spread_entries(None, {}, [])       # broker=None: crashes if ungated
    check("disabled switch returns before touching the broker", True)
except Exception as e:
    check("disabled switch returns before touching the broker", False, str(e)[:60])
src_main = open("main.py").read()
check("manage_spreads is NOT behind the kill switch (open packages stay managed)",
      'SPXBOT_SPREADS' not in src_main.split("def manage_spreads")[1].split("def ")[0])
_os.environ.pop("SPXBOT_SPREADS", None)
_imp.reload(M)


print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 74)
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
