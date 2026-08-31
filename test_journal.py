"""Journal rebuild tests. Run: python3 test_journal.py

WHY THIS FILE EXISTS. rebuild_from_alpaca kept only the newest buy and newest
sell per option symbol, so any contract traded twice lost every round trip but
its last. Six or more settled trips vanished from the reported P&L and from the
learner's training data, and since winners are what get re-entered, nearly all
of them were winners. Nothing in the suite noticed for weeks.

Every check below fails against the old implementation.
"""
from __future__ import annotations
import types

import main as M

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  - ' + detail if detail else ''}")


def O(sym, side, coid="", at="2026-08-01T15:00:00Z", qty="1", px="1.00", oclass=""):
    return types.SimpleNamespace(symbol=sym, side=side, client_order_id=coid,
                                 order_class=oclass, filled_qty=qty,
                                 filled_avg_price=px, filled_at=at)


class FakeBroker:
    def __init__(self, orders):
        self._o = orders

    def closed_orders(self, limit=500):
        return self._o          # NEWEST FIRST, as Alpaca returns them


S = "GOOGL260904C00200000"
COID = "SPXB-tech-b-33-6-1-40-1-1"

print("=" * 74)
print("1. THE BUG: a contract traded twice keeps BOTH round trips")
print("=" * 74)
# newest first: sell2, buy2, sell1, buy1
orders = [
    O(S, "OrderSide.SELL", at="2026-08-04T19:00:00Z", px="3.00"),
    O(S, "OrderSide.BUY",  at="2026-08-04T14:00:00Z", px="2.00", coid=COID),
    O(S, "OrderSide.SELL", at="2026-08-01T19:00:00Z", px="1.50"),
    O(S, "OrderSide.BUY",  at="2026-08-01T14:00:00Z", px="1.00", coid=COID),
]
j, pf = M.rebuild_from_alpaca(FakeBroker(orders))
check("two round trips are journaled, not one", len(j) == 2, f"got {len(j)}")
pnls = sorted(x["pnl"] for x in j)
check("both P&Ls are correct and neither is fabricated",
      pnls == [50.0, 100.0], str(pnls))
check("the OLDER trip is the one that used to vanish",
      any(abs(x["pnl"] - 50.0) < 1e-6 for x in j))

print()
print("=" * 74)
print("2. An OPEN position must not be journaled")
print("=" * 74)
orders = [O(S, "OrderSide.BUY", at="2026-08-05T14:00:00Z", px="2.00", coid=COID)]
j, pf = M.rebuild_from_alpaca(FakeBroker(orders))
check("a buy with no sell produces no row", len(j) == 0, f"got {len(j)}")
check("but its features are still exposed for the open position", S in pf)

print()
print("=" * 74)
print("3. Re-entered and still open: one settled trip, no invented exit")
print("=" * 74)
orders = [
    O(S, "OrderSide.BUY",  at="2026-08-04T14:00:00Z", px="9.00", coid=COID),
    O(S, "OrderSide.SELL", at="2026-08-01T19:00:00Z", px="1.50"),
    O(S, "OrderSide.BUY",  at="2026-08-01T14:00:00Z", px="1.00", coid=COID),
]
j, pf = M.rebuild_from_alpaca(FakeBroker(orders))
check("exactly one settled trip", len(j) == 1, f"got {len(j)}")
check("the new entry is NOT paired with the old exit",
      abs(j[0]["pnl"] - 50.0) < 1e-6 if j else False,
      str(j[0]["pnl"]) if j else "no rows")

print()
print("=" * 74)
print("4. FIFO lot matching across partial fills")
print("=" * 74)
orders = [
    O(S, "OrderSide.SELL", at="2026-08-02T19:00:00Z", px="3.00", qty="1"),
    O(S, "OrderSide.SELL", at="2026-08-02T18:00:00Z", px="2.00", qty="1"),
    O(S, "OrderSide.BUY",  at="2026-08-01T14:00:00Z", px="1.00", qty="2", coid=COID),
]
j, pf = M.rebuild_from_alpaca(FakeBroker(orders))
check("a 2-lot buy closed by two 1-lot sells makes two rows", len(j) == 2, f"got {len(j)}")
check("each row prices one contract, not two",
      sorted(x["pnl"] for x in j) == [100.0, 200.0],
      str(sorted(x["pnl"] for x in j)))

print()
print("=" * 74)
print("5. Same-second fills sort by Alpaca's newest-first order, not by luck")
print("=" * 74)
T = "2026-08-01T15:00:00Z"
orders = [O(S, "OrderSide.SELL", at=T, px="2.00"),
          O(S, "OrderSide.BUY",  at=T, px="1.00", coid=COID)]
j, _ = M.rebuild_from_alpaca(FakeBroker(orders))
check("a tied buy/sell pair still journals as a round trip", len(j) == 1, f"got {len(j)}")
check("and in the right direction (+100, not -100)",
      abs(j[0]["pnl"] - 100.0) < 1e-6 if j else False,
      str(j[0]["pnl"]) if j else "no rows")

print()
print("=" * 74)
print("6. Features are the ENTRY's, and mleg legs never enter the journal")
print("=" * 74)
orders = [
    O(S, "OrderSide.SELL", at="2026-08-01T19:00:00Z", px="2.00", coid="SPXB-news-x-1-1-1-1-1-1"),
    O(S, "OrderSide.BUY",  at="2026-08-01T14:00:00Z", px="1.00", coid=COID),
]
j, _ = M.rebuild_from_alpaca(FakeBroker(orders))
check("sleeve comes from the BUY, not the SELL",
      bool(j) and j[0]["sleeve"] == "tech", j[0]["sleeve"] if j else "no rows")
orders = [
    O("SPY260904P00690000", "OrderSide.SELL", oclass="mleg", at="2026-08-01T19:00:00Z"),
    O("SPY260904P00690000", "OrderSide.BUY", oclass="mleg", at="2026-08-01T14:00:00Z"),
    O(S, "OrderSide.SELL", at="2026-08-01T19:00:00Z", px="2.00"),
    O(S, "OrderSide.BUY",  at="2026-08-01T14:00:00Z", px="1.00", coid=COID),
]
j, _ = M.rebuild_from_alpaca(FakeBroker(orders))
check("mleg legs still excluded after the rewrite",
      all("P00690000" not in x["symbol"] for x in j) and len(j) == 1,
      str([x["symbol"] for x in j]))

print()
print("=" * 74)
print("7. The recovered trips must not replay as phone notifications")
print("=" * 74)
sent_box = []
import alerts as _al
_orig = _al.send_alert
_al.send_alert = lambda *a, **k: sent_box.append(a)
try:
    j = [{"symbol": S, "closed_on": "2026-08-01", "pnl": 50.0, "sleeve": "tech"},
         {"symbol": S, "closed_on": "2026-08-04", "pnl": 100.0, "sleeve": "tech"}]
    st = {"notified_trades": [f"{S}|2026-08-04|100.0"]}      # pre-fix state
    n = M.notify_settled_trades(st, j)
    check("first run after the fix sends nothing", n == 0 and not sent_box, f"sent {n}")
    check("and marks the state so it only happens once",
          st.get("journal_rebuild_v") == 2)
    check("recovered keys are now in the seen set",
          f"{S}|2026-08-01|50.0" in set(st["notified_trades"]))
    j.append({"symbol": S, "closed_on": "2026-08-06", "pnl": 25.0, "sleeve": "tech"})
    n2 = M.notify_settled_trades(st, j)
    check("a genuinely new trade DOES notify on the next run", n2 == 1, f"sent {n2}")
finally:
    _al.send_alert = _orig

print()
print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 74)
for f in FAIL:
    print("  FAILED:", f)
raise SystemExit(1 if FAIL else 0)
