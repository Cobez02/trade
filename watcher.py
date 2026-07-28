"""
Continuous position watcher — the "someone is actually looking at this" layer.

The hourly cron is a scanner: it decides what to BUY. This process decides what
to SELL, and it does so on every quote update rather than once an hour. The gap
between those two cadences is not academic: a position that read -29% at one
hourly check was liquidated at -51% at the next, and the entire loss happened in
the 41 minutes when nothing was watching.

Design, in order of how much each part matters when things go wrong:

  1. A resting broker-side STOP sits on every position. Alpaca evaluates it
     continuously whether or not this process — or this whole container — is
     alive. Everything else here is an improvement on top of that floor, not a
     replacement for it. Options accept `stop` but reject `trailing_stop` and
     OCO, so the trailing behaviour has to be emulated by re-placing the stop.

  2. Quotes arrive over a websocket, so "watching" means reacting to ticks, not
     polling a clock. Three threads, deliberately separated:
        stream  — writes ticks into `latest`, touches nothing slow
        rest    — refills `latest` for any symbol the stream has gone quiet on
        main    — reads `latest`, decides, and places orders
     The socket must never be behind an HTTP round-trip, or the "continuous"
     monitoring stalls for a second every time a decision is made.

  3. Every sell is guarded three ways — an in-flight set, a check for existing
     open SELL orders, and a re-read of the live position — because the watcher,
     the resting stop and the hourly cron can all reach for the same position at
     the same moment, and selling a contract twice opens a SHORT option position.

Run:  python3 watcher.py            (runs until the closing bell, then exits)
"""
from __future__ import annotations
import os, sys, time, json, threading, datetime as dt
from collections import defaultdict

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (MarketOrderRequest, StopOrderRequest,
                                     GetOrdersRequest)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest
from alpaca.data.enums import OptionsFeed

try:
    from alpaca.data.live.option import OptionDataStream
except Exception:                                   # older alpaca-py
    OptionDataStream = None

import exitrules as R

EVAL_SECONDS      = 0.25    # how often the decision loop looks at `latest`
POLL_SECONDS      = 2.0     # REST refill cadence when the stream is quiet
STREAM_STALE_S    = 4.0     # a symbol unheard-from this long gets a REST poll
POSITION_REFRESH  = 30.0    # how often to pick up newly-opened positions
FLATTEN_REFRESH   = 5.0     # ...tightened once we are closing the book
EOD_FLATTEN_MIN   = int(os.environ.get("SPXBOT_EOD_FLATTEN_MIN", "15"))
LOG_PATH          = os.environ.get("SPXBOT_WATCH_LOG", "watcher.log")
USE_STREAM        = os.environ.get("SPXBOT_WATCH_STREAM", "1") == "1"
DRY_RUN           = os.environ.get("SPXBOT_WATCH_DRY", "0") == "1"


def log(msg: str):
    line = f"{dt.datetime.utcnow().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def dte_from_occ(sym: str):
    """Days to expiry parsed straight out of the OCC symbol.

    The watcher may be the only thing running, so it should not depend on the
    cron's journal to know a contract is about to expire.
    """
    try:
        for i, ch in enumerate(sym):
            if ch.isdigit():
                d = dt.datetime.strptime(sym[i:i + 6], "%y%m%d").date()
                return (d - dt.datetime.now(dt.timezone.utc).date()).days
    except Exception:
        pass
    return None


class Watcher:
    def __init__(self, key: str, secret: str):
        self.trading = TradingClient(key, secret, paper=True)
        self.data = OptionHistoricalDataClient(key, secret)
        self.key, self.secret = key, secret
        self.peaks: dict[str, float] = {}       # symbol -> best pnl% seen
        self.breach: dict[str, int] = defaultdict(int)   # consecutive breaches
        self.breach_since: dict[str, float] = {}         # when it started
        self.inflight: set[str] = set()         # symbols with a sell in progress
        self.entries: dict[str, float] = {}     # symbol -> entry price
        self.qtys: dict[str, int] = {}
        self.stops: dict[str, str] = {}         # symbol -> resting stop order id
        self.stop_px: dict[str, float] = {}
        self.latest: dict[str, dict] = {}       # symbol -> newest quote seen
        self.seen_seq: dict[str, int] = {}      # last seq the decision loop used
        self.lock = threading.Lock()
        self.qlock = threading.Lock()
        self.exits_done: list[dict] = []
        self.stream = None
        self.stream_alive = False
        self.subscribed: set[str] = set()
        self.tick_count = 0

    # -- market clock --------------------------------------------------------
    def minutes_to_close(self):
        try:
            c = self.trading.get_clock()
            if not c.is_open:
                return None
            return (c.next_close - c.timestamp).total_seconds() / 60.0
        except Exception:
            return None

    # -- quote intake ---------------------------------------------------------
    def put_quote(self, sym, bid, ask, ts, src):
        """Single funnel for both the socket and the REST fallback.

        Sequence numbers matter more than they look: the decision loop runs far
        faster than quotes arrive, and re-counting one cached quote three times
        would satisfy CONFIRM_TICKS with a single print — turning the noise
        guard into a rubber stamp.
        """
        try:
            bid = float(bid or 0); ask = float(ask or 0)
        except Exception:
            return
        with self.qlock:
            prev = self.latest.get(sym)
            if prev and prev["bid"] == bid and prev["ask"] == ask:
                prev["seen_at"] = time.time()       # same quote, still current
                return
            self.latest[sym] = {"bid": bid, "ask": ask, "ts": ts, "src": src,
                                "seq": (prev["seq"] + 1) if prev else 1,
                                "seen_at": time.time()}
            self.tick_count += 1

    # -- websocket ------------------------------------------------------------
    def start_stream(self):
        if not USE_STREAM or OptionDataStream is None:
            log("stream disabled — REST polling only")
            return
        try:
            self.stream = OptionDataStream(self.key, self.secret,
                                           feed=OptionsFeed.INDICATIVE)
        except Exception as e:
            log(f"stream init failed ({str(e)[:70]}) — REST polling only")
            self.stream = None
            return

        async def on_tick(q):
            # Deliberately does nothing slow. Any HTTP call here would stall
            # every other symbol's quotes behind it.
            self.put_quote(q.symbol, getattr(q, "bid_price", None),
                           getattr(q, "ask_price", None),
                           getattr(q, "timestamp", None), "ws")

        self._on_tick = on_tick

        def worker():
            try:
                self.stream_alive = True
                self.stream.run()
            except Exception as e:
                log(f"stream ended: {str(e)[:90]}")
            finally:
                self.stream_alive = False
                log("stream down — REST fallback carries the watch")

        threading.Thread(target=worker, daemon=True, name="ws").start()
        time.sleep(1.5)

    def subscribe(self, symbols):
        if not self.stream:
            return
        new = [s for s in symbols if s not in self.subscribed]
        if not new:
            return
        try:
            self.stream.subscribe_quotes(self._on_tick, *new)
            self.subscribed.update(new)
            log(f"  stream subscribed: {', '.join(new)}")
        except Exception as e:
            log(f"  subscribe failed ({str(e)[:70]}) — REST covers these")

    # -- position bookkeeping -------------------------------------------------
    def refresh_positions(self):
        try:
            pos = self.trading.get_all_positions()
        except Exception as e:
            log(f"position refresh failed: {str(e)[:80]}")
            return sorted(self.entries.keys())
        live = set()
        for p in pos:
            s = p.symbol
            live.add(s)
            self.entries[s] = float(p.avg_entry_price)
            self.qtys[s] = abs(int(float(p.qty)))
        # forget positions that are gone, so a re-entry starts with a fresh peak
        for s in list(self.entries):
            if s not in live:
                self.entries.pop(s, None); self.qtys.pop(s, None)
                self.peaks.pop(s, None); self.breach.pop(s, None)
                self.breach_since.pop(s, None)
                self.stops.pop(s, None); self.stop_px.pop(s, None)
                self.inflight.discard(s)
        return sorted(live)

    # -- resting broker-side stop --------------------------------------------
    def ensure_broker_stop(self, sym: str):
        """Keep a resting STOP under the position, ratcheting it up with the peak.

        This is the part that still works when this process is dead. It is set
        deliberately BELOW the watcher's own trigger so that under normal
        operation the watcher exits first and this never fires."""
        entry = self.entries.get(sym)
        if entry is None or sym in self.inflight:
            return
        want = R.broker_stop_price(entry, self.peaks.get(sym))
        have = self.stop_px.get(sym)
        if have is not None and want <= have + 0.004:
            return                                  # only ever ratchet upward
        old = self.stops.get(sym)
        if DRY_RUN:
            self.stops[sym] = f"dry-{sym}"; self.stop_px[sym] = want
            log(f"  [dry] would rest stop {sym} @ ${want:.2f}")
            return
        try:
            if old:
                try:
                    self.trading.cancel_order_by_id(old)
                except Exception:
                    pass
                time.sleep(0.3)
            o = self.trading.submit_order(StopOrderRequest(
                symbol=sym, qty=self.qtys.get(sym, 1), side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY, stop_price=want))
            self.stops[sym] = str(o.id); self.stop_px[sym] = want
            log(f"  broker stop {sym} @ ${want:.2f}"
                f"{' (raised)' if have else ''}")
        except Exception as e:
            log(f"  broker stop FAILED {sym}: {str(e)[:90]}")

    def cancel_broker_stop(self, sym: str):
        """Must happen BEFORE any watcher sell, or the resting stop and the
        market order both execute and we end up short the contract."""
        oid = self.stops.pop(sym, None); self.stop_px.pop(sym, None)
        if not oid:
            return
        try:
            self.trading.cancel_order_by_id(oid)
        except Exception:
            pass

    # -- selling --------------------------------------------------------------
    def has_open_sell(self, sym: str) -> bool:
        try:
            orders = self.trading.get_orders(GetOrdersRequest(
                status=QueryOrderStatus.OPEN, symbols=[sym], limit=20))
            return any("SELL" in str(o.side).upper() and
                       str(o.id) != self.stops.get(sym) for o in orders)
        except Exception:
            return False

    def exit_position(self, sym: str, reason: str, pnl_pct):
        with self.lock:
            if sym in self.inflight:
                return
            self.inflight.add(sym)
        if DRY_RUN:
            pp = f"{pnl_pct:+.1%}" if pnl_pct is not None else "n/a"
            log(f"[dry] WOULD EXIT {sym} — {reason} [{pp}]")
            self.exits_done.append({"symbol": sym, "qty": self.qtys.get(sym),
                                    "reason": reason, "pnl_pct": pnl_pct,
                                    "dry": True,
                                    "at": dt.datetime.utcnow().isoformat()[:19]})
            return
        try:
            self.cancel_broker_stop(sym)
            time.sleep(0.35)
            if self.has_open_sell(sym):
                log(f"  SKIP {sym}: a sell is already working"); return
            try:
                live = self.trading.get_open_position(sym)
                qty = abs(int(float(live.qty)))
            except Exception:
                log(f"  SKIP {sym}: position already gone"); return
            if qty <= 0:
                return
            o = self.trading.submit_order(MarketOrderRequest(
                symbol=sym, qty=qty, side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY))
            pp = f"{pnl_pct:+.1%}" if pnl_pct is not None else "n/a"
            log(f"EXIT {sym} x{qty} — {reason} [{pp}] order {str(o.id)[:8]}")
            self.exits_done.append({"symbol": sym, "qty": qty, "reason": reason,
                                    "pnl_pct": pnl_pct,
                                    "at": dt.datetime.utcnow().isoformat()[:19]})
        except Exception as e:
            log(f"  EXIT FAILED {sym}: {str(e)[:110]}")
            self.inflight.discard(sym)      # let a later tick retry
        # deliberately NOT discarding inflight on success: the position is gone
        # and refresh_positions() clears it once Alpaca confirms.

    # -- the decision, per quote ---------------------------------------------
    def evaluate(self, sym: str, flatten: bool = False):
        entry = self.entries.get(sym)
        if entry is None or sym in self.inflight:
            return
        with self.qlock:
            q = self.latest.get(sym)
            q = dict(q) if q else None
        if not q:
            # No quote has ever arrived for this contract. Everywhere else that
            # means "do nothing" — but at the bell it must not, or a dead feed
            # becomes the reason a position is carried overnight.
            if flatten:
                self.exit_position(sym, "eod-flatten (no quote)", None)
            return
        # Only a genuinely new quote may advance the confirmation counter. At
        # the bell we look anyway, because there `need` is one tick regardless.
        fresh = q["seq"] != self.seen_seq.get(sym)
        if not fresh and not flatten:
            return
        self.seen_seq[sym] = q["seq"]

        bid, ask = q["bid"], q["ask"]
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0
        quote = {"bid": bid, "ask": ask, "mid": mid,
                 "spread_pct": ((ask - bid) / mid) if mid else None}
        age = None
        if q.get("ts") is not None:
            try:
                age = (dt.datetime.now(dt.timezone.utc) - q["ts"]).total_seconds()
            except Exception:
                age = None
        if age is None:
            age = time.time() - q["seen_at"]

        d = R.decide(entry, quote, self.peaks.get(sym),
                     dte_left=dte_from_occ(sym), flatten=flatten, age_s=age)
        if d["peak_pct"] is not None:
            self.peaks[sym] = d["peak_pct"]
        if d["action"] != "exit":
            self.breach[sym] = 0
            self.breach_since.pop(sym, None)
            return

        # A single bad print must not liquidate a position. Require the breach
        # to survive both a few quotes and a little wall-clock -- except at the
        # bell, where there is no time left to be careful.
        if flatten:
            self.exit_position(sym, d["reason"], d["pnl_pct"])
            return
        if fresh:
            self.breach[sym] += 1
            self.breach_since.setdefault(sym, time.time())
        elapsed = time.time() - self.breach_since.get(sym, time.time())
        if not R.confirm_ok(self.breach[sym], elapsed,
                            d["pnl_pct"], d.get("trigger")):
            return
        threading.Thread(target=self.exit_position,
                         args=(sym, d["reason"], d["pnl_pct"]),
                         daemon=True).start()

    # -- REST refill (fallback + belt-and-braces alongside the stream) --------
    def sweep(self, symbols):
        if not symbols:
            return
        try:
            qs = self.data.get_option_latest_quote(OptionLatestQuoteRequest(
                symbol_or_symbols=list(symbols), feed=OptionsFeed.INDICATIVE))
        except Exception as e:
            log(f"quote sweep failed: {str(e)[:80]}")
            return
        for s, v in qs.items():
            self.put_quote(s, v.bid_price, v.ask_price,
                           getattr(v, "timestamp", None), "rest")

    def stale_symbols(self, symbols):
        """Symbols the socket has gone quiet on. An idle contract simply may not
        tick for a while, and 'no quote' is indistinguishable from 'stream is
        silently dead' — so REST refills either way rather than guessing."""
        now = time.time()
        out = []
        with self.qlock:
            for s in symbols:
                q = self.latest.get(s)
                if not q or now - q["seen_at"] > STREAM_STALE_S:
                    out.append(s)
        return out

    # -- main loop ------------------------------------------------------------
    def run(self):
        log(f"watcher starting{' [DRY RUN — no orders]' if DRY_RUN else ''}")
        # Check the clock BEFORE touching orders. Everything below places or
        # cancels resting stops, and doing that outside regular hours means
        # queueing DAY orders into a session nobody has looked at yet.
        mins = self.minutes_to_close()
        if mins is None:
            log("market is closed — nothing to watch, exiting")
            return
        log(f"market open, {mins:.0f} min to the bell")
        symbols = self.refresh_positions()
        log(f"watching {len(symbols)} positions: {', '.join(symbols) or '(none)'}")
        self.start_stream()
        self.subscribe(symbols)
        self.sweep(symbols)
        for s in symbols:
            self.ensure_broker_stop(s)

        last_refresh = time.time()
        last_rest = 0.0
        last_beat = 0.0
        while True:
            mins = self.minutes_to_close()
            if mins is None:
                log("market closed — watcher exiting")
                break
            flatten = mins <= EOD_FLATTEN_MIN
            now = time.time()

            gap = FLATTEN_REFRESH if flatten else POSITION_REFRESH
            if now - last_refresh > gap:
                prev = set(symbols)
                symbols = self.refresh_positions()
                for s in symbols:
                    if s not in prev:
                        log(f"  now watching new position {s} @ {self.entries[s]}")
                self.subscribe(symbols)
                if not flatten:
                    for s in symbols:
                        self.ensure_broker_stop(s)
                last_refresh = now
                if flatten and not symbols:
                    log("book is flat — watcher exiting")
                    break

            if not symbols:
                time.sleep(POLL_SECONDS); continue

            # REST refills whatever the socket has not spoken about lately.
            if now - last_rest > POLL_SECONDS:
                stale = self.stale_symbols(symbols)
                if stale:
                    self.sweep(stale)
                last_rest = now

            for s in list(symbols):
                self.evaluate(s, flatten=flatten)

            if now - last_beat > 300:
                src = "ws" if self.stream_alive else "rest"
                log(f"  [{mins:.0f}m to close] {len(symbols)} open, "
                    f"{self.tick_count} ticks, feed={src}")
                last_beat = now

            time.sleep(EVAL_SECONDS)

        log(f"watcher done — {len(self.exits_done)} exits, {self.tick_count} ticks")
        try:
            if self.stream:
                self.stream.stop()
        except Exception:
            pass
        try:
            json.dump(self.exits_done, open("watcher_exits.json", "w"), indent=2)
        except Exception:
            pass


def main():
    key = os.environ.get("ALPACA_API_KEY"); sec = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        print("ERROR: set ALPACA_API_KEY and ALPACA_SECRET_KEY", file=sys.stderr)
        sys.exit(2)
    Watcher(key, sec).run()


if __name__ == "__main__":
    main()
