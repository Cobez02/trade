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
print("8. THE RACE GUARD: broker truth, checked in the last instant")
print("=" * 74)
# 2026-09-01: the watcher's entry scan and the hourly run both read state before
# either fill landed and both opened NVDA, nine seconds apart. state-based guards
# cannot see that. These check the guard that can.
OCC = "NVDA260925C00225000"


class _P:
    def __init__(self, sym, qty="1"):
        self.symbol = sym
        self.qty = qty


class _Brk:
    def __init__(self, pos=(), orders=(), pos_raises=False, ord_raises=False):
        self._p, self._o = list(pos), list(orders)
        self._pr, self._or = pos_raises, ord_raises

    def positions(self):
        if self._pr:
            raise RuntimeError("alpaca down")
        return self._p

    def open_orders(self, limit=200):
        if self._or:
            raise RuntimeError("alpaca down")
        return self._o


ok, why = M.broker_clear_to_open(_Brk(), OCC, "NVDA")
check("clear when the broker holds nothing", ok and why == "", why)

ok, why = M.broker_clear_to_open(_Brk(pos=[_P(OCC, "1")]), OCC, "NVDA")
check("BLOCKS when the broker already holds the contract", not ok, why)

ok, why = M.broker_clear_to_open(_Brk(orders=[_P(OCC)]), OCC, "NVDA")
check("BLOCKS when a working order already exists (the 9-second race)", not ok, why)

ok, why = M.broker_clear_to_open(_Brk(pos_raises=True), OCC, "NVDA")
check("FAILS CLOSED when the position query errors", not ok, why)

ok, why = M.broker_clear_to_open(_Brk(ord_raises=True), OCC, "NVDA")
check("FAILS CLOSED when the open-order query errors", not ok, why)

ok, _ = M.broker_clear_to_open(_Brk(pos=[_P("AAPL260918C00322500")]), OCC, "NVDA")
check("does NOT block an unrelated contract", ok)

src = open("main.py").read()
seg = src.split("broker_clear_to_open(broker, contract.symbol, und)")[0]
check("the guard sits AFTER sizing and IMMEDIATELY before buy_to_open",
      "buy_to_open" not in seg.split("cost = qty * limit_px * 100")[-1],
      "nothing may submit an order before the guard runs")

print()
print("=" * 74)
print("9. closed_orders PAGINATES: the learner must stop forgetting")
print("=" * 74)
# 2026-09-02: the account held 520 closed orders and the old single-request
# call returned exactly 500, hiding the 16 OLDEST filled ones. The learner
# count went DOWN (88 -> 85) as new orders pushed old round trips out.
import types as _t
import engine as _E


class _FakeTrading:
    """Returns 500 per page, like Alpaca, over a 1,150-order history."""

    def __init__(self, n=1150):
        import datetime as _dt
        base = _dt.datetime(2026, 9, 1, 12, 0, 0)
        self.all = [_t.SimpleNamespace(id=f"o{i}",
                                       submitted_at=base - _dt.timedelta(minutes=i))
                    for i in range(n)]
        self.calls = 0

    def get_orders(self, req):
        self.calls += 1
        until = getattr(req, "until", None)
        pool = self.all if until is None else [o for o in self.all
                                               if o.submitted_at < until]
        return pool[:min(getattr(req, "limit", 500), 500)]


b = _E.Broker.__new__(_E.Broker)
b.trading = _FakeTrading(1150)
got = _E.Broker.closed_orders(b)
check("fetches past the 500-per-page ceiling", len(got) == 1150, f"got {len(got)}")
check("makes more than one request", b.trading.calls > 1, f"{b.trading.calls} calls")
check("no duplicates across pages", len({o.id for o in got}) == len(got))
check("reaches the OLDEST order", got[-1].id == "o1149", got[-1].id)

b2 = _E.Broker.__new__(_E.Broker)
b2.trading = _FakeTrading(1150)
check("honours an explicit total cap", len(_E.Broker.closed_orders(b2, limit=600)) == 600)

b3 = _E.Broker.__new__(_E.Broker)
b3.trading = _FakeTrading(120)
check("stops early when history is short", len(_E.Broker.closed_orders(b3)) == 120)


class _Boom:
    def __init__(self):
        self.n = 0

    def get_orders(self, req):
        self.n += 1
        if self.n > 1:
            raise RuntimeError("alpaca down mid-pagination")
        import datetime as _dt
        base = _dt.datetime(2026, 9, 1, 12, 0, 0)
        return [_t.SimpleNamespace(id=f"o{i}", submitted_at=base - _dt.timedelta(minutes=i))
                for i in range(500)]


b4 = _E.Broker.__new__(_E.Broker)
b4.trading = _Boom()
got4 = _E.Broker.closed_orders(b4)
check("a mid-pagination failure returns the partial page, not nothing",
      len(got4) == 500, f"got {len(got4)}")
check("and MARKS the history incomplete rather than hiding it",
      getattr(b4, "order_fetch_complete", None) is False)
check("a clean full fetch is marked complete",
      getattr(b, "order_fetch_complete", None) is True)

print()
print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 74)
for f in FAIL:
    print("  FAILED:", f)
raise SystemExit(1 if FAIL else 0)
