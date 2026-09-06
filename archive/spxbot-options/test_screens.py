"""
Screen test harness — pins the ECONOMICS of the Tier-1 negative screens, not just
their plumbing. Run: python3 test_screens.py

No network, no broker, no Alpaca, standard library only. Every date-dependent
check passes an explicit `day=`; nothing here reads the clock, because a suite
that depends on today's date is a suite that rots.

What is actually being tested:

  * `third_friday` against real calendar dates, including the two months that
    break the `(4 - weekday) % 7` formula if the modulus is misplaced: one whose
    1st IS a Friday and one whose 1st is a Saturday.
  * `ex_ante_skewness` — the Boyer-Vorkink core — against an INDEPENDENT Monte
    Carlo of the same payoff moments. That is the single most valuable check in
    the file: the closed form is a truncated-moment expansion whose terms cancel
    catastrophically in the tail, and only a second method catches it.
  * that the screens fail in the direction they claim to. `skew_verdict` fails
    OPEN by design (an uncomputable skew must not halt trading); `spread_verdict`
    fails CLOSED by design (an unreadable quote must not be traded). Both are
    asserted deliberately, because getting either backwards is silent.
  * the regression case: the trade shape this bot actually shipped with — cheap,
    short-dated, near-the-money, quoted 15% wide — is rejected, for more than one
    reason, with every screen reporting.
"""
from __future__ import annotations
import sys, math, random, datetime as dt

import screens as SC

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")


def close(name, got, want, tol, detail=""):
    """Relative comparison against `want` (absolute when `want` is ~0)."""
    if got is None or want is None or not isinstance(got, (int, float)):
        check(name, False, detail or f"got {got!r}, want {want!r}")
        return
    scale = abs(want) if abs(want) > 1e-9 else 1.0
    err = abs(got - want) / scale
    check(name, err <= tol,
          detail or f"got {got:.6g} vs {want:.6g}  ({err*100:.3f}% off, tol {tol*100:g}%)")


# The last two are not paranoia: an int wider than a float64 is what a bad JSON
# payload or a units mix-up produces, and float()/int() raise OverflowError on it
# — a *third* exception type that a `except (TypeError, ValueError)` guard misses.
HUGE = 10 ** 400
GARBAGE = [None, "abc", float("nan"), float("inf"), float("-inf"), -1, 0, [], {}, object(),
           HUGE, -HUGE]


# --------------------------------------------------------------------------
print("=" * 74)
print("1. third_friday — against the real calendar")
print("=" * 74)
KNOWN = {
    (2026, 7):  dt.date(2026, 7, 17),
    (2026, 1):  dt.date(2026, 1, 16),
    (2026, 8):  dt.date(2026, 8, 21),
    (2027, 12): dt.date(2027, 12, 17),
    (2026, 2):  dt.date(2026, 2, 20),
}
for (y, m), want in KNOWN.items():
    got = SC.third_friday(y, m)
    check(f"{y}-{m:02d} -> {want}", got == want, f"got {got}")

# The two off-by-seven traps in `1 + (4 - weekday) % 7`.
check("month whose 1st IS a Friday (2026-05-01) -> 2026-05-15",
      SC.third_friday(2026, 5) == dt.date(2026, 5, 15),
      f"1st is {dt.date(2026,5,1):%A}, got {SC.third_friday(2026,5)}")
check("month whose 1st is a Saturday (2026-08-01) -> 2026-08-21",
      SC.third_friday(2026, 8) == dt.date(2026, 8, 21),
      f"1st is {dt.date(2026,8,1):%A}, got {SC.third_friday(2026,8)}")

bad_day, bad_dom, n = [], [], 0
for y in range(2024, 2031):
    for m in range(1, 13):
        d = SC.third_friday(y, m)
        n += 1
        if d.weekday() != 4:
            bad_day.append(d)
        if not (15 <= d.day <= 21):
            bad_dom.append(d)
        if d.month != m or d.year != y:
            bad_dom.append(d)
check(f"all {n} months 2024-2030 land on a Friday", not bad_day, str(bad_day[:3]))
check(f"all {n} months land on day-of-month 15..21", not bad_dom, str(bad_dom[:3]))

print()
print("=" * 74)
print("2. in_expiration_blackout — Garcia-Ares & Muravyev's two days")
print("=" * 74)
EXP = dt.date(2026, 7, 17)                    # July 2026 monthly expiration
check("expiry Friday 2026-07-17 -> BLOCKED", SC.in_expiration_blackout(EXP)["ok"] is False,
      SC.in_expiration_blackout(EXP)["reason"][:52])
mon = dt.date(2026, 7, 20)
check("the following Monday 2026-07-20 -> BLOCKED",
      SC.in_expiration_blackout(mon)["ok"] is False,
      SC.in_expiration_blackout(mon)["reason"][:52])
check("the Thursday before 2026-07-16 -> ok",
      SC.in_expiration_blackout(dt.date(2026, 7, 16))["ok"] is True)
check("the Tuesday after 2026-07-21 -> ok",
      SC.in_expiration_blackout(dt.date(2026, 7, 21))["ok"] is True)
check("a Monday that is NOT expiry+3 (2026-07-13) -> ok",
      SC.in_expiration_blackout(dt.date(2026, 7, 13))["ok"] is True,
      f"{dt.date(2026,7,13):%A}, {(dt.date(2026,7,13)-EXP).days}d from expiry")
check("a Monday two weeks after (2026-07-27) -> ok",
      SC.in_expiration_blackout(dt.date(2026, 7, 27))["ok"] is True)

check("ISO string '2026-07-17' -> BLOCKED",
      SC.in_expiration_blackout("2026-07-17")["ok"] is False)
check("ISO string '2026-07-20' -> BLOCKED",
      SC.in_expiration_blackout("2026-07-20")["ok"] is False)
check("ISO string '2026-07-16' -> ok",
      SC.in_expiration_blackout("2026-07-16")["ok"] is True)

# dt.datetime is a SUBCLASS of dt.date, so an isinstance() gate lets one through.
# dt.datetime.now() is the obvious thing a caller passes.
try:
    r = SC.in_expiration_blackout(dt.datetime(2026, 7, 20, 10, 30))
    check("a datetime on the post-expiry Monday -> BLOCKED, does not raise",
          r["ok"] is False, r["reason"][:52])
except Exception as e:                                       # noqa: BLE001
    check("a datetime on the post-expiry Monday -> BLOCKED, does not raise",
          False, f"raised {type(e).__name__}: {e}")
r = SC.in_expiration_blackout(dt.datetime(2026, 7, 17, 15, 59))
check("a datetime on expiry Friday -> BLOCKED", r["ok"] is False)

# NOT garbage: 20260717 is ISO 8601 *basic* format, which date.fromisoformat()
# has accepted since Python 3.11. It must be parsed, not shrugged off — a screen
# that silently passed a real expiry Friday because it arrived unhyphenated would
# be the exact failure this blackout exists to prevent. Guarded by version so the
# suite states the right expectation on either side of 3.11.
_basic_iso_parses = True
try:
    dt.date.fromisoformat("20260717")
except ValueError:
    _basic_iso_parses = False
r = SC.in_expiration_blackout("20260717")
check("ISO-basic '20260717' is parsed as the expiry Friday, not treated as garbage",
      (r["ok"] is False) if _basic_iso_parses else (r["ok"] is True),
      f"py{sys.version_info.major}.{sys.version_info.minor} basic-ISO "
      f"{'supported' if _basic_iso_parses else 'unsupported'} — {r['reason']}")

# The string form of the dt.datetime trap. An ISO *timestamp* is what a data API
# hands back, and date.fromisoformat() rejects every one of these. Failing open
# would mean the blackout silently does not fire on the one day it exists for.
for lbl, s in [("naive", "2026-07-17T09:30:00"),
               ("tz-aware", "2026-07-17T09:30:00-04:00"),
               ("Z suffix", "2026-07-17T13:30:00Z"),
               ("space separator", "2026-07-17 09:30:00")]:
    check(f"ISO timestamp string ({lbl}) on expiry Friday -> BLOCKED",
          SC.in_expiration_blackout(s)["ok"] is False, s)
check("ISO timestamp string on the post-expiry Monday -> BLOCKED",
      SC.in_expiration_blackout("2026-07-20 10:00:00")["ok"] is False)
check("ISO timestamp string on the Thursday before -> ok",
      SC.in_expiration_blackout("2026-07-16T09:30:00")["ok"] is True)

for g in GARBAGE + ["2026-13-45", "next tuesday", 3.5, "", "2026-07-17T25:99:99"]:
    try:
        r = SC.in_expiration_blackout(g)
        ok = r["ok"] is True and isinstance(r.get("reason"), str) and r["reason"]
    except Exception as e:                                   # noqa: BLE001
        ok = False
        r = f"raised {type(e).__name__}"
    check(f"garbage {str(g)[:18]!r} -> ok True with a reason, never raises", bool(ok),
          r["reason"] if isinstance(r, dict) else str(r))

print()
print("=" * 74)
print("3. ex_ante_skewness — sign, magnitude and ordering")
print("=" * 74)
far = SC.ex_ante_skewness(100, 120, 3 / 365, 0.35, True)      # 20% OTM, 3 DTE
near = SC.ex_ante_skewness(100, 100, 90 / 365, 0.35, True)    # ATM, 90 DTE
check("far-OTM short-dated call has LARGE positive skew", far is not None and far > 50,
      f"K/S=1.20, 3 DTE -> {far}")
check("near-the-money longer-dated call has small skew", near is not None and near < 5,
      f"K/S=1.00, 90 DTE -> {near}")
check("far-OTM skew is >> near-ATM skew", far / near > 20, f"ratio {far/near:.0f}x")

# Ladder across moneyness at fixed T: skew must fall monotonically toward ATM.
for is_call in (True, False):
    tag = "call" if is_call else "put"
    for vol in (0.15, 0.25, 0.35, 0.60):
        steps = [0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15]
        lad = [SC.ex_ante_skewness(100, 100 * (1 + s) if is_call else 100 * (1 - s),
                                   7 / 365, vol, is_call) for s in steps]
        ok = all(x is not None for x in lad) and all(lad[i] < lad[i + 1] for i in range(len(lad) - 1))
        check(f"{tag} ladder monotone DECREASING toward ATM (7 DTE, vol {vol})", ok,
              " < ".join("%.3g" % x for x in lad) if all(x is not None for x in lad) else str(lad))

# Ladder across T at fixed moneyness, over the horizon this bot trades.
# NOTE: beyond ~30-60 DTE at high vol the lognormal's OWN right skew (which grows
# with sigma^2 T) starts to dominate the truncation effect and the call curve
# turns back up. That is real, not a defect; it is out of the traded regime and
# is asserted separately below.
DTES = (1, 2, 3, 5, 7, 10, 14, 21, 30)
for is_call in (True, False):
    tag = "call" if is_call else "put"
    for vol in (0.15, 0.20, 0.30, 0.35, 0.50, 0.60):
        K = 105.0 if is_call else 95.0
        lad = [SC.ex_ante_skewness(100, K, d / 365, vol, is_call) for d in DTES]
        ok = all(x is not None for x in lad) and all(lad[i] > lad[i + 1] for i in range(len(lad) - 1))
        check(f"{tag} ladder monotone DECREASING in T (5% OTM, vol {vol}, 1-30 DTE)", ok,
              " > ".join("%.4g" % x for x in lad) if all(x is not None for x in lad) else str(lad))

lo = SC.ex_ante_skewness(100, 101, 120 / 365, 0.60, True)
hi = SC.ex_ante_skewness(100, 101, 10 / 365, 0.60, True)
check("documented: at 120 DTE / 60% vol the call curve has turned back UP",
      lo > hi, f"10 DTE {hi} vs 120 DTE {lo} — lognormal's own skew takes over")

# Positivity: a long option payoff is non-negative with an atom at zero, so its
# skewness is positive by construction. A negative value would PASS skew_verdict.
neg, none_ct, tot = [], 0, 0
for vol in (0.08, 0.10, 0.15, 0.20, 0.25, 0.35, 0.50, 0.80, 1.20):
    for d in (1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60, 90, 120):
        for otm in (0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15):
            for is_call in (True, False):
                K = 100 * (1 + otm) if is_call else 100 * (1 - otm)
                v = SC.ex_ante_skewness(100, K, d / 365, vol, is_call)
                tot += 1
                if v is None:
                    none_ct += 1
                elif v <= 0:
                    neg.append((vol, d, otm, "C" if is_call else "P", v))
check(f"{tot}-point grid: skewness is ALWAYS positive for OTM contracts", not neg,
      f"{len(neg)} negative, e.g. {neg[:2]}" if neg else f"{tot - none_ct} computed, {none_ct} None")
check(f"{tot}-point grid: every point is computable", none_ct == 0, f"{none_ct} None")

# The put branch, hard. An OTM put's payoff is positively skewed even though the
# underlying move is DOWN. A sign error in the binomial expansion lands here.
p_far = SC.ex_ante_skewness(100, 80, 7 / 365, 0.35, False)
p_near = SC.ex_ante_skewness(100, 99, 30 / 365, 0.35, False)
check("OTM put returns a LARGE POSITIVE skew (20% OTM, 7 DTE)",
      p_far is not None and p_far > 100, f"{p_far}")
check("near-ATM put skew is small and positive", 0 < p_near < 5, f"{p_near}")
check("deep-OTM put >> near-ATM put", p_far / p_near > 20, f"ratio {p_far/p_near:.0f}x")

# Put/call sanity at equal ABSOLUTE distance from spot. They are not identical —
# ln(115/100) != -ln(85/100), so the lognormal does not treat them symmetrically —
# but a sign-flipped expansion shows up as orders of magnitude, not tens of percent.
for dist in (2, 5, 8, 10, 12, 15):
    c = SC.ex_ante_skewness(100, 100 + dist, 7 / 365, 0.35, True)
    p = SC.ex_ante_skewness(100, 100 - dist, 7 / 365, 0.35, False)
    ratio = c / p
    check(f"put and call at +/-{dist} of spot are within 3x of each other",
          (1 / 3.0) <= ratio <= 3.0, f"call {c:.4g} / put {p:.4g} = {ratio:.3f}x")

BAD_INPUT = [
    ("t <= 0",        (100, 105, 0.0, 0.35)),
    ("t negative",    (100, 105, -0.5, 0.35)),
    ("vol <= 0",      (100, 105, 7 / 365, 0.0)),
    ("vol negative",  (100, 105, 7 / 365, -0.35)),
    ("spot <= 0",     (0, 105, 7 / 365, 0.35)),
    ("spot negative", (-100, 105, 7 / 365, 0.35)),
    ("strike <= 0",   (100, 0, 7 / 365, 0.35)),
    ("strike neg",    (100, -105, 7 / 365, 0.35)),
    ("None spot",     (None, 105, 7 / 365, 0.35)),
    ("None strike",   (100, None, 7 / 365, 0.35)),
    ("None t",        (100, 105, None, 0.35)),
    ("None vol",      (100, 105, 7 / 365, None)),
    ("'abc' spot",    ("abc", 105, 7 / 365, 0.35)),
    ("'abc' vol",     (100, 105, 7 / 365, "abc")),
    ("inf spot",      (float("inf"), 105, 7 / 365, 0.35)),
    ("inf t",         (100, 105, float("inf"), 0.35)),
    ("nan vol",       (100, 105, 7 / 365, float("nan")),),
    ("nan strike",    (100, float("nan"), 7 / 365, 0.35)),
    ("list spot",     ([1, 2], 105, 7 / 365, 0.35)),
]
for label, args in BAD_INPUT:
    try:
        got = SC.ex_ante_skewness(*args)
        ok = got is None
        det = f"got {got!r}"
    except Exception as e:                                   # noqa: BLE001
        ok, det = False, f"raised {type(e).__name__}: {e}"
    check(f"{label} -> None, never raises", ok, det)

for label, args in [("K = 3*S, 1 DTE", (100, 300, 1 / 365, 0.35, True)),
                    ("K = 3*S, T=1e-6", (100, 300, 1e-6, 0.35, True)),
                    ("K = 3*S, T=1e-9, vol 0.05", (100, 300, 1e-9, 0.05, True)),
                    ("K = S/3, 1 DTE put", (100, 33.3, 1 / 365, 0.35, False)),
                    ("K = 3*S, 1 DTE, vol 5.0", (100, 300, 1 / 365, 5.0, True))]:
    try:
        got = SC.ex_ante_skewness(*args)
        ok = got is None or (isinstance(got, float) and math.isfinite(got))
        det = f"got {got!r}"
    except Exception as e:                                   # noqa: BLE001
        ok, det = False, f"raised {type(e).__name__}: {e}"
    check(f"{label} -> None or a finite float, never inf/nan", ok, det)

# Where None is the RIGHT answer: past roughly K/S = 1.5 at 1 DTE the exercise
# probability is below e^-1500, so the true skewness (~exp(-logP/2)) is larger
# than a float64 can hold. Returning None there is honest — but skew_verdict
# fails OPEN on None, so this is the one place the skew screen has a blind spot.
# It matters only if nothing else catches the contract, so assert that something
# does: at that distance the moneyness gate has already rejected it outright.
BLIND = [(100.0, 150.0, 1 / 365, 0.10, True), (100.0, 200.0, 1 / 365, 0.10, True),
         (100.0, 100 / 1.5, 1 / 365, 0.10, False), (100.0, 50.0, 1 / 365, 0.10, False)]
for spot, K, t, vol, call in BLIND:
    s = SC.ex_ante_skewness(spot, K, t, vol, call)
    check(f"K={K:g} {'call' if call else 'put'} 1DTE vol 0.10 -> None "
          f"(P(exercise) < 1e-650, skew exceeds float64)", s is None, f"got {s!r}")
    check("  ...and the fail-open skew branch is covered: moneyness rejects it anyway",
          SC.moneyness_verdict(spot, K, call)["ok"] is False
          and SC.skew_verdict(s)["ok"] is True,
          f"moneyness: {SC.moneyness_verdict(spot, K, call)['reason'][:44]}")
r = SC.screen_entry(spot=100.0, strike=200.0, is_call=True, dte=1, bid=0.90, ask=0.92,
                    vol=0.10, closes=None, day=dt.date(2026, 7, 8))
_keys = {f.split(":", 1)[0] for f in r["failed"]}
check("  ...and screen_entry REJECTS it despite the skew screen abstaining",
      r["ok"] is False and r["checks"]["skew"]["skew"] is None
      and {"moneyness", "dte"} <= _keys,
      f"skew abstained, but failed on {sorted(_keys)}")

print()
print("=" * 74)
print("4. ex_ante_skewness vs MONTE CARLO — independent verification")
print("=" * 74)


def mc_payoff_skewness(spot, strike, t, vol, is_call, n, seed):
    """Sample skewness of the option payoff under the same real-world lognormal
    the closed form assumes (drift 0). Deterministic: fixed seed, own Random."""
    rnd = random.Random(seed)
    gauss = rnd.gauss
    m = math.log(spot) - 0.5 * vol * vol * t
    s = vol * math.sqrt(t)
    s1 = s2 = s3 = 0.0
    for _ in range(n):
        st = math.exp(m + s * gauss(0.0, 1.0))
        x = (st - strike) if is_call else (strike - st)
        if x < 0.0:
            x = 0.0
        s1 += x
        s2 += x * x
        s3 += x * x * x
    m1, m2, m3 = s1 / n, s2 / n, s3 / n
    var = m2 - m1 * m1
    return (m3 - 3 * m1 * m2 + 2 * m1 ** 3) / var ** 1.5


# Tolerance: Monte-Carlo skewness converges like n^-1/2 with a large constant —
# the estimator is a ratio of third to 3/2-power second moments of a variable
# that is zero most of the time. Measured across five seeds at 400k paths, every
# case below lands within 2.0% of the closed form. 6% is 3x the worst observed
# sampling error, so it will not flap, while a sign error or a wrong binomial
# term misses by orders of magnitude, not percent.
N_PATHS, MC_TOL = 400_000, 0.06
MC_CASES = [
    ("call  5% OTM,  7 DTE, vol 0.35", (100, 105, 7 / 365, 0.35, True), 20260728),
    ("call  ATM,    30 DTE, vol 0.30", (100, 100, 30 / 365, 0.30, True), 20260729),
    ("put   5% OTM,  7 DTE, vol 0.35", (100, 95, 7 / 365, 0.35, False), 20260730),
    ("call  2% OTM, 14 DTE, vol 0.40", (100, 102, 14 / 365, 0.40, True), 20260731),
    ("put   3% OTM, 21 DTE, vol 0.30", (100, 97, 21 / 365, 0.30, False), 20260801),
    ("call 10% OTM,  7 DTE, vol 0.35", (100, 110, 7 / 365, 0.35, True), 20260802),
]
print(f"  {N_PATHS:,} paths per case, fixed seeds, tolerance {MC_TOL*100:.0f}% relative")
for label, args, seed in MC_CASES:
    cf = SC.ex_ante_skewness(*args)
    sim = mc_payoff_skewness(*args, N_PATHS, seed)
    close(f"MC {label}", sim, cf, MC_TOL,
          f"closed form {cf:.4f} vs simulated {sim:.4f}  ({(sim-cf)/cf*100:+.2f}%)")

print()
print("=" * 74)
print("5. skew_verdict — fails OPEN, deliberately")
print("=" * 74)
r = SC.skew_verdict(None)
check("None -> ok True (DESIGN CHOICE: an uncomputable skew must not halt trading)",
      r["ok"] is True and "not computable" in r["reason"], r["reason"])
check("None -> carries skew=None so the caller can log the skip", r["skew"] is None)
r = SC.skew_verdict(4.0001)
check("just above the 4.0 threshold -> BLOCKED", r["ok"] is False, r["reason"][:60])
check("far above threshold -> BLOCKED", SC.skew_verdict(732876.0)["ok"] is False)
r = SC.skew_verdict(4.0)
check("EXACTLY at the threshold -> ok (boundary is inclusive-pass)", r["ok"] is True,
      r["reason"])
check("below threshold -> ok", SC.skew_verdict(3.9)["ok"] is True)
check("custom threshold 50.0 lets 36.95 through", SC.skew_verdict(36.95, 50.0)["ok"] is True)
check("custom threshold 2.0 blocks 3.0", SC.skew_verdict(3.0, 2.0)["ok"] is False)
check("custom threshold 2.0 admits exactly 2.0", SC.skew_verdict(2.0, 2.0)["ok"] is True)

# skew normally arrives from ex_ante_skewness() as float-or-None, but this
# function is also fed values off a config file or a cached JSON blob. Comparing
# a str to a float raises TypeError, which the module header forbids outright.
for g in GARBAGE + ["3.5.1", (), 20260717.0]:
    if g is None or isinstance(g, (int, float)) and not isinstance(g, bool):
        continue                                  # numeric inputs are the normal path
    try:
        r = SC.skew_verdict(g)
        ok = r["ok"] is True and "not computable" in r["reason"] and r["skew"] is None
        det = r["reason"]
    except Exception as e:                                   # noqa: BLE001
        ok, det = False, f"raised {type(e).__name__}: {e}"
    check(f"non-numeric skew {str(g)[:14]!r} -> ok True, 'not computable', never raises",
          ok, det)
check("a numeric STRING skew is still honoured, not shrugged off",
      SC.skew_verdict("9.0")["ok"] is False and SC.skew_verdict("3.0")["ok"] is True)
check("a huge int skew (float64 overflow) -> ok True, never raises",
      SC.skew_verdict(HUGE)["ok"] is True)
r = SC.skew_verdict(float("nan"))
check("NaN skew -> ok True, 'not computable' (same branch as None)",
      r["ok"] is True and "not computable" in r["reason"] and r["skew"] is None, r["reason"])
check("+inf skew -> BLOCKED, NOT shrugged off as 'not computable'",
      SC.skew_verdict(float("inf"))["ok"] is False,
      "inf is ordered, so the comparison is meaningful — the most extreme "
      "lottery there is must not fail open")
check("-inf skew -> ok (a very NEGATIVE skew is not a lottery)",
      SC.skew_verdict(float("-inf"))["ok"] is True)
# A broken threshold must fall back to the default, not disable the screen:
# nan would make every comparison False and silently pass everything.
check("a non-numeric THRESHOLD falls back to the 4.0 default, still blocking 9.0",
      SC.skew_verdict(9.0, "abc")["ok"] is False,
      SC.skew_verdict(9.0, "abc")["reason"][:52])
check("a NaN threshold falls back to the default rather than passing everything",
      SC.skew_verdict(9.0, float("nan"))["ok"] is False)
check("a None threshold falls back to the default", SC.skew_verdict(9.0, None)["ok"] is False)

print()
print("=" * 74)
print("6. moneyness_verdict — Ni (2009), symmetric across calls and puts")
print("=" * 74)
r = SC.moneyness_verdict(100, 120, True)
check("20%-OTM call -> BLOCKED", r["ok"] is False, r["reason"][:56])
check("5%-OTM call -> ok", SC.moneyness_verdict(100, 105, True)["ok"] is True)
r = SC.moneyness_verdict(100, 95, True)
check("ITM call (negative otm) -> ok", r["ok"] is True and r["otm"] < 0, f"otm {r['otm']}")

r = SC.moneyness_verdict(100, 80, False)
check("PUT with K = 0.80*S is 20% OTM -> BLOCKED", r["ok"] is False, r["reason"][:56])
r = SC.moneyness_verdict(100, 120, False)
check("PUT with K = 1.20*S is ITM -> ok", r["ok"] is True and r["otm"] < 0, f"otm {r['otm']}")
check("5%-OTM put -> ok", SC.moneyness_verdict(100, 95, False)["ok"] is True)

asym = []
for x in (0.0, 0.02, 0.05, 0.10, 0.14, 0.16, 0.20, 0.30):
    c = SC.moneyness_verdict(100, 100 * (1 + x), True)
    p = SC.moneyness_verdict(100, 100 * (1 - x), False)
    if c["ok"] != p["ok"] or abs(c["otm"] - p["otm"]) > 1e-9:
        asym.append((x, c, p))
check("calls and puts are treated symmetrically across 8 moneyness levels", not asym,
      str(asym[:1]))

check("exactly 15% OTM passes (K=115, S=100)", SC.moneyness_verdict(100, 115, True)["ok"] is True,
      f"otm {SC.moneyness_verdict(100,115,True)['otm']}")
check("15.01% OTM fails (K=115.01, S=100)",
      SC.moneyness_verdict(100, 115.01, True)["ok"] is False,
      f"otm {SC.moneyness_verdict(100,115.01,True)['otm']}")
check("exact-boundary arithmetic: otm == max_otm passes (K=150, max_otm=0.50)",
      SC.moneyness_verdict(100, 150, True, 0.50)["ok"] is True,
      f"otm {SC.moneyness_verdict(100,150,True,0.50)['otm']}")
check("one tick past a custom boundary fails (K=150.01, max_otm=0.50)",
      SC.moneyness_verdict(100, 150.01, True, 0.50)["ok"] is False)

for label, args in [("spot None", (None, 105, True)), ("strike None", (100, None, True)),
                    ("spot 'abc'", ("abc", 105, True)), ("strike 'abc'", (100, "abc", True)),
                    ("spot 0", (0, 105, True)), ("strike 0", (100, 0, True)),
                    ("spot negative", (-100, 105, True)), ("strike negative", (100, -5, True)),
                    ("spot inf", (float("inf"), 105, True)),
                    ("strike nan", (100, float("nan"), True)),
                    ("spot list", ([1], 105, True))]:
    try:
        r = SC.moneyness_verdict(*args)
        ok = r["ok"] is True and r["otm"] is None and "not computable" in r["reason"]
        det = r["reason"]
    except Exception as e:                                   # noqa: BLE001
        ok, det = False, f"raised {type(e).__name__}: {e}"
    check(f"{label} -> ok True, 'not computable'", ok, det)

print()
print("=" * 74)
print("7. dte_verdict — 0DTE is the most-condemned bucket in the literature")
print("=" * 74)
r = SC.dte_verdict(0)
check("0 DTE -> BLOCKED", r["ok"] is False, r["reason"][:60])
check("1 DTE -> BLOCKED", SC.dte_verdict(1)["ok"] is False)
check("2 DTE -> ok (the default floor)", SC.dte_verdict(2)["ok"] is True)
check("7 DTE -> ok", SC.dte_verdict(7)["ok"] is True)
check("negative DTE (expired) -> BLOCKED", SC.dte_verdict(-1)["ok"] is False,
      SC.dte_verdict(-1)["reason"][:40])
r = SC.dte_verdict(None)
check("None -> ok True with a reason", r["ok"] is True and r["reason"] == "DTE unknown",
      r["reason"])
check("custom min_dte=5 blocks 4", SC.dte_verdict(4, 5)["ok"] is False)
check("custom min_dte=5 admits 5", SC.dte_verdict(5, 5)["ok"] is True)
check("custom min_dte=0 admits 0DTE (tunable, not hard-wired)",
      SC.dte_verdict(0, 0)["ok"] is True)
r = SC.dte_verdict("abc")
check("unparseable DTE -> ok True with a reason (fails open, like an unknown DTE)",
      r["ok"] is True and "unparseable" in r["reason"], r["reason"])

print()
print("=" * 74)
print("8. spread_verdict — this one FAILS CLOSED, unlike skew")
print("=" * 74)
BLIND = [
    ("unparseable bid", ("abc", 1.02)),
    ("unparseable ask", (0.98, "abc")),
    ("None bid", (None, 1.02)),
    ("None ask", (0.98, None)),
    ("crossed market (ask < bid)", (1.10, 0.90)),
    ("zero bid", (0.0, 1.02)),
    ("zero ask", (0.98, 0.0)),
    ("both zero", (0.0, 0.0)),
    ("negative bid", (-0.98, 1.02)),
    ("negative ask", (0.98, -1.02)),
    ("nan bid", (float("nan"), 1.02)),
    ("inf ask", (0.98, float("inf"))),
    ("list bid", ([1], 1.02)),
]
for label, (b, a) in BLIND:
    try:
        r = SC.spread_verdict(b, a)
        ok = r["ok"] is False and "refusing to trade blind" in r["reason"]
        det = r["reason"]
    except Exception as e:                                   # noqa: BLE001
        ok, det = False, f"raised {type(e).__name__}: {e}"
    check(f"{label} -> BLOCKED, 'refusing to trade blind'", ok, det)

r = SC.spread_verdict(0.99, 1.01)
check("2% spread -> ok", r["ok"] is True, r["reason"])
r = SC.spread_verdict(0.925, 1.075)
check("REGRESSION: the 15% spread this bot shipped with -> BLOCKED", r["ok"] is False,
      r["reason"][:70])
close("15% spread reports spread_pct 0.15", r["spread_pct"], 0.15, 1e-9)

r2 = SC.spread_verdict(0.98, 1.02)
check("spread_pct is computed off the MID, not the ask: 0.98/1.02 -> 0.04 exactly",
      r2["spread_pct"] == 0.04,
      f"got {r2['spread_pct']!r}  (mid 1.00 -> 0.04; off the ask it would be 0.0392)")

check("execution.py is importable, so rt_cost is populated", SC._ex is not None)
rt = r["rt_cost"]
check("rt_cost populated at the 15% spread", isinstance(rt, float) and math.isfinite(rt),
      f"{rt!r}")
check("modelled round trip at a 15% spread is in research note 07's 10-11% band",
      rt is not None and 0.10 <= rt <= 0.11,
      f"EXACT: {rt:.6f} = {rt*100:.4f}% of premium  "
      f"(= {rt/0.005:.1f} months of the best documented 0.5%/month edge, per trade)")
check("rt_cost is populated on the PASSING path too",
      isinstance(SC.spread_verdict(0.99, 1.01)["rt_cost"], float))
check("a tighter quote models a cheaper round trip",
      SC.spread_verdict(0.99, 1.01)["rt_cost"] < rt,
      f"2% spread -> {SC.spread_verdict(0.99,1.01)['rt_cost']*100:.2f}% vs {rt*100:.2f}%")
check("custom max_spread=0.20 admits the 15% quote",
      SC.spread_verdict(0.925, 1.075, 0.20)["ok"] is True)
check("custom max_spread=0.01 blocks the 2% quote",
      SC.spread_verdict(0.99, 1.01, 0.01)["ok"] is False)

print()
print("=" * 74)
print("9. premium_verdict — fixed costs on a cheap contract")
print("=" * 74)
r = SC.premium_verdict(0.20)
check("$0.20 contract -> BLOCKED", r["ok"] is False, r["reason"])
# $0.60 is paid per ORDER (Rule 606 reports payment per contract on routed
# orders), so a ROUND TRIP is two of them: $1.20 on a $20 premium = 6.0%.
# Note 07 §I.1 states it outright — ">3% each way, >6% round trip" — and so do
# this module's own header and premium_verdict's docstring.
want_pfof = 2.0 * 0.60 / (0.20 * 100.0)
close("the arithmetic itself: 2 x $0.60 / $20 premium", want_pfof, 0.06, 1e-12,
      f"{want_pfof*100:.1f}% round trip")
check("the reason quotes 6.0% round-trip PFOF on a $0.20 contract",
      "6.0% round trip" in r["reason"], r["reason"])
r10 = SC.premium_verdict(0.10)
check("$0.10 contract quotes 12.0% (the figure scales as 1.20/premium)",
      "12.0% round trip" in r10["reason"], r10["reason"])
check("$1.00 contract -> ok", SC.premium_verdict(1.00)["ok"] is True)
check("EXACTLY $0.50 -> ok (boundary is inclusive-pass)",
      SC.premium_verdict(0.50)["ok"] is True, SC.premium_verdict(0.50)["reason"])
check("$0.49 -> BLOCKED", SC.premium_verdict(0.49)["ok"] is False)
check("custom min_price=0.10 admits $0.20", SC.premium_verdict(0.20, 0.10)["ok"] is True)
check("custom min_price=2.00 blocks $1.00", SC.premium_verdict(1.00, 2.00)["ok"] is False)
for label, p in [("zero", 0.0), ("negative", -1.0), ("None", None), ("'abc'", "abc"),
                 ("nan", float("nan")), ("inf", float("inf")), ("list", [1])]:
    try:
        r = SC.premium_verdict(p)
        ok, det = r["ok"] is False, r["reason"]
    except Exception as e:                                   # noqa: BLE001
        ok, det = False, f"raised {type(e).__name__}: {e}"
    check(f"{label} price -> BLOCKED (fails closed, like the spread gate)", ok, det)

print()
print("=" * 74)
print("10. max_daily_return / lottery_verdict — the MAX factor")
print("=" * 74)
JUMP = [100.0] * 15 + [120.0] * 5              # one +20% day, inside the window
CALM = [100.0 * 1.003 ** i for i in range(40)]  # +0.3%/day, forever
close("max_daily_return finds the +20% day", SC.max_daily_return(JUMP), 0.20, 1e-6)
check("a series with a +20% day -> BLOCKED", SC.lottery_verdict(JUMP)["ok"] is False,
      SC.lottery_verdict(JUMP)["reason"][:60])
check("a calm series -> ok", SC.lottery_verdict(CALM)["ok"] is True,
      SC.lottery_verdict(CALM)["reason"])
check("custom max_lottery=0.50 admits the +20% day",
      SC.lottery_verdict(JUMP, 0.50)["ok"] is True)

check("fewer than 3 closes -> max_daily_return None", SC.max_daily_return([100.0, 101.0]) is None)
r = SC.lottery_verdict([100.0, 101.0])
check("fewer than 3 closes -> ok True with a reason",
      r["ok"] is True and "not computable" in r["reason"], r["reason"])
check("empty series -> ok True", SC.lottery_verdict([])["ok"] is True)

DIRTY = [100.0, None, 0.0, -5.0, 101.0, float("nan"), 102.0, float("inf"), 103.0]
try:
    got = SC.max_daily_return(DIRTY)
    ok = got is not None and abs(got - 0.01) < 1e-6
    det = f"dropped None/0/negative/nan/inf, got MAX {got}"
except Exception as e:                                       # noqa: BLE001
    ok, det = False, f"raised {type(e).__name__}: {e}"
check("zero/negative/None/nan/inf entries are DROPPED, not raised on", ok, det)
for label, series in [("'abc' entries", [100.0, "abc", 101.0, 102.0]),
                      ("a bare string", "abc"), ("an int", 5), ("None", None),
                      ("dicts", [{}, {}, {}, {}])]:
    try:
        SC.max_daily_return(series)
        SC.lottery_verdict(series)
        ok, det = True, "no raise"
    except Exception as e:                                   # noqa: BLE001
        ok, det = False, f"raised {type(e).__name__}: {e}"
    check(f"{label} -> never raises", ok, det)

# The lookback is 21 returns. A +50% day 40 bars back must be invisible.
OLD_JUMP = [100.0] * 20 + [150.0] * 40          # jump is the 40th-from-last return
NEW_JUMP = [100.0] * 45 + [150.0] * 15          # same jump, 15th-from-last return
close("a +50% day 40 bars back is IGNORED (lookback=21)", SC.max_daily_return(OLD_JUMP),
      0.0, 1e-9, f"MAX {SC.max_daily_return(OLD_JUMP)} over {len(OLD_JUMP)} closes")
check("...and the underlying therefore passes", SC.lottery_verdict(OLD_JUMP)["ok"] is True,
      SC.lottery_verdict(OLD_JUMP)["reason"])
close("positive control: the SAME +50% day 15 bars back IS caught",
      SC.max_daily_return(NEW_JUMP), 0.50, 1e-6)
check("...and the underlying therefore fails", SC.lottery_verdict(NEW_JUMP)["ok"] is False)
check("custom lookback=5 also ignores a jump 10 bars back",
      SC.max_daily_return([100.0] * 20 + [150.0] + [150.0] * 9, lookback=5) == 0.0)

print()
print("=" * 74)
print("11. screen_entry — the combiner")
print("=" * 74)
WED = dt.date(2026, 7, 8)          # a Wednesday; July 2026 expiry is Fri the 17th
SEVEN = {"spread", "premium", "dte", "moneyness", "expiration", "skew", "lottery"}

CLEAN = dict(spot=100.0, strike=104.0, is_call=True, dte=7, bid=1.19, ask=1.21,
             vol=0.35, closes=CALM, day=WED)
r = SC.screen_entry(**CLEAN)
check("a clean trade passes (4% OTM, 7 DTE, 1.7% spread, $1.20, calm tape)",
      r["ok"] is True and r["failed"] == [],
      f"failed={r['failed']}  skew={r['checks']['skew'].get('skew')}")
check("...and every one of the seven screens reported", set(r["checks"]) == SEVEN,
      str(sorted(r["checks"])))

# The shape this bot actually opened: near the money, days to expiry, quoted 15%
# wide on a sub-$0.50 contract.
BAD = dict(spot=100.0, strike=101.5, is_call=True, dte=3, bid=0.37, ask=0.43,
           vol=0.35, closes=CALM, day=WED)
r = SC.screen_entry(**BAD)
check("REGRESSION: 1.5% OTM / 3 DTE / 15% spread / $0.40 premium -> REJECTED",
      r["ok"] is False, f"{len(r['failed'])} reasons")
check("...for MULTIPLE reasons, not just the first", len(r["failed"]) >= 2,
      " | ".join(f[:44] for f in r["failed"]))
check("...spread is one of them", not r["checks"]["spread"]["ok"])
check("...premium is another", not r["checks"]["premium"]["ok"])

# Three simultaneous violations: every screen must still run and report.
ALLBAD = dict(spot=100.0, strike=130.0, is_call=True, dte=0, bid=0.30, ask=0.50,
              vol=0.35, closes=JUMP, day=EXP)
r = SC.screen_entry(**ALLBAD)
check("spread AND dte AND moneyness violated together -> >=3 failures",
      len(r["failed"]) >= 3, f"{len(r['failed'])}: " + " | ".join(f.split(':')[0] for f in r['failed']))
check("...all seven checks present regardless of how early the first one failed",
      set(r["checks"]) == SEVEN, str(sorted(r["checks"])))
check("...every check dict carries a reason string",
      all(isinstance(v.get("reason"), str) and v["reason"] for v in r["checks"].values()))
check("...the expiration screen fired too (day IS the expiry Friday)",
      not r["checks"]["expiration"]["ok"])
check("...and the lottery screen fired too (+20% day in the tape)",
      not r["checks"]["lottery"]["ok"])

OVERRIDES = [
    ("max_skew",   dict(spot=100.0, strike=110.0, is_call=True, dte=7, bid=1.19, ask=1.21,
                        vol=0.25, closes=CALM, day=WED), {"max_skew": 50.0}, "skew"),
    ("max_otm",    dict(spot=100.0, strike=120.0, is_call=True, dte=7, bid=1.19, ask=1.21,
                        vol=None, closes=CALM, day=WED), {"max_otm": 0.25}, "moneyness"),
    ("min_dte",    dict(spot=100.0, strike=104.0, is_call=True, dte=1, bid=1.19, ask=1.21,
                        vol=None, closes=CALM, day=WED), {"min_dte": 1}, "dte"),
    ("max_spread", dict(spot=100.0, strike=104.0, is_call=True, dte=7, bid=0.95, ask=1.05,
                        vol=None, closes=CALM, day=WED), {"max_spread": 0.15}, "spread"),
    ("min_price",  dict(spot=100.0, strike=104.0, is_call=True, dte=7, bid=0.298, ask=0.302,
                        vol=None, closes=CALM, day=WED), {"min_price": 0.10}, "premium"),
    ("max_lottery", dict(spot=100.0, strike=104.0, is_call=True, dte=7, bid=1.19, ask=1.21,
                         vol=None, closes=JUMP, day=WED), {"max_lottery": 0.50}, "lottery"),
]
for name, trade, cfg, key in OVERRIDES:
    base = SC.screen_entry(**trade)
    over = SC.screen_entry(**trade, config=cfg)
    ok = (base["ok"] is False and not base["checks"][key]["ok"]
          and over["ok"] is True and over["checks"][key]["ok"] and over["failed"] == [])
    check(f"config override '{name}' flips a would-fail trade to pass", ok,
          f"default: {base['checks'][key]['reason'][:38]} -> {cfg}: ok={over['ok']}")

r = SC.screen_entry(spot=100.0, strike=104.0, is_call=True, dte=7, bid=1.19, ask=1.21,
                    vol=None, closes=None, day=WED)
check("vol absent -> skew check reports ok True with a skip reason",
      r["checks"]["skew"]["ok"] is True and "skipped" in r["checks"]["skew"]["reason"],
      r["checks"]["skew"]["reason"])
check("closes absent -> lottery check reports ok True with a skip reason",
      r["checks"]["lottery"]["ok"] is True and "no price history" in r["checks"]["lottery"]["reason"],
      r["checks"]["lottery"]["reason"])
check("...and the trade still passes overall", r["ok"] is True, str(r["failed"]))
check("optional inputs absent still yields all seven checks", set(r["checks"]) == SEVEN)

BASE = dict(spot=100.0, strike=104.0, is_call=True, dte=7, bid=1.19, ask=1.21,
            vol=0.35, closes=CALM, day=WED, config=None)
raised = []
n_total = 0
for arg in BASE:
    for g in GARBAGE + [dt.datetime(2026, 7, 20, 9, 30), "2026-07-17", {"max_skew": "abc"},
                        {"min_dte": None}, [1, 2, 3], (0.0, 0.0)]:
        kw = dict(BASE)
        kw[arg] = g
        n_total += 1
        try:
            out = SC.screen_entry(**kw)
            if not (isinstance(out, dict) and "ok" in out and "failed" in out
                    and set(out["checks"]) == SEVEN):
                raised.append((arg, repr(g)[:20], "malformed result"))
        except Exception as e:                               # noqa: BLE001
            raised.append((arg, repr(g)[:20], f"{type(e).__name__}: {e}"))
check(f"TOTALITY: {n_total} garbage substitutions across every argument, none raises",
      not raised, f"{len(raised)} failures, e.g. {raised[:3]}" if raised else
      "every call returned a well-formed dict with all seven checks")

r = SC.screen_entry(**{**BASE, "config": "not-a-dict"})
check("a non-dict config degrades to the defaults instead of raising",
      r["ok"] is True, str(r["failed"]))
r = SC.screen_entry(**{**BASE, "config": {"max_spread": "abc", "min_dte": None}})
check("unusable config VALUES degrade to the defaults instead of raising",
      r["ok"] is True, str(r["failed"]))
r = SC.screen_entry(**{**BASE, "day": dt.date(2026, 7, 17)})
check("day= is honoured: the July 2026 expiry Friday rejects an otherwise clean trade",
      r["ok"] is False and not r["checks"]["expiration"]["ok"],
      r["checks"]["expiration"]["reason"][:60])
r = SC.screen_entry(**{**BASE, "day": dt.date(2026, 7, 20)})
check("day= is honoured: the post-expiry Monday rejects it too",
      r["ok"] is False and not r["checks"]["expiration"]["ok"])

# Puts through the combiner, since the skew branch is the fragile one.
r = SC.screen_entry(spot=100.0, strike=96.0, is_call=False, dte=7, bid=1.19, ask=1.21,
                    vol=0.35, closes=CALM, day=WED)
check("a clean PUT passes the combiner", r["ok"] is True,
      f"skew={r['checks']['skew'].get('skew')} otm={r['checks']['moneyness']['otm']}")
r = SC.screen_entry(spot=100.0, strike=80.0, is_call=False, dte=7, bid=1.19, ask=1.21,
                    vol=0.35, closes=CALM, day=WED)
check("a 20%-OTM PUT is rejected on BOTH moneyness and skew",
      r["ok"] is False and not r["checks"]["moneyness"]["ok"] and not r["checks"]["skew"]["ok"],
      f"skew={r['checks']['skew'].get('skew')}")

print()
print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
print("=" * 74)
sys.exit(1 if FAIL else 0)
