"""
Tier-1 negative screens: the trades the literature says not to take.

WHY THIS MODULE EXISTS
----------------------
Nine independent research streams were run against this bot's design. They did
not agree about much, but every one of them independently condemned the same
combination the bot was built on: **buying naked, short-dated, out-of-the-money
options and managing them with percentage stops.**

The evidence, with sources:

  Boyer & Vorkink (JF 2014), "Stock options as lotteries" - options sorted into
    the highest ex-ante-skewness quintile returned about **-35% per week for
    7-day calls and -60% per week for 7-day puts**, measured at MIDPOINT prices,
    before the 75-100% spreads those contracts actually quote. The spread
    between skew-sorted portfolios is 10-50% per week.

  Ni (2009), "Stock option returns: a puzzle" - equity calls with K/S > 1.15
    returned **-36.86% per month**.

  Bogousslavsky & Muravyev (2023) - 889,967 actual retail option round-trips
    across 8 brokers, 2020-2022: mean **-0.93% per trade**, option **purchases
    -3.95%**, 0DTE about 4.7% worse. These are realized fills, not midpoints.

  Bryzgalova, Pavlova & Sikorskaya (JF 2023) - retail effective spread **6.7%
    of premium** overall; the sub-$250-premium bucket, which is **41.6% of all
    retail option trades**, costs **11.6%**; deep-OTM costs **28.4%**.

  Garcia-Ares & Muravyev (2025) - delta-hedged option returns are **-0.43% over
    expiration Friday plus the following Monday**, which is roughly **half of
    all abnormal option returns in a month, concentrated in two days**. Pure
    calendar effect, free to avoid.

  Dew-Becker (2025) - the harvestable variance risk premium ran a Sharpe of
    +0.74 before 2012 and **-0.12 after**.

  Duarte, Jones, Khorram & Mo, via Garcia-Ares & Muravyev (2025) - the best
    bias-corrected option anomaly in the literature is about **0.5% per month,
    Sharpe ~0.5**. That is the ceiling any screen here is defending.

Every function is pure and total: no network, no clock reads except where a date
is passed in, no exceptions on garbage input. A screen that raises inside the
entry loop would either halt trading or - worse - be caught by a broad `except`
and silently pass the trade through.

The screens are NEGATIVE. None of them says a trade is good. They say a trade is
one the literature has specifically measured losing money on. That asymmetry is
deliberate: the evidence for avoiding things is far stronger than the evidence
for picking them.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional, Sequence

try:
    import execution as _ex
except ImportError:      # pragma: no cover - lets the module import standalone
    _ex = None


# ---------------------------------------------------------------------------
# Thresholds. Every one carries the citation that set it.
# ---------------------------------------------------------------------------

# Boyer-Vorkink's highest-skew quintile is the one that loses 35-60%/week. Their
# breakpoint varies by sample; 4.0 is a conservative standing threshold for the
# short-dated equity contracts this bot looks at, and the tests pin the values
# it produces on representative contracts.
MAX_EX_ANTE_SKEW = 4.0

# Ni (2009): K/S > 1.15 calls return -36.86%/month. Symmetric bound for puts.
MAX_MONEYNESS_OTM = 0.15

# Beckmeyer et al.: contracts inside 1 hour of expiry quote 19.0% and cost 10.5%
# effective; >2% OTM calls cost 80.2% effective. Below ~2 DTE, vega is so small
# that a correct volatility view cannot pay for the spread - the trade is a pure
# direction bet with a fee attached.
MIN_DTE = 2

# The old gate was 15%, which admits a round trip costing ~11% of premium
# against a best-documented edge of 0.5%/month. Research note 07 recommends
# 2-4%. 4% is the loose end of that band; it is set here as the default and is
# still 3.6x tighter than what shipped.
MAX_SPREAD_PCT = 0.04

# Bryzgalova et al.: the sub-$250-premium bucket costs 11.6% effective. Below
# about $0.50 the fixed regulatory fees alone (~$0.0854/contract round-trip)
# start to matter, and PFOF capture of $0.60/contract is 6% round-trip on a
# $0.20 contract.
MIN_CONTRACT_PRICE = 0.50

# Bali, Cakici & Whitelaw (2011): stocks in the top MAX decile underperform.
# Retail attention concentrates there and options on them carry the biggest
# skewness premium.
MAX_LOTTERY_RETURN = 0.15


# ---------------------------------------------------------------------------
# 1. Ex-ante skewness (Boyer & Vorkink 2014)
# ---------------------------------------------------------------------------
def _norm_cdf(x: float) -> float:
    if x > 40:
        return 1.0
    if x < -40:
        return 0.0
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _log_norm_cdf(x: float) -> float:
    """log Phi(x), accurate arbitrarily far into the left tail.

    math.log(_norm_cdf(x)) is useless where this module needs it most: _norm_cdf
    hard-clamps to 0.0 below x = -40, and long before that the CDF has so few
    significant digits left that differences of moments built from it are noise.
    A 7-DTE 15%-OTM call at 15% vol sits at x = -6.7; a 3-DTE contract sits
    deeper still. Those are not corner cases here, they ARE the lottery bucket
    the screen exists to reject, so the log CDF is computed directly.
    """
    if x > -1.0:
        p = _norm_cdf(x)
        return math.log(p) if p > 0.0 else -math.inf
    y = -x / math.sqrt(2.0)                     # y >= 0.7071
    if y < 25.0:
        e = math.erfc(y)                        # erfc is accurate and normal here
        if e > 0.0:
            return math.log(0.5 * e)
    # Asymptotic expansion, valid for large y:
    #   erfc(y) = exp(-y^2)/(y*sqrt(pi)) * (1 - 1/(2y^2) + 3/(4y^4) - 15/(8y^6) + ...)
    # At y = 25 the first neglected term is ~1e-13 relative, so this is seamless.
    w = 1.0 / (2.0 * y * y)
    series = 1.0 - w * (1.0 - 3.0 * w * (1.0 - 5.0 * w * (1.0 - 7.0 * w)))
    return (-y * y - math.log(y) - 0.5 * math.log(math.pi) - math.log(2.0)
            + math.log(series))


def _truncated_moment(spot: float, strike: float, t: float, vol: float,
                      drift: float, j: int, is_call: bool) -> float:
    """E[S_T^j * 1{S_T > K}] (call) or E[S_T^j * 1{S_T < K}] (put).

    Reference implementation, kept because it is the definition the docstring of
    `ex_ante_skewness` refers to. That function does NOT call it: differencing
    these raw moments binomially destroys every significant digit in the tail
    (see the reformulation there), so it is correct as a statement of the maths
    and unusable as an algorithm.

    Under the real-world lognormal ln S_T ~ N(m, s^2), with
    m = ln S0 + (drift - vol^2/2) t and s = vol * sqrt(t):

        E[S_T^j 1{S_T>K}] = exp(j m + j^2 s^2 / 2) * Phi( (m - ln K)/s + j s )

    This is the moment machinery behind Boyer & Vorkink's ex-ante skewness. The
    drift is the physical drift, NOT the risk-free rate: skewness of the
    *return distribution the buyer faces* is a real-world quantity.
    """
    s = vol * math.sqrt(t)
    if s <= 0:
        return 0.0
    m = math.log(spot) + (drift - 0.5 * vol * vol) * t
    z = (m - math.log(strike)) / s + j * s
    tail = _norm_cdf(z) if is_call else _norm_cdf(-z)
    return math.exp(j * m + 0.5 * j * j * s * s) * tail


def ex_ante_skewness(spot: float, strike: float, t_years: float, vol: float,
                     is_call: bool = True, drift: float = 0.0) -> Optional[float]:
    """Skewness of the option's payoff (== skewness of its return).

    Return = payoff/price - 1 is an affine transform of the payoff, and skewness
    is invariant to affine transforms, so the payoff skewness IS the return
    skewness. That is the whole trick in Boyer & Vorkink's construction, and it
    means this can be computed at entry from quantities we already have.

    Higher is worse. A far-OTM, short-dated contract has enormous positive
    skewness - it almost always expires worthless and occasionally pays 20x -
    and Boyer & Vorkink show buyers systematically overpay for exactly that
    shape. Their top-skew quintile of 7-day contracts lost about 35% (calls) to
    60% (puts) per week at midpoint prices.

    Returns None on unusable inputs rather than raising.
    """
    try:
        S, K = float(spot), float(strike)
        t, v = float(t_years), float(vol)
        mu = float(drift)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(x) for x in (S, K, t, v, mu)):
        return None
    if S <= 0 or K <= 0 or t <= 0 or v <= 0:
        return None

    call = bool(is_call)
    sgn = 1.0 if call else -1.0     # +1 keeps the call algebra, -1 flips it to the put

    # The binomial expansion of E[(S-K)^n 1{S>K}] in raw truncated moments is
    # exact on paper and catastrophic in floating point: for an OTM contract the
    # terms are near-equal tail quantities that cancel to relative order
    # (s/|d|)^n, so the THIRD moment loses ~3*log10(|d|/s) digits. At S=100,
    # K=115, 7 DTE, 15% vol that is every digit there is, and the naive form
    # returns a large NEGATIVE skewness for a payoff that is positively skewed
    # by construction — which then sails straight through skew_verdict.
    #
    # Reformulation. With s = vol*sqrt(t), m = ln S + (mu - vol^2/2) t and
    # d = (m - ln K)/s, write P = P(exercise) and
    #     R_j = E[S_T^j 1{exercise}] / (K^j P) = exp(j s d + j^2 s^2/2)
    #                                            * Phi(sgn(d + j s)) / Phi(sgn d)
    # so that m_n = K^n P D_n with
    #     D_1 = R_1 - 1,  D_2 = R_2 - 2R_1 + 1,  D_3 = R_3 - 3R_2 + 3R_1 - 1
    # (the put flips the sign of the odd differences). log R_j is exact to
    # machine precision, expm1 turns it into R_j - 1 with no cancellation at
    # all, K cancels out of the skewness entirely, and the 1/sqrt(P) blow-up
    # that makes deep-OTM skewness enormous becomes an explicit analytic factor
    # instead of an emergent one. Verified against Monte Carlo and against an
    # independent positive-integrand quadrature in test_screens.py.
    try:
        s = v * math.sqrt(t)
        if not (s > 0.0):
            return None
        m = math.log(S) + (mu - 0.5 * v * v) * t
        d = (m - math.log(K)) / s
        log_p = _log_norm_cdf(sgn * d)
        r = [math.expm1(j * s * d + 0.5 * j * j * s * s
                        + _log_norm_cdf(sgn * (d + j * s)) - log_p)
             for j in (1, 2, 3)]
    except (OverflowError, ValueError, ZeroDivisionError):
        return None

    d1 = sgn * r[0]
    d2 = r[1] - 2.0 * r[0]
    d3 = sgn * (r[2] - 3.0 * r[1] + 3.0 * r[0])
    p = math.exp(log_p) if log_p > -700.0 else 0.0

    var = d2 - p * d1 * d1
    mu3 = d3 - 3.0 * p * d1 * d2 + 2.0 * p * p * d1 ** 3
    if not math.isfinite(var) or var <= 0.0:
        return None
    # A long OTM option's payoff is non-negative with an atom at zero, so its
    # third central moment is strictly positive. If the arithmetic disagrees,
    # the third difference has run out of significant digits and the honest
    # answer is "not computable" — NOT a negative number, which would pass the
    # screen. ITM contracts genuinely can be negatively skewed (a deep ITM put
    # is a short forward), so the invariant is asserted only where it holds.
    if sgn * d < 0.0 and not (mu3 > 0.0):
        return None
    try:
        skew = math.exp(-0.5 * log_p) * mu3 / var ** 1.5
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(skew):
        return None
    return round(skew, 4)


def skew_verdict(skew: Optional[float], threshold: float = MAX_EX_ANTE_SKEW) -> dict:
    """Turn an ex-ante skewness into a pass/fail with a readable reason."""
    if skew is None:
        # Unknown is not the same as fine, but blocking on a failed calculation
        # would halt trading whenever the vol input is missing. Pass with a flag
        # so the caller can log it.
        return {"ok": True, "skew": None, "reason": "ex-ante skew not computable"}
    # `skew` normally arrives straight from ex_ante_skewness(), which returns
    # float-or-None - but this function is also called with values that came out
    # of a config file or a cached JSON blob, where "3.5" and None-shaped junk
    # both turn up. Comparing a str to a float raises TypeError, which the module
    # header forbids, so coerce first and treat failure as "not computable".
    # NaN joins that branch; +/-inf deliberately does NOT, because inf is ordered
    # and an infinite skew must still be *blocked* rather than shrugged through.
    try:
        skew = float(skew)
    except (TypeError, ValueError, OverflowError):
        return {"ok": True, "skew": None, "reason": "ex-ante skew not computable"}
    if math.isnan(skew):
        return {"ok": True, "skew": None, "reason": "ex-ante skew not computable"}
    # An unusable threshold falls back to the documented default rather than
    # disabling the screen: threshold=nan would make every comparison False and
    # silently pass everything, which is the one outcome worth ruling out.
    try:
        threshold = float(threshold)
    except (TypeError, ValueError, OverflowError):
        threshold = MAX_EX_ANTE_SKEW
    if math.isnan(threshold):
        threshold = MAX_EX_ANTE_SKEW
    if skew > threshold:
        return {"ok": False, "skew": skew,
                "reason": f"ex-ante skew {skew:.1f} > {threshold:.1f} — Boyer-Vorkink "
                          f"top-skew quintile returned -35%/wk (calls) to -60%/wk (puts)"}
    return {"ok": True, "skew": skew, "reason": f"ex-ante skew {skew:.1f} acceptable"}


# ---------------------------------------------------------------------------
# 2. Moneyness (Ni 2009)
# ---------------------------------------------------------------------------
def moneyness_verdict(spot: float, strike: float, is_call: bool,
                      max_otm: float = MAX_MONEYNESS_OTM) -> dict:
    """Reject contracts further OTM than Ni's documented -36.86%/month bucket."""
    try:
        S, K = float(spot), float(strike)
    except (TypeError, ValueError, OverflowError):
        return {"ok": True, "otm": None, "reason": "moneyness not computable"}
    if S <= 0 or K <= 0 or not math.isfinite(S) or not math.isfinite(K):
        return {"ok": True, "otm": None, "reason": "moneyness not computable"}
    otm = (K / S - 1.0) if is_call else (1.0 - K / S)
    if otm > max_otm:
        return {"ok": False, "otm": round(otm, 4),
                "reason": f"{otm*100:.1f}% OTM > {max_otm*100:.0f}% — Ni (2009) measured "
                          f"-36.9%/month on K/S>1.15 equity calls"}
    return {"ok": True, "otm": round(otm, 4), "reason": f"{otm*100:+.1f}% OTM"}


# ---------------------------------------------------------------------------
# 3. Expiration-rollover blackout (Garcia-Ares & Muravyev 2025)
# ---------------------------------------------------------------------------
def third_friday(year: int, month: int) -> dt.date:
    """The monthly option expiration date (third Friday)."""
    d = dt.date(year, month, 1)
    # weekday(): Monday=0 ... Friday=4
    first_friday = 1 + (4 - d.weekday()) % 7
    return dt.date(year, month, first_friday + 14)


def in_expiration_blackout(day: dt.date) -> dict:
    """Is `day` the monthly expiration Friday or the following Monday?

    Garcia-Ares & Muravyev find delta-hedged option returns of **-0.43% across
    those two days**, which is roughly half of all abnormal option returns in a
    month compressed into two sessions. The mechanism is the expiration
    rollover: customer order imbalance runs about -12% as positions roll, and
    the resulting price pressure is what Goyal-Saretto's famous straddle result
    was largely picking up (their portfolio formed on exactly that Monday).

    This is the cheapest screen in the module. It costs two trading days a month
    and requires no data at all.
    """
    try:
        # dt.datetime is a SUBCLASS of dt.date, so `isinstance(day, dt.date)`
        # accepts one — and then `day == exp` is silently False for the expiry
        # Friday itself and `day - exp` raises outright on the Monday. A caller
        # passing dt.datetime.now() is the obvious mistake, so normalise here.
        if isinstance(day, dt.datetime):
            d = day.date()
        elif isinstance(day, dt.date):
            d = day
        else:
            s = str(day).strip()
            try:
                d = dt.date.fromisoformat(s)
            except ValueError:
                # Same trap one level down: an ISO *timestamp* string is what an
                # API hands back ("2026-07-17T09:30:00-04:00"), and
                # date.fromisoformat rejects it. Failing open there would let the
                # blackout silently not fire on the one day it exists for.
                d = dt.datetime.fromisoformat(
                    s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s).date()
    except (TypeError, ValueError):
        return {"ok": True, "reason": "date not parseable"}
    exp = third_friday(d.year, d.month)
    if d == exp:
        return {"ok": False, "reason": f"monthly expiration Friday ({exp}) — "
                                       f"Garcia-Ares & Muravyev: -0.43% delta-hedged "
                                       f"across expiry Fri + following Mon"}
    # The Monday after expiration Friday (Tuesday if Monday is a holiday is not
    # detectable without a calendar; the Friday leg carries most of the effect).
    if d.weekday() == 0 and (d - exp).days == 3:
        return {"ok": False, "reason": f"post-expiration Monday (expiry was {exp}) — "
                                       f"customer roll imbalance ~-12%, the other half "
                                       f"of the -0.43% two-day effect"}
    return {"ok": True, "reason": "not in expiration-rollover window"}


# ---------------------------------------------------------------------------
# 4. Days to expiry - the vega argument
# ---------------------------------------------------------------------------
def dte_verdict(dte: Optional[int], min_dte: int = MIN_DTE) -> dict:
    """Reject contracts too short-dated for a volatility view to pay.

    Vega goes to zero as T goes to zero. That is not a detail - it means that on
    a 0-2 DTE contract you cannot be paid for being right about volatility, only
    for being right about direction, and you pay a spread for the privilege.
    Beckmeyer et al. measure 19.0% quoted / 10.5% effective inside the last hour;
    Bogousslavsky & Muravyev find 0DTE about 4.7pp worse than the already-negative
    retail average.
    """
    if dte is None:
        return {"ok": True, "dte": None, "reason": "DTE unknown"}
    try:
        d = int(dte)
    except (TypeError, ValueError, OverflowError):
        return {"ok": True, "dte": None, "reason": "DTE unparseable"}
    if d < min_dte:
        return {"ok": False, "dte": d,
                "reason": f"{d} DTE < {min_dte} — vega ~0, so a volatility view cannot "
                          f"pay; 0DTE retail fills run ~4.7pp worse than average"}
    return {"ok": True, "dte": d, "reason": f"{d} DTE"}


# ---------------------------------------------------------------------------
# 5. Spread and premium gates (research note 07)
# ---------------------------------------------------------------------------
def spread_verdict(bid: float, ask: float, max_spread: float = MAX_SPREAD_PCT) -> dict:
    """Reject on quoted spread, and report the modelled round-trip cost.

    The identity (research note 07 §H):
        RT = S(2 - c_e - c_x) / (2 + S(1 - c_e)) + fees/premium

    At S = 15% - the gate this bot shipped with - a realistic capture profile
    (half the half-spread on entry, nothing on a forced exit) gives **~11.0% of
    premium per round trip**. The best bias-corrected option anomaly in the
    literature is 0.5%/month. The old gate was loose by about 22 months of the
    best documented edge, per trade.
    """
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError, OverflowError):
        return {"ok": False, "spread_pct": None, "rt_cost": None,
                "reason": "quote unparseable — refusing to trade blind"}
    if not (math.isfinite(b) and math.isfinite(a)) or b <= 0 or a <= 0 or a < b:
        return {"ok": False, "spread_pct": None, "rt_cost": None,
                "reason": "quote invalid — refusing to trade blind"}
    mid = (a + b) / 2.0
    sp = (a - b) / mid
    rt = None
    if _ex is not None:
        try:
            rt = _ex.roundtrip_cost_frac(b, a)
        except Exception:
            rt = None
    if sp > max_spread:
        return {"ok": False, "spread_pct": round(sp, 4), "rt_cost": rt,
                "reason": f"spread {sp*100:.1f}% > {max_spread*100:.0f}% — modelled "
                          f"round trip {('%.1f%%' % (rt*100)) if rt else 'n/a'} of premium "
                          f"vs a best-documented edge of 0.5%/month"}
    return {"ok": True, "spread_pct": round(sp, 4), "rt_cost": rt,
            "reason": f"spread {sp*100:.1f}%, round trip "
                      f"{('%.1f%%' % (rt*100)) if rt else 'n/a'}"}


def premium_verdict(price: float, min_price: float = MIN_CONTRACT_PRICE) -> dict:
    """Reject contracts cheap enough that fixed costs dominate.

    Alpaca's Q1 2026 Rule 606 filing shows **$0.60 per contract** of payment for
    order flow (formula: $0.06 + 9% of spread dollars, capped at $0.60 - the cap
    binds above a 6-cent spread, so effectively all flow pays it). On a $0.20
    contract that is 6% round-trip before anything else. Regulatory fees add
    about $0.0854/contract round-trip. Bryzgalova et al. measure the sub-$250-
    premium bucket - 41.6% of all retail option trades - at 11.6% effective.
    """
    try:
        p = float(price)
    except (TypeError, ValueError, OverflowError):
        return {"ok": False, "price": None, "reason": "price unparseable"}
    if not math.isfinite(p) or p <= 0:
        return {"ok": False, "price": None, "reason": "price invalid"}
    if p < min_price:
        # $0.60 is paid per ORDER (Rule 606 reports payment per contract on
        # orders routed), so a round trip is two of them: $1.20 against a $20
        # premium on a $0.20 contract = 6%. Note 07 §I.1 states it explicitly —
        # ">3% each way, >6% round trip" — as do this module's own header and
        # this function's docstring. The one-sided form silently halved it.
        pfof = 2.0 * 0.60 / (p * 100.0)
        return {"ok": False, "price": round(p, 2),
                "reason": f"${p:.2f} contract < ${min_price:.2f} — $0.60/contract PFOF "
                          f"alone is {pfof*100:.1f}% round trip"}
    return {"ok": True, "price": round(p, 2), "reason": f"${p:.2f} contract"}


# ---------------------------------------------------------------------------
# 6. MAX lottery screen (Bali, Cakici & Whitelaw 2011)
# ---------------------------------------------------------------------------
def max_daily_return(closes: Sequence[float], lookback: int = 21) -> Optional[float]:
    """The MAX factor: largest single-day return over the lookback."""
    try:
        vals = [float(c) for c in closes if c is not None and math.isfinite(float(c)) and float(c) > 0]
    except (TypeError, ValueError, OverflowError):
        return None
    if len(vals) < 3:
        return None
    tail = vals[-(lookback + 1):]
    rets = [tail[i] / tail[i - 1] - 1.0 for i in range(1, len(tail))]
    return round(max(rets), 4) if rets else None


def lottery_verdict(closes: Sequence[float], threshold: float = MAX_LOTTERY_RETURN) -> dict:
    """Reject underlyings that just printed a lottery-sized single-day move.

    Bali, Cakici & Whitelaw (2011): top-MAX-decile stocks underperform. Bali,
    Hirshleifer, Peng, Tang & Wang (NBER w29543) show the mechanism running
    through social media - discussion of lottery stocks drives retail buying and
    **weaker** subsequent returns. Barber & Odean (RFS 2008) measure a +29.50%
    buy imbalance in the top attention decile and conclude investors do not
    benefit from it.

    An options bot reading a crowd-sentiment feed is aimed directly at this
    bucket, which is why the screen sits here rather than inside a sleeve.
    """
    mx = max_daily_return(closes)
    if mx is None:
        return {"ok": True, "max_ret": None, "reason": "MAX not computable"}
    if mx > threshold:
        return {"ok": False, "max_ret": mx,
                "reason": f"MAX {mx*100:.1f}% > {threshold*100:.0f}% in the last month — "
                          f"top-MAX-decile stocks underperform (Bali-Cakici-Whitelaw)"}
    return {"ok": True, "max_ret": mx, "reason": f"MAX {mx*100:.1f}%"}


# ---------------------------------------------------------------------------
# 7. The combined screen
# ---------------------------------------------------------------------------
def _cfg(cfg: dict, key: str, default: float) -> float:
    """A threshold from `config`, falling back to the default if it is unusable.

    Overrides arrive from a config file or an environment variable, where 0.04
    is the string "0.04" and a typo is None. `float > str` raises, and a raise
    inside the entry loop is exactly the failure mode this module exists to
    avoid, so a bad override degrades to the documented default instead.
    """
    try:
        v = float(cfg[key])
    except (KeyError, TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


def screen_entry(*, spot: float, strike: float, is_call: bool, dte: Optional[int],
                 bid: float, ask: float, vol: Optional[float] = None,
                 closes: Optional[Sequence[float]] = None,
                 day: Optional[dt.date] = None,
                 config: Optional[dict] = None) -> dict:
    """Run every Tier-1 screen. Returns {ok, failed: [...], checks: {...}}.

    ALL screens run even after the first failure, so the log shows every reason
    a trade was rejected rather than just the first one. That matters for the
    learning loop: "rejected for spread" and "rejected for spread AND skew AND
    moneyness" are different pieces of information about the signal that
    produced it.

    `config` may override any threshold by name (max_skew, max_otm, min_dte,
    max_spread, min_price, max_lottery) so the thresholds stay tunable without
    editing this file.
    """
    # A non-dict here (a list from a mis-typed config file, say) must not take
    # the entry loop down: `config or {}` passes a str straight through to
    # .get(). Totality is the whole point of this module.
    cfg = config if isinstance(config, dict) else {}
    checks: dict = {}

    price = None
    try:
        if bid is not None and ask is not None and float(bid) > 0 and float(ask) > 0:
            price = (float(bid) + float(ask)) / 2.0
    except (TypeError, ValueError, OverflowError):
        price = None

    checks["spread"] = spread_verdict(bid, ask, _cfg(cfg, "max_spread", MAX_SPREAD_PCT))
    checks["premium"] = (premium_verdict(price, _cfg(cfg, "min_price", MIN_CONTRACT_PRICE))
                         if price is not None
                         else {"ok": False, "reason": "no usable quote"})
    checks["dte"] = dte_verdict(dte, _cfg(cfg, "min_dte", MIN_DTE))
    checks["moneyness"] = moneyness_verdict(spot, strike, is_call,
                                            _cfg(cfg, "max_otm", MAX_MONEYNESS_OTM))
    checks["expiration"] = in_expiration_blackout(day or dt.date.today())

    if vol is not None and dte is not None:
        try:
            t_years = max(int(dte), 0) / 365.0
        except (TypeError, ValueError, OverflowError):
            t_years = 0.0
        sk = ex_ante_skewness(spot, strike, t_years, vol, is_call) if t_years > 0 else None
        checks["skew"] = skew_verdict(sk, _cfg(cfg, "max_skew", MAX_EX_ANTE_SKEW))
    else:
        checks["skew"] = {"ok": True, "skew": None,
                          "reason": "no vol input — ex-ante skew skipped"}

    if closes:
        checks["lottery"] = lottery_verdict(closes, _cfg(cfg, "max_lottery", MAX_LOTTERY_RETURN))
    else:
        checks["lottery"] = {"ok": True, "max_ret": None, "reason": "no price history"}

    failed = [f"{k}: {v.get('reason')}" for k, v in checks.items() if not v.get("ok", True)]
    return {"ok": len(failed) == 0, "failed": failed, "checks": checks}
