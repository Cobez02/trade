"""
Continuous exit rules — the logic a human day-trader applies while watching a
position tick by tick, expressed as PURE FUNCTIONS so it can be replayed against
recorded quote streams without touching the broker.

Why this file exists separately from watcher.py: the decision ("should I be out
of this right now?") is the part that loses money when it's wrong, so it is kept
free of sockets, clocks and order plumbing and tested directly.

Three ideas do the work:

1. BID-BASED P&L. The bid is what you would actually collect. Marking a position
   at the mid flatters it by half the spread — that is precisely how a position
   reading -29% at the last hourly check got liquidated at -51%.

2. SPREAD-AWARE STOP FLOOR. A raw bid stop is unfair to wide contracts: on a
   33%-wide market the bid sits ~17% below mid, so a -30% bid stop would fire
   while the contract is only -13% on mid — stopped out by the spread rather
   than by the trade going wrong. The stop is therefore loosened by half the
   quoted spread, which is exactly the amount the bid is depressed by. Capped so
   a pathological market can't loosen the stop indefinitely.

3. PEAK TRAILING instead of a fixed take-profit. A fixed +45% target caps every
   winner while losers still run to the stop. Replaying one session's trades
   through a fixed target turned +$63 into +$7. The trail arms once the position
   has proven itself, then follows the peak up and never retreats.
"""
from __future__ import annotations
import os

# --- tunables ---------------------------------------------------------------
STOP_PCT          = -0.30   # base stop, measured on the bid
TRAIL_ARM_PCT     = 0.25    # profit at which the trail switches on
TRAIL_GIVEBACK    = 0.20    # exit after retracing this much FROM THE PEAK
MAX_SPREAD_RELIEF = 0.10    # most the stop may be loosened for a wide market
HARD_STOP_PCT     = -0.60   # spread relief can never push the stop past this
CONFIRM_TICKS     = 3       # consecutive breaching quotes before acting
CONFIRM_MIN_SEC   = 1.0     # ...and the breach must have lasted at least this long
PANIC_MARGIN      = 0.08    # a breach this far past the trigger skips confirmation
MAX_QUOTE_AGE_S   = 30.0    # older than this and we refuse to act on it

# A market that blows out AFTER entry is information, not just noise. Every
# position clears a 15% spread gate on the way in, so a 50%-wide quote means
# liquidity has evaporated since. Loosening the stop to accommodate that is
# backwards — it keeps us in the exact contract we can no longer get out of.
# Only fires on a position already losing, so a wide but WINNING contract is
# left to the trail rather than panic-sold into its own spread.
SPREAD_EXIT_PCT   = 0.50
SPREAD_EXIT_LOSS  = -0.15

# Beyond this the quote is not a wide market, it is an ABSENT one — a 0.01 x
# 0.40 print is the book showing no bid, not the contract being worth a cent.
# Market-selling into it realises a catastrophic fill on a position that still
# has value. Treated as an unusable quote: hold, and let the resting broker
# stop or a real bid decide instead.
NO_MARKET_SPREAD  = 1.20


def _pnl_pct(bid: float, entry: float) -> float:
    return (bid - entry) / entry if entry else 0.0


def effective_stop(spread_pct: float | None) -> float:
    """Stop threshold after widening for the quoted spread.

    The bid is depressed below mid by roughly half the spread, so half the
    spread is given back. Without this, wide contracts get stopped out for being
    wide rather than for losing.
    """
    relief = 0.0
    if spread_pct and spread_pct > 0:
        relief = min(spread_pct / 2.0, MAX_SPREAD_RELIEF)
    return max(STOP_PCT - relief, HARD_STOP_PCT)


def quote_ok(q: dict | None, age_s: float | None = None) -> tuple[bool, str]:
    """Reject quotes we must not trade on. Acting on a bad quote is worse than
    acting late: a single zero-bid print would liquidate the whole book."""
    if not q:
        return False, "no quote"
    bid, ask = q.get("bid") or 0, q.get("ask") or 0
    if bid <= 0 or ask <= 0:
        return False, "zero/absent side"
    if ask < bid:
        return False, "crossed market"
    mid = (bid + ask) / 2
    if mid > 0 and (ask - bid) / mid >= NO_MARKET_SPREAD:
        return False, f"no market ({(ask - bid) / mid:.0%} wide)"
    if age_s is not None and age_s > MAX_QUOTE_AGE_S:
        return False, f"stale ({age_s:.0f}s)"
    return True, "ok"


def update_peak(peak_pct: float | None, cur_pct: float) -> float:
    """Peak only ever ratchets up, so the trail can never loosen."""
    return cur_pct if peak_pct is None else max(peak_pct, cur_pct)


# ---------------------------------------------------------------------------
# OVERNIGHT EXEMPTION  (SPXBOT_OVERNIGHT, default OFF)
# ---------------------------------------------------------------------------
# Connor asked on 2026-09-02 for the singles sleeve to be able to hold
# overnight "if it thinks it will make a profit". This is that switch, and it
# ships OFF because the measurement says the answer is usually no.
#
# WHAT WAS MEASURED, on the live-parity intraday rig at 21-35 DTE / $450 cap,
# 486 trades over 2024-03..2026-08, each trade priced BOTH ways:
#
#   arm                                   fit half      test half
#   flat at 15:45 (current)              +$5.53/trade  +$4.09/trade
#   hold overnight -> next 15:45         +$1.52        +$1.56
#   hold overnight -> next open          +$0.89        +$2.87
#   paired difference                    -$3.56/trade overall, t = -0.89
#
# So unconditional overnight costs about $3.56 a trade, in the same direction
# in both halves. Then the gates, judged only on the trades each one selects:
#
#   gate                fit delta   test delta
#   winner at 15:45     -$17.27     -$15.46     <- "let winners run" is the WORST
#   loser  at 15:45     + $8.12     +$10.93     <- the only gate that helps
#   DTE >= 25           -$38.00     -$41.50     (n=2 each half, meaningless)
#   premium < $300      - $4.52     - $0.95
#   premium >= $300     - $3.64     - $3.71
#
# The trading-folklore rule is the actively harmful one. The only gate with a
# consistent sign is holding LOSERS, and even that merely turns a -$35.74
# average into -$27.62: a smaller loss, not a profit, on trades you would
# rather not have had. It is also not significant (t about 0.7), and it means
# carrying overnight gap risk on positions already going against you.
#
# So: the switch exists, it defaults off, and when it is on it implements the
# gate the data supports rather than the one intuition suggests. Enabling it is
# a decision to accept a measured expected loss for a smaller variance of
# outcome on losers. I do not recommend it.
OVERNIGHT_ENABLED = os.environ.get("SPXBOT_OVERNIGHT", "0") == "1"
OVERNIGHT_MAX_LOSS_PCT = float(os.environ.get("SPXBOT_OVERNIGHT_MAX_LOSS", "-0.50"))
OVERNIGHT_MIN_DTE = int(os.environ.get("SPXBOT_OVERNIGHT_MIN_DTE", "3"))


def may_hold_overnight(pnl_pct, dte_left, quote_ok_flag):
    """(hold, reason) - may this position skip the bell?

    Every clause is a refusal by default. A position is carried ONLY when the
    switch is on, the quote is usable, expiry is not imminent, the position is
    down (the only gate with support in both halves), and it is not down so far
    that another night could take the rest.
    """
    if not OVERNIGHT_ENABLED:
        return False, ""
    if not quote_ok_flag or pnl_pct is None:
        return False, ""          # never carry a position we cannot price
    if dte_left is not None and dte_left <= OVERNIGHT_MIN_DTE:
        return False, ""          # theta into expiry is the one certainty here
    if pnl_pct >= 0:
        return False, ""          # winners overnight measured -$15 to -$17/trade
    if pnl_pct <= OVERNIGHT_MAX_LOSS_PCT:
        return False, ""          # already deeply wrong; do not fund another night
    return True, f"down {pnl_pct:+.0%}, {dte_left} DTE"


def decide(entry: float, q: dict, peak_pct: float | None,
           dte_left: int | None = None, flatten: bool = False,
           age_s: float | None = None) -> dict:
    """Return the exit decision for one position at one instant.

    Never raises — a watcher that dies on a malformed quote stops protecting
    every other position too, so unusable input yields a 'hold, and say why'.
    """
    ok, why = quote_ok(q, age_s)

    # Flatten is checked FIRST and does not consult the quote, because the
    # decision does not depend on a price — it is "be flat by the bell". An
    # earlier version refused to flatten on a malformed quote, which meant a
    # position whose feed had broken was the one position carried overnight:
    # exactly the exposure the rule exists to remove.
    if flatten:
        cur = _pnl_pct(float(q["bid"]), entry) if ok else None
        shown = f"{cur:+.0%}" if cur is not None else f"no usable quote ({why})"
        hold, reason = may_hold_overnight(cur, dte_left, ok)
        if hold:
            return {"action": "hold", "reason": f"overnight ({reason}) {shown}",
                    "pnl_pct": cur, "peak_pct": peak_pct, "confident": ok,
                    "trigger": None}
        return {"action": "exit", "reason": f"eod-flatten {shown}",
                "pnl_pct": cur, "peak_pct": peak_pct, "confident": ok,
                "trigger": None}

    if not ok:
        return {"action": "hold", "reason": f"unusable quote: {why}",
                "pnl_pct": None, "peak_pct": peak_pct, "confident": False,
                "trigger": None}

    bid = float(q["bid"])
    cur = _pnl_pct(bid, entry)
    peak = update_peak(peak_pct, cur)
    stop = effective_stop(q.get("spread_pct"))

    # Expiry risk next — a contract about to expire is not worth defending.
    if dte_left is not None and dte_left <= 1:
        return {"action": "exit", "reason": f"time-stop ({dte_left} DTE) {cur:+.0%}",
                "pnl_pct": cur, "peak_pct": peak, "confident": True,
                "trigger": None}

    # Trailing exit, once the position has earned the trail.
    if peak >= TRAIL_ARM_PCT and (peak - cur) >= TRAIL_GIVEBACK:
        return {"action": "exit",
                "reason": f"trail: peaked {peak:+.0%}, now {cur:+.0%}",
                "pnl_pct": cur, "peak_pct": peak, "confident": True,
                "trigger": peak - TRAIL_GIVEBACK}

    # Liquidity has deteriorated past the point where the stop can be trusted:
    # the spread relief below would only widen the stop further and keep us in.
    sp = q.get("spread_pct") or 0
    if sp >= SPREAD_EXIT_PCT and cur <= SPREAD_EXIT_LOSS:
        return {"action": "exit",
                "reason": f"liquidity gone: spread {sp:.0%}, {cur:+.0%}",
                "pnl_pct": cur, "peak_pct": peak, "confident": True,
                "trigger": SPREAD_EXIT_LOSS}

    # Hard stop, measured honestly on the bid.
    if cur <= stop:
        return {"action": "exit",
                "reason": f"stop {cur:+.0%} (threshold {stop:+.0%})",
                "pnl_pct": cur, "peak_pct": peak, "confident": True,
                "trigger": stop}

    return {"action": "hold", "reason": "", "pnl_pct": cur,
            "peak_pct": peak, "confident": True, "trigger": None}


def confirm_ok(ticks: int, elapsed_s: float, cur_pct: float | None,
               trigger_pct: float | None) -> bool:
    """Has a breach earned the right to be acted on?

    Tick-counting alone is the wrong debounce once quotes arrive over a socket.
    At a 2s REST cadence three ticks is six seconds of sustained breach; on a
    live stream three ticks can be fifty milliseconds, which is noise. So both
    conditions must hold: enough quotes AND enough wall-clock.

    The exception is a breach far past the trigger. Waiting out a debounce while
    a contract falls off a cliff is how the confirmation logic becomes the thing
    that costs money, so a move well beyond the threshold acts at once.
    """
    if trigger_pct is None:
        # Not a price-triggered exit — the bell and the expiry calendar are
        # facts, not prints, and debouncing a fact just delays it.
        return True
    if cur_pct is not None and cur_pct <= trigger_pct - PANIC_MARGIN:
        return True
    return ticks >= CONFIRM_TICKS and elapsed_s >= CONFIRM_MIN_SEC


def broker_stop_price(entry: float, peak_pct: float | None) -> float:
    """Price for the resting broker-side STOP order.

    This is the floor that survives the watcher dying, the container being
    reclaimed, or the network dropping. It is deliberately looser than the
    watcher's own trigger so that in normal operation the watcher — which sees
    the bid — exits first, and this only fires when nothing is watching.
    """
    floor = entry * (1 + HARD_STOP_PCT)
    if peak_pct is not None and peak_pct >= TRAIL_ARM_PCT:
        trailed = entry * (1 + peak_pct - TRAIL_GIVEBACK - 0.10)
        floor = max(floor, trailed)
    return max(round(floor, 2), 0.01)


# ---------------------------------------------------------------------------
# Defined-risk credit spreads (sleeve "spreads") — package-level exits
# ---------------------------------------------------------------------------
def spread_decide(credit: float, net_now: float, dte_left,
                  take_frac: float = 0.50, stop_mult: float = 2.00,
                  time_dte: int = 2):
    """Exit decision for a put credit spread, on its NET value.

    `credit` is what we were paid to open (positive). `net_now` is what the
    package costs to buy back right now (its current net value, positive).
    The three documented rules, in priority order:

      stop        net_now >= stop_mult * credit   (loss of ~1x credit; fires
                  before max loss ever comes into play)
      take-profit net_now <= take_frac * credit   (the managed-early rule
                  with the strongest large-sample support)
      time        dte_left <= time_dte            (gamma week and assignment
                  risk are not our trade)

    Total function: unreadable inputs -> hold with a reason, never an
    exception — this sits in the loop that manages live positions.
    Returns {"action": "hold"|"exit", "reason": str, "pnl_frac": float|None}
    where pnl_frac is P&L as a fraction of max profit (the credit).
    """
    try:
        c = float(credit); n = float(net_now)
        if not (c > 0) or not (n >= 0) or c != c or n != n:
            return {"action": "hold", "reason": "unreadable credit/value", "pnl_frac": None}
    except (TypeError, ValueError, OverflowError):
        return {"action": "hold", "reason": "unreadable credit/value", "pnl_frac": None}
    pnl_frac = round((c - n) / c, 4)
    if n >= stop_mult * c:
        return {"action": "exit",
                "reason": f"spread stop: value {n:.2f} >= {stop_mult:g}x credit {c:.2f}",
                "pnl_frac": pnl_frac}
    if n <= take_frac * c:
        return {"action": "exit",
                "reason": f"spread take-profit: value {n:.2f} <= {take_frac:.0%} of credit {c:.2f}",
                "pnl_frac": pnl_frac}
    try:
        if dte_left is not None and int(dte_left) <= int(time_dte):
            return {"action": "exit",
                    "reason": f"spread time-exit: {int(dte_left)} DTE <= {int(time_dte)}",
                    "pnl_frac": pnl_frac}
    except (TypeError, ValueError):
        pass
    return {"action": "hold", "reason": f"holding ({pnl_frac:+.0%} of credit)", "pnl_frac": pnl_frac}
