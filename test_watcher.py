"""
Watcher test harness — replays quote paths through the real decision path with a
fake broker. Places NO orders. Run: python3 test_watcher.py

What is actually being tested here is not "does the arithmetic work" (exitrules
is tested separately) but the three things that only go wrong once the rules are
wired to a live socket and a live account:

  * the confirmation window, which is the difference between reacting to a move
    and reacting to a print
  * the sequence guard, without which a fast decision loop satisfies the
    confirmation requirement by reading one cached quote repeatedly
  * the double-sell guards, because selling a long option twice does not close
    the position — it opens a short one
"""
from __future__ import annotations
import sys, time, types, datetime as dt

import exitrules as R
import watcher as W

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
class FakeOrder:
    _n = 0

    def __init__(self, symbol, side, qty):
        FakeOrder._n += 1
        self.id = f"ord-{FakeOrder._n:04d}"
        self.symbol, self.side, self.qty = symbol, side, qty


class FakePosition:
    def __init__(self, symbol, qty, entry):
        self.symbol, self.qty, self.avg_entry_price = symbol, str(qty), str(entry)


class FakeTrading:
    """Records every call so the tests can assert on ordering, not just counts."""

    def __init__(self, positions=None, open_sells=False, clock_open=True,
                 minutes_left=120.0):
        self.positions = positions or {}
        self.calls = []                 # ordered log of ("verb", symbol)
        self.orders = []
        self.cancelled = []
        self.open_sells = open_sells
        self.clock_open, self.minutes_left = clock_open, minutes_left
        self.position_gone = set()

    def get_clock(self):
        now = dt.datetime.now(dt.timezone.utc)
        return types.SimpleNamespace(
            is_open=self.clock_open, timestamp=now,
            next_close=now + dt.timedelta(minutes=self.minutes_left))

    def get_all_positions(self):
        return [p for s, p in self.positions.items() if s not in self.position_gone]

    def get_open_position(self, sym):
        if sym in self.position_gone or sym not in self.positions:
            raise Exception("position does not exist")
        return self.positions[sym]

    def get_orders(self, req):
        if not self.open_sells:
            return []
        return [types.SimpleNamespace(id="foreign-1", side="OrderSide.SELL")]

    def cancel_order_by_id(self, oid):
        self.calls.append(("cancel", oid))
        self.cancelled.append(oid)

    def submit_order(self, req):
        kind = "stop" if hasattr(req, "stop_price") else "market"
        self.calls.append((f"submit-{kind}", req.symbol))
        o = FakeOrder(req.symbol, req.side, req.qty)
        if kind == "stop":
            o.stop_price = req.stop_price
        self.orders.append(o)
        return o


def mkwatcher(**kw):
    w = W.Watcher.__new__(W.Watcher)
    W.Watcher.__init__.__wrapped__ if False else None
    # build the instance without touching the network
    import threading
    from collections import defaultdict
    w.trading = FakeTrading(**kw)
    w.data = None
    w.key = w.secret = "x"
    w.peaks, w.breach, w.breach_since = {}, defaultdict(int), {}
    w.inflight, w.entries, w.qtys = set(), {}, {}
    w.stops, w.stop_px, w.latest, w.seen_seq = {}, {}, {}, {}
    w.lock, w.qlock = threading.Lock(), threading.Lock()
    w.exits_done, w.stream, w.stream_alive = [], None, False
    w.subscribed, w.tick_count = set(), 0
    return w


def sells(w):
    return [c for c in w.trading.calls if c[0] == "submit-market"]


# --------------------------------------------------------------------------
print("=" * 74)
print("1. Confirmation window — a single bad print must not sell")
print("=" * 74)
w = mkwatcher(positions={"AAA260731C00100000": FakePosition("AAA260731C00100000", 5, 1.00)})
sym = "AAA260731C00100000"
w.entries[sym], w.qtys[sym] = 1.00, 5
w.put_quote(sym, 0.60, 0.64, None, "ws")      # -40%, well past the -30% stop
w.evaluate(sym)
check("one breaching print does not sell", len(sells(w)) == 0,
      f"breach={w.breach[sym]}")

w.put_quote(sym, 0.605, 0.645, None, "ws")
w.evaluate(sym)
check("two breaching prints do not sell", len(sells(w)) == 0,
      f"breach={w.breach[sym]}")

w.put_quote(sym, 0.61, 0.65, None, "ws")
w.evaluate(sym)
check("three prints inside 1s still do not sell (time floor)",
      len(sells(w)) == 0, f"breach={w.breach[sym]}, elapsed<1s")

time.sleep(1.1)
w.put_quote(sym, 0.606, 0.646, None, "ws")
w.evaluate(sym)
time.sleep(0.4)
check("breach sustained past the time floor DOES sell", len(sells(w)) == 1)

print()
print("=" * 74)
print("2. Sequence guard — a cached quote must not confirm itself")
print("=" * 74)
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.put_quote(sym, 0.60, 0.64, None, "ws")
for _ in range(40):                            # decision loop spinning at 0.25s
    w.evaluate(sym)
time.sleep(0.2)
check("40 evaluations of ONE quote never sell", len(sells(w)) == 0,
      f"breach={w.breach[sym]} (must be 1)")
check("breach counter advanced exactly once", w.breach[sym] == 1)

print()
print("=" * 74)
print("3. Recovery resets the counter")
print("=" * 74)
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.put_quote(sym, 0.60, 0.64, None, "ws"); w.evaluate(sym)
w.put_quote(sym, 0.61, 0.65, None, "ws"); w.evaluate(sym)
w.put_quote(sym, 0.90, 0.94, None, "ws"); w.evaluate(sym)   # recovers to -10%
check("recovery zeroes the breach counter", w.breach[sym] == 0)
time.sleep(1.1)
w.put_quote(sym, 0.62, 0.66, None, "ws"); w.evaluate(sym)
time.sleep(0.3)
check("post-recovery breach must re-earn confirmation", len(sells(w)) == 0)

print()
print("=" * 74)
print("4. Panic margin — a collapse skips the debounce")
print("=" * 74)
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.put_quote(sym, 0.50, 0.52, None, "ws")       # -50%, far past the ~-30% stop
w.evaluate(sym)
time.sleep(0.4)
check("a -50% print sells on the first tick", len(sells(w)) == 1)

print()
print("=" * 74)
print("5. Bad quotes never trigger a sale")
print("=" * 74)
STALE = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=120)
BAD = {
    # each case supplies six DISTINCT quotes, so the sequence guard cannot be
    # the thing preventing the sale — the quote rejection has to be
    "zero bid":        [(0.0, 0.40 + i * 0.01, None) for i in range(6)],
    "crossed market":  [(0.80, 0.40 + i * 0.01, None) for i in range(6)],
    "stale (120s)":    [(0.50 - i * 0.01, 0.54, STALE) for i in range(6)],
    # 0.01 x 0.40 is not a cheap contract, it is an empty bid side. Selling
    # into it books -99% on something the mid says is worth 0.20.
    "phantom market":  [(0.01, 0.40 + i * 0.01, None) for i in range(6)],
}
for label, path in BAD.items():
    w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
    w.entries[sym], w.qtys[sym] = 1.00, 5
    for bid, ask, ts in path:
        w.put_quote(sym, bid, ask, ts, "ws")
        w.evaluate(sym)
        time.sleep(0.25)
    check(f"{label} -> no sell", len(sells(w)) == 0)

print()
print("=" * 74)
print("6. Double-sell guards")
print("=" * 74)
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.exit_position(sym, "test", -0.4)
w.exit_position(sym, "test", -0.4)             # in-flight set must block this
w.exit_position(sym, "test", -0.4)
check("in-flight set blocks repeat sells", len(sells(w)) == 1,
      f"{len(sells(w))} market orders")

w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)}, open_sells=True)
w.entries[sym], w.qtys[sym] = 1.00, 5
w.exit_position(sym, "test", -0.4)
check("existing open SELL blocks a second one", len(sells(w)) == 0)

w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.trading.position_gone.add(sym)
w.exit_position(sym, "test", -0.4)
check("vanished position blocks the sell", len(sells(w)) == 0)

print()
print("=" * 74)
print("7. The resting stop is cancelled BEFORE the market sell")
print("=" * 74)
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.ensure_broker_stop(sym)
stop_id = w.stops.get(sym)
w.exit_position(sym, "test", -0.4)
verbs = [c[0] for c in w.trading.calls]
ok = ("cancel" in verbs and "submit-market" in verbs
      and verbs.index("cancel") < verbs.index("submit-market"))
check("cancel precedes the market sell", ok, " -> ".join(verbs))
check("the cancelled id is the resting stop", stop_id in w.trading.cancelled)

print()
print("=" * 74)
print("8. Broker stop ratchets up, never down")
print("=" * 74)
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.ensure_broker_stop(sym)
first = w.stop_px[sym]
w.ensure_broker_stop(sym)
check("unchanged peak places no second stop", len(w.trading.orders) == 1,
      f"${first:.2f}")
w.peaks[sym] = 0.60                                    # position ran to +60%
w.ensure_broker_stop(sym)
check("a new peak raises the stop", w.stop_px[sym] > first,
      f"${first:.2f} -> ${w.stop_px[sym]:.2f}")
raised = w.stop_px[sym]
w.peaks[sym] = 0.30                                    # give-back
w.ensure_broker_stop(sym)
check("a falling peak does NOT lower the stop", w.stop_px[sym] == raised)

print()
print("=" * 74)
print("9. Flatten overrides everything, including the debounce")
print("=" * 74)
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.put_quote(sym, 1.40, 1.44, None, "ws")       # +40%, nothing wrong with it
w.evaluate(sym, flatten=True)
check("a winner is closed at the bell on the first look", len(sells(w)) == 1,
      w.exits_done[0]["reason"] if w.exits_done else "")

w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.put_quote(sym, 0.0, 0.0, None, "ws")         # unusable quote at the bell
w.evaluate(sym, flatten=True)
check("a broken quote does NOT strand a position overnight", len(sells(w)) == 1,
      w.exits_done[0]["reason"] if w.exits_done else "still open at the bell")

w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.evaluate(sym, flatten=True)                  # no quote has EVER arrived
check("a position with no quote at all is still flattened", len(sells(w)) == 1)

print()
print("=" * 74)
print("10. Expiry parsed from the OCC symbol")
print("=" * 74)
today = dt.datetime.now(dt.timezone.utc).date()
for days in (0, 1, 5, 30):
    d = today + dt.timedelta(days=days)
    s = f"NVDA{d.strftime('%y%m%d')}P00200000"
    got = W.dte_from_occ(s)
    check(f"{s} -> {days} DTE", got == days, f"got {got}")
check("garbage symbol yields None", W.dte_from_occ("NOTASYMBOL") is None)

w = mkwatcher(positions={}, )
exp = (today + dt.timedelta(days=1)).strftime("%y%m%d")
s2 = f"NVDA{exp}P00200000"
w.trading.positions = {s2: FakePosition(s2, 1, 1.00)}
w.entries[s2], w.qtys[s2] = 1.00, 1
w.put_quote(s2, 1.30, 1.34, None, "ws")        # +30%, healthy
w.evaluate(s2)
time.sleep(0.4)
check("1-DTE contract is time-stopped even while winning", len(sells(w)) == 1,
      w.exits_done[0]["reason"] if w.exits_done else "no exit")

print()
print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
print("=" * 74)
sys.exit(1 if FAIL else 0)
