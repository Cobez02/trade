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
import os, sys, time, json, math, threading, datetime as dt
from collections import defaultdict

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (MarketOrderRequest, StopOrderRequest,
                                     StopLimitOrderRequest, GetOrdersRequest)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest
from alpaca.data.enums import OptionsFeed

try:
    from alpaca.data.live.option import OptionDataStream
except Exception:                                   # older alpaca-py
    OptionDataStream = None

import exitrules as R
import execution as X

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
    #
    # TWO CHANGES, both from the same finding about how Alpaca elects sell-stops.
    #
    # Alpaca's documented condition is: "Your sell stop order will only elect if
    # there is a trade on the consolidated tape at or lower than your stop
    # price" — and on election it becomes a MARKET order. Note the asymmetry:
    # the "not outside of the NBBO" qualifier appears only in the BUY-stop
    # condition. A sell stop has no such protection.
    #
    #   (a) THE TRIGGER WAS INSIDE THE NOISE BAND. Prints alternate between bid
    #       and ask continuously, so the lowest entirely-non-adverse print given
    #       any quote is the BID. A stop at or above the bid is elected by the
    #       next seller-initiated print with the mid unmoved — the spread alone
    #       fires it. `execution.safe_stop_price` pushes the trigger a full
    #       quoted spread below the bid, which is the minimum that survives a
    #       complex-order leg printing through. It is guaranteed never to move a
    #       stop UP toward the band and is monotone in the desired stop, so the
    #       ratchet below is unaffected.
    #
    #   (b) A STOP-MARKET HANDS THE BOOK A BLANK CHEQUE. An option's inside
    #       quote can be a 1-lot; a market order in that book has no price
    #       protection whatsoever, and an elected stop becomes exactly that.
    #       These are now STOP-LIMIT orders with the limit one full spread below
    #       the trigger. In the normal case the fill is identical. In the
    #       pathological case — the one this whole file exists for — the limit
    #       refuses instead of printing into an empty book, and the watcher's own
    #       tick-by-tick exit (which sees the bid) is still there to work the
    #       position.
    #
    # The trade-off is real and is stated rather than hidden: a stop-limit can go
    # unfilled in a genuine gap. That is why this is the FLOOR and not the
    # primary exit. If the quote is unusable we cannot measure the noise band at
    # all, and there `safe_stop_price` leaves the trigger alone and we fall back
    # to a stop-market — with no quote, an unprotected fill beats no stop.
    def _stop_prices(self, sym: str, desired: float):
        """(trigger, limit_or_None, note) for the resting stop on `sym`.

        The limit is one full quoted spread below the TRIGGER (note 07 §E.5:
        "at least one full quoted spread beyond the trigger"), not below the
        bid. The first version of this method priced it off the bid via
        marketable_limit, which inverted the protection: a stop deep below the
        market — the normal state of a fresh hard-stop — got a limit ABOVE its
        trigger, failed the sanity check, and silently fell back to the
        stop-market this rework exists to eliminate. Only a stop that had
        already ratcheted up near the market kept its limit. By election time
        the market has, by definition, traded down to the trigger, so the
        placement-time bid is stale anyway; the spread is the only part of the
        quote worth carrying forward as a noise-band estimate."""
        with self.qlock:
            q = dict(self.latest.get(sym) or {})
        bid, ask = q.get("bid"), q.get("ask")
        trigger = X.safe_stop_price(bid, ask, desired)
        risk = X.stop_election_risk(bid, ask, trigger)
        limit = None
        quote = X._quote(bid, ask)          # execution's own usability test
        if quote is not None:
            b, a, _mid = quote
            raw = trigger - (a - b)
            # Round DOWN to a tick: §E.5 says "at least" one spread, and a
            # lower sell limit is strictly more fillable.
            t = X.tick_size(raw)
            limit = max(round(math.floor(raw / t + 1e-9) * t, 4), 0.01)
            if limit > trigger:             # both floors are $0.01, so this
                limit = None                # is unreachable — belt+braces
        return trigger, limit, risk.get("note", "")

    def ensure_broker_stop(self, sym: str):
        """Keep a resting STOP under the position, ratcheting it up with the peak.

        This is the part that still works when this process is dead. It is set
        deliberately BELOW the watcher's own trigger so that under normal
        operation the watcher exits first and this never fires."""
        entry = self.entries.get(sym)
        if entry is None or sym in self.inflight:
            return
        desired = R.broker_stop_price(entry, self.peaks.get(sym))
        want, limit, note = self._stop_prices(sym, desired)
        have = self.stop_px.get(sym)
        if have is not None and want <= have + 0.004:
            return                                  # only ever ratchet upward
        old = self.stops.get(sym)
        kind = "stop-limit" if limit is not None else "stop-market"
        if DRY_RUN:
            self.stops[sym] = f"dry-{sym}"; self.stop_px[sym] = want
            lim = f" limit ${limit:.2f}" if limit is not None else ""
            log(f"  [dry] would rest {kind} {sym} @ ${want:.2f}{lim} "
                f"(wanted ${desired:.2f}) — {note}")
            return
        try:
            if old:
                try:
                    self.trading.cancel_order_by_id(old)
                except Exception:
                    pass
                time.sleep(0.3)
            qty = self.qtys.get(sym, 1)
            if limit is not None:
                req = StopLimitOrderRequest(
                    symbol=sym, qty=qty, side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    stop_price=want, limit_price=limit)
            else:
                req = StopOrderRequest(
                    symbol=sym, qty=qty, side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY, stop_price=want)
            o = self.trading.submit_order(req)
            self.stops[sym] = str(o.id); self.stop_px[sym] = want
            lim = f" limit ${limit:.2f}" if limit is not None else ""
            log(f"  broker {kind} {sym} @ ${want:.2f}{lim}"
                f"{' (raised)' if have else ''}"
                f"{'' if want >= desired - 0.004 else f' [pushed down from ${desired:.2f}: {note[:70]}]'}")
        except Exception as e:
            # A rejected stop-limit must not leave the position naked. Retry once
            # as a plain stop: an unprotected fill still beats no floor at all.
            if limit is not None:
                try:
                    o = self.trading.submit_order(StopOrderRequest(
                        symbol=sym, qty=self.qtys.get(sym, 1), side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY, stop_price=want))
                    self.stops[sym] = str(o.id); self.stop_px[sym] = want
                    log(f"  broker stop-limit rejected ({str(e)[:60]}) — "
                        f"fell back to stop-market {sym} @ ${want:.2f}")
                    return
                except Exception as e2:
                    e = e2
            log(f"  broker stop FAILED {sym}: {str(e)[:90]}")

    def adopt_broker_stops(self, symbols):
        """Adopt resting SELL stops that this process did not place.

        The watcher runs as two consecutive GitHub Actions jobs (a job caps at
        6h and the session is 6.5), and the hourly cron arms disaster floors of
        its own. So at startup the book usually already HAS resting stops —
        placed by the previous job or by main.py — and this process's in-memory
        maps know nothing about them. Without adoption that ignorance is fatal,
        not cosmetic: has_open_sell() reads the unknown stop as "a sell is
        already working" and exit_position() refuses to sell — INCLUDING THE
        19:45 FLATTEN, which is exactly how positions get carried overnight —
        while ensure_broker_stop() tries to rest a second stop on contracts the
        first already reserves, which the broker rejects.

        Adopting means tracking the existing order as ours, so the upward
        ratchet and cancel-before-sell operate on it. If a symbol somehow has
        several resting stops, keep the HIGHEST trigger (the most ratcheted
        state) and cancel the rest — duplicates double-reserve the contracts."""
        for sym in symbols:
            if sym in self.stops:
                continue
            try:
                orders = self.trading.get_orders(GetOrdersRequest(
                    status=QueryOrderStatus.OPEN, symbols=[sym], limit=20))
            except Exception as e:
                log(f"  stop adoption failed {sym}: {str(e)[:70]}")
                continue
            found = []
            for o in orders:
                if "SELL" not in str(getattr(o, "side", "")).upper():
                    continue
                sp = getattr(o, "stop_price", None)
                if sp is None:
                    continue
                try:
                    found.append((float(sp), str(o.id)))
                except (TypeError, ValueError):
                    continue
            if not found:
                continue
            found.sort(reverse=True)
            px, oid = found[0]
            self.stops[sym] = oid
            self.stop_px[sym] = px
            log(f"  adopted resting stop {sym} @ ${px:.2f} (order {oid[:8]})")
            for extra_px, extra_id in found[1:]:
                try:
                    self.trading.cancel_order_by_id(extra_id)
                    log(f"  cancelled duplicate stop {sym} @ ${extra_px:.2f}")
                except Exception:
                    pass

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
            # Every SKIP below must release the in-flight claim. An early
            # return here is NOT a completed exit, and evaluate() refuses any
            # symbol in `inflight` — so keeping the claim would convert a
            # transient condition (a working sell that later cancels, a network
            # blip that made the position read as gone) into a position this
            # watcher permanently refuses to manage. The claim is retained only
            # on a successful submit, where refresh_positions() clears it once
            # the position is confirmed gone.
            if self.has_open_sell(sym):
                log(f"  SKIP {sym}: a sell is already working")
                self.inflight.discard(sym); return
            try:
                live = self.trading.get_open_position(sym)
                qty = abs(int(float(live.qty)))
            except Exception:
                log(f"  SKIP {sym}: position already gone")
                self.inflight.discard(sym); return
            if qty <= 0:
                self.inflight.discard(sym); return
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
        # Adopt BEFORE ensuring: the previous watcher job or the hourly cron
        # has usually stopped these positions already, and placing over an
        # unknown resting stop is a rejection at best and a frozen flatten at
        # worst (see adopt_broker_stops).
        self.adopt_broker_stops(symbols)
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
                # Adoption runs during flatten too — cancel-before-sell needs
                # the resting stop's id even (especially) at the bell.
                self.adopt_broker_stops(symbols)
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
