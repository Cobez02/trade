"""
Daily autonomous run loop for the SPX-Beater options bot.

Each scheduled run:
  1. Connect to Alpaca paper account (source of truth for cash/positions).
  2. Mark-to-market and manage exits (take-profit / stop / time-stop).
  3. Scan all four sleeves for fresh signals.
  4. Size conservatively and place LONG option trades within budget.
  5. Update durable state + benchmark vs S&P (SPY), then build the report.

Reads credentials from env: ALPACA_API_KEY, ALPACA_SECRET_KEY.
"""
from __future__ import annotations
import os, re, sys, json, math, datetime as dt

import engine
from engine import (
    Broker, load_state, save_state,
    SLEEVES, SLEEVE_ALLOCATION, MAX_PREMIUM_PER_TRADE, MAX_OPEN_PER_SLEEVE,
    TAKE_PROFIT_PCT, STOP_LOSS_PCT, TIME_STOP_DTE, BENCHMARK_SYMBOL, START_EQUITY,
    MAX_SPREAD_PCT,
)
from strategies import all_signals
import learn
import exitrules

OCC_RE = re.compile(r'^([A-Z]+)(\d{6})([CP])(\d{8})$')

# --- end-of-day flat rule ----------------------------------------------------
# Connor's requirement: carry NOTHING overnight. Two cooperating limits:
#   NO_NEW_ENTRY_MIN — stop opening once the remaining session is too short for a
#     thesis to beat its own round-trip spread (entries may cost up to
#     MAX_SPREAD_PCT going in and the same coming out).
#   EOD_FLATTEN_MIN  — force-close everything still open inside this window.
# Both are minutes-to-close, read from Alpaca's own clock rather than a hardcoded
# 20:00 UTC, so early closes (July 3rd, Christmas Eve, etc.) flatten correctly
# instead of leaving the book open through a 13:00 ET bell.
EOD_FLATTEN_MIN   = int(os.environ.get("SPXBOT_EOD_FLATTEN_MIN", "15"))
NO_NEW_ENTRY_MIN  = int(os.environ.get("SPXBOT_NO_NEW_ENTRY_MIN", "90"))


def minutes_to_close(broker: Broker):
    """Minutes left in the current session, or None if it can't be determined.

    Both timestamps come off the SAME clock object, so a skewed container clock
    cannot make the bot think it has more (or less) runway than it really has.
    Returns None on failure, and every caller treats None as 'do not act' —
    guessing here would either strand positions overnight or dump the book at noon.
    """
    try:
        ck = broker.clock()
        if not ck.is_open:
            return None
        return (ck.next_close - ck.timestamp).total_seconds() / 60.0
    except Exception:
        return None

def parse_occ(sym: str):
    m = OCC_RE.match(sym)
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    try:
        exp = dt.date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError:
        return None
    return {"underlying": root, "expiration": exp.isoformat(),
            "type": "call" if cp == "C" else "put", "strike": int(strike) / 1000.0}

def today() -> str:
    return dt.date.today().isoformat()

def dte(expiration_iso: str) -> int:
    try:
        e = dt.date.fromisoformat(expiration_iso)
        return (e - dt.date.today()).days
    except Exception:
        return 999


# --- feature fingerprint <-> client_order_id (makes learning stateless) ------
def build_tag(sleeve: str, feat: dict) -> str:
    d = "b" if feat.get("direction") == "bull" else "e"
    rsi = feat.get("rsi"); rsi = str(int(rsi)) if isinstance(rsi, (int, float)) else "na"
    dtev = feat.get("dte"); dtev = str(int(dtev)) if isinstance(dtev, (int, float)) else "na"
    tu = feat.get("trend_up"); dirn = feat.get("direction")
    aligned = (dirn == "bull" and tu is True) or (dirn == "bear" and tu is False)
    tr = "1" if aligned else "0"
    sc = int(round((feat.get("signal_score") or 0) * 100))
    # a negative score would emit a stray "-" and corrupt the hyphen split
    scv = f"n{abs(sc)}" if sc < 0 else str(sc)
    mr = feat.get("macd_rising")
    m = "1" if mr is True else ("0" if mr is False else "na")
    sp = feat.get("spread_pct")
    spv = str(int(round(sp * 100))) if isinstance(sp, (int, float)) else "na"
    return f"SPXB-{sleeve}-{d}-{rsi}-{dtev}-{tr}-{scv}-{m}-{spv}"

def parse_coid(coid: str) -> dict:
    if not coid or not coid.startswith("SPXB-"):
        return {}
    p = coid.split("-")
    if len(p) < 7:
        return {}
    sleeve, d, rsi, dtev, tr, sc = p[1], p[2], p[3], p[4], p[5], p[6]
    direction = "bull" if d == "b" else "bear"
    aligned = (tr == "1")
    def num(x):
        try:
            if isinstance(x, str) and x.startswith("n"):
                return -float(x[1:])
            return float(x)
        except Exception: return None
    # v2 tags carry MACD + entry spread: SPXB-slv-d-rsi-dte-tr-sc-macd-spread-<epoch>
    # v1 tags stop at <sc> and leave both as None (bucketed "na", not learned from).
    macd, spread = None, None
    if len(p) >= 10:
        mv, spv = p[7], p[8]
        if mv in ("0", "1"):
            macd = (mv == "1")
        sv = num(spv)
        if sv is not None:
            spread = sv / 100
    return {"sleeve": sleeve, "direction": direction,
            "type": "call" if d == "b" else "put",
            "rsi": num(rsi),
            "dte": (int(float(dtev)) if num(dtev) is not None else None),
            "trend_up": (aligned == (direction == "bull")),
            "macd_rising": macd,
            "spread_pct": spread,
            "signal_score": (num(sc) / 100 if num(sc) is not None else 0)}

def rebuild_from_alpaca(broker: Broker):
    """Reconstruct the learning journal + open-position features from Alpaca's own
    filled-order history. This is what makes the whole system survive with NO
    external state store: every entry's features live in its client_order_id."""
    orders = broker.closed_orders()
    buys, sells = {}, {}
    for o in orders:  # newest-first
        try:
            filled = float(o.filled_qty or 0) > 0
        except Exception:
            filled = False
        if not filled:
            continue
        sym, side = o.symbol, str(o.side).upper()
        # Orders arrive newest-first, so the FIRST one seen is the newest. An
        # unconditional assignment would leave the OLDEST buy in place, which on a
        # re-entered contract would pair an old entry price with a new exit and
        # book a fabricated P&L into the learning journal.
        if "BUY" in side:
            if sym not in buys:
                buys[sym] = o
        elif "SELL" in side and sym not in sells:
            sells[sym] = o
    journal, pos_feat = [], {}
    for sym, b in buys.items():
        feat = parse_coid(getattr(b, "client_order_id", "") or "")
        pos_feat[sym] = feat
        if sym in sells:
            # If the newest buy came AFTER the newest sell, the contract was
            # re-entered and is open right now -- there is no settled round trip
            # to learn from yet. Journaling it would invent an exit.
            try:
                if str(getattr(b, "filled_at", "") or "") > str(getattr(sells[sym], "filled_at", "") or ""):
                    continue
            except Exception:
                pass
            try:
                ep = float(b.filled_avg_price); xp = float(sells[sym].filled_avg_price)
                q = float(b.filled_qty or 1)
                if ep <= 0:
                    continue
                info = parse_occ(sym) or {}
                journal.append({
                    "symbol": sym, "sleeve": feat.get("sleeve", "unknown"),
                    "underlying": info.get("underlying"), "type": info.get("type"),
                    "strike": info.get("strike"), "expiration": info.get("expiration"),
                    "pnl": round((xp - ep) * q * 100, 2), "pnl_pct": round((xp - ep) / ep, 4),
                    "exit_reason": "closed", "closed_on": str(getattr(sells[sym], "filled_at", "") or "")[:10],
                    "features": feat, "buckets": __import__("learn").feature_keys(feat),
                })
            except Exception:
                pass
    return journal, pos_feat


# ---------------------------------------------------------------------------
def manage_exits(broker: Broker, state: dict, notes: list, sleeve_map: dict, pos_feat: dict):
    """Reconcile against Alpaca positions; apply exit rules; realize closed P&L.
    Only runs while the market is open — closing orders are rejected otherwise."""
    try:
        if not broker.clock().is_open:
            return
    except Exception:
        pass
    mins = minutes_to_close(broker)
    flatten = mins is not None and mins <= EOD_FLATTEN_MIN
    if flatten:
        notes.append(f"EOD FLATTEN — {mins:.0f} min to close, closing every open position.")
    live = {p.symbol: p for p in broker.positions()}
    # 1) close positions per rules
    for sym, pos in list(live.items()):
        info = parse_occ(sym) or {}
        meta = state["positions"].get(sym, {})
        sleeve = meta.get("sleeve") or sleeve_map.get(sym, "unknown")
        try:
            plpc = float(pos.unrealized_plpc)          # Alpaca's own unrealized %
        except Exception:
            plpc = 0.0
        d = dte(meta.get("expiration") or info.get("expiration", ""))
        reason = None
        if plpc >= TAKE_PROFIT_PCT:
            reason = f"take-profit {plpc:+.0%}"
        elif plpc <= STOP_LOSS_PCT:
            reason = f"stop-loss {plpc:+.0%}"
        elif d <= TIME_STOP_DTE:
            reason = f"time-stop ({d} DTE)"
        elif flatten:
            # Last in the chain on purpose. If take-profit or a stop would have
            # fired anyway, record THAT as the cause — the learner's lessons are
            # only as good as the attribution, and labelling a genuine +45%
            # take-profit as "eod-flatten" would hide the signal that worked.
            # No spread check here: the whole point is to end the day flat, so a
            # wide book gets closed at whatever it costs rather than carried.
            reason = f"eod-flatten {plpc:+.0%}"
        if reason:
            try:
                qty = abs(int(float(pos.qty)))
                # Clear any resting protective stop FIRST. It reserves the
                # contracts, so a close placed alongside it is rejected for
                # insufficient quantity — and if both did fill we would not be
                # flat, we would be short the contract.
                for o in broker.open_sell_orders(sym):
                    try:
                        broker.cancel(o.id)
                    except Exception:
                        pass
                broker.sell_to_close(sym, qty)
                realized = float(pos.unrealized_pl)
                feat = meta.get("features") or pos_feat.get(sym, {})
                closed = {
                    "symbol": sym, "sleeve": sleeve,
                    "underlying": info.get("underlying"), "type": info.get("type"),
                    "strike": info.get("strike"), "expiration": info.get("expiration"),
                    "qty": qty, "entry": meta.get("entry_price"),
                    "exit_reason": reason, "pnl": round(realized, 2),
                    "pnl_pct": round(plpc, 4), "closed_on": today(),
                    "thesis": meta.get("thesis", ""), "features": feat,
                }
                state["closed"].append(closed)
                learn.record_lesson(state, closed)      # <-- feed the learner
                state["positions"].pop(sym, None)
                # mark WHAT preceded the outcome, good or bad
                why = (f"RSI {feat.get('rsi','?')}, {learn.align_bucket(feat)}, "
                       f"DTE {feat.get('dte','?')}, score {feat.get('signal_score','?')}")
                verdict = "WIN" if realized > 0 else "LOSS"
                notes.append(f"CLOSED {sym} [{sleeve}] {verdict} {reason}, "
                             f"P&L ${realized:+.0f} — entry was: {why}")
            except Exception as e:
                notes.append(f"close-failed {sym}: {e}")
    # 2) reconcile state entries Alpaca no longer holds (expired/assigned/closed elsewhere)
    for sym in list(state["positions"].keys()):
        if sym not in live:
            meta = state["positions"].pop(sym)
            info = parse_occ(sym) or {}
            state["closed"].append({
                "symbol": sym, "sleeve": meta.get("sleeve", "unknown"),
                "underlying": info.get("underlying"), "type": info.get("type"),
                "strike": info.get("strike"), "expiration": info.get("expiration"),
                "qty": meta.get("qty"), "entry": meta.get("entry_price"),
                "exit_reason": "expired/closed", "pnl": None, "pnl_pct": None,
                "closed_on": today(), "thesis": meta.get("thesis", ""),
            })
            notes.append(f"RECONCILED {sym} [{meta.get('sleeve')}] — no longer held")


def ensure_broker_stops(broker: Broker, state: dict, notes: list):
    """Make sure every open position has a resting stop underneath it.

    The watcher places these too, but the watcher is a process that can die and
    a container that can be reclaimed. Doing it here as well means the floor is
    established by whatever runs — and if only the hourly cron ever runs, every
    position still has a stop Alpaca evaluates continuously rather than one this
    code re-checks sixty minutes from now.

    Deliberately does NOT ratchet with a peak: this path sees the book once an
    hour and has no idea what happened in between. Trailing is the watcher's
    job; this is the disaster floor.
    """
    try:
        if not broker.clock().is_open:
            return
    except Exception:
        return
    mins = minutes_to_close(broker)
    if mins is not None and mins <= EOD_FLATTEN_MIN:
        return                      # the book is being flattened; don't re-arm
    try:
        covered = {o.symbol for o in broker.open_sell_orders()}
    except Exception:
        return
    for p in broker.positions():
        if p.symbol in covered:
            continue
        try:
            entry = float(p.avg_entry_price or 0)
            qty = abs(int(float(p.qty)))
            if entry <= 0 or qty <= 0:
                continue
            px = exitrules.broker_stop_price(entry, None)
            broker.rest_stop(p.symbol, qty, px)
            notes.append(f"STOP ARMED {p.symbol} x{qty} @ ${px:.2f} "
                         f"({exitrules.HARD_STOP_PCT:+.0%} from ${entry:.2f}) — "
                         f"holds whether or not anything of ours is running")
        except Exception as e:
            notes.append(f"stop-arm failed {p.symbol}: {str(e)[:70]}")


def sleeve_open_count(state: dict, sleeve: str) -> int:
    return sum(1 for m in state["positions"].values() if m.get("sleeve") == sleeve)

def sleeve_open_cost(state: dict, sleeve: str) -> float:
    return sum(m.get("entry_cost", 0) for m in state["positions"].values()
              if m.get("sleeve") == sleeve)

def already_positioned(state: dict, sleeve: str, underlying: str, direction: str) -> bool:
    want = "call" if direction == "bull" else "put"
    for m in state["positions"].values():
        if m.get("sleeve") == sleeve and m.get("underlying") == underlying and m.get("type") == want:
            return True
    return False


def open_new_trades(broker: Broker, state: dict, signals: dict, api_key, secret, notes: list, pending: set):
    acct = broker.account()
    try:
        cash = float(acct.cash)
    except Exception:
        cash = 0.0
    market_open = False
    try:
        market_open = bool(broker.clock().is_open)
    except Exception:
        pass
    # always record the latest signals for the dashboard
    for sleeve in SLEEVES:
        state.setdefault("last_signals", {})[sleeve] = [
            {"underlying": s["underlying"], "direction": s["direction"], "thesis": s["thesis"]}
            for s in signals.get(sleeve, [])
        ]
    if not market_open:
        notes.append("Market closed — signals recorded, no orders placed this run.")
        return

    # Nothing is carried overnight, so anything opened now must be closed by the
    # bell. Inside NO_NEW_ENTRY_MIN there isn't enough session left for a move to
    # clear the round-trip spread, and the trade would be a near-guaranteed loss
    # taken purely to satisfy the scan. Record the signals, place nothing.
    # Dedicated close-the-book runs (see trade.yml) manage exits and stop there.
    # Without this, the extra runs scheduled to catch an early-close bell would
    # also fire a full entry scan on ordinary days, churning trades at 16:45 UTC
    # for no reason other than that the cron existed.
    if os.environ.get("SPXBOT_FLATTEN_ONLY") == "1":
        notes.append("Flatten-only run — exits managed, entry scan skipped.")
        return

    mins_left = minutes_to_close(broker)
    if mins_left is not None and mins_left <= NO_NEW_ENTRY_MIN:
        notes.append(f"NO NEW ENTRIES — {mins_left:.0f} min to close "
                     f"(cutoff {NO_NEW_ENTRY_MIN}); positions will be flattened at "
                     f"{EOD_FLATTEN_MIN} min.")
        return

    # apply learning: process sleeves best-weight first; skip paused sleeves
    ordered = sorted(SLEEVES, key=lambda s: learn.sleeve_weight(state, s), reverse=True)
    for sleeve in ordered:
        weight = learn.sleeve_weight(state, sleeve)
        if weight <= 0.0:
            notes.append(f"SKIP sleeve '{sleeve}' — paused by learner (weight 0).")
            continue
        for sig in signals.get(sleeve, []):
            if sleeve_open_count(state, sleeve) >= MAX_OPEN_PER_SLEEVE:
                break
            und, direction = sig["underlying"], sig["direction"]
            if already_positioned(state, sleeve, und, direction):
                continue
            if (sleeve, und) in pending:      # a working order already exists for this name
                continue
            budget_left = SLEEVE_ALLOCATION - sleeve_open_cost(state, sleeve)
            per_trade = min(MAX_PREMIUM_PER_TRADE, budget_left)
            if per_trade < 30:
                continue
            spot = broker.stock_price(und)
            if not spot:
                continue
            contract = broker.find_contract(und, direction, spot)
            if not contract:
                notes.append(f"no liquid contract for {und} [{sleeve}]")
                continue
            q = broker.option_quote(contract.symbol)
            ask = (q["ask"] if q else broker.option_ask(contract.symbol))  # price at the ask so it fills
            if not ask or ask <= 0:
                continue
            # liquidity gate: skip contracts whose bid/ask spread would sink the trade on entry
            if q and q.get("spread_pct") is not None and q["spread_pct"] > MAX_SPREAD_PCT:
                notes.append(f"SKIP {und} {direction} [{sleeve}] — bid/ask spread "
                             f"{q['spread_pct']*100:.0f}% > {MAX_SPREAD_PCT*100:.0f}% (illiquid)")
                continue
            info = parse_occ(contract.symbol) or {}
            # snapshot the FEATURES present at entry (this is what the learner reads)
            ind = broker.indicators(und)
            strike = info.get("strike") or float(contract.strike_price)
            feat = {
                "sleeve": sleeve, "direction": direction, "signal_score": round(sig.get("score", 0), 3),
                "rsi": ind.get("rsi"), "macd_hist": ind.get("macd_hist"),
                "macd_rising": ind.get("macd_rising"), "trend_up": ind.get("trend_up"),
                "dte": (dt.date.fromisoformat(info["expiration"]) - dt.date.today()).days
                        if info.get("expiration") else None,
                "moneyness_pct": round(strike / spot - 1, 4),
                "spread_pct": (q or {}).get("spread_pct"), "type": info.get("type"),
            }
            # apply learned GATES: block feature buckets that reliably lose
            gate = learn.is_gated(state, feat)
            if gate:
                notes.append(f"GATED {und} {direction} [{sleeve}] — matches losing bucket {gate}")
                continue
            # weight-based sizing: proven sleeves may take a second contract
            qty = int(per_trade // (ask * 100))
            if weight >= 1.4 and (qty + 1) * ask * 100 <= min(budget_left, cash):
                qty += 1
            if qty < 1:
                continue
            cost = qty * ask * 100
            if cost > cash:
                continue
            try:
                tag = build_tag(sleeve, feat)   # fingerprint -> recoverable from Alpaca
                broker.buy_to_open(contract.symbol, qty, tag, limit_price=ask * 1.03)
                state["positions"][contract.symbol] = {
                    "sleeve": sleeve, "underlying": und, "direction": direction,
                    "type": info.get("type"), "strike": strike,
                    "expiration": info.get("expiration"), "qty": qty,
                    "entry_price": round(ask, 2), "entry_cost": round(cost, 2),
                    "entry_date": today(), "thesis": sig["thesis"], "spot_at_entry": spot,
                    "features": feat,
                }
                cash -= cost
                notes.append(f"OPENED {qty}x {contract.symbol} [{sleeve}] @ ${ask:.2f} "
                             f"(${cost:.0f}, w{weight}) — {sig['thesis']}")
            except Exception as e:
                notes.append(f"order-failed {und} [{sleeve}]: {e}")


def update_benchmark(broker: Broker, state: dict):
    spy = broker.stock_price(BENCHMARK_SYMBOL)
    if spy is None:
        return
    if not state.get("benchmark_start_price"):
        state["benchmark_start_price"] = spy
    # Map the $100k paper account onto a $10k experiment: the account only ever
    # buys options and never deposits/withdraws, so (equity - baseline) is pure
    # strategy P&L. bot_equity = $10k + that P&L.
    baseline = state.get("account_baseline", START_EQUITY)
    try:
        raw_equity = float(broker.account().equity)
    except Exception:
        raw_equity = baseline
    equity = START_EQUITY + (raw_equity - baseline)
    bench_equity = START_EQUITY * (spy / state["benchmark_start_price"])
    # one point per day (replace if same day)
    hist = state["equity_history"]
    if hist and hist[-1]["date"] == today():
        hist[-1] = {"date": today(), "equity": round(equity, 2),
                    "benchmark_equity": round(bench_equity, 2), "spy": spy}
    else:
        hist.append({"date": today(), "equity": round(equity, 2),
                     "benchmark_equity": round(bench_equity, 2), "spy": spy})


def run():
    api_key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret:
        print("ERROR: set ALPACA_API_KEY and ALPACA_SECRET_KEY", file=sys.stderr)
        sys.exit(2)

    broker = Broker(api_key, secret, paper=True)
    state = load_state()
    notes = []

    # env fallbacks keep a FIXED $10k baseline & benchmark even with no state store
    if not state.get("started"):
        state["started"] = os.environ.get("SPXBOT_STARTED") or today()
        bench = os.environ.get("SPXBOT_BENCH_START")
        state["benchmark_start_price"] = float(bench) if bench else broker.stock_price(BENCHMARK_SYMBOL)
        state["start_equity"] = START_EQUITY          # $10k experiment base
        base = os.environ.get("SPXBOT_BASELINE")
        if base:
            state["account_baseline"] = float(base)
        else:
            try:
                state["account_baseline"] = float(broker.account().equity)   # e.g. $100k
            except Exception:
                state["account_baseline"] = START_EQUITY
        notes.append(f"Initialized $10k experiment (account baseline "
                     f"${state['account_baseline']:.0f}), SPY {state['benchmark_start_price']}")

    # Rebuild the learning journal + open-position features from Alpaca's order
    # history — this is what lets the bot run with NO external state store.
    journal, pos_feat = rebuild_from_alpaca(broker)
    # Reconstruct OPEN positions from Alpaca every run (stateless-safe): this keeps
    # duplicate-entry guards and per-sleeve caps/budgets correct without a state file.
    state["positions"] = {}
    for p in broker.positions():
        f = pos_feat.get(p.symbol, {})
        info = parse_occ(p.symbol) or {}
        try:
            q = abs(int(float(p.qty)))
        except Exception:
            q = 1
        try:
            cost = abs(float(p.cost_basis))
        except Exception:
            cost = float(p.avg_entry_price or 0) * q * 100
        state["positions"][p.symbol] = {
            "sleeve": f.get("sleeve", "unknown"), "underlying": info.get("underlying"),
            "direction": f.get("direction") or ("bull" if info.get("type") == "call" else "bear"),
            "type": info.get("type"), "strike": info.get("strike"),
            "expiration": info.get("expiration"), "qty": q,
            "entry_price": float(p.avg_entry_price or 0), "entry_cost": round(cost, 2),
            "features": f, "entry_date": today(),
        }
    sleeve_map = {s: f.get("sleeve", "unknown") for s, f in pos_feat.items()}
    manage_exits(broker, state, notes, sleeve_map, pos_feat)
    ensure_broker_stops(broker, state, notes)
    if journal:                                    # Alpaca is authoritative for settled trades
        state["journal"] = journal
        state["closed"] = [{
            "symbol": j["symbol"], "sleeve": j["sleeve"], "underlying": j.get("underlying"),
            "type": j.get("type"), "strike": j.get("strike"), "expiration": j.get("expiration"),
            "pnl": j["pnl"], "pnl_pct": j["pnl_pct"], "exit_reason": j.get("exit_reason", "closed"),
            "closed_on": j.get("closed_on"), "thesis": "", "features": j["features"],
        } for j in journal]
    lrn = learn.learn(state)                       # recompute weights/gates/lessons
    if lrn["n_trades"]:
        notes.append(f"Learner: {lrn['n_trades']} settled trades | "
                     + " · ".join(f"{s}=w{v['weight']}" for s, v in lrn["sleeve"].items()
                                  if v["n"] > 0))
    # Sweep stale working orders. A limit priced off a quote that has since gone
    # illiquid will either sit forever or fill at a price the spread gate would
    # now reject, so pull anything whose spread has widened past the gate.
    for o in broker.open_orders():
        try:
            # BUYs only. The resting protective stops are working SELL orders,
            # and cancelling those because the spread widened would remove the
            # floor at exactly the moment it is most needed.
            if "BUY" not in str(getattr(o, "side", "")).upper():
                continue
            q = broker.option_quote(o.symbol) or {}
            sp = q.get("spread_pct")
            if sp is not None and sp > MAX_SPREAD_PCT:
                broker.cancel(o.id)
                notes.append(f"CANCELLED working order {o.symbol} qty={o.qty} — "
                             f"spread widened to {sp*100:.0f}% (> {MAX_SPREAD_PCT*100:.0f}%)")
        except Exception:
            pass

    # names that already have a WORKING (unfilled) order — don't re-submit them
    pending = set()
    for o in broker.open_orders():
        coid = getattr(o, "client_order_id", "") or ""
        info = parse_occ(o.symbol) or {}
        if coid.startswith("SPXB-") and info.get("underlying"):
            pending.add((coid.split("-")[1], info["underlying"]))
    signals = all_signals(broker, api_key, secret)
    open_new_trades(broker, state, signals, api_key, secret, notes, pending)
    # Again, because anything that just filled has no floor under it yet. The
    # call skips symbols already covered, so a second pass costs one API read.
    ensure_broker_stops(broker, state, notes)
    update_benchmark(broker, state)

    state["run_log"].append({"date": today(),
                             "time": dt.datetime.now(dt.timezone.utc).isoformat(),
                             "notes": notes})
    state["run_log"] = state["run_log"][-60:]     # keep last 60 runs
    save_state(state)

    for n in notes:
        print(n)
    return state, notes


if __name__ == "__main__":
    run()
