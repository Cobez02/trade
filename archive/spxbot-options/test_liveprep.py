"""Go-live prep checks: flatten sweep + live-mode plumbing. Run: python3 test_liveprep.py"""
from __future__ import annotations
import os, sys, types

import main as M

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")

def O(sym, side, oclass="", oid="x", qty="1"):
    return types.SimpleNamespace(symbol=sym, side=side, order_class=oclass,
                                 id=oid, qty=qty)

class FakeBroker:
    def __init__(self, orders):
        self._o = orders
        self.cancelled = []
    def open_orders(self):
        return self._o
    def cancel(self, oid):
        self.cancelled.append(oid)

print("=" * 70)
print("1. sweep_entry_buys — buys die, protection and packages survive")
print("=" * 70)
b = FakeBroker([
    O("AAPL260814C00230000", "OrderSide.BUY", oid="buy1"),
    O("AAPL260814C00230000", "OrderSide.SELL", oid="stop1"),          # resting stop
    O("SPY260814P00700000", "OrderSide.BUY", oclass="mleg", oid="mleg1"),
    O("QQQ260814C00700000", "OrderSide.BUY", oid="buy2"),
])
notes = []
M.sweep_entry_buys(b, notes)
check("both plain entry buys cancelled", set(b.cancelled) == {"buy1", "buy2"},
      str(b.cancelled))
check("the resting SELL stop is NOT cancelled", "stop1" not in b.cancelled)
check("mleg parents are NOT cancelled", "mleg1" not in b.cancelled)
check("every cancel is noted", sum("FLATTEN SWEEP" in n for n in notes) == 2)

class BoomBroker(FakeBroker):
    def cancel(self, oid):
        raise RuntimeError("api down")
b2 = BoomBroker([O("A", "OrderSide.BUY", oid="b")])
notes2 = []
M.sweep_entry_buys(b2, notes2)
check("cancel failure is noted, never raised",
      any("sweep failed" in n for n in notes2))

print()
print("=" * 70)
print("2. broker_paper — live only on an explicit env flip")
print("=" * 70)
os.environ.pop("SPXBOT_LIVE", None)
check("default is paper", M.broker_paper() is True)
os.environ["SPXBOT_LIVE"] = "0"
check("SPXBOT_LIVE=0 stays paper", M.broker_paper() is True)
os.environ["SPXBOT_LIVE"] = "1"
check("SPXBOT_LIVE=1 goes live", M.broker_paper() is False)
os.environ.pop("SPXBOT_LIVE", None)

print()
print("=" * 70)
print("3. wiring — the sweep runs in the flatten path of run()")
print("=" * 70)
src = open("main.py").read()
seg = src[src.index("def run():"):]
check("run() gates the sweep on flatten-only OR the flatten window",
      "sweep_entry_buys(broker, notes)" in seg
      and 'SPXBOT_FLATTEN_ONLY' in seg.split("sweep_entry_buys")[0][-400:])
check("Broker construction honors the live flag",
      "paper=broker_paper()" in seg)

print()
print("=" * 70)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 70)
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
