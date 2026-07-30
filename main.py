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
import time as time_mod
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

import engine
from engine import (
    Broker, load_state, save_state,
    SLEEVES, ACTIVE_SLEEVES, SLEEVE_ALLOCATION, MAX_PREMIUM_PER_TRADE,
    MAX_OPEN_PER_SLEEVE, TAKE_PROFIT_PCT, STOP_LOSS_PCT, TIME_STOP_DTE,
    BENCHMARK_SYMBOL, START_EQUITY, MAX_SPREAD_PCT,
)
from strategies import all_signals, crowd_veto
import learn
import exitrules
import screens
import execution
import vol as volmod

OCC_RE = re.compile(r'^([A-Z]+)(\d{6})([CP])(\d{8})$')

def _dt_sleep(seconds: float):
    """Indirection for best_quoted's resample pause (patchable in tests)."""
    import time as _t
    _t.sleep(seconds)

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
        # Spread legs must NEVER enter the singles journal: an mleg close
        # would read as a fake single round-trip and poison the learner.
        try:
            if str(getattr(o, "order_class", "") or "").lower() == "mleg":
                continue
            if str(getattr(o, "client_order_id", "") or "").startswith("SPXS-"):
                continue
        except Exception:
            pass
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
    raw_positions = broker.positions()
    # Split out credit-spread packages BEFORE any singles rule runs. Their
    # legs must never be flattened or stop-lossed individually: market-selling
    # one leg of a spread converts a bounded position into a naked short.
    # Spreads are overnight-exempt (owner decision 2026-07-30) and managed by
    # manage_spreads(); orphan shorts (assignment debris) go to reconcile.
    spread_pkgs, spread_members = engine.detect_spreads(raw_positions)
    live = {}
    for p in raw_positions:
        if p.symbol in spread_members:
            continue
        try:
            if int(float(p.qty)) < 0:
                notes.append(f"ORPHAN SHORT LEG {p.symbol} — excluded from "
                             f"singles exits; reconcile owns it")
                continue
        except Exception:
            continue
        live[p.symbol] = p
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


def reconcile_assignments(broker: Broker, notes: list):
    """An options-only book must never silently hold stock.

    A short put that gets assigned deposits LONG STOCK into the account (a
    short call would deposit short stock). Either is outside every rule this
    bot has, so the response is immediate and dumb on purpose: flatten the
    equity at market, loudly. The orphaned long leg the assignment leaves
    behind re-enters the ordinary machinery as a single and is closed by it."""
    try:
        for p in broker.positions():
            sym = getattr(p, "symbol", "") or ""
            if parse_occ(sym):
                continue                      # an option — not our problem here
            try:
                q = int(float(p.qty))
            except Exception:
                continue
            if q == 0:
                continue
            side = OrderSide.SELL if q > 0 else OrderSide.BUY
            try:
                broker.trading.submit_order(MarketOrderRequest(
                    symbol=sym, qty=abs(q), side=side,
                    time_in_force=TimeInForce.DAY))
                notes.append(f"ASSIGNMENT REPAIR — {'sold' if q > 0 else 'covered'} "
                             f"{abs(q)} shares of {sym} at market (stock in an "
                             f"options-only book = a short leg was assigned)")
            except Exception as e:
                notes.append(f"ASSIGNMENT REPAIR FAILED {sym}: {str(e)[:80]}")
    except Exception:
        pass


def manage_spreads(broker: Broker, state: dict, notes: list):
    """Exit management + bookkeeping for credit-spread packages.

    Spreads are OVERNIGHT-EXEMPT (owner decision 2026-07-30): there is no
    flatten branch here. The exits are exactly the three documented rules in
    exitrules.spread_decide, and the disaster floor is the structure itself.
    Closed spreads are journaled into state['spreads_closed'], which run()
    merges into the learner's journal after the singles rebuild (the rebuild
    excludes mleg orders, so spread history lives here)."""
    try:
        if not broker.clock().is_open:
            return
    except Exception:
        return
    raw = broker.positions()
    pkgs, _members = engine.detect_spreads(raw)
    book = state.setdefault("spreads_open", {})
    closed_log = state.setdefault("spreads_closed", [])
    open_keys = {f"{p['short']}|{p['long']}" for p in pkgs}

    # 1) packages that left the book since last run -> find the close fill, journal it
    for k in list(book):
        if k in open_keys:
            continue
        rec = book.pop(k)
        pnl = None
        try:
            for o in broker.closed_orders(150):
                if str(getattr(o, "order_class", "") or "").lower() != "mleg":
                    continue
                legs = getattr(o, "legs", None) or []
                syms = {str(getattr(x, "symbol", "") or "") for x in legs}
                if rec["short"] in syms and rec["long"] in syms:
                    fp = getattr(o, "filled_avg_price", None)
                    if fp is None:
                        continue
                    fpv = float(fp)
                    if fpv >= 0:          # a net DEBIT fill = the buyback
                        pnl = round((rec["credit"] - fpv) * 100 * rec.get("qty", 1), 2)
                        break
        except Exception:
            pass
        max_loss = max((rec["width"] - rec["credit"]) * 100 * rec.get("qty", 1), 1.0)
        row = {
            "symbol": f"{rec['short']}|{rec['long']}", "sleeve": "spreads",
            "underlying": rec.get("underlying"), "type": "put_spread",
            "strike": rec.get("short_strike"), "expiration": rec.get("expiry_iso"),
            "qty": rec.get("qty", 1), "entry": rec.get("credit"),
            "exit_reason": rec.get("last_reason", "closed"),
            "pnl": pnl, "pnl_pct": (round(pnl / max_loss, 4) if pnl is not None else None),
            "closed_on": today(), "thesis": rec.get("thesis", ""),
            "features": {"type": "put_spread", "dte": rec.get("dte_entry"),
                         "direction": "bull", "spread_pct": rec.get("net_cost_frac")},
        }
        closed_log.append(row)
        state["spreads_closed"] = closed_log[-100:]
        learn.record_lesson(state, row)
        notes.append(f"SPREAD CLOSED {rec.get('underlying')} "
                     f"{rec.get('short_strike'):g}/{rec.get('long_strike'):g}P — "
                     f"P&L {'$%+.0f' % pnl if pnl is not None else 'unknown'} "
                     f"(credit {rec['credit']:.2f})")

    # 2) packages on the broker that we have no book entry for (state loss,
    #    watcher-opened, container swap): adopt with recovered credit
    for p in pkgs:
        k = f"{p['short']}|{p['long']}"
        if k in book:
            continue
        credit = None
        try:
            for o in broker.closed_orders(150):
                coid = str(getattr(o, "client_order_id", "") or "")
                if not coid.startswith("SPXS-"):
                    continue
                legs = getattr(o, "legs", None) or []
                syms = {str(getattr(x, "symbol", "") or "") for x in legs}
                if p["short"] in syms and p["long"] in syms:
                    fp = getattr(o, "filled_avg_price", None)
                    credit = abs(float(fp)) if fp is not None else None
                    if not credit:
                        m = re.match(r"SPXS-(\d+)-", coid)
                        credit = int(m.group(1)) / 100.0 if m else None
                    break
        except Exception:
            credit = None
        if not credit or credit <= 0:
            credit = 0.25 * p["width"]
            notes.append(f"SPREAD ADOPTED with assumed credit {credit:.2f} "
                         f"(25% of width) — exits will fire early, never late")
        book[k] = {"short": p["short"], "long": p["long"], "qty": p["qty"],
                   "underlying": p["underlying"], "width": p["width"],
                   "short_strike": p["short_strike"], "long_strike": p["long_strike"],
                   "expiry_ymd": p["expiry_ymd"], "credit": round(credit, 2),
                   "opened": today()}

    # 3) exit management on every open package
    for p in pkgs:
        k = f"{p['short']}|{p['long']}"
        rec = book.get(k)
        if rec is None:
            continue
        q = broker.spread_quote(p["short"], p["long"])
        if not q:
            notes.append(f"SPREAD {p['underlying']} {p['short_strike']:g}/"
                         f"{p['long_strike']:g}P — no usable net quote this run")
            continue
        d = exitrules.spread_decide(
            rec["credit"], max(q["mid"], 0.01), engine.spread_dte(p["expiry_ymd"]),
            take_frac=engine.SPREADS_TAKE_FRAC, stop_mult=engine.SPREADS_STOP_MULT,
            time_dte=engine.SPREADS_TIME_DTE)
        rec["last_reason"] = d["reason"]
        notes.append(f"SPREAD {p['underlying']} {p['short_strike']:g}/"
                     f"{p['long_strike']:g}P value {q['mid']:.2f} vs credit "
                     f"{rec['credit']:.2f} -> {d['reason']}")
        if d["action"] != "exit":
            continue
        # one working close order at a time, across every process
        dup = False
        try:
            for o in broker.open_orders():
                if str(getattr(o, "order_class", "") or "").lower() != "mleg":
                    continue
                legs = getattr(o, "legs", None) or []
                syms = {str(getattr(x, "symbol", "") or "") for x in legs}
                if p["short"] in syms and p["long"] in syms:
                    dup = True
                    break
        except Exception:
            pass
        if dup:
            notes.append(f"SPREAD CLOSE already working for {k} — not duplicating")
            continue
        try:
            limit = max(round(q["ask"] + 0.02, 2), 0.01)
            broker.close_spread(p["short"], p["long"], p["qty"], limit)
            notes.append(f"SPREAD CLOSING {p['underlying']} {p['short_strike']:g}/"
                         f"{p['long_strike']:g}P x{p['qty']} — {d['reason']} "
                         f"(limit {limit:.2f})")
        except Exception as e:
            notes.append(f"SPREAD CLOSE FAILED {k}: {str(e)[:90]}")


def try_spread_entries(broker: Broker, state: dict, notes: list):
    """Open put credit spreads when implied vol is RICH vs our own forecast.

    The mirror of the singles' timing edge: the singles path refuses to BUY
    when the quote is above model value; this path SELLS defined risk in that
    exact condition — implied at least SPREADS_RICH_PTS above the Yang-Zhang
    realized forecast, trend not falling, and the package priced liquidly."""
    if os.environ.get("SPXBOT_FLATTEN_ONLY") == "1":
        return
    mins_left = minutes_to_close(broker)
    if mins_left is None or mins_left <= NO_NEW_ENTRY_MIN:
        return
    book = state.setdefault("spreads_open", {})
    if len(book) >= engine.SPREADS_MAX_OPEN:
        return
    committed = sum(max((r["width"] - r["credit"]) * 100 * r.get("qty", 1), 0)
                    for r in book.values())
    for und in engine.SPREADS_UNDERLYINGS:
        if len(book) >= engine.SPREADS_MAX_OPEN:
            break
        if any(r.get("underlying") == und for r in book.values()):
            continue                       # one package per underlying
        try:
            spot = broker.stock_price(und)
            bars = broker.daily_bars(und, days=120)
            if not spot or bars is None or len(bars) < 30:
                continue
            fc = volmod.forecast_vol(bars, horizon=7)
            if fc is None:
                fc = volmod.realized_vol(bars, window=21, method="yang_zhang")
            if fc is None or fc != fc or fc <= 0:
                continue
            ind = broker.indicators(und) or {}
            if ind.get("trend_up") is False:
                notes.append(f"SPREAD SKIP {und} — downtrend (no bull puts "
                             f"into a falling market)")
                continue
            cand = broker.find_put_spread(und, spot, float(fc))
            if not cand:
                notes.append(f"SPREAD SKIP {und} — no liquid strike pair in "
                             f"{engine.SPREADS_DTE_MIN}-{engine.SPREADS_DTE_MAX} DTE")
                continue
            s_sym, l_sym = cand["short"].symbol, cand["long"].symbol
            # implied of the SHORT leg vs forecast: the richness we are selling
            lq = broker.option_quote(s_sym)
            iv = None
            if lq and cand["dte"]:
                iv = execution.implied_vol(
                    lq["mid"], spot, float(cand["short"].strike_price),
                    cand["dte"] / 365.0, is_call=False)
            if iv is None:
                notes.append(f"SPREAD SKIP {und} — implied vol unreadable")
                continue
            if iv - fc < engine.SPREADS_RICH_PTS:
                notes.append(f"SPREAD NO EDGE {und} — implied {iv*100:.1f}% vs "
                             f"forecast {fc*100:.1f}% (need +{engine.SPREADS_RICH_PTS*100:.0f}pts)")
                continue
            q = broker.spread_quote(s_sym, l_sym)
            if not q:
                notes.append(f"SPREAD SKIP {und} — no two-sided net quote")
                continue
            credit = round(q["mid"] - 0.01, 2)     # sell just under net mid
            width = cand["width"]
            if credit <= 0 or credit / width < engine.SPREADS_MIN_CREDIT_FRAC:
                notes.append(f"SPREAD SKIP {und} — credit {credit:.2f} is "
                             f"{credit/width*100 if width else 0:.0f}% of width "
                             f"(floor {engine.SPREADS_MIN_CREDIT_FRAC*100:.0f}%)")
                continue
            net_cost = (q["ask"] - q["bid"]) / credit if credit else 9.9
            if net_cost > engine.SPREADS_NET_COST_FRAC:
                notes.append(f"SPREAD SCREENED OUT {und} — package round trip "
                             f"{net_cost*100:.1f}% of credit (cap "
                             f"{engine.SPREADS_NET_COST_FRAC*100:.0f}%)")
                continue
            max_loss = (width - credit) * 100
            if max_loss > engine.SPREADS_MAX_LOSS:
                notes.append(f"SPREAD SKIP {und} — max loss ${max_loss:.0f} > "
                             f"${engine.SPREADS_MAX_LOSS:.0f}")
                continue
            if committed + max_loss > engine.SPREADS_ALLOCATION:
                notes.append(f"SPREAD BUDGET STOP {und} — ${committed:.0f} "
                             f"committed + ${max_loss:.0f} > "
                             f"${engine.SPREADS_ALLOCATION:.0f} allocation")
                continue
            tag = f"SPXS-{int(round(credit*100))}-{int(round(width*100))}-{int(time_mod.time())}"
            broker.submit_spread(s_sym, l_sym, 1, credit, tag)
            exp_iso = (parse_occ(s_sym) or {}).get("expiration")
            book[f"{s_sym}|{l_sym}"] = {
                "short": s_sym, "long": l_sym, "qty": 1, "underlying": und,
                "width": width, "short_strike": float(cand["short"].strike_price),
                "long_strike": float(cand["long"].strike_price),
                "expiry_ymd": cand["expiry_ymd"], "expiry_iso": exp_iso,
                "credit": credit, "opened": today(), "dte_entry": cand["dte"],
                "net_cost_frac": round(net_cost, 4),
                "thesis": f"IV {iv*100:.1f}% rich vs YZ forecast {fc*100:.1f}%",
            }
            committed += max_loss
            notes.append(f"OPENED SPREAD {und} {cand['short'].strike_price}/"
                         f"{cand['long'].strike_price}P exp {cand['expiry_ymd']} — "
                         f"credit {credit:.2f} on {width:g} wide (max loss "
                         f"${max_loss:.0f}, RT {net_cost*100:.1f}% of credit, "
                         f"IV {iv*100:.1f}% vs forecast {fc*100:.1f}%)")
        except Exception as e:
            notes.append(f"SPREAD ENTRY ERROR {und}: {str(e)[:90]}")


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
    _, spread_members = engine.detect_spreads(broker.positions())
    for p in broker.positions():
        if p.symbol in covered or p.symbol in spread_members:
            continue                 # a spread leg NEVER gets a singles stop
        try:
            if int(float(p.qty)) < 0:
                continue             # short leg / assignment debris
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

    # Names at peak retail attention are a veto on NEW buys in either
    # direction: crowd lottery demand inflates exactly those premia, and this
    # bot's only order type is buying premium. Defensive only — it removes
    # candidates, never creates or inverts one — and fail-open: a dead feed
    # returns {} and the Tier-1 screens still stand. Fetched once per run.
    crowd = crowd_veto()
    if crowd:
        state.setdefault("last_signals", {})["_crowd_veto"] = crowd

    # apply learning: process sleeves best-weight first.
    # There is no `weight <= 0` skip any more. A paused sleeve stops producing
    # the evidence that would unpause it, so `sleeve_weight` is now bounded in
    # [0.25, 1.25] and never reaches zero. See learn.py's module docstring.
    # Entry iterates ACTIVE_SLEEVES only; retired sleeves (engine.RETIRED_SLEEVES)
    # keep their open positions, exits, journal rows and dashboard panel.
    ordered = sorted(ACTIVE_SLEEVES, key=lambda s: learn.sleeve_weight(state, s), reverse=True)
    for sleeve in ordered:
        weight = learn.sleeve_weight(state, sleeve)
        for sig in signals.get(sleeve, []):
            if sleeve_open_count(state, sleeve) >= MAX_OPEN_PER_SLEEVE:
                break
            und, direction = sig["underlying"], sig["direction"]
            if und in crowd:
                notes.append(f"CROWD VETO {und} {direction} [{sleeve}] — {crowd[und]}")
                continue
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
            cands = broker.find_contracts(und, direction, spot, n=3)
            if not cands:
                notes.append(f"no liquid contract for {und} [{sleeve}]")
                continue
            # Judge each signal on the 2-3 nearest liquid strikes and each
            # strike's tightest reading, not one strike at one instant — the
            # indicative feed's spreads flutter (day-1: 11%->33%->10% on one
            # contract across consecutive hours), so a single wide read is
            # often staleness, not truth. Near misses get re-sampled twice,
            # ~18s apart. The gates below still deliver the actual verdict;
            # nothing here can loosen them. (Day-2 post-mortem change.)
            contract, q, sel_notes = execution.best_quoted(
                cands, lambda c: broker.option_quote(c.symbol),
                max_spread=engine.MAX_SPREAD_PCT, resample=2,
                sleep_fn=lambda: _dt_sleep(18),
                # Affordability guides selection: prefer the tightest strike a
                # contract of which FITS the per-trade budget (learned sizing
                # still applies after the screens and can only shrink it).
                max_cost=per_trade)
            for sn in sel_notes:
                notes.append(f"{und}: {sn}")
            if contract is None or not q:
                # No two-sided quote on ANY candidate during market hours.
                # An earlier version synthesised a bid/ask here; every
                # downstream decision then computed on an invented spread.
                # "No usable quote" is a correct and useful answer. Skip.
                notes.append(f"{und}: no two-sided quote on any candidate — skipped")
                continue
            ask, bid = q["ask"], q["bid"]
            if not ask or ask <= 0:
                continue
            info = parse_occ(contract.symbol) or {}
            # snapshot the FEATURES present at entry (this is what the learner reads)
            ind = broker.indicators(und)
            strike = info.get("strike") or float(contract.strike_price)
            dte = ((dt.date.fromisoformat(info["expiration"]) - dt.date.today()).days
                   if info.get("expiration") else None)
            is_call = (direction == "bull")
            feat = {
                "sleeve": sleeve, "direction": direction, "signal_score": round(sig.get("score", 0), 3),
                "rsi": ind.get("rsi"), "macd_hist": ind.get("macd_hist"),
                "macd_rising": ind.get("macd_rising"), "trend_up": ind.get("trend_up"),
                "dte": dte,
                "moneyness_pct": round(strike / spot - 1, 4),
                "spread_pct": (q or {}).get("spread_pct"), "type": info.get("type"),
            }

            # ---- volatility anchor -----------------------------------------
            # One realized-vol estimate feeds three separate decisions below, so
            # it is computed once. Yang-Zhang is the gap-aware estimator: the
            # gap-blind alternatives (Parkinson, Garman-Klass, Rogers-Satchell)
            # measured 2.2-2.5 vol points LOW on this watchlist because they
            # cannot see overnight moves, and a downward-biased vol forecast
            # makes every option look expensive.
            ref_vol = None
            closes = None
            try:
                bars = broker.daily_bars(und, days=120)
                if bars is not None and len(bars) >= 30:
                    closes = [float(c) for c in bars["close"].tolist()]
                    # HAR-RV forecast first (the conditional forecast the
                    # research phase built — Jensen-corrected, gap-inclusive);
                    # trailing Yang-Zhang only as the fallback ESTIMATE when
                    # the fit is unusable. The distinction is the alpha: HAR
                    # conditions on the 1/5/22-day cascade instead of assuming
                    # yesterday's vol level simply persists.
                    fv = volmod.forecast_vol(bars, horizon=7)
                    if fv is not None:
                        ref_vol = float(fv)
                    else:
                        rv = volmod.realized_vol(bars, window=21, method="yang_zhang")
                        if rv is not None and rv == rv and rv > 0:
                            ref_vol = float(rv)
            except Exception:
                ref_vol = None
            feat["ref_vol"] = round(ref_vol, 4) if ref_vol else None

            # ---- TIER-1 NEGATIVE SCREENS -----------------------------------
            # The trades nine independent research streams each condemned. This
            # runs BEFORE the learner, because the learner reasons from this
            # bot's own 7 trades while these thresholds come from samples of
            # 889,967 retail round-trips and up. Note the spread gate inside is
            # 4%, not the 15% this bot shipped with: at 15% the modelled round
            # trip is 10.93% of premium against a best-documented option anomaly
            # of 0.5%/month, i.e. ~22 months of the best known edge per trade.
            verdict = screens.screen_entry(
                spot=spot, strike=strike, is_call=is_call, dte=dte,
                bid=bid, ask=ask, vol=ref_vol, closes=closes, day=dt.date.today())
            if not verdict["ok"]:
                notes.append(f"SCREENED OUT {und} {direction} [{sleeve}] — "
                             + "; ".join(verdict["failed"]))
                continue

            # ---- EXECUTION TIMING (Muravyev & Pearson) ---------------------
            # Only cross the spread when the contract is cheap against our own
            # volatility forecast. This is the ex-ante edge identity
            # E[edge] ~ vega * (sigma_forecast - sigma_implied); paying the ask
            # for a contract whose implied vol is already above our forecast is
            # paying the spread to buy something we think is overpriced.
            t_years = max(dte or 0, 0) / 365.0
            iv = None
            if ref_vol and t_years > 0 and bid:
                mid = (float(bid) + float(ask)) / 2.0
                iv = execution.implied_vol(mid, spot, strike, t_years, is_call=is_call)
                te = execution.timing_edge(spot, strike, t_years, ref_vol,
                                           bid, ask, is_call=is_call, side="buy")
                if not te["favorable"]:
                    notes.append(
                        f"NO EDGE {und} {direction} [{sleeve}] — implied "
                        f"{(iv*100 if iv else float('nan')):.1f}% vs forecast "
                        f"{ref_vol*100:.1f}%; {te['reason']}")
                    continue
                feat["iv_at_entry"] = round(iv, 4) if iv else None
                feat["vol_edge_pts"] = round((ref_vol - iv) * 100, 2) if iv else None

            # ---- LEARNED SIZING (never a block) ----------------------------
            mult, why = learn.size_multiplier(state, feat)
            size_budget = per_trade * weight * mult

            # ---- price the order -------------------------------------------
            # A marketable limit, not `ask * 1.03`. The old multiplier was a
            # blind 3% of premium given away on every entry; at the 4% spread
            # gate the entire quoted spread is 4%, so 1.03 was paying up to
            # 75% of a spread that did not need to be crossed at all.
            limit_px = execution.marketable_limit(bid, ask, side="buy",
                                                  slippage_frac=0.0)
            if limit_px is None:
                limit_px = round(float(ask), 2)

            qty = int(size_budget // (limit_px * 100))
            if qty < 1:
                # Never round a discouraged trade back up to 1 contract: that
                # would silently discard the learner's only lever.
                if mult < 1.0 or weight < 1.0:
                    notes.append(f"SIZED OUT {und} {direction} [{sleeve}] — "
                                 f"×{mult} learned, ×{weight} sleeve"
                                 + (f" ({'; '.join(why)})" if why else ""))
                else:
                    # Every rejection must say so. This branch was silent for
                    # three days and TSLA — which passed every quality gate
                    # with 1.4-2.2% scanned spreads — kept vanishing from the
                    # notes without a verdict: its contracts simply cost more
                    # than the per-trade cap. An unexplained absence reads as
                    # a bug; a stated reason is a design fact Connor can see.
                    notes.append(f"TOO RICH {und} {direction} [{sleeve}] — one "
                                 f"contract ${limit_px * 100:,.0f} vs per-trade "
                                 f"budget ${size_budget:,.0f} (cap ${per_trade:,.0f})")
                continue
            cost = qty * limit_px * 100
            if cost > cash or cost > budget_left:
                notes.append(f"BUDGET STOP {und} {direction} [{sleeve}] — "
                             f"${cost:,.0f} vs cash ${cash:,.0f} / "
                             f"day budget left ${budget_left:,.0f}")
                continue
            try:
                tag = build_tag(sleeve, feat)   # fingerprint -> recoverable from Alpaca
                broker.buy_to_open(contract.symbol, qty, tag, limit_price=limit_px)
                state["positions"][contract.symbol] = {
                    "sleeve": sleeve, "underlying": und, "direction": direction,
                    "type": info.get("type"), "strike": strike,
                    "expiration": info.get("expiration"), "qty": qty,
                    "entry_price": round(limit_px, 2), "entry_cost": round(cost, 2),
                    "entry_date": today(), "thesis": sig["thesis"], "spot_at_entry": spot,
                    "features": feat,
                }
                cash -= cost
                rt = (verdict["checks"].get("spread", {}) or {}).get("rt_cost")
                notes.append(
                    f"OPENED {qty}x {contract.symbol} [{sleeve}] @ ${limit_px:.2f} "
                    f"(${cost:.0f}, sleeve ×{weight}, learned ×{mult}"
                    + (f", vol edge {feat.get('vol_edge_pts'):+.1f}pts" if feat.get("vol_edge_pts") else "")
                    + (f", round trip {rt*100:.1f}%" if rt else "")
                    + f") — {sig['thesis']}")
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
    reconcile_assignments(broker, notes)
    manage_spreads(broker, state, notes)
    ensure_broker_stops(broker, state, notes)
    if journal:                                    # Alpaca is authoritative for settled trades
        state["journal"] = journal
        state["closed"] = [{
            "symbol": j["symbol"], "sleeve": j["sleeve"], "underlying": j.get("underlying"),
            "type": j.get("type"), "strike": j.get("strike"), "expiration": j.get("expiration"),
            "pnl": j["pnl"], "pnl_pct": j["pnl_pct"], "exit_reason": j.get("exit_reason", "closed"),
            "closed_on": j.get("closed_on"), "thesis": "", "features": j["features"],
        } for j in journal]
    # Spread history lives in spreads_closed (the singles rebuild excludes
    # mleg orders); merge it so the learner and reports see the whole book.
    for r in state.get("spreads_closed", []):
        state["journal"].append(r)
        state["closed"].append(r)
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
            if str(getattr(o, "order_class", "") or "").lower() == "mleg":
                continue     # packages are managed by manage_spreads
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
    try_spread_entries(broker, state, notes)
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
