"""
execution.py — pre-trade execution quality: timing, cost, ticks, and stop safety.

WHY THIS MODULE EXISTS
The entry logic already decides *what* to buy. Two research findings say that *how* and
*when* the order is sent is worth more than the signal itself.

FINDING 1 — execution timing is worth half the spread.
  Muravyev & Pearson, "Options Trading Costs Are Lower Than You Think," RFS 33(11):
  4973-5014 (2020): 20.4M option trades on the 39 highest-option-volume US names, 882
  trading days, April 2003 - October 2006, Nanex tick data on BOTH options and underlyings.
      quoted (trades at random times)      8.4c   5.0% of the $1.70 avg premium
      conventional effective spread        6.4c   3.8%
      timing-adjusted, BSM-implied model   4.2c   2.5%   <- what a TIMER pays
      timing-adjusted, subset that times   1.3c   0.76%
  Mechanism, one line of arithmetic: price the option off the LIVE underlying and its own
  recent implied vol, compare to the quoted mid, cross only when the BSM value is on YOUR
  side. Option quotes lag the underlying — regressing the next minute's mid change on that
  gap gives R^2 = 22% at one minute (10% at ten, 3% at an hour), coefficient averaging
  0.54: "in just one minute the option price moves more than half the distance required to
  converge to the implied price." For a SPY-scale name that halves the effective half-
  spread (2.5% -> 1.25% of premium) and drops the required apparent edge from ~5.0 to ~2.5
  annualized vol points at a 30-vol level (note 08 §12.5). Note 07 §A.2 is the mirror-image
  warning: for NON-timers, M&P's adjusted spread EQUALS their conventional spread (6.2c =
  6.2c) — submit on signal without looking at the quote and you get ZERO of this discount.
  There is no partial credit.
  Caveats: 2003-06 is pre-penny and pre-HFT-saturation; the 39 names are the most
  option-active in the market; M&P read ~40% of trades as institutional algos and note that
  "at most only a few retail investors have the resources... to time their option trades",
  so 4.2c belongs to someone with colocated data, not someone polling REST; and Alpaca's
  free option feed is INDICATIVE, not OPRA (note 07 §I.3), so the mid itself is approximate.
  `timing_edge` removes obviously-bad moments. It is a filter, not a cost estimate.

FINDING 2 — Alpaca stop-market orders on options are a loss-manufacturing device.
  Alpaca's own docs (https://docs.alpaca.markets/docs/orders-at-alpaca), verbatim: "Your
  sell stop order will only elect if there is a trade on the consolidated tape at or lower
  than your stop price." / "Your buy stop order will only elect if there is a trade on the
  consolidated tape that is at or above your stop price THAT IS NOT OUTSIDE OF THE NBBO."
  / "Once the order is elected, the stop order becomes a market order." The NBBO qualifier
  appears only on the BUY side; if that asymmetry is literal, sell stops are electable by
  prints outside the NBBO, and complex-order legs and auction prints hit the consolidated
  tape well away from a single series' own quote.
  Combine that with the retail option spread — Bryzgalova, Pavlova & Sikorskaya: quoted
  13.7%, effective 6.7% (note 07 §A.4). A print at the bid versus one at the ask differs by
  13.7% OF PREMIUM, so ordinary print alternation elects a percentage-based stop with no
  adverse move by anyone: you do not need a predator, the spread is sufficient (note 07
  §E.3). The elected order then becomes a MARKET order into a book whose inside may be a
  1-lot. Alpaca is paid $0.60/contract of PFOF under the disclosed formula "$0.06 per
  contract + 9% of spread dollars, capped at $0.60" (Rule 606(a)(1), Q1 2026); the cap
  binds above a 6-cent quoted spread and every venue/order-type cell in the filing reports
  exactly $0.60, so the broker's revenue is a contractual linear function of the spread the
  bot pays.

This module places no orders, opens no sockets, and imports nothing but the standard
library. Every function is pure and TOTAL — it never raises and never returns NaN, because
in an unattended trading loop an exception in a pricing helper means an unmanaged position.
Sources: /home/claude/research/notes/07-microstructure-and-execution.md (§A, §D.1, §E, §H,
§I) and /home/claude/research/notes/08-volatility-forecasting.md (§12.5).
"""
from __future__ import annotations

import math
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants — every one of these carries a citation.
# ---------------------------------------------------------------------------

# Alpaca fee schedule, EFFECTIVE 2026-07-01, per contract per round trip (note 07 §I.2,
# https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf):
#   ORF $0.015 both sides -> $0.0300      OCC $0.025 both sides -> $0.0500
#   CAT $0.0003 both sides -> $0.0006     TAF $0.00329 sells only
#   SEC $0.0000206 x notional, sells only -> $0.00206 at a $1.00 contract
#   Commission $0.00 (self-directed cash account)
# Components sum to $0.08595 at a $1.00 contract; note 07's headline figure is $0.0854 and
# we use it so the bot's numbers reconcile with the note. SEC is the only notional-
# dependent term and moves the total by $0.002 per $1.00 of price — 0.2bp of premium. As a
# fraction of premium: 0.085% at $1.00, 0.43% at $0.20, 0.028% at $3.00.
REG_FEES_PER_CONTRACT_ROUNDTRIP = 0.0854

# Vega floor below which an implied vol is not a number, it is a rumour. Option prices are
# quoted in whole cents, so the smallest possible price error is half a tick, $0.005, and
# dVol = dPrice/vega. Requiring half a tick of price noise to map to at most ~25 vol points
# of IV noise gives vega >= 0.005/0.25 = 0.02 per 1.00 of vol; below that, implied_vol
# returns None. Not academic: the bot trades short-dated OTM contracts, exactly where vega
# collapses and a Newton solver silently returns whatever its last iterate happened to be.
MIN_VEGA_FOR_IV = 0.02

# Bisection bracket and hard cap. Bisection, not Newton: Newton's step is
# price_error/vega, which on a deep-OTM 1-day contract either explodes or divides by zero.
# Bisection cannot diverge; it can only fail to bracket, which we detect.
IV_VOL_LO, IV_VOL_HI = 0.005, 5.0
IV_MAX_ITER = 128

# Penny Interval Program, in force 2025-2026 (MIAX all-exchange penny program page; Cboe
# class-removal notice; SEC filing SR-CBOE-2025-069 of 2025-09-24; note 07 §D.1) — these
# are exchange rules, not estimates:
#   program member (425 classes): $0.01 below $3.00, $0.05 at/above
#   SPY / QQQ / IWM:              $0.01 on ALL series (hence `all_penny=True`)
#   non-program class:            $0.05 below $3.00, $0.10 at/above
# The $3.00 line is a discontinuity, not a gradient — $2.99 -> $3.00 quintuples the tick,
# and Ernst & Spatt's regression discontinuity measured exactly this boundary. Note 07
# advises keeping the entry range in $0.50-$2.50 and staying clear of it.
TICK_BOUNDARY = 3.00
TICK_FINE, TICK_COARSE = 0.01, 0.05

# Cost-verdict bands (note 07 §H.2): move the spread gate to 2-4%, which puts required
# gross edge in the 1.5-3.5% range, "where a genuinely strong signal could plausibly
# compete." 7.3% is the note's OPTIMISTIC figure for the bot's current 15% gate (half-
# spread capture on BOTH sides); past it, no published edge covers the trade — the best
# documented bias-corrected option anomaly is 0.5%/month (Duarte/Jones/Khorram/Mo via
# Garcia-Ares & Muravyev).
COST_OK_MAX = 0.035
COST_EXPENSIVE_MAX = 0.073

# Returned instead of a cost when the quote is unusable. Finite (survives a JSON round trip
# into state.json) and enormous (every `cost < gate` comparison rejects). Never NaN: NaN
# compares False against everything, which would silently ADMIT trades.
COST_SENTINEL = 9.99

_SQRT2 = math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


# ---------------------------------------------------------------------------
# Input hygiene. Every public function funnels its arguments through _num first.
# ---------------------------------------------------------------------------
def _num(x: Any) -> Optional[float]:
    """Coerce to a FINITE float, or None. bool is rejected on purpose: True would
    otherwise silently become a $1.00 price."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _quote(bid: Any, ask: Any) -> Optional[tuple[float, float, float]]:
    """(bid, ask, mid) for a usable two-sided quote, else None. Rejects non-numeric,
    non-finite, zero/negative either side, and crossed markets. A zero bid is not a cheap
    contract, it is an empty book — the same rejection the watcher makes."""
    b, a = _num(bid), _num(ask)
    if b is None or a is None or b <= 0.0 or a <= 0.0 or a < b:
        return None
    return b, a, 0.5 * (a + b)


# ---------------------------------------------------------------------------
# Black-Scholes
# ---------------------------------------------------------------------------
def norm_cdf(x: Any) -> float:
    """Standard normal CDF via math.erf (no scipy in this environment). Garbage in -> 0.0,
    the safe sentinel: it propagates to a zero option value, and every caller in this bot
    treats a zero theoretical value as untradeable."""
    v = _num(x)
    if v is None:
        return 0.0
    return 0.5 * (1.0 + math.erf(v / _SQRT2))


def _norm_pdf(x: float) -> float:
    if x * x > 1500.0:          # exp(-750) underflows to 0.0 anyway; skip the math
        return 0.0
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def _d1_d2(spot: float, strike: float, t: float, vol: float, rate: float
           ) -> tuple[float, float]:
    sv = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / sv
    return d1, d1 - sv


def bs_price(spot: Any, strike: Any, t_years: Any, vol: Any,
             rate: Any = 0.0, is_call: Any = True) -> float:
    """European Black-Scholes price. No dividends (index/ETF options on a paper account; a
    dividend yield would enter as a spot haircut and is not modelled).

    Returns 0.0 — never NaN, never raising — for anything that is not a well-posed pricing
    problem (None, NaN, non-positive spot or strike): zero means "no tradeable value here."
    t_years <= 0 is NOT an error, it returns intrinsic value max(S-K,0); vol <= 0 returns
    FORWARD intrinsic max(S - K*exp(-rT), 0), the zero-vol limit and lower arb bound."""
    s, k, t, v, r = (_num(spot), _num(strike), _num(t_years), _num(vol), _num(rate))
    if s is None or k is None or t is None or v is None or r is None:
        return 0.0
    if s <= 0.0 or k <= 0.0:
        return 0.0
    call = bool(is_call)

    if t <= 0.0:                                   # expiry: intrinsic, no discounting
        return max(s - k, 0.0) if call else max(k - s, 0.0)

    disc_k = k * math.exp(-r * t)
    if v <= 0.0:                                   # zero-vol limit == lower arb bound
        return max(s - disc_k, 0.0) if call else max(disc_k - s, 0.0)

    d1, d2 = _d1_d2(s, k, t, v, r)
    if call:
        return s * norm_cdf(d1) - disc_k * norm_cdf(d2)
    return disc_k * norm_cdf(-d2) - s * norm_cdf(-d1)


def bs_greeks(spot: Any, strike: Any, t_years: Any, vol: Any,
              rate: Any = 0.0, is_call: Any = True) -> dict:
    """Black-Scholes greeks. UNITS — read this before sizing anything off it:
      delta          per 1.00 of underlying     (call in [0,1], put in [-1,0])
      gamma          per 1.00^2 of underlying   (>= 0 for long options)
      vega           PER 1.00 CHANGE IN VOL, i.e. per 100 vol points, NOT per point. A
                     vega of 12.1 means moving vol from 20% to 21% (0.20 -> 0.21) changes
                     the option value by 0.121. This is Hull's convention. The other
                     convention (vega/100) is the classic off-by-100 bug: it would make
                     every vol-scaled position 100x too large and would do so silently,
                     because both numbers look plausible.
      theta          per YEAR, negative for a long option (the raw dV/dt derivative)
      theta_per_day  theta / 365, the number you actually want in a daily loop

    Returns all-zeros for unusable input, and at t_years <= 0 returns the expiry limit
    (delta 0/1 by moneyness, everything else 0) rather than dividing by sqrt(0)."""
    zero = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "theta_per_day": 0.0}
    s, k, t, v, r = (_num(spot), _num(strike), _num(t_years), _num(vol), _num(rate))
    if s is None or k is None or t is None or v is None or r is None:
        return dict(zero)
    if s <= 0.0 or k <= 0.0:
        return dict(zero)
    call = bool(is_call)

    if t <= 0.0 or v <= 0.0:
        # At expiry (or zero vol) the option is a forward: delta is a step function and the
        # second-order greeks are 0 (or undefined exactly at the money — 0 is the sane,
        # non-exploding answer for a position sizer).
        itm = (s > k) if call else (s < k)
        return {"delta": (1.0 if call else -1.0) if itm else 0.0,
                "gamma": 0.0, "vega": 0.0, "theta": 0.0, "theta_per_day": 0.0}

    sqrt_t = math.sqrt(t)
    d1, d2 = _d1_d2(s, k, t, v, r)
    pdf, disc_k = _norm_pdf(d1), k * math.exp(-r * t)

    delta = norm_cdf(d1) if call else norm_cdf(d1) - 1.0
    gamma = pdf / (s * v * sqrt_t) if s * v * sqrt_t > 0 else 0.0
    vega = s * sqrt_t * pdf                                    # per 1.00 of vol
    theta_common = -(s * pdf * v) / (2.0 * sqrt_t)
    if call:
        theta = theta_common - r * disc_k * norm_cdf(d2)
    else:
        theta = theta_common + r * disc_k * norm_cdf(-d2)

    out = {"delta": delta, "gamma": gamma, "vega": vega,
           "theta": theta, "theta_per_day": theta / 365.0}
    # Belt and braces: an overflow anywhere above must not reach an order ticket.
    return {kk: (vv if math.isfinite(vv) else 0.0) for kk, vv in out.items()}


def implied_vol(price: Any, spot: Any, strike: Any, t_years: Any,
                rate: Any = 0.0, is_call: Any = True) -> Optional[float]:
    """Implied vol by BISECTION over [IV_VOL_LO, IV_VOL_HI] = [0.005, 5.0], hard-capped at
    IV_MAX_ITER iterations. Returns None, never a bogus number.

    Bisection rather than Newton on purpose: Newton's step is (price_error / vega), and on
    the deep-OTM short-dated contracts this bot actually trades vega is ~1e-12, so the step
    either overflows or divides by zero, and the usual "cap the step" workaround just
    returns the last iterate dressed up as an answer.

    None when: any input is unusable (None / NaN / non-positive spot, strike or price);
    t_years <= 0 (no vol at expiry, only intrinsic); the target price violates no-arbitrage
    bounds — below forward intrinsic, or at/above the upper bound (spot for a call,
    K*exp(-rT) for a put); the true vol lies outside the bracket, detected by failing to
    bracket; or vega at the solution is below MIN_VEGA_FOR_IV = 0.02 per 1.00 of vol (see
    that constant — below it half a tick of price noise moves the answer by >25 vol
    points, so the number would be arithmetic, not information)."""
    p, s, k, t, r = (_num(price), _num(spot), _num(strike), _num(t_years), _num(rate))
    if p is None or s is None or k is None or t is None or r is None:
        return None
    if s <= 0.0 or k <= 0.0 or p <= 0.0 or t <= 0.0:
        return None
    call = bool(is_call)

    disc_k = k * math.exp(-r * t)
    lower = max(s - disc_k, 0.0) if call else max(disc_k - s, 0.0)
    upper = s if call else disc_k
    if p <= lower or p >= upper:        # outside no-arb: no finite vol reproduces it
        return None

    lo, hi = IV_VOL_LO, IV_VOL_HI
    p_lo = bs_price(s, k, t, lo, r, call)
    p_hi = bs_price(s, k, t, hi, r, call)
    if p <= p_lo or p >= p_hi:          # true vol is outside the bracket; do not guess
        return None

    for _ in range(IV_MAX_ITER):
        mid = 0.5 * (lo + hi)
        if bs_price(s, k, t, mid, r, call) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-10:
            break
    vol = 0.5 * (lo + hi)

    if bs_greeks(s, k, t, vol, r, call)["vega"] < MIN_VEGA_FOR_IV:
        return None
    return vol if math.isfinite(vol) else None


# ---------------------------------------------------------------------------
# FINDING 1: the timing check
# ---------------------------------------------------------------------------
def timing_edge(spot: Any, strike: Any, t_years: Any, ref_vol: Any,
                bid: Any, ask: Any, is_call: Any = True, side: Any = "buy") -> dict:
    """Muravyev-Pearson staleness check — run before every marketable order. Price the
    option off the LIVE underlying and its own recent implied vol (`ref_vol`, typically the
    IV from the last good option quote) and compare to the quoted mid. M&P regress the next
    minute's mid change on exactly this gap: R^2 = 22%, coefficient 0.54.

    Returns {"bsm", "mid", "edge", "favorable", "reason"}.
      edge       SIGNED SO THAT POSITIVE IS IN YOUR FAVOUR, as a fraction of the mid.
                 buy: (bsm - mid)/mid.  sell: (mid - bsm)/mid. That orientation is what
                 makes `min_edge_frac` a single threshold for both sides; `edge_raw`
                 carries the unoriented (bsm - mid)/mid.
      favorable  buy -> bsm > mid (the stale quote is cheap relative to where the
                 underlying now is). sell -> bsm < mid. Symmetric, by construction.

    On unusable input bsm/mid/edge are None and favorable is False — the fail-safe
    direction is "do not cross," since a non-timer gets none of M&P's discount but also
    loses nothing by waiting one more tick. The rate is pinned at 0: this compares two
    prices computed seconds apart on the same contract, so the discount factor cancels to
    the fourth decimal at the 0-45 DTE horizons this bot trades."""
    def bad(reason: str) -> dict:
        return {"bsm": None, "mid": None, "edge": None, "edge_raw": None,
                "favorable": False, "reason": reason}

    sd = str(side).strip().lower() if side is not None else ""
    if sd not in ("buy", "sell"):
        return bad(f"unknown side {side!r} (expected 'buy' or 'sell')")

    q = _quote(bid, ask)
    if q is None:
        return bad("unusable quote (zero/negative/crossed/missing)")
    _b, _a, mid = q

    s, k, t, v = _num(spot), _num(strike), _num(t_years), _num(ref_vol)
    if s is None or k is None or t is None or v is None:
        return bad("unusable pricing inputs")
    if s <= 0.0 or k <= 0.0 or v <= 0.0:
        return bad("non-positive spot, strike or reference vol")
    if t <= 0.0:
        # At expiry the quote cannot be stale relative to anything — there is no time value
        # left to converge. Refuse rather than compare against intrinsic.
        return bad("expired (t_years <= 0): no staleness to exploit")

    bsm = bs_price(s, k, t, v, 0.0, is_call)
    if bsm <= 0.0:
        return bad("theoretical value is zero — contract is worthless on this vol")

    edge_raw = (bsm - mid) / mid
    edge = edge_raw if sd == "buy" else -edge_raw
    favorable = edge > 0.0
    verb = "cheap" if sd == "buy" else "rich"
    reason = (f"{sd}: bsm {bsm:.4f} vs mid {mid:.4f} -> {edge:+.2%} "
              f"({'quote is ' + verb + ', cross' if favorable else 'quote is against you, wait'})")
    return {"bsm": bsm, "mid": mid, "edge": edge, "edge_raw": edge_raw,
            "favorable": favorable, "reason": reason}


def should_cross(spot: Any, strike: Any, t_years: Any, ref_vol: Any,
                 bid: Any, ask: Any, is_call: Any = True, side: Any = "buy",
                 min_edge_frac: Any = 0.0) -> bool:
    """The one-line call site. True only when the BSM value is on your side of the mid by
    at least `min_edge_frac` (a fraction of the mid). min_edge_frac=0.0 reproduces M&P's
    naive filter, which is what earned the 4.2c number; raising it trades fill rate for
    cost. Any doubt returns False."""
    m = _num(min_edge_frac)
    if m is None:
        return False
    res = timing_edge(spot, strike, t_years, ref_vol, bid, ask, is_call, side)
    return bool(res["favorable"]) and res["edge"] is not None and res["edge"] >= m


# ---------------------------------------------------------------------------
# FINDING 2 (part 1): the cost model
# ---------------------------------------------------------------------------
def roundtrip_cost_frac(bid: Any, ask: Any, capture_entry: Any = 0.5,
                        capture_exit: Any = 0.0,
                        notional_per_contract: Any = None) -> float:
    """Expected round-trip cost as a FRACTION OF THE ENTRY PREMIUM PAID.

    The identity (note 07 §H.1), with S = (ask-bid)/mid the relative quoted spread and
    c_e / c_x the fractions of the HALF-spread captured on entry / exit:

        RT = S*(2 - c_e - c_x) / (2 + S*(1 - c_e))  +  fees/premium

    Derivation: entry = M + h(1-c_e), exit = M - h(1-c_x) with h = S*M/2; the numerator is
    entry-exit and the denominator is entry, not the mid — which is why RT is slightly
    BELOW S even at zero capture.

    Capture conventions: 0.00 = you cross fully (pay the whole half-spread); 0.50 = you
    split the difference, which is BPS retail (6.7% effective vs 13.7% quoted, a ratio of
    0.51) and also what FINDING 1's timing filter buys you; 1.00 = filled exactly at the
    mid (BPS: 3.8% of trades, not systematically attainable). The defaults encode note 07
    §D.3 — entries are discretionary so assume half-spread capture, MANDATORY exits
    (force-close at the bell, stop, time-stop) capture nothing.

    `notional_per_contract` is dollars of premium per contract and only scales the fixed
    regulatory fee; None derives it from the quote (entry price x 100). Returns
    COST_SENTINEL (999% of premium — see that constant) for an unusable quote or a capture
    fraction outside [0,1]."""
    q = _quote(bid, ask)
    ce, cx = _num(capture_entry), _num(capture_exit)
    if q is None or ce is None or cx is None:
        return COST_SENTINEL
    if not (0.0 <= ce <= 1.0) or not (0.0 <= cx <= 1.0):
        return COST_SENTINEL
    b, a, mid = q

    spread_frac = (a - b) / mid                                     # S
    spread_cost = spread_frac * (2.0 - ce - cx) / (2.0 + spread_frac * (1.0 - ce))

    entry_px = mid + 0.5 * (a - b) * (1.0 - ce)
    notional = _num(notional_per_contract)
    if notional is None or notional <= 0.0:
        notional = entry_px * 100.0
    fee_frac = REG_FEES_PER_CONTRACT_ROUNDTRIP / notional if notional > 0 else 0.0

    total = spread_cost + fee_frac
    return total if math.isfinite(total) else COST_SENTINEL


def required_gross_edge(bid: Any, ask: Any, **kw: Any) -> float:
    """Gross return on premium the trade must earn just to break even. Numerically
    identical to roundtrip_cost_frac by construction — it exists under its own name because
    the two questions get asked by different code, and confusing "cost I pay" with "edge I
    need" is how a break-even filter ends up off by a factor of 2. Note 07 §H.3 argues for
    a 2x buffer on top (the estimate is itself uncertain: indicative feed, costs roughly
    double in the last hour of a 0DTE, mandatory exits get no capture) — that is the
    CALLER's multiple to apply, not ours."""
    return roundtrip_cost_frac(bid, ask, **kw)


def cost_verdict(bid: Any, ask: Any, premium: Any = None, **kw: Any) -> dict:
    """{"rt_cost", "verdict": "ok"|"expensive"|"prohibitive", "note"}. `premium` is the
    per-contract option price in quote units (e.g. 1.00, not 100) and only scales the fixed
    fee; None derives it from the quote. Bands (note 07 §H.2): ok <= 3.5%, expensive
    <= 7.3%, prohibitive above, the upper edge being the note's OPTIMISTIC estimate for the
    bot's current 15% gate. For scale, the best documented post-publication bias-corrected
    option anomaly is ~0.5% per month, so a 7.3% round trip is ~15 months of it, per trade."""
    p = _num(premium)
    notional = p * 100.0 if (p is not None and p > 0.0) else None
    cost = roundtrip_cost_frac(bid, ask, notional_per_contract=notional, **kw)

    if cost >= COST_SENTINEL:
        return {"rt_cost": COST_SENTINEL, "verdict": "prohibitive",
                "note": "unusable quote or bad capture assumption — refusing to price it"}
    if cost <= COST_OK_MAX:
        v, n = "ok", (f"{cost:.2%} round trip — inside note 07's 2-4% gate "
                      f"recommendation; a real signal can clear this")
    elif cost <= COST_EXPENSIVE_MAX:
        v, n = "expensive", (f"{cost:.2%} round trip — needs ~{cost * 2:.1%} gross edge "
                             f"at the 2x buffer note 07 §H.3 argues for")
    else:
        v, n = "prohibitive", (f"{cost:.2%} round trip — no documented option anomaly "
                               f"covers this; at the best published edge of ~0.5%/month "
                               f"that is ~{cost / 0.005:.0f} months of alpha per trade")
    return {"rt_cost": cost, "verdict": v, "note": n}


# ---------------------------------------------------------------------------
# FINDING 2 (part 2): ticks, stops, and marketable limits
# ---------------------------------------------------------------------------
def tick_size(price: Any, all_penny: bool = False) -> float:
    """Minimum price increment, Penny Interval Program (note 07 §D.1): $0.01 below $3.00,
    $0.05 at and above, for the 425 program classes. SPY / QQQ / IWM quote in pennies on
    ALL series -> pass all_penny=True. $3.00 exactly is on the COARSE side of the line.

    Garbage in -> 0.05, the coarse tick, because any multiple of $0.05 is also a multiple
    of $0.01 and so is valid in BOTH regimes; guessing $0.01 would produce off-tick limits
    that the exchange rejects."""
    if all_penny:
        return TICK_FINE
    p = _num(price)
    if p is None or p < 0.0:
        return TICK_COARSE
    return TICK_FINE if p < TICK_BOUNDARY else TICK_COARSE


def round_to_tick(price: Any, side: Any = "buy", all_penny: bool = False
                  ) -> Optional[float]:
    """Round a limit price to a valid tick, CONSERVATIVELY for the given side: buy rounds
    DOWN (never bid more than you meant to), sell rounds UP (never offer less). Returns
    None — not a number — for unusable input, because a None limit price cannot be mistaken
    for an order instruction, whereas a 0.0 sell limit could be and would sell at any price
    the book happened to show."""
    p = _num(price)
    sd = str(side).strip().lower() if side is not None else ""
    if p is None or p <= 0.0 or sd not in ("buy", "sell"):
        return None
    t = tick_size(p, all_penny=all_penny)
    n = p / t
    if abs(n - round(n)) < 1e-9:                # already on-tick; do not drift on dust
        out = round(n) * t
    else:
        out = (math.floor(n) if sd == "buy" else math.ceil(n)) * t
    return round(max(out, 0.0), 4)


def stop_election_risk(bid: Any, ask: Any, stop_price: Any) -> dict:
    """Can this sell-stop be elected by ordinary print noise, with no adverse move?

    Alpaca: "Your sell stop order will only elect if there is a trade on the consolidated
    tape at or lower than your stop price" — and then it becomes a MARKET order. Prints
    alternate between bid and ask constantly, so the lowest ordinary, entirely-non-adverse
    print given this quote is the BID: any stop at or above the bid is elected by the next
    seller-initiated print with the mid unmoved.

    Returns {"electable_by_noise", "min_print", "note"}. `min_print` is the bid and is the
    OPTIMISTIC floor — Alpaca's sell-stop condition, unlike its buy-stop condition, carries
    no "not outside of the NBBO" qualifier, and OPRA carries complex-order legs and auction
    prints that land well away from a single series' quote, so the true floor is lower and
    in principle unbounded. Unusable quote -> electable_by_noise=True, min_print=None: if
    we cannot prove the stop is out of the noise band, we assume it is in it."""
    q = _quote(bid, ask)
    sp = _num(stop_price)
    if q is None or sp is None or sp <= 0.0:
        return {"electable_by_noise": True, "min_print": None,
                "note": "unusable quote or stop — assuming the stop is electable"}
    b, a, mid = q
    spread_frac = (a - b) / mid
    if sp >= b:
        depth_at_bid = (mid - b) / mid
        note = (f"ELECTABLE BY NOISE: stop {sp:.2f} sits at/above the bid {b:.2f}. "
                f"The quote is {spread_frac:.1%} wide, so a print at the bid is "
                f"{depth_at_bid:.1%} below the mid with the mid unmoved — the spread "
                f"alone elects it, and election converts it to a market order.")
        return {"electable_by_noise": True, "min_print": b, "note": note}
    room = (b - sp) / mid
    note = (f"stop {sp:.2f} is {room:.1%} of mid below the bid {b:.2f} "
            f"({spread_frac:.1%}-wide quote); ordinary bid prints do not reach it, "
            f"though prints outside the NBBO still can.")
    return {"electable_by_noise": False, "min_print": b, "note": note}


def safe_stop_price(bid: Any, ask: Any, desired_stop: Any) -> float:
    """Push a sell-stop below the band ordinary quote noise can reach.

    Buffer = one full quoted spread below the bid. Note 07 §E.5 recommends setting a
    stop-limit's limit "at least one full quoted spread beyond the trigger"; the same
    buffer on the trigger itself is the minimum that survives a complex-order leg printing
    a spread through the bid. Result is rounded DOWN to a valid tick.

    GUARANTEES (both tested): never returns a value ABOVE desired_stop — this only moves a
    stop further from the noise band, never toward it (sole exception: the $0.01 floor, the
    minimum valid option price, wins if desired_stop is below it); and monotone
    non-decreasing in desired_stop, so a trailing stop that ratchets up cannot ratchet down.
    An unusable quote leaves desired_stop alone (floored and tick-rounded): with no quote
    there is no noise band to measure, and silently moving a risk limit on bad data is
    worse than leaving it."""
    d = _num(desired_stop)
    if d is None:
        return 0.01
    q = _quote(bid, ask)
    if q is None:
        target = d
    else:
        b, a, _mid = q
        target = min(d, b - (a - b))
    # Floor to a tick DOWNWARD (further from the noise band = safer), not via
    # round_to_tick's side semantics, which are about order sides rather than safety.
    t = tick_size(target)
    if target > 0.0:
        n = target / t
        target = (round(n) if abs(n - round(n)) < 1e-9 else math.floor(n)) * t
    return round(max(target, 0.01), 4)


def marketable_limit(bid: Any, ask: Any, side: Any = "buy",
                     slippage_frac: Any = 0.0, all_penny: bool = False
                     ) -> Optional[float]:
    """The price to send INSTEAD of a market order. A market order in an option book has no
    price protection and the inside may be a 1-lot (note 07 §D.4); an elected stop becomes
    exactly this, and is the mechanism in FINDING 2. A marketable limit gets the same fill
    in the normal case and refuses the pathological one.

        buy  -> ask + slippage_frac * (ask - bid), rounded DOWN to a tick
        sell -> bid - slippage_frac * (ask - bid), rounded UP to a tick

    slippage_frac is extra room in units of the quoted spread; 0.0 means "cross exactly at
    the touch and no further." Rounding is conservative for the side, so the result never
    crosses further than asked. If a rounded BUY lands below the ask (only possible when
    the feed hands us an off-tick quote) it is bumped to the nearest tick at or above the
    ask — an unfillable limit is a worse failure than one tick. None for an unusable quote
    or an unknown side."""
    q = _quote(bid, ask)
    sd = str(side).strip().lower() if side is not None else ""
    sl = _num(slippage_frac)
    if q is None or sd not in ("buy", "sell") or sl is None or sl < 0.0:
        return None
    b, a, _mid = q
    spread = a - b

    raw = (a + sl * spread) if sd == "buy" else (b - sl * spread)
    px = round_to_tick(raw, sd, all_penny=all_penny)
    if px is None:
        return None

    t = tick_size(raw, all_penny=all_penny)
    if sd == "buy" and px < a - 1e-9:            # off-tick feed: restore marketability
        px = round(math.ceil(a / t - 1e-9) * t, 4)
    elif sd == "sell" and px > b + 1e-9:
        px = round(math.floor(b / t + 1e-9) * t, 4)
    return max(px, 0.01)


# ---------------------------------------------------------------------------
# Candidate selection on a fluttering feed
# ---------------------------------------------------------------------------
def best_quoted(candidates, quote_fn, max_spread: float = 0.04,
                resample: int = 2, resample_band: float = 1.75,
                sleep_fn=None, max_cost: float = None):
    """Pick the tightest-quoted candidate contract; re-sample near misses.

    Born from the day-2 zero-trade post-mortem: every signal was judged on
    ONE strike's quote at ONE instant, on Alpaca's indicative (non-NBBO)
    feed, whose quoted spreads flutter — the same contract read 11%, 33%,
    10%, 21% across consecutive hours on day 1. Two consequences: adjacent
    strikes of the same liquid underlying quote very differently at any
    moment, and a single wide read may be staleness rather than truth.

    This helper fixes the MEASUREMENT, never the STANDARD:
      * quote every candidate once, keep each candidate's tightest reading;
      * if the best is a near miss (within `resample_band` x max_spread),
        re-read up to `resample` more rounds, keeping per-candidate minima;
      * return the tightest (candidate, quote, notes) seen overall.

    The caller's spread gate still delivers the verdict on what we return —
    a candidate that never tightens inside the cap is still rejected there.
    Total function: a raising/None-returning quote_fn marks that candidate
    unusable in that round rather than raising out.

    quote_fn(candidate) -> {"bid","ask","mid","spread_pct"} | None.
    sleep_fn() is called between resample rounds (None -> no sleep; tests
    inject a counter).
    Returns (candidate|None, quote|None, notes: list[str]).
    """
    notes = []
    if not candidates:
        return None, None, notes

    def read(c):
        try:
            q = quote_fn(c)
        except Exception:
            return None
        if not q:
            return None
        sp = q.get("spread_pct")
        if not isinstance(sp, (int, float)) or not math.isfinite(sp) or sp < 0:
            return None
        return q

    best = {}                                   # idx -> (spread, quote)
    for i, c in enumerate(candidates):
        q = read(c)
        if q is not None:
            best[i] = (q["spread_pct"], q)

    if not best:
        return None, None, notes

    def fits(i):
        """Affordability as part of SELECTION (2026-07-30, Connor's call).

        A tighter strike that one contract of cannot fit in the per-trade
        budget is not the best candidate — it is a guaranteed TOO-RICH
        rejection. Prefer the tightest AFFORDABLE candidate; if nothing
        fits, fall back to tightest overall so the rejection downstream
        still reports the truth."""
        if max_cost is None:
            return True
        ask = best[i][1].get("ask")
        return isinstance(ask, (int, float)) and 0 < ask * 100 <= max_cost

    def tightest():
        pool = [k for k in best if fits(k)] or list(best)
        i = min(pool, key=lambda k: best[k][0])
        return i, best[i][0], best[i][1]

    i0, sp0, q0 = tightest()
    if len(best) > 1:
        spreads = ", ".join(f"{best[k][0]*100:.1f}%" for k in sorted(best))
        notes.append(f"strike scan: {len(best)} quoted, spreads [{spreads}], "
                     f"tightest {sp0*100:.1f}%")

    rounds = 0
    while sp0 > max_spread and sp0 <= max_spread * resample_band and rounds < resample:
        rounds += 1
        if sleep_fn is not None:
            sleep_fn()
        for i in list(best):
            q = read(candidates[i])
            if q is not None and q["spread_pct"] < best[i][0]:
                best[i] = (q["spread_pct"], q)
        i1, sp1, q1 = tightest()
        if sp1 < sp0:
            notes.append(f"resample {rounds}: spread {sp0*100:.1f}% -> {sp1*100:.1f}%")
        i0, sp0, q0 = i1, sp1, q1
        if sp0 <= max_spread:
            break

    return candidates[i0], q0, notes
