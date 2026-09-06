"""
Statistics test harness — pins the four measured defects that stats.py exists to
fix. No network, no broker, no numpy, no scipy. Run: python3 test_stats.py

stats.py is not a maths library, it is a set of guard rails, so what is tested
here is mostly *properties* rather than values:

  * n_eff must collapse 7 same-day trades to ~1, because that is the sample size
    that actually exists
  * required_n_for_gate must come out enormously larger than MIN_SAMPLES_GATE=5,
    because that gap is the whole finding
  * dedupe_dims must notice that `direction` and `trend_align` are one column
  * gate_decision must NEVER return 0 — a hard gate is an absorbing state, and
    an absorbing state is the difference between "discouraged" and "dead"
  * every public function must be total. These run inside a trading loop; an
    unhandled TypeError there is a financial bug, so the last section throws
    garbage at all 23 of them and asserts nothing raises.

Headline figures that go into the write-up are printed again, together, at the
bottom, so they can be quoted without re-deriving them.
"""
from __future__ import annotations
import sys, math, inspect, itertools

import stats as S

PASS, FAIL = [], []
HEADLINE: dict = {}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")


def close(name, got, want, tol=1e-9):
    try:
        ok = abs(float(got) - float(want)) <= tol
    except (TypeError, ValueError, OverflowError):
        ok = False
    check(name, ok, f"got {got!r}, want {want!r} (tol {tol:g})")


def section(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def headline(key, value, note=""):
    HEADLINE[key] = (value, note)


def is_float(x):
    return isinstance(x, float) and not isinstance(x, bool)


def frange(lo, hi, step):
    out, x, i = [], lo, 0
    while x <= hi + 1e-12:
        out.append(round(x, 10))
        i += 1
        x = lo + i * step
    return out


NAN, INF = float("nan"), float("inf")
GARBAGE = [None, "", "abc", NAN, INF, -INF, -1, [], {}, 0, True, "na",
           [1, 2, 3], [[1, 2], [3]], {"a": 1}, [None, NAN], ["x", 1]]


# --------------------------------------------------------------------------
section("1. norm_cdf / norm_ppf — the only numerics everything else stands on")
close("norm_cdf(0) = 0.5", S.norm_cdf(0), 0.5, 0.0)
close("norm_cdf(1.96) ~ 0.975", S.norm_cdf(1.96), 0.975, 1e-5)
close("norm_cdf(-1.96) ~ 0.025", S.norm_cdf(-1.96), 0.025, 1e-5)
close("norm_ppf(0.975) ~ 1.95996", S.norm_ppf(0.975), 1.959963985, 1e-6)
close("norm_ppf(0.95) ~ 1.64485", S.norm_ppf(0.95), 1.644853627, 1e-6)
close("norm_ppf(0.5) = 0", S.norm_ppf(0.5), 0.0, 1e-12)

worst, worst_x = 0.0, None
for x in frange(-3.0, 3.0, 0.01):
    err = abs(S.norm_ppf(S.norm_cdf(x)) - x)
    if err > worst:
        worst, worst_x = err, x
check("norm_ppf(norm_cdf(x)) round-trips over [-3,3] to 1e-6",
      worst <= 1e-6, f"worst |err| = {worst:.3e} at x = {worst_x}")

# Acklam is a three-branch rational fit. The middle branch is what everyone
# hits; the two tail branches are only reached outside [0.02425, 0.97575] and
# are where a transcription error would hide.
LOW = [1e-8, 1e-5, 1e-3, 0.01, 0.024, 0.024249]
HIGH = [1 - p for p in LOW]
check("lower tail branch is exercised (p < 0.02425)", all(p < 0.02425 for p in LOW))
check("upper tail branch is exercised (p > 0.97575)", all(p > 0.97575 for p in HIGH))
lo_err = max(abs(S.norm_cdf(S.norm_ppf(p)) - p) for p in LOW)
hi_err = max(abs(S.norm_cdf(S.norm_ppf(p)) - p) for p in HIGH)
check("lower tail branch inverts correctly", lo_err <= 1e-9, f"max |err| = {lo_err:.3e}")
check("upper tail branch inverts correctly", hi_err <= 1e-9, f"max |err| = {hi_err:.3e}")
check("tails are antisymmetric: ppf(p) = -ppf(1-p)",
      all(abs(S.norm_ppf(p) + S.norm_ppf(1 - p)) < 1e-9 for p in LOW))
check("ppf is strictly increasing across both branch seams",
      all(S.norm_ppf(a) < S.norm_ppf(b) for a, b in
          zip([0.0242, 0.02425, 0.0243, 0.9757, 0.97575, 0.9758],
              [0.02425, 0.0243, 0.0244, 0.97575, 0.9758, 0.9759])))

close("norm_ppf(0) clamps to -40", S.norm_ppf(0.0), -40.0, 0.0)
close("norm_ppf(1) clamps to +40", S.norm_ppf(1.0), 40.0, 0.0)
close("norm_ppf(-5) clamps to -40", S.norm_ppf(-5.0), -40.0, 0.0)
close("norm_ppf(5) clamps to +40", S.norm_ppf(5.0), 40.0, 0.0)
close("norm_cdf(inf) = 1.0", S.norm_cdf(INF), 1.0, 0.0)
close("norm_cdf(-inf) = 0.0", S.norm_cdf(-INF), 0.0, 0.0)

for bad in (None, "abc", "", NAN, INF, -INF, [], {}):
    for fn, nm in ((S.norm_cdf, "norm_cdf"), (S.norm_ppf, "norm_ppf")):
        try:
            got = fn(bad)
            ok = is_float(got) and not math.isnan(got)
        except Exception as e:
            got, ok = f"{type(e).__name__}", False
        check(f"{nm}({bad!r}) returns a non-NaN float, never raises", ok, f"-> {got!r}")


# --------------------------------------------------------------------------
section("2. intraclass_correlation — is a 'day' one observation or seven?")
close("perfectly homogeneous clusters -> rho = 1",
      S.intraclass_correlation([[1, 1, 1], [0, 0, 0]]), 1.0, 1e-9)
close("noise clusters with identical means -> rho = 0",
      S.intraclass_correlation([[1, -1], [1, -1], [1, -1], [1, -1]]), 0.0, 1e-9)
close("single cluster -> exactly 1.0 (honest 'fully clustered' default)",
      S.intraclass_correlation([[0.1, -0.2, 0.3, 0.4]]), 1.0, 0.0)
close("all-singleton clusters -> exactly 0.0",
      S.intraclass_correlation([[1], [2], [3], [4]]), 0.0, 0.0)
close("empty input -> 1.0 (k<2)", S.intraclass_correlation([]), 1.0, 0.0)

RHO_CASES = [
    [[1, 1, 1], [0, 0, 0]],
    [[1, -1], [1, -1]],
    [[0.5, 0.4, 0.6], [-0.5, -0.4, -0.6], [0.0, 0.1, -0.1]],
    [[1, 2, 3, 4, 5], [1, 2, 3, 4, 5]],
    [[3, -3, 3, -3], [-3, 3, -3, 3]],
    [[1], [2], [3]],
    [[0, 0], [0, 0], [0, 0]],
    [[1e6, -1e6], [1e6, -1e6]],
]
rhos = [S.intraclass_correlation(g) for g in RHO_CASES]
check("rho is never negative", all(r >= 0.0 for r in rhos), f"min = {min(rhos)}")
check("rho is never above 1", all(r <= 1.0 for r in rhos), f"max = {max(rhos)}")
check("rho is always a float", all(is_float(r) for r in rhos))

close("None entries are dropped, not counted",
      S.intraclass_correlation([[1, None, 1], [0, None, 0]]), 1.0, 1e-9)
close("NaN entries are dropped, not counted",
      S.intraclass_correlation([[1, NAN, 1], [0, NAN, 0]]), 1.0, 1e-9)
close("a cluster emptied by cleaning drops out entirely",
      S.intraclass_correlation([[1, 1], [0, 0], [None, NAN]]),
      S.intraclass_correlation([[1, 1], [0, 0]]), 1e-12)


# --------------------------------------------------------------------------
section("3. design_effect — Moulton: deff = 1 + (m_bar - 1) * rho")
for r in (0.0, 0.25, 0.5, 0.99, 1.0, -3.0, 7.0):
    close(f"deff(1, {r}) = 1.0 — a cluster of one costs nothing",
          S.design_effect(1, r), 1.0, 0.0)
for m in (1, 2, 7, 100, 1e6):
    close(f"deff({m:g}, 0) = 1.0 — uncorrelated clusters cost nothing",
          S.design_effect(m, 0), 1.0, 0.0)
close("deff(7, 1.0) = 7.0 — 7 identical trades are 1 observation",
      S.design_effect(7, 1.0), 7.0, 1e-12)
close("deff(3.5, 0.5) = 2.25", S.design_effect(3.5, 0.5), 2.25, 1e-12)

mono_r = all(S.design_effect(5, a) <= S.design_effect(5, b)
             for a, b in zip(frange(0, 0.99, 0.01), frange(0.01, 1.0, 0.01)))
mono_m = all(S.design_effect(a, 0.5) <= S.design_effect(b, 0.5)
             for a, b in zip(frange(1, 49, 1), frange(2, 50, 1)))
check("monotone non-decreasing in rho", mono_r)
check("monotone non-decreasing in mean cluster size", mono_m)
check("strictly increasing in rho once m > 1", S.design_effect(5, 0.4) < S.design_effect(5, 0.5))
check("strictly increasing in m once rho > 0", S.design_effect(4, 0.4) < S.design_effect(5, 0.4))
check("deff is never below 1", all(S.design_effect(m, r) >= 1.0
                                   for m in (0, 0.5, 1, 2, 9) for r in (-1, 0, 0.5, 1, 9)))


# --------------------------------------------------------------------------
section("4. effective_n — HEADLINE: 7 trades on one day are not 7 observations")
# The real journal: every settled trade closed on 2026-07-27.
REAL_DAY = [-0.5077, 0.9206, -0.3500, 0.6089, -0.5000, -0.5402, 0.5645]
one_day = S.effective_n([REAL_DAY])
print(f"        one cluster of 7 -> {one_day}")
headline("n_eff, 7 real trades all on 2026-07-27", one_day["n_eff"],
         f"n=7, k=1, rho={one_day['rho']}, deff={one_day['deff']}")
check("n_eff for the real single-day journal is in [1.0, 1.5] as documented",
      1.0 <= one_day["n_eff"] <= 1.5, f"n_eff = {one_day['n_eff']}")
check("  ...while the naive count still reads 7", one_day["n"] == 7)
check("  ...via rho = 1 and deff = 7", one_day["rho"] == 1.0 and one_day["deff"] == 7.0,
      f"rho={one_day['rho']}, deff={one_day['deff']}")

seven_days = S.effective_n([[v] for v in REAL_DAY])
print(f"        seven clusters of 1 -> {seven_days}")
headline("n_eff, the same 7 outcomes spread over 7 separate days", seven_days["n_eff"])
check("the same 7 outcomes across 7 days give n_eff ~ 7",
      abs(seven_days["n_eff"] - 7.0) < 0.5, f"n_eff = {seven_days['n_eff']}")

close("empty input -> n_eff 0.0", S.effective_n([])["n_eff"], 0.0, 0.0)
check("empty input -> the zero dict",
      S.effective_n([]) == {"n": 0, "n_clusters": 0, "mean_cluster_size": 0.0,
                            "rho": 0.0, "deff": 1.0, "n_eff": 0.0})
check("all-NaN input is treated as empty", S.effective_n([[NAN, None]])["n"] == 0)

GROUPINGS = [
    [REAL_DAY], [[v] for v in REAL_DAY],
    [REAL_DAY[:4], REAL_DAY[4:]], [REAL_DAY[:2], REAL_DAY[2:4], REAL_DAY[4:]],
    [[1, 1], [0, 0], [1, 0]], [[1, 2, 3], [4], [5, 6]],
    [[0.0] * 50], [[i] for i in range(50)], [[1, -1] * 25],
]
res = [S.effective_n(g) for g in GROUPINGS]
check("n_eff is never below 1.0 when n > 0",
      all(r["n_eff"] >= 1.0 for r in res if r["n"] > 0),
      f"min = {min(r['n_eff'] for r in res if r['n'] > 0)}")
check("n_eff never exceeds the raw n", all(r["n_eff"] <= r["n"] for r in res))
check("n_eff never exceeds n/deff (rounding cannot inflate it)",
      all(r["n_eff"] <= max(1.0, r["n"] / r["deff"]) + 0.01 for r in res))
check("deff is always >= 1", all(r["deff"] >= 1.0 for r in res))
check("partial clustering sits strictly between the two extremes",
      1.0 < S.effective_n([REAL_DAY[:4], REAL_DAY[4:]])["n_eff"] <= 7.0,
      f"2 days -> n_eff = {S.effective_n([REAL_DAY[:4], REAL_DAY[4:]])['n_eff']}")


# --------------------------------------------------------------------------
section("5. bonferroni_alpha / fwer — HEADLINE: the gate was ~certain to fire")
close("bonferroni_alpha(0.05, 17) ~ 0.00294", S.bonferroni_alpha(0.05, 17), 0.00294, 5e-6)
close("bonferroni_alpha(0.05, 1) = 0.05", S.bonferroni_alpha(0.05, 1), 0.05, 0.0)
check("n_tests < 1 is floored at 1, not divided by zero",
      S.bonferroni_alpha(0.05, 0) == 0.05 and S.bonferroni_alpha(0.05, -4) == 0.05)

# 0.337 is the MEASURED SIZE of the old rule "n>=5 and win_rate<=0.25" under the
# bot's own 40% base win rate: P(at most 1 win in 5 | p=0.40). It is NOT the
# 0.25 win-rate threshold, and using 0.25 here is the easy mistake — it gives
# 0.9925, not 0.9991. Both are printed so the write-up quotes the right one.
size_of_rule = sum(math.comb(5, k) * 0.40 ** k * 0.60 ** (5 - k)
                   for k in range(6) if k / 5 <= 0.25)
close("size of 'n>=5 and win_rate<=0.25' at the bot's 40% base rate = 0.337",
      size_of_rule, 0.337, 5e-4)
f_337 = S.fwer(size_of_rule, 17)
f_25 = S.fwer(0.25, 17)
headline("fwer(0.337, 17) — family-wise error across the 17 buckets", round(f_337, 4),
         "0.337 is the measured size of the old gate rule")
headline("fwer(0.25, 17) — NOT the headline figure", round(f_25, 4),
         "0.25 is the win-rate threshold, not a per-test alpha")
close("fwer(0.337, 17) ~ 0.9991 to 3 decimals", f_337, 0.9991, 5e-4)
close("fwer(0.25, 17) = 0.9925 (the number the docstring used to claim was 0.9991)",
      f_25, 0.9925, 5e-4)
check("both are above 0.99 — either way a gate fires on noise", f_337 > 0.99 and f_25 > 0.99)

for a in (0.0, 0.05, 0.25, 0.5, 1.0):
    close(f"fwer({a}, 0) = 0 — no tests, no false positives", S.fwer(a, 0), 0.0, 0.0)
for n in (0, 1, 5, 17, 1000):
    close(f"fwer(0, {n}) = 0 — a test that never fires never false-fires",
          S.fwer(0.0, n), 0.0, 0.0)
for n in (1, 2, 17, 1000):
    close(f"fwer(1, {n}) = 1 — a test that always fires always false-fires",
          S.fwer(1.0, n), 1.0, 0.0)
check("fwer is monotone increasing in n_tests",
      all(S.fwer(0.05, n) <= S.fwer(0.05, n + 1) for n in range(0, 60)))
check("fwer is monotone increasing in alpha",
      all(S.fwer(a, 17) <= S.fwer(a + 0.01, 17) for a in frange(0.0, 0.98, 0.01)))
check("fwer stays inside [0, 1] for out-of-range alpha",
      all(0.0 <= S.fwer(a, 17) <= 1.0 for a in (-5, -0.1, 1.1, 99)))


# --------------------------------------------------------------------------
section("6. effective_trials — M correlated trials are fewer than M trials")
for m in (1, 2, 17, 1000):
    close(f"rho = 0 -> {m} trials stay {m}", S.effective_trials(m, 0.0), float(m), 1e-9)
for m in (1, 2, 17, 1000):
    close(f"rho = 1 -> {m} identical trials collapse to 1",
          S.effective_trials(m, 1.0), 1.0, 1e-9)
for r in (0.0, 0.3, 1.0):
    close(f"M = 1 at rho {r} is 1.0", S.effective_trials(1, r), 1.0, 1e-12)
check("monotone decreasing in rho",
      all(S.effective_trials(20, a) >= S.effective_trials(20, b)
          for a, b in zip(frange(0, 0.99, 0.01), frange(0.01, 1.0, 0.01))))
check("strictly decreasing in rho for M > 1",
      S.effective_trials(20, 0.1) > S.effective_trials(20, 0.2))
check("result always in [1, M]",
      all(1.0 - 1e-12 <= S.effective_trials(m, r) <= m + 1e-12
          for m in (1, 2, 17, 500) for r in (0, 0.25, 0.5, 0.75, 1)))


# --------------------------------------------------------------------------
section("7. required_n_for_gate — HEADLINE: 5 samples is not a sample")
n_unc = S.required_n_for_gate()
n_bon = S.required_n_for_gate(alpha=S.bonferroni_alpha(0.05, 17))
MIN_SAMPLES_GATE = 5          # the shipped constant, learn.py:22
print(f"        defaults (p_null=0.40, p_alt=0.25, alpha=0.05, power=0.80) -> n = {n_unc}")
print(f"        under Bonferroni across 17 buckets (alpha=0.00294)        -> n = {n_bon}")
headline("required_n_for_gate() at default args", n_unc,
         "one-sided proportion test, null 40% vs alt 25%, alpha 0.05, power 0.80")
headline("required_n_for_gate() under Bonferroni(0.05, 17)", n_bon,
         "alpha = 0.00294")
headline("shortfall of MIN_SAMPLES_GATE = 5", f"{n_unc / 5:.1f}x uncorrected, "
         f"{n_bon / 5:.1f}x corrected")
check("default args return ~61", 55 <= n_unc <= 67, f"exact value: {n_unc}")
check("Bonferroni-corrected returns ~131", 120 <= n_bon <= 142, f"exact value: {n_bon}")
check("the requirement is >> MIN_SAMPLES_GATE = 5",
      n_unc > 10 * MIN_SAMPLES_GATE,
      f"{n_unc} vs 5 — short by {n_unc / MIN_SAMPLES_GATE:.1f}x")
check("  ...and by ~26x once multiplicity is paid for",
      n_bon > 25 * MIN_SAMPLES_GATE, f"{n_bon}/5 = {n_bon / MIN_SAMPLES_GATE:.1f}x")

# Testing against a 50% coin instead of the bot's own 40% base rate is the
# flattering error: it makes the effect look bigger and the requirement smaller.
n_coin = S.required_n_for_gate(p_null=0.50)
check("a 50% null understates the requirement (why 0.40 is the default)",
      n_coin < n_unc, f"p_null=0.50 -> {n_coin}, p_null=0.40 -> {n_unc} "
                      f"({n_unc / n_coin:.2f}x)")

check("more power costs more samples",
      S.required_n_for_gate(power=0.95) > S.required_n_for_gate(power=0.80))
check("a smaller alpha costs more samples",
      S.required_n_for_gate(alpha=0.001) > S.required_n_for_gate(alpha=0.05))
check("a smaller effect costs more samples",
      S.required_n_for_gate(p_alt=0.35) > S.required_n_for_gate(p_alt=0.25))
check("the result is a plain int", isinstance(n_unc, int) and not isinstance(n_unc, bool))

for args in [dict(p_null=0.25, p_alt=0.50), dict(p_null=0.5, p_alt=0.5),
             dict(p_null=0.0, p_alt=0.0), dict(p_null=1.0, p_alt=0.5),
             dict(p_null=-1, p_alt=-2), dict(alpha=NAN), dict(power=NAN),
             dict(p_null=None), dict(p_alt="abc"), dict(p_null=INF)]:
    try:
        got, raised = S.required_n_for_gate(**args), False
    except Exception as e:
        got, raised = type(e).__name__, True
    check(f"degenerate {args} falls back to 61 rather than raising",
          (not raised) and got == 61, f"-> {got!r}")


# --------------------------------------------------------------------------
section("8. cramers_v — HEADLINE: direction and trend_align are one column")
DIR = ["call", "call", "put"]
ALI = ["ct", "ct", "wt"]
v_collinear = S.cramers_v(DIR, ALI)
headline("cramers_v of two perfectly collinear labellings", round(v_collinear, 4))
close("perfectly collinear labellings -> exactly 1.0000", v_collinear, 1.0, 0.0)
close("  ...still 1.0 on the real journal's direction vs trend_align",
      S.cramers_v(["call"] * 5 + ["put"] * 2,
                  ["counter_trend"] * 5 + ["with_trend"] * 2), 1.0, 0.0)
close("a labelling against itself is 1.0",
      S.cramers_v(["a", "b", "c", "a"], ["a", "b", "c", "a"]), 1.0, 1e-12)

INDEP_A = ["x", "x", "y", "y"] * 5
INDEP_B = ["p", "q", "p", "q"] * 5
v_indep = S.cramers_v(INDEP_A, INDEP_B)
close("exactly independent labellings -> 0.0", v_indep, 0.0, 1e-12)
check("independent labellings are far below the 0.85 dedupe threshold",
      v_indep < 0.85, f"V = {v_indep:.4f}")
v_mild = S.cramers_v(["x"] * 5 + ["y"] * 5, ["p", "p", "p", "q", "q"] + ["q"] * 5)
check("a merely-associated pair is below threshold too", v_mild < 0.85, f"V = {v_mild:.4f}")

close("a constant first dimension -> 0.0 (constant guard owns this, not collinearity)",
      S.cramers_v(["only"] * 6, ["a", "b", "a", "b", "a", "b"]), 0.0, 0.0)
close("a constant second dimension -> 0.0",
      S.cramers_v(["a", "b", "a", "b", "a", "b"], ["only"] * 6), 0.0, 0.0)
close("both constant -> 0.0", S.cramers_v(["z"] * 4, ["y"] * 4), 0.0, 0.0)

SYM = [(DIR, ALI), (INDEP_A, INDEP_B), (["a", "b", "c"], ["p", "p", "q"]),
       (["a", "b", "a", "c"], ["p", "q", "p", "q"]), (["only"] * 4, ["a", "b", "a", "b"])]
check("V(a, b) == V(b, a) for every pair tested",
      all(S.cramers_v(a, b) == S.cramers_v(b, a) for a, b in SYM))

close("'na' entries are excluded pairwise",
      S.cramers_v(DIR + ["na", "na"], ALI + ["wt", "ct"]), 1.0, 0.0)
close("None entries are excluded pairwise",
      S.cramers_v(DIR + [None, None], ALI + ["wt", "ct"]), 1.0, 0.0)
close("an 'na' on either side kills the pair",
      S.cramers_v(DIR + ["call"], ALI + ["na"]), 1.0, 0.0)
close("empty input -> 0.0", S.cramers_v([], []), 0.0, 0.0)
close("all-'na' input -> 0.0", S.cramers_v(["na"] * 4, ["na"] * 4), 0.0, 0.0)
check("mismatched lengths zip to the shorter, no raise",
      S.cramers_v(DIR * 3, ALI) == S.cramers_v(DIR, ALI))

V_CASES = SYM + [([], []), (["na"] * 3, ["na"] * 3), (["a"] * 30, ["b"] * 30),
                 (list(range(20)), list(range(20))), (["a", 1, "a", 1], ["p", "q", "p", "q"])]
vs = [S.cramers_v(a, b) for a, b in V_CASES]
check("V is always in [0, 1]", all(0.0 <= v <= 1.0 for v in vs), f"range {min(vs)}..{max(vs)}")
check("V is always a float", all(is_float(v) for v in vs))


# --------------------------------------------------------------------------
section("9. dedupe_dims — one hypothesis must not be counted twice")
# The real journal shape: trend_up was False on every trade, so align_bucket()
# made counter_trend <=> call and with_trend <=> put. `regime` is a genuinely
# independent split; `sleeve` is constant.
ROWS = [{"buckets": {"direction": d, "trend_align": t, "regime": g, "sleeve": "news"}}
        for d, t, g in zip(["call"] * 4 + ["put"] * 4,
                           ["counter_trend"] * 4 + ["with_trend"] * 4,
                           ["A", "B"] * 4)]
DIMS = ["direction", "trend_align", "regime", "sleeve"]
kept, dropped = S.dedupe_dims(ROWS, DIMS)
print(f"        kept    = {kept}")
print(f"        dropped = {dropped}")
headline("dedupe_dims on the real journal shape", f"kept {kept}, dropped {dropped}")

check("the collinear second dim is dropped", "trend_align" in dropped)
check("  ...mapped to the dim that kept it", dropped.get("trend_align", (None,))[0] == "direction")
close("  ...with V ~ 1.0 recorded as the reason", dropped.get("trend_align", (None, 0))[1], 1.0, 1e-4)
check("the first of the collinear pair is kept", "direction" in kept)
check("the genuinely independent dim is kept", "regime" in kept)
check("the constant dim is silently skipped — not kept",
      "sleeve" not in kept, f"kept = {kept}")
check("the constant dim is silently skipped — not dropped either",
      "sleeve" not in dropped, f"dropped = {dropped}")
check("kept and dropped are disjoint", not (set(kept) & set(dropped)))

kept_r, dropped_r = S.dedupe_dims(ROWS, list(reversed(DIMS)))
print(f"        reversed: kept = {kept_r}, dropped = {dropped_r}")
check("ORDER MATTERS: reversing the list keeps the OTHER collinear dim",
      "trend_align" in kept_r and "direction" in dropped_r,
      f"kept = {kept_r}")
check("  ...and the drop reason points the other way",
      dropped_r.get("direction", (None,))[0] == "trend_align")
check("  ...while the independent dim survives either ordering", "regime" in kept_r)
check("  ...and the constant dim is skipped either ordering",
      "sleeve" not in kept_r and "sleeve" not in dropped_r)

check("a threshold above 1.0 drops nothing",
      S.dedupe_dims(ROWS, DIMS, threshold=1.01)[1] == {})
check("a threshold of 0.0 keeps only the first informative dim",
      S.dedupe_dims(ROWS, DIMS, threshold=0.0)[0] == ["direction"])
check("no rows -> nothing kept, nothing dropped", S.dedupe_dims([], DIMS) == ([], {}))
check("no dims -> nothing kept, nothing dropped", S.dedupe_dims(ROWS, []) == ([], {}))
check("a dim absent from every row is skipped as constant",
      S.dedupe_dims(ROWS, ["nonexistent"]) == ([], {}))
check("rows missing 'buckets' entirely do not break it",
      S.dedupe_dims([{"x": 1}] * 4 + ROWS, DIMS)[0] == kept)
check("'na'-only dims count as constant",
      S.dedupe_dims([{"buckets": {"d": "na"}} for _ in range(4)], ["d"]) == ([], {}))


# --------------------------------------------------------------------------
section("10. eb_shrink — a 5-trade bucket does not get to claim -21%")
check("k = 0 -> []", S.eb_shrink([]) == [])
one = S.eb_shrink([{"key": "solo", "mean": -0.21, "var": 0.3, "n": 5}])
check("k = 1 -> one row", len(one) == 1)
close("k = 1 -> shrunk == mean (nothing to shrink toward)", one[0]["shrunk"], -0.21, 1e-12)
close("k = 1 -> weight 1.0", one[0]["weight"], 1.0, 0.0)
close("k = 1 -> pooled == mean", one[0]["pooled"], -0.21, 1e-12)
close("k = 1 -> tau2 == 0", one[0]["tau2"], 0.0, 0.0)

BUCKETS = [{"key": "thin", "mean": -0.40, "var": 0.25, "n": 5},
           {"key": "fat", "mean": 0.05, "var": 0.25, "n": 200},
           {"key": "mid", "mean": 0.02, "var": 0.25, "n": 60}]
out = {r["key"]: r for r in S.eb_shrink(BUCKETS)}
pooled = out["thin"]["pooled"]
for k, r in out.items():
    print(f"        {k:<5} n={r['n']:<4} mean={r['mean']:+.4f} -> shrunk={r['shrunk']:+.6f} "
          f"weight={r['weight']:.4f}")
headline("eb_shrink: thin bucket (n=5, mean -0.40)",
         f"weight {out['thin']['weight']}, shrunk {out['thin']['shrunk']} (pooled {pooled})")
headline("eb_shrink: fat bucket (n=200, mean +0.05)",
         f"weight {out['fat']['weight']}, shrunk {out['fat']['shrunk']}")

check("a thin noisy extreme bucket gets weight near 0",
      out["thin"]["weight"] < 0.15, f"B = {out['thin']['weight']}")
travelled = abs(out["thin"]["shrunk"] - pooled) / abs(out["thin"]["mean"] - pooled)
check("  ...and is pulled most of the way to pooled",
      travelled < 0.15, f"retains only {travelled:.1%} of its distance from pooled")
check("  ...so a -0.40 bucket no longer claims -0.40",
      abs(out["thin"]["shrunk"]) < 0.05, f"shrunk = {out['thin']['shrunk']:+.4f}")
check("a fat bucket with the same variance keeps most of its own mean",
      out["fat"]["weight"] > 0.75, f"B = {out['fat']['weight']}")
check("  ...so its shrunk value stays near its own mean",
      abs(out["fat"]["shrunk"] - out["fat"]["mean"]) < abs(out["fat"]["mean"] - pooled) * 0.5)
check("more samples -> more weight, at equal variance",
      out["thin"]["weight"] < out["mid"]["weight"] < out["fat"]["weight"],
      f"{out['thin']['weight']} < {out['mid']['weight']} < {out['fat']['weight']}")

SHRINK_SETS = [BUCKETS,
               [{"key": "a", "mean": 0.5, "var": 1.0, "n": 3},
                {"key": "b", "mean": -0.5, "var": 1.0, "n": 3}],
               [{"key": f"k{i}", "mean": i * 0.1 - 0.5, "var": 0.4, "n": 2 ** i}
                for i in range(8)],
               [{"key": "x", "mean": 0.0, "var": 0.0, "n": 1},
                {"key": "y", "mean": 5.0, "var": 0.0, "n": 1}],
               [{"key": "z", "mean": -1e6, "var": 1e6, "n": 9},
                {"key": "w", "mean": 1e6, "var": 1e6, "n": 9}]]
between_ok, tau_ok, wt_ok = True, True, True
for st in SHRINK_SETS:
    for r in S.eb_shrink(st):
        lo, hi = sorted((r["mean"], r["pooled"]))
        if not (lo - 1e-6 <= r["shrunk"] <= hi + 1e-6):
            between_ok = False
        if r["tau2"] < 0:
            tau_ok = False
        if not (0.0 <= r["weight"] <= 1.0):
            wt_ok = False
check("shrunk always lies between the bucket mean and pooled, inclusive", between_ok)
check("tau2 is never negative", tau_ok)
check("the shrinkage factor B always lies in [0, 1]", wt_ok)

HOMO = [{"key": "a", "mean": 0.1, "var": 0.2, "n": 10},
        {"key": "b", "mean": 0.1, "var": 0.2, "n": 50},
        {"key": "c", "mean": 0.1, "var": 0.9, "n": 3}]
homo = S.eb_shrink(HOMO)
check("homogeneous buckets give tau2 == 0 exactly",
      all(r["tau2"] == 0.0 for r in homo), f"tau2 = {[r['tau2'] for r in homo]}")
check("  ...and every shrunk value equals pooled",
      all(r["shrunk"] == r["pooled"] for r in homo))
close("  ...which is the common mean", homo[0]["pooled"], 0.1, 1e-9)

GARBAGE_ROWS = [{"key": "no_mean"},
                {"key": "none_mean", "mean": None, "var": 0.2, "n": 5},
                {"key": "nan_mean", "mean": NAN, "var": 0.2, "n": 5},
                {"key": "nan_var", "mean": 0.1, "var": NAN, "n": 5},
                {"key": "neg_var", "mean": 0.1, "var": -1.0, "n": 5},
                {"key": "zero_n", "mean": 0.1, "var": 0.2, "n": 0},
                {"key": "str_mean", "mean": "abc", "var": 0.2, "n": 5},
                "not a dict", None, 42,
                {"key": "ok1", "mean": 0.1, "var": 0.2, "n": 10},
                {"key": "ok2", "mean": -0.1, "var": 0.2, "n": 30}]
try:
    surv, raised = S.eb_shrink(GARBAGE_ROWS), False
except Exception as e:
    surv, raised = type(e).__name__, True
check("garbage rows are dropped, not raised on", not raised, f"-> {surv!r}" if raised else "")
check("  ...and exactly the two valid rows survive",
      (not raised) and [r["key"] for r in surv] == ["ok1", "ok2"],
      f"survivors = {[r['key'] for r in surv] if not raised else surv}")
check("every output row carries all four added keys",
      all({"shrunk", "weight", "pooled", "tau2"} <= set(r) for r in S.eb_shrink(BUCKETS)))
check("the internal _vi scratch field is not leaked",
      all("_vi" not in r for r in S.eb_shrink(BUCKETS) + one))
check("a zero-variance bucket does not produce infinite weight",
      all(math.isfinite(r["shrunk"]) and math.isfinite(r["weight"])
          for r in S.eb_shrink(SHRINK_SETS[3])))


# --------------------------------------------------------------------------
section("11. gate_decision — THE absorbing-state fix. mult is NEVER 0.")
# A hard gate blocks the entries that would generate the samples needed to leave
# the gate, so P(exit) = 0 and the bucket is dead forever. Everything in this
# section exists to prove the replacement cannot do that.
sweep_min, sweep_max, worst_case, n_calls = 2.0, 0.0, None, 0
zero_seen = False
NE_GRID = [0, 0.001, 0.5, 1, 1.2, 5, 10, 29, 29.999, 30, 30.001, 31, 45, 60, 90,
           119, 120, 121, 500, 1000, 9999, 10000]
P_GRID = [-1.0, -0.5, -0.21, -0.1, 0.0, 0.1, 0.5, 1.0]
for m in frange(-5.0, 5.0, 0.1):
    for ne in NE_GRID:
        for p in P_GRID:
            d = S.gate_decision(m, ne, p)
            mult = d["mult"]
            n_calls += 1
            if mult == 0:
                zero_seen = True
            if mult < sweep_min:
                sweep_min, worst_case = mult, (m, ne, p)
            sweep_max = max(sweep_max, mult)
print(f"        swept {n_calls} combinations of shrunk_mean x n_eff x pooled")
print(f"        observed mult range: [{sweep_min}, {sweep_max}]  "
      f"(floor {S.GATE_FLOOR}, ceil {S.GATE_CEIL})")
headline("gate_decision sweep", f"{n_calls} combinations, mult range "
         f"[{sweep_min}, {sweep_max}], GATE_FLOOR={S.GATE_FLOOR}")
check("mult is NEVER 0 anywhere in the sweep", not zero_seen)
check("mult never drops below GATE_FLOOR", sweep_min >= S.GATE_FLOOR,
      f"min = {sweep_min} at (mean, n_eff, pooled) = {worst_case}")
check("mult never exceeds GATE_CEIL", sweep_max <= S.GATE_CEIL, f"max = {sweep_max}")
check("the floor is actually reached (the penalty is real, not cosmetic)",
      abs(sweep_min - S.GATE_FLOOR) < 1e-9)
check("the ceiling is actually reached (the bonus is real too)",
      abs(sweep_max - S.GATE_CEIL) < 1e-9)
check("GATE_FLOOR itself is strictly positive — no absorbing state by construction",
      S.GATE_FLOOR > 0, f"GATE_FLOOR = {S.GATE_FLOOR}")

check("below activation the multiplier is exactly 1.0",
      all(S.gate_decision(m, ne)["mult"] == 1.0
          for m in (-5, -0.5, -0.21, 0, 0.5, 5) for ne in (0, 1, 1.2, 10, 29, 29.999)))
check("below activation the confidence is exactly 0.0",
      all(S.gate_decision(m, ne)["confidence"] == 0.0
          for m in (-5, -0.5, 0, 5) for ne in (0, 1, 10, 29.999)))

at_boundary = S.gate_decision(-5.0, S.GATE_ACTIVATION_N, 0.0)
just_over = S.gate_decision(-5.0, S.GATE_ACTIVATION_N + 1e-6, 0.0)
close("continuity: at n_eff == activation_n exactly, mult == 1.0",
      at_boundary["mult"], 1.0, 0.0)
close("continuity: at n_eff == activation_n exactly, confidence == 0.0",
      at_boundary["confidence"], 0.0, 0.0)
check("continuity: no cliff just past the boundary",
      abs(just_over["mult"] - 1.0) < 1e-3, f"mult = {just_over['mult']}")
check("continuity: the worst possible input at the boundary is still full size",
      min(S.gate_decision(m, S.GATE_ACTIVATION_N, p)["mult"]
          for m in frange(-5, 5, 0.5) for p in P_GRID) == 1.0)

mono_ok = True
for ne in (31, 45, 60, 120, 1000):
    seq = [S.gate_decision(m, ne, 0.0)["mult"] for m in frange(-3.0, 3.0, 0.05)]
    if any(b < a - 1e-12 for a, b in zip(seq, seq[1:])):
        mono_ok = False
check("monotone: a worse shrunk_mean never raises the multiplier", mono_ok)
check("strictly worse evidence gives strictly less size, above activation",
      S.gate_decision(-0.05, 120, 0.0)["mult"] < S.gate_decision(0.0, 120, 0.0)["mult"]
      < S.gate_decision(0.05, 120, 0.0)["mult"])
check("what matters is the gap to pooled, not the level",
      S.gate_decision(-0.10, 120, 0.0)["mult"] == S.gate_decision(0.40, 120, 0.50)["mult"])

conf4 = S.gate_decision(-1.0, 4 * S.GATE_ACTIVATION_N, 0.0)["confidence"]
close("confidence saturates at 1.0 by n_eff = 4 * activation_n", conf4, 1.0, 0.0)
check("confidence never exceeds 1.0",
      all(S.gate_decision(-1.0, ne)["confidence"] <= 1.0
          for ne in (120, 121, 500, 5000, 100000, 1e12)))
check("confidence is never negative",
      all(S.gate_decision(-1.0, ne)["confidence"] >= 0.0 for ne in NE_GRID))
check("confidence is monotone increasing in n_eff",
      all(S.gate_decision(-1.0, a)["confidence"] <= S.gate_decision(-1.0, b)["confidence"]
          for a, b in zip(NE_GRID, NE_GRID[1:])))

BAD_INPUTS = [(NAN, 100), (100, NAN), (NAN, NAN), (None, 100), (100, None),
              ("abc", 100), (100, "abc"), ("", ""), (INF, 100), (-INF, 100),
              ([], {}), (0.5, 100, NAN), (0.5, 100, None), (0.5, 100, "abc"),
              (0.5, 100, INF), (0, 0, 0, 0), (0.5, 100, 0.0, 0),
              (0.5, 100, 0.0, -5), (0.5, 100, 0.0, "abc"), (0.5, 100, 0.0, NAN)]
for args in BAD_INPUTS:
    try:
        d, raised = S.gate_decision(*args), False
    except Exception as e:
        d, raised = type(e).__name__, True
    ok = (not raised) and d["mult"] == 1.0 and isinstance(d.get("reason"), str) and d["reason"]
    check(f"gate_decision{args} -> mult 1.0 with a reason, never raises", ok,
          f"-> {d if raised else d['reason']}")

# The actual failure. `call` was gated on a shrunk mean of -0.21 from an
# effective sample of 1.2, the bot went permanently short-only, and the gate did
# not even protect the four call positions already open. Under the new rule the
# same evidence does not move size at all.
regression = S.gate_decision(-0.21, 1.2)
print(f"        shrunk_mean=-0.21, n_eff=1.2 -> {regression}")
headline("gate_decision(-0.21, 1.2) — the real gated bucket",
         f"mult {regression['mult']}, confidence {regression['confidence']}")
close("REGRESSION the-bot-went-short-only: mult is EXACTLY 1.0", regression["mult"], 1.0, 0.0)
close("REGRESSION the-bot-went-short-only: confidence is EXACTLY 0.0",
      regression["confidence"], 0.0, 0.0)
check("REGRESSION the-bot-went-short-only: the reason names the missing evidence",
      "n_eff" in regression["reason"] and "activation" in regression["reason"],
      regression["reason"])
check("REGRESSION: even at the raw n=7 the old rule used, size is untouched",
      S.gate_decision(-0.21, 7.0)["mult"] == 1.0)
check("REGRESSION: only at n_eff >= 30 does -0.21 start costing size",
      S.gate_decision(-0.21, 60.0)["mult"] < 1.0,
      f"n_eff=60 -> mult {S.gate_decision(-0.21, 60.0)['mult']}")
check("REGRESSION: and even then it never reaches zero",
      S.gate_decision(-0.21, 1e9)["mult"] >= S.GATE_FLOOR,
      f"n_eff=1e9 -> mult {S.gate_decision(-0.21, 1e9)['mult']}")

check("every return carries mult, confidence and reason",
      all({"mult", "confidence", "reason"} == set(S.gate_decision(m, ne).keys())
          for m in (-1, 0, 1, NAN) for ne in (1, 100, NAN)))


# --------------------------------------------------------------------------
section("12. sharpe_variance — and the raw-vs-excess kurtosis trap")
check("n < 2 -> inf", all(S.sharpe_variance(0.5, n) == INF for n in (-5, 0, 1)))
close("sr=0, n=101, kurt=3 -> exactly 1/100", S.sharpe_variance(0, 101, 0.0, 3.0), 0.01, 0.0)
close("sr=0, n=1001, kurt=3 -> exactly 1/1000",
      S.sharpe_variance(0, 1001, 0.0, 3.0), 0.001, 1e-15)

# CONVENTION: `kurtosis` is RAW (Normal = 3.0), not excess (Normal = 0.0).
# RAW 3.0 is the correct call. Passing 0.0 pretends the returns are thin-tailed,
# shrinks the variance, and makes every downstream PSR/DSR/MinTRL too
# optimistic. At sr = 0 the two agree (the kurtosis term is multiplied by SR^2),
# so the convention can only be pinned at a non-zero Sharpe.
v_raw = S.sharpe_variance(0.5, 101, 0.0, 3.0)
v_excess_mistake = S.sharpe_variance(0.5, 101, 0.0, 0.0)
close("RAW kurtosis 3.0 reproduces the normal-case bracket: (1 + 0.5*SR^2)/(n-1)",
      v_raw, (1.0 + 0.5 * 0.25) / 100.0, 1e-15)
check("the excess-kurtosis mistake (0.0) gives a DIFFERENT, SMALLER variance",
      v_excess_mistake < v_raw,
      f"raw 3.0 -> {v_raw}, excess-mistake 0.0 -> {v_excess_mistake} "
      f"({v_excess_mistake / v_raw:.1%} of it) — 3.0 is correct")
close("  ...specifically (1 - 0.25*SR^2)/(n-1)", v_excess_mistake,
      (1.0 - 0.25 * 0.25) / 100.0, 1e-15)
check("at sr = 0 the two conventions coincide, so they cannot be told apart there",
      S.sharpe_variance(0, 101, 0.0, 3.0) == S.sharpe_variance(0, 101, 0.0, 0.0))
check("fatter tails raise the variance",
      S.sharpe_variance(0.5, 101, 0.0, 3.0) < S.sharpe_variance(0.5, 101, 0.0, 9.0))

neg_skew = S.sharpe_variance(-0.1, 100, -20.0, 3.0)
pos_skew = S.sharpe_variance(0.1, 100, 20.0, 3.0)
check("a large negative skew cannot produce a negative variance",
      neg_skew > 0, f"var = {neg_skew:.4e}")
check("a large positive skew cannot either", pos_skew > 0, f"var = {pos_skew:.4e}")
close("  ...both land on the 1e-9 bracket floor / (n-1)", neg_skew, 1e-9 / 99, 1e-18)
check("variance is positive across a wide skew/kurtosis sweep",
      all(S.sharpe_variance(s, 100, g, k) > 0
          for s in (-3, -1, -0.1, 0, 0.1, 1, 3)
          for g in (-30, -5, -1, 0, 1, 5, 30)
          for k in (0, 1, 3, 10, 100)))
check("variance falls as n grows",
      all(S.sharpe_variance(0.5, a) > S.sharpe_variance(0.5, b)
          for a, b in zip([2, 10, 100, 1000], [10, 100, 1000, 10000])))


# --------------------------------------------------------------------------
section("13. psr — P(true Sharpe > benchmark)")
for sr in (-1.0, 0.0, 0.5, 2.0):
    close(f"sr == benchmark ({sr}) -> 0.5", S.psr(sr, 100, sr), 0.5, 1e-12)
check("psr is monotone increasing in sr",
      all(S.psr(a, 100, 0.0) <= S.psr(b, 100, 0.0)
          for a, b in zip(frange(-1, 0.95, 0.05), frange(-0.95, 1.0, 0.05))))
check("  ...and strictly so around the benchmark",
      S.psr(0.1, 100) < S.psr(0.2, 100) < S.psr(0.5, 100))
check("psr is monotone decreasing in the benchmark",
      all(S.psr(0.5, 100, a) >= S.psr(0.5, 100, b)
          for a, b in zip(frange(-1, 0.95, 0.05), frange(-0.95, 1.0, 0.05))))
big_n = [S.psr(0.5, n) for n in (10, 100, 1000, 10000, 100000)]
check("psr -> 1.0 as n grows with sr > benchmark",
      all(a <= b for a, b in zip(big_n, big_n[1:])) and big_n[-1] > 0.999,
      f"n=1e5 -> {big_n[-1]:.6f}")
check("psr -> 0.0 as n grows with sr < benchmark",
      S.psr(-0.5, 100000) < 0.001, f"{S.psr(-0.5, 100000):.6e}")

base = S.psr(0.5, 100, 0.0, 0.0, 3.0)
skewed = S.psr(0.5, 100, 0.0, -1.5, 3.0)
fat = S.psr(0.5, 100, 0.0, 0.0, 12.0)
both = S.psr(0.5, 100, 0.0, -1.5, 12.0)
check("negative skew LOWERS psr", skewed < base, f"{base:.6f} -> {skewed:.6f}")
check("fat tails LOWER psr", fat < base, f"{base:.6f} -> {fat:.6f}")
check("both together lower it further than either alone",
      both < skewed and both < fat, f"both -> {both:.6f}")
check("positive skew raises psr (the mirror image)", S.psr(0.5, 100, 0.0, 1.5, 3.0) > base)
check("psr is always in [0, 1]",
      all(0.0 <= S.psr(s, n, b, g, k) <= 1.0
          for s in (-3, 0, 3) for n in (0, 2, 100, 10000)
          for b in (-1, 0, 1) for g in (-5, 0, 5) for k in (0, 3, 20)))
close("n < 2 -> 0.5 (infinite variance, no information)", S.psr(5.0, 1), 0.5, 0.0)


# --------------------------------------------------------------------------
section("14. min_track_record_length — HEADLINE: ~1,400 trades, not 4")
for sr, bm in ((0.0, 0.0), (-0.5, 0.0), (0.5, 0.5), (0.1, 0.9)):
    check(f"sr {sr} <= benchmark {bm} -> inf (no data proves a worse thing better)",
          S.min_track_record_length(sr, bm) == INF)

# A plausible PER-TRADE sleeve Sharpe with the negative skew and fat tails that
# long options actually have. The bot moved a sleeve weight on 4 trades.
SR_TRADE, SKEW, KURT = 0.0445, -0.5, 6.0
mintrl = S.min_track_record_length(SR_TRADE, 0.0, SKEW, KURT, 0.95)
MIN_SAMPLES_SLEEVE = 4        # the shipped constant, learn.py:21
print(f"        per-trade SR {SR_TRADE}, skew {SKEW}, kurtosis {KURT}, 95% confidence")
print(f"        -> MinTRL = {mintrl:.1f} trades   (MIN_SAMPLES_SLEEVE = {MIN_SAMPLES_SLEEVE})")
headline("MinTRL at a plausible per-trade sleeve Sharpe", round(mintrl, 1),
         f"sr={SR_TRADE}, benchmark=0, skew={SKEW}, kurtosis={KURT}, confidence=0.95")
headline("shortfall of MIN_SAMPLES_SLEEVE = 4", f"{mintrl / MIN_SAMPLES_SLEEVE:.0f}x")
check("MinTRL is in the thousands, as documented",
      1000 <= mintrl <= 2000, f"exact value: {mintrl:.4f}")
check("  ...i.e. ~1,400 trades", abs(mintrl - 1400) < 100, f"{mintrl:.1f}")
check("MinTRL >> MIN_SAMPLES_SLEEVE = 4",
      mintrl > 100 * MIN_SAMPLES_SLEEVE,
      f"{mintrl:.0f} vs 4 — short by {mintrl / MIN_SAMPLES_SLEEVE:.0f}x")

seq = [S.min_track_record_length(s, 0.0, 0.0, 3.0) for s in frange(0.02, 1.0, 0.02)]
check("monotone decreasing in (sr - benchmark) via sr",
      all(a >= b for a, b in zip(seq, seq[1:])), f"{seq[0]:.0f} down to {seq[-1]:.1f}")
seq_b = [S.min_track_record_length(1.0, b, 0.0, 3.0) for b in frange(0.0, 0.9, 0.05)]
check("monotone increasing as the benchmark closes in",
      all(a <= b for a, b in zip(seq_b, seq_b[1:])), f"{seq_b[0]:.1f} up to {seq_b[-1]:.0f}")
check("a bigger edge needs less track record",
      S.min_track_record_length(0.5) < S.min_track_record_length(0.1))
check("higher confidence needs more track record",
      S.min_track_record_length(0.1, confidence=0.99) >
      S.min_track_record_length(0.1, confidence=0.95))
check("negative skew lengthens the required track record",
      S.min_track_record_length(0.5, 0.0, -1.5, 3.0) >
      S.min_track_record_length(0.5, 0.0, 0.0, 3.0))
check("fat tails lengthen it too",
      S.min_track_record_length(0.5, 0.0, 0.0, 12.0) >
      S.min_track_record_length(0.5, 0.0, 0.0, 3.0))
check("MinTRL is always >= 1", all(S.min_track_record_length(s) >= 1.0
                                   for s in (0.01, 0.1, 1.0, 10.0)))


# --------------------------------------------------------------------------
section("15. expected_max_sharpe — the bar the BEST of N random rules clears")
for v in (0.0, 0.5, 4.0):
    close(f"n_trials = 1 at var {v} -> 0.0 (no selection, no inflation)",
          S.expected_max_sharpe(1, v), 0.0, 0.0)
check("positive for n >= 2",
      all(S.expected_max_sharpe(n, 0.5) > 0 for n in (2, 3, 5, 20, 100, 1000)))
seq = [S.expected_max_sharpe(n, 0.5) for n in (1, 2, 3, 5, 10, 20, 50, 100, 500, 1000)]
check("monotone increasing in n_trials", all(a <= b for a, b in zip(seq, seq[1:])),
      f"{seq[0]:.4f} up to {seq[-1]:.4f}")
seq_v = [S.expected_max_sharpe(20, v) for v in frange(0.0, 2.0, 0.1)]
check("monotone increasing in sr_variance", all(a <= b for a, b in zip(seq_v, seq_v[1:])),
      f"{seq_v[0]:.4f} up to {seq_v[-1]:.4f}")
close("zero variance across trials -> 0.0 (nothing to select on)",
      S.expected_max_sharpe(100, 0.0), 0.0, 0.0)
check("scales as sqrt(variance)",
      abs(S.expected_max_sharpe(20, 4.0) - 2 * S.expected_max_sharpe(20, 1.0)) < 1e-9)
check("negative variance is clamped, not sqrt'd", S.expected_max_sharpe(20, -1.0) == 0.0)
check("trying 20 variants sets a real bar",
      S.expected_max_sharpe(20, 0.5) > 1.0,
      f"SR_0 = {S.expected_max_sharpe(20, 0.5):.4f} just to match luck")


# --------------------------------------------------------------------------
section("16. deflated_sharpe — the same Sharpe stops being a discovery")
d = S.deflated_sharpe(1.5, 250, 5, 0.5)
check("returns exactly the three documented keys",
      set(d.keys()) == {"sr0", "dsr", "is_discovery"}, str(d))
few = S.deflated_sharpe(1.5, 250, 5, 0.5)
many = S.deflated_sharpe(1.5, 250, 100, 0.5)
print(f"        SR 1.5 over 250 obs, 5 trials   -> {few}")
print(f"        SR 1.5 over 250 obs, 100 trials -> {many}")
headline("deflated_sharpe(1.5, n=250) at 5 trials", f"dsr {few['dsr']}, discovery {few['is_discovery']}")
headline("deflated_sharpe(1.5, n=250) at 100 trials", f"dsr {many['dsr']}, discovery {many['is_discovery']}")
check("a strong Sharpe with few trials IS a discovery", few["is_discovery"] is True)
check("THE SAME Sharpe with many trials is NOT", many["is_discovery"] is False)
check("  ...because the bar SR_0 rose", many["sr0"] > few["sr0"],
      f"{few['sr0']} -> {many['sr0']}")
check("  ...and the DSR collapsed", many["dsr"] < few["dsr"], f"{few['dsr']} -> {many['dsr']}")
check("is_discovery is a real bool", isinstance(few["is_discovery"], bool))
check("dsr is always in [0, 1]",
      all(0.0 <= S.deflated_sharpe(s, n, t, v)["dsr"] <= 1.0
          for s in (-2, 0, 1.5, 5) for n in (0, 2, 250) for t in (1, 20, 1000)
          for v in (0.0, 0.5)))
check("dsr is monotone decreasing in n_trials",
      all(S.deflated_sharpe(1.5, 250, a, 0.5)["dsr"] >= S.deflated_sharpe(1.5, 250, b, 0.5)["dsr"]
          for a, b in zip([1, 2, 5, 10, 20, 50, 100], [2, 5, 10, 20, 50, 100, 200])))
check("the discovery threshold really is 0.95",
      S.deflated_sharpe(1.5, 250, 20, 0.5)["is_discovery"] ==
      (S.deflated_sharpe(1.5, 250, 20, 0.5)["dsr"] > 0.95))


# --------------------------------------------------------------------------
section("17-18. kelly_fraction / kelly_growth_penalty")
close("mu=0.08, sigma=0.20 -> f* = 2.0", S.kelly_fraction(0.08, 0.20), 2.0, 1e-12)
close("mu = 0 -> 0.0", S.kelly_fraction(0.0, 0.2), 0.0, 0.0)
for mu in (-0.01, -1.0, -1e9):
    close(f"negative mu ({mu}) -> 0.0, never a short bet", S.kelly_fraction(mu, 0.2), 0.0, 0.0)
for sg in (0.0, -0.1, -5.0):
    close(f"sigma <= 0 ({sg}) -> 0.0", S.kelly_fraction(0.1, sg), 0.0, 0.0)
check("NaN inputs -> 0.0",
      S.kelly_fraction(NAN, 0.2) == 0.0 and S.kelly_fraction(0.1, NAN) == 0.0)
check("f* is never negative",
      all(S.kelly_fraction(m, s) >= 0 for m in (-2, -0.1, 0, 0.1, 2) for s in (0.01, 0.2, 5)))
check("f* rises with mu and falls with sigma",
      S.kelly_fraction(0.1, 0.2) > S.kelly_fraction(0.05, 0.2) and
      S.kelly_fraction(0.1, 0.2) > S.kelly_fraction(0.1, 0.4))

close("c = 1 (full Kelly) -> 1.0 of the growth rate", S.kelly_growth_penalty(1.0), 1.0, 0.0)
close("c = 0.5 (half Kelly) -> EXACTLY 0.75 — the 75%-for-a-quarter-of-the-variance claim",
      S.kelly_growth_penalty(0.5), 0.75, 0.0)
close("c = 2 -> 0.0, zero growth despite a positive edge",
      S.kelly_growth_penalty(2.0), 0.0, 0.0)
check("c = 2.5 -> negative growth: overbetting a winning system loses",
      S.kelly_growth_penalty(2.5) < 0, f"g/g* = {S.kelly_growth_penalty(2.5)}")
close("c = 0 -> 0.0 (no bet, no growth)", S.kelly_growth_penalty(0.0), 0.0, 0.0)
close("c = 0.25 -> 0.4375", S.kelly_growth_penalty(0.25), 0.4375, 1e-12)
check("the maximum is at c = 1",
      all(S.kelly_growth_penalty(c) <= 1.0 + 1e-12 for c in frange(-1, 3, 0.05)))
check("symmetric about c = 1",
      all(abs(S.kelly_growth_penalty(1 - x) - S.kelly_growth_penalty(1 + x)) < 1e-12
          for x in frange(0, 1.5, 0.1)))


# --------------------------------------------------------------------------
section("19. drawdown_probability — HEADLINE: full Kelly halves you, 50/50")
p_half = S.drawdown_probability(0.5, 1.0)
headline("drawdown_probability(0.5, 1.0) — P(ever halving) at full Kelly", p_half)
close("full Kelly: P(equity ever halves) = 0.5 EXACTLY", p_half, 0.5, 0.0)
close("half Kelly: P(ever halving) = 0.125", S.drawdown_probability(0.5, 0.5), 0.125, 1e-12)
close("quarter Kelly: P(ever halving) = 0.0078125",
      S.drawdown_probability(0.5, 0.25), 0.0078125, 1e-12)
for c in (2.0, 2.5, 10.0, 1e9):
    close(f"c = {c:g} >= 2 -> 1.0, certain ruin in the limit",
          S.drawdown_probability(0.5, c), 1.0, 0.0)
for x in (0.0, 1.0, -0.5, 2.0, 1e9, NAN):
    close(f"x = {x} is outside (0,1) -> 1.0", S.drawdown_probability(x, 1.0), 1.0, 0.0)
close("c <= 0 -> 1.0", S.drawdown_probability(0.5, 0.0), 1.0, 0.0)
check("monotone: a larger c raises the probability of any given drawdown",
      all(S.drawdown_probability(0.5, a) <= S.drawdown_probability(0.5, b)
          for a, b in zip(frange(0.05, 1.95, 0.05), frange(0.10, 2.0, 0.05))))
check("monotone in x: a shallower drawdown is likelier",
      all(S.drawdown_probability(a, 1.0) <= S.drawdown_probability(b, 1.0)
          for a, b in zip(frange(0.05, 0.90, 0.05), frange(0.10, 0.95, 0.05))))
check("always in [0, 1]",
      all(0.0 <= S.drawdown_probability(x, c) <= 1.0
          for x in (-1, 0, 0.01, 0.5, 0.99, 1, 2) for c in (-1, 0, 0.1, 1, 2, 10)))
check("at full Kelly a 90% drawdown is still a 1-in-10 event",
      abs(S.drawdown_probability(0.1, 1.0) - 0.1) < 1e-12)


# --------------------------------------------------------------------------
section("20. time_to_multiple — and what a '10x in a year' Sharpe really is")
for m, s in ((1.0, 1.0), (0.5, 1.0), (0.0, 1.0), (-2, 1.0)):
    check(f"multiple {m} <= 1 -> inf", S.time_to_multiple(m, s) == INF)
for s in (0.0, -0.5, -10):
    check(f"sharpe {s} <= 0 -> inf", S.time_to_multiple(10, s) == INF)
close("time_to_multiple(10, 1.0) ~ 4.605 years", S.time_to_multiple(10, 1.0), 4.605170186, 1e-6)
close("time_to_multiple(2, 1.0) ~ 1.386 years", S.time_to_multiple(2, 1.0), 1.386294361, 1e-6)
close("Medallion-ish 2.5 gross 10x's in ~0.74 years",
      S.time_to_multiple(10, 2.5), 0.736827, 1e-5)

# Invert the "10x in one year" claim: solve 2*ln(10)/SR^2 = 1.
sr_1yr = math.sqrt(2.0 * math.log(10.0))
headline("Sharpe for a 10x in one year (expectation)", round(sr_1yr, 4),
         "= sqrt(2*ln 10); this is the E[time] inversion, not a probability")
close("solving T = 1 at multiple 10 gives Sharpe ~2.146", sr_1yr, 2.145966026, 1e-6)
close("  ...and it round-trips: T(10, 2.146) = 1.0", S.time_to_multiple(10, sr_1yr), 1.0, 1e-12)
check("2.146 is above Medallion's ~2.5 gross? No — just below it",
      sr_1yr < 2.5, f"{sr_1yr:.4f} vs 2.5")

# The docstring used to offer 1.72 as "a coin-flip chance of getting there".
# Under the same full-Kelly model (log drift SR^2/2, log vol SR) the 50%
# first-passage Sharpe is found by bisection on the reflection formula below.
# It comes out at 1.802, not 1.72. The two legitimate figures are 2.146
# (expected time) and 1.802 (50% chance of TOUCHING 10x inside the year).
B = math.log(10.0)


def p_touch(sr):
    nu, sg = sr * sr / 2.0, sr
    return (S.norm_cdf((nu - B) / sg) +
            math.exp(2.0 * nu * B / (sg * sg)) * S.norm_cdf((-B - nu) / sg))


lo, hi = 0.5, 4.0
for _ in range(200):
    mid = (lo + hi) / 2.0
    if p_touch(mid) < 0.5:
        lo = mid
    else:
        hi = mid
sr_coinflip = (lo + hi) / 2.0
print(f"        expectation variant : Sharpe {sr_1yr:.4f}  (E[time to 10x] = 1 year)")
print(f"        coin-flip variant   : Sharpe {sr_coinflip:.4f}  "
      f"(50% chance of touching 10x inside 1 year) — NOT 1.72")
headline("Sharpe for a 10x in one year (coin flip)", round(sr_coinflip, 4),
         "50% chance of TOUCHING 10x inside the year; the docstring's 1.72 is neither variant")
close("the coin-flip variant is 1.802, not 1.72", sr_coinflip, 1.8022, 1e-3)
check("  ...and 1.72 reproduces neither figure",
      abs(sr_coinflip - 1.72) > 0.05 and abs(sr_1yr - 1.72) > 0.05,
      f"expectation {sr_1yr:.4f}, coin flip {sr_coinflip:.4f}")
check("the coin-flip Sharpe is below the expectation Sharpe, as it must be",
      sr_coinflip < sr_1yr)
check("monotone: a higher Sharpe reaches any multiple sooner",
      all(S.time_to_multiple(10, a) >= S.time_to_multiple(10, b)
          for a, b in zip(frange(0.1, 2.9, 0.1), frange(0.2, 3.0, 0.1))))
check("monotone: a bigger multiple takes longer",
      all(S.time_to_multiple(a, 1.0) <= S.time_to_multiple(b, 1.0)
          for a, b in zip(frange(1.1, 9.9, 0.1), frange(1.2, 10.0, 0.1))))


# --------------------------------------------------------------------------
section("21. prob_reach_multiple — HEADLINE: a coin flip 10x's you 9.09% free")
free = S.prob_reach_multiple(10.0, 0.1)
print(f"        zero edge, target 10x, ruin 0.1x ($10k -> $100k before $1k)")
print(f"        -> {free:.6f}   (exact martingale answer (10-1)/(100-1) = {9 / 99:.6f})")
headline("prob_reach_multiple(10, 0.1) with zero edge", round(free, 6),
         "the free benchmark for any '10x challenge'; = 9/99 exactly")
close("zero edge -> 0.0909 to 4 decimals", free, 0.0909, 1e-4)
close("  ...and it is exactly 9/99, the optional-stopping answer", free, 9.0 / 99.0, 1e-9)
check("  ...and it is NOT 0.5 (the log-drift misreading)", abs(free - 0.5) > 0.4,
      f"{free:.6f}, not 0.500000")
check("a fair game gives 9.09% at ANY volatility (mu=0 => lambda=-1 regardless)",
      all(abs(S.prob_reach_multiple(10, 0.1, 0.0, s) - 9 / 99) < 1e-9
          for s in (0.01, 0.05, 0.2, 0.61, 1.0, 3.0)))

check("positive edge raises it",
      S.prob_reach_multiple(10, 0.1, 0.03, 0.61) > free,
      f"mu=+0.03, sg=0.61 -> {S.prob_reach_multiple(10, 0.1, 0.03, 0.61):.6f}")
check("negative edge lowers it",
      S.prob_reach_multiple(10, 0.1, -0.03, 0.61) < free,
      f"mu=-0.03, sg=0.61 -> {S.prob_reach_multiple(10, 0.1, -0.03, 0.61):.6f}")
check("monotone increasing in the edge",
      all(S.prob_reach_multiple(10, 0.1, a, 0.61) <= S.prob_reach_multiple(10, 0.1, b, 0.61)
          for a, b in zip(frange(-0.2, 0.19, 0.01), frange(-0.19, 0.2, 0.01))))

# The journal's realised per-trade dispersion is ~0.61. A learning loop that
# makes the bot worse by the measured 2.7-3.3pp per trade turns the free 9.09%
# into this:
harmed = S.prob_reach_multiple(10.0, 0.1, -0.030, 0.61)
headline("prob_reach_multiple at the measured -3.0pp/trade harm", round(harmed, 6),
         "vol 0.61 per trade (the journal's realised dispersion)")
check("at a realistically negative per-trade edge it falls BELOW the free 9.09%",
      harmed < free, f"{harmed:.6f} < {free:.6f} — worse than a coin flip")
check("  ...by a material margin", harmed < free * 0.85,
      f"{harmed / free:.1%} of the free benchmark")

for args, why in [((10.0, 1.0), "ruin >= 1"), ((10.0, 1.5), "ruin > 1"),
                  ((10.0, 0.0), "ruin = 0"), ((10.0, -0.5), "ruin < 0"),
                  ((1.0, 0.1), "target <= 1"), ((0.5, 0.1), "target < 1"),
                  ((-3.0, 0.1), "target < 0"),
                  ((10.0, 0.1, 0.0, 0.0), "sigma = 0"),
                  ((10.0, 0.1, 0.0, -1.0), "sigma < 0"),
                  ((INF, 0.1), "target = inf"), ((10.0, 0.1, INF, 1.0), "edge = inf"),
                  ((NAN, 0.1), "target = NaN"), ((10.0, NAN), "ruin = NaN")]:
    try:
        got, raised = S.prob_reach_multiple(*args), False
    except Exception as e:
        got, raised = type(e).__name__, True
    check(f"degenerate ({why}) -> 0.0 without raising",
          (not raised) and got == 0.0, f"-> {got!r}")

check("always in [0, 1]",
      all(0.0 <= S.prob_reach_multiple(t, r, m, s) <= 1.0
          for t in (1.5, 10, 1000) for r in (0.01, 0.1, 0.9)
          for m in (-10, -0.1, 0, 0.1, 10) for s in (0.01, 0.61, 5)))
check("a huge positive edge approaches 1.0",
      S.prob_reach_multiple(10, 0.1, 5.0, 0.61) > 0.999,
      f"{S.prob_reach_multiple(10, 0.1, 5.0, 0.61):.6f}")
check("a huge negative edge approaches 0.0",
      S.prob_reach_multiple(10, 0.1, -5.0, 0.61) < 0.001,
      f"{S.prob_reach_multiple(10, 0.1, -5.0, 0.61):.6e}")
check("a nearer target is easier to reach",
      S.prob_reach_multiple(2, 0.1) > S.prob_reach_multiple(10, 0.1) >
      S.prob_reach_multiple(100, 0.1))


# --------------------------------------------------------------------------
section("22. TOTALITY SWEEP — every public function, every kind of garbage")
# These run inside a trading loop. An unhandled exception there is a financial
# bug, not a stack trace. `inspect` enumerates the public callables so a
# function added to stats.py without a test still gets swept.
PUBLIC = sorted((n, f) for n, f in vars(S).items()
                if callable(f) and not n.startswith("_")
                and getattr(f, "__module__", None) == "stats")
print(f"        {len(PUBLIC)} public callables discovered: "
      f"{', '.join(n for n, _ in PUBLIC)}")
check("inspect found every documented public function", len(PUBLIC) >= 23,
      f"{len(PUBLIC)} found")

raises, calls = [], 0
for name, fn in PUBLIC:
    params = list(inspect.signature(fn).parameters.values())
    n_all = len(params)
    n_req = sum(1 for p in params if p.default is p.empty)
    # uniform garbage at every arity
    for g in GARBAGE:
        for k in range(n_req, n_all + 1):
            calls += 1
            try:
                fn(*([g] * k))
            except Exception as e:
                raises.append(f"{name}({g!r} x{k}) -> {type(e).__name__}: {e}")
    # mixed garbage in the first two positions, which is where the type
    # confusions that actually happen (a None where a list was expected next to
    # a string where a float was expected) live
    for combo in itertools.product(GARBAGE, repeat=min(n_all, 2)):
        args = (list(combo) + [NAN] * n_all)[:max(n_req, min(n_all, 2))]
        calls += 1
        try:
            fn(*args)
        except Exception as e:
            raises.append(f"{name}{tuple(args)!r} -> {type(e).__name__}: {e}")
print(f"        {calls} garbage calls made")
check("no public function raises on any garbage input", not raises,
      f"{len(raises)} raised" if raises else f"{calls} calls, 0 raises")
for r in raises[:15]:
    print(f"        ! {r}")

# and the return types stay usable
bad_types = []
for name, fn in PUBLIC:
    params = list(inspect.signature(fn).parameters.values())
    n_req = sum(1 for p in params if p.default is p.empty)
    for g in (None, "abc", NAN, []):
        try:
            got = fn(*([g] * n_req))
        except Exception:
            continue
        if not isinstance(got, (float, int, dict, list, tuple, bool)):
            bad_types.append(f"{name}({g!r}) -> {type(got).__name__}")
check("every garbage return is a plain number/dict/list/tuple", not bad_types,
      "; ".join(bad_types[:5]))

nan_returns = []
for name, fn in PUBLIC:
    params = list(inspect.signature(fn).parameters.values())
    n_req = sum(1 for p in params if p.default is p.empty)
    for g in (None, "abc", NAN, [], 0, -1):
        try:
            got = fn(*([g] * n_req))
        except Exception:
            continue
        if is_float(got) and math.isnan(got):
            nan_returns.append(f"{name}({g!r})")
check("no public function returns NaN on garbage — a NaN propagates silently",
      not nan_returns, "; ".join(nan_returns[:8]))


# --------------------------------------------------------------------------
print()
print("=" * 74)
print("HEADLINE NUMBERS (for the write-up)")
print("=" * 74)
for k, (v, note) in HEADLINE.items():
    print(f"  {k}")
    print(f"      = {v}" + (f"   [{note}]" if note else ""))

print()
print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
print("=" * 74)
sys.exit(1 if FAIL else 0)
