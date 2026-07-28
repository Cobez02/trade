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
                 minutes_left=120.0, reject_stoplimit=False):
        self.positions = positions or {}
        self.calls = []                 # ordered log of ("verb", symbol)
        self.orders = []
        self.cancelled = []
        self.open_sells = open_sells
        self.clock_open, self.minutes_left = clock_open, minutes_left
        self.position_gone = set()
        self.reject_stoplimit = reject_stoplimit

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
        has_stop = getattr(req, "stop_price", None) is not None
        has_lim = getattr(req, "limit_price", None) is not None
        kind = ("stoplimit" if has_stop and has_lim
                else "stop" if has_stop else "market")
        if kind == "stoplimit" and self.reject_stoplimit:
            self.calls.append(("reject-stoplimit", req.symbol))
            raise Exception("simulated broker rejection of stop-limit")
        self.calls.append((f"submit-{kind}", req.symbol))
        o = FakeOrder(req.symbol, req.side, req.qty)
        if has_stop:
            o.stop_price = req.stop_price
        if has_lim:
            o.limit_price = req.limit_price
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
print("11. _stop_prices — trigger below the noise band, limit below the trigger")
print("=" * 74)
# The regression this section exists to prevent: the first stop-limit version
# priced the limit off the BID, so a stop deep below the market — the normal
# state of a fresh hard-stop — got limit=None and silently fell back to the
# stop-market the rework was supposed to eliminate.
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5

w.put_quote(sym, 2.00, 2.10, None, "ws")
trig, lim, note = w._stop_prices(sym, 0.75)          # deep stop, quote far above
check("deep stop: trigger untouched", trig == 0.75, f"trig={trig}")
check("deep stop: limit is NOT None (the regression)", lim is not None,
      f"lim={lim}")
check("deep stop: limit one spread below trigger", lim == 0.65,
      f"lim={lim} (trigger 0.75 - spread 0.10)")

trig, lim, note = w._stop_prices(sym, 2.05)          # desired inside the band
check("in-band stop pushed below bid-spread", trig == 1.90, f"trig={trig}")
check("in-band: limit one spread below the pushed trigger", lim == 1.80,
      f"lim={lim}")
check("in-band: election-risk note explains the push", "bid" in note, note[:60])

trig, lim, note = w._stop_prices(sym, 1.90)          # desired exactly at ceiling
check("stop at the ceiling passes through", trig == 1.90, f"trig={trig}")

w2 = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w2.entries[sym] = 1.00
trig, lim, note = w2._stop_prices(sym, 0.75)         # no quote ever arrived
check("no quote: trigger = desired (never move a risk limit on no data)",
      trig == 0.75, f"trig={trig}")
check("no quote: no limit -> stop-market fallback", lim is None)

w2.latest[sym] = {"bid": 2.10, "ask": 2.00, "seq": 1}    # crossed quote injected
trig, lim, note = w2._stop_prices(sym, 0.75)
check("crossed quote treated as unusable", trig == 0.75 and lim is None,
      f"trig={trig} lim={lim}")

w2.put_quote(sym, 0.0, 0.0, None, "rest")                # zeroed quote
trig, lim, note = w2._stop_prices(sym, 0.75)
check("zero quote treated as unusable", trig == 0.75 and lim is None)

# Penny option: both floors collide at $0.01 and must not invert.
w3 = mkwatcher(positions={sym: FakePosition(sym, 5, 0.10)})
w3.entries[sym] = 0.10
w3.put_quote(sym, 0.05, 0.15, None, "ws")
trig, lim, note = w3._stop_prices(sym, 0.03)
check("penny option: trigger floored at $0.01", trig == 0.01, f"trig={trig}")
check("penny option: limit <= trigger even at the floor", lim is None or lim <= trig,
      f"trig={trig} lim={lim}")

# Property sweep: over a grid of quotes and desired stops, the invariants that
# make the ratchet and the order legal must hold without exception.
bad = []
quotes = [(2.00, 2.10), (0.98, 1.02), (0.05, 0.15), (4.90, 5.40), (1.00, 1.00),
          (0.50, 0.52), (3.00, 3.20)]
desireds = [0.01, 0.03, 0.10, 0.50, 0.75, 0.94, 1.00, 1.50, 1.90, 2.00,
            2.05, 3.10, 5.00]
for (b, a) in quotes:
    wq = mkwatcher(positions={sym: FakePosition(sym, 1, 1.00)})
    wq.entries[sym] = 1.00
    wq.put_quote(sym, b, a, None, "ws")
    prev_trig = None
    for d in sorted(desireds):
        tg, lm, _ = wq._stop_prices(sym, d)
        if tg > d + 1e-9 and d >= 0.01:
            bad.append(f"trigger {tg} ABOVE desired {d} at quote {b}/{a}")
        if lm is not None and lm > tg + 1e-9:
            bad.append(f"limit {lm} above trigger {tg} at quote {b}/{a} d={d}")
        if lm is not None and lm < 0.01 - 1e-9:
            bad.append(f"limit {lm} below $0.01 at quote {b}/{a} d={d}")
        if prev_trig is not None and tg < prev_trig - 1e-9:
            bad.append(f"trigger fell {prev_trig}->{tg} as desired rose to {d} "
                       f"at quote {b}/{a}")
        prev_trig = tg
    usable = b > 0 and a > 0 and a >= b
    tg, lm, _ = wq._stop_prices(sym, 0.75)
    if usable and a > b and lm is None and tg > 0.011:
        bad.append(f"usable quote {b}/{a} produced no limit (stop-market leak)")
for m in bad[:8]:
    print("        " + m)
check(f"91-point sweep: trigger<=desired, limit<=trigger, limit>=$0.01, "
      f"monotone, no stop-market leak", not bad, f"{len(bad)} violations")

print()
print("=" * 74)
print("12. ensure_broker_stop — stop-limit resting orders, end to end")
print("=" * 74)
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.put_quote(sym, 0.98, 1.02, None, "ws")
w.ensure_broker_stop(sym)
kinds = [c[0] for c in w.trading.calls]
check("usable quote -> a STOP-LIMIT rests, not a stop-market",
      "submit-stoplimit" in kinds, str(kinds))
o = w.trading.orders[-1]
desired0 = R.broker_stop_price(1.00, None)
check("resting trigger == broker_stop_price (deep stop untouched)",
      abs(o.stop_price - desired0) < 1e-9,
      f"stop={o.stop_price} desired={desired0}")
check("resting limit == trigger - spread",
      abs(o.limit_price - round(o.stop_price - 0.04, 2)) < 1e-9,
      f"limit={o.limit_price}")
check("stop_px bookkeeping records the trigger",
      abs(w.stop_px[sym] - o.stop_price) < 1e-9)

first_px = w.stop_px[sym]
w.peaks[sym] = 0.60                                    # ran to +60% -> trail arms
w.ensure_broker_stop(sym)
check("ratchet raises the resting stop-limit", w.stop_px[sym] > first_px,
      f"${first_px:.2f} -> ${w.stop_px[sym]:.2f}")
check("old order cancelled before the new one rests",
      len(w.trading.cancelled) == 1)
o2 = w.trading.orders[-1]
check("raised order still carries a limit", getattr(o2, "limit_price", None)
      is not None, f"limit={getattr(o2, 'limit_price', None)}")
check("raised limit still one spread below raised trigger",
      abs(o2.limit_price - round(o2.stop_price - 0.04, 2)) < 1e-9)

raised_px = w.stop_px[sym]
w.peaks[sym] = 0.20                                    # give-back
w.ensure_broker_stop(sym)
check("falling peak does not lower the stop-limit", w.stop_px[sym] == raised_px)

# Spread widens after placement: the safety ceiling drops, want < have, and the
# upward-only ratchet must hold the existing (higher) stop rather than replace.
n_orders = len(w.trading.orders)
w.put_quote(sym, 0.80, 1.30, None, "ws")               # ugly wide quote
w.ensure_broker_stop(sym)
check("widening spread cannot pull the resting stop back down",
      len(w.trading.orders) == n_orders and w.stop_px[sym] == raised_px,
      f"px=${w.stop_px[sym]:.2f}")

# Broker rejects the stop-limit: the position must not be left naked.
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)},
              reject_stoplimit=True)
w.entries[sym], w.qtys[sym] = 1.00, 5
w.put_quote(sym, 0.98, 1.02, None, "ws")
w.ensure_broker_stop(sym)
kinds = [c[0] for c in w.trading.calls]
check("rejected stop-limit retries as a plain stop",
      "reject-stoplimit" in kinds and "submit-stop" in kinds, str(kinds))
check("fallback stop is tracked so cancel-before-sell still works",
      w.stops.get(sym) is not None and w.stop_px.get(sym) is not None,
      f"id={w.stops.get(sym)}")
check("fallback rests at the same safe trigger",
      abs(w.stop_px[sym] - w.trading.orders[-1].stop_price) < 1e-9)
check("fallback order has no limit (it IS the stop-market)",
      getattr(w.trading.orders[-1], "limit_price", None) is None)

# No quote at all -> plain stop-market (documented fallback), not a crash.
w = mkwatcher(positions={sym: FakePosition(sym, 5, 1.00)})
w.entries[sym], w.qtys[sym] = 1.00, 5
w.ensure_broker_stop(sym)
kinds = [c[0] for c in w.trading.calls]
check("no quote -> plain stop-market rests", "submit-stop" in kinds and
      "submit-stoplimit" not in kinds, str(kinds))

print()
print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
print("=" * 74)
sys.exit(1 if FAIL else 0)
