"""
Statistical machinery for the learning loop.

WHY THIS MODULE EXISTS
----------------------
The first version of `learn.py` gated a feature bucket after 5 trades with a
win-rate at or below 25%. A Monte-Carlo replay of that exact code, run against
the bot's own journal, found a **56-59% probability that the learning loop made
the bot worse**, costing 2.7-3.3 percentage points per trade. The harm only
disappeared once the gate threshold reached ~50 samples. Four separate defects
produced that result, and each one has a fix in here:

1. CLUSTERING. All 7 settled trades happened on a single day. Trades that share
   a day share the market's move, so they are not 7 independent observations.
   Moulton's design effect `deff = 1 + (m_bar - 1) * rho` puts the effective
   sample size at **n_eff ~ 1.0-1.5**, not 7. -> `effective_n()`

2. MULTIPLE TESTING. The gate rule was applied to 17 buckets at once. Against
   the bot's own 40% base win rate (3 of 7) its size is
   P(<=1 win in 5 | p=0.40) = **0.337** for a nominal 5% target, so the
   family-wise error rate across those buckets is fwer(0.337, 17) = **0.9991** -
   a gate was very close to certain to fire on noise alone. Required sample size
   for one honest gate decision: n = **61** uncorrected, **131** under
   Bonferroni. The bot used 5. -> `bonferroni_alpha()`, `required_n_for_gate()`

3. COLLINEARITY. `align_bucket()` returns `with_trend` iff
   `(bull and trend_up) or (bear and not trend_up)`. Every trade in the journal
   had `trend_up=False`, so `counter_trend <=> bull <=> call` with r = 1.0000.
   Gating "call" and "counter_trend" was gating one hypothesis twice while the
   `if len(buckets) < 2: continue` guard saw two populated buckets and allowed
   it. Cramer's V detects this. -> `cramers_v()`, `dedupe_dims()`

4. ABSORBING STATES. This is the worst one. A hard gate blocks new entries in
   the bucket, so the bucket can never accumulate the samples that would clear
   the gate, so `learn()` re-fires it forever. P(exit) = 0. The bot became
   permanently short-only and the gate did not even protect the four call
   positions already open. A gate must be a *continuous* multiplier that never
   reaches zero, so evidence can always flow back. -> `gate_decision()`

Also here: the Bailey/Lopez de Prado track-record statistics, because the other
half of the problem is claiming a result from too little data. Moving one sleeve
weight honestly needs a Minimum Track Record Length of roughly **1,400 trades**.
The bot moved sleeve weights on 4.

Sources are cited per function. Pure functions, no I/O, no dependencies beyond
the standard library and numpy - so every one of these is unit-testable and the
tests do not need a broker.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

EULER_GAMMA = 0.5772156649015329


# ---------------------------------------------------------------------------
# Normal distribution helpers (no scipy in this environment)
# ---------------------------------------------------------------------------
def norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf. Total: never raises, never NaN."""
    try:
        x = float(x)
    except (TypeError, ValueError, OverflowError):
        return 0.5
    if math.isnan(x):
        return 0.5
    if x > 40:
        return 1.0
    if x < -40:
        return 0.0
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation).

    Accurate to ~1.15e-9 in absolute value over the open unit interval, which is
    far more precision than any decision here needs. Returns +/-inf-ish clamps
    at the boundaries rather than raising, because callers are inside a trading
    loop and an exception there is a financial bug.
    """
    try:
        p = float(p)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if math.isnan(p):
        return 0.0
    if p <= 0.0:
        return -40.0
    if p >= 1.0:
        return 40.0

    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425

    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5])*q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)


# ---------------------------------------------------------------------------
# 1. Clustering: how many INDEPENDENT observations do we actually have?
# ---------------------------------------------------------------------------
def intraclass_correlation(groups: Sequence[Sequence[float]]) -> float:
    """One-way-ANOVA intraclass correlation of outcomes grouped by cluster.

    `groups` is a list of lists: one inner list per cluster (here, per trading
    day). Returns rho in [0, 1]. rho = 0 means membership in a day tells you
    nothing about the outcome; rho = 1 means every trade on a day had the same
    outcome, so the day contributed exactly one observation.

    Clamped at 0 because a negative ICC estimate is a finite-sample artifact,
    and letting it go negative would *inflate* the effective sample size - the
    exact error this module exists to prevent.
    """
    clean = [[float(x) for x in _seq(g) if x is not None and not _isnan(x)]
             for g in _seq(groups)]
    clean = [g for g in clean if len(g) > 0]
    k = len(clean)
    n_total = sum(len(g) for g in clean)
    if k < 2 or n_total <= k:
        # Either one cluster (rho is unidentified but the honest default is
        # "fully clustered") or every cluster is a singleton (rho = 0).
        return 1.0 if k < 2 else 0.0

    grand = sum(sum(g) for g in clean) / n_total
    ss_between = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in clean)
    ss_within = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in clean)
    df_between, df_within = k - 1, n_total - k
    ms_between = ss_between / df_between
    ms_within = ss_within / df_within if df_within > 0 else 0.0

    # Balanced-design n0 correction for unequal cluster sizes.
    sum_sq = sum(len(g) ** 2 for g in clean)
    n0 = (n_total - sum_sq / n_total) / (k - 1)
    if n0 <= 0 or (ms_between + (n0 - 1) * ms_within) <= 0:
        return 0.0
    rho = (ms_between - ms_within) / (ms_between + (n0 - 1) * ms_within)
    return max(0.0, min(1.0, rho))


def design_effect(mean_cluster_size: float, rho: float) -> float:
    """Moulton's design effect: deff = 1 + (m_bar - 1) * rho.

    Moulton (1986, 1990), "Random group effects and the precision of regression
    estimates". Standard errors computed as if clustered observations were
    independent are too small by a factor of sqrt(deff).
    """
    try:
        m = max(1.0, float(mean_cluster_size))
        r = max(0.0, min(1.0, float(rho)))
    except (TypeError, ValueError, OverflowError):
        return 1.0
    return 1.0 + (m - 1.0) * r


def effective_n(groups: Sequence[Sequence[float]]) -> dict:
    """Effective sample size after correcting for clustering.

    Returns {n, n_clusters, mean_cluster_size, rho, deff, n_eff}.

    The bot's journal at the time of writing: 7 trades, all on 2026-07-27, with
    outcomes that moved together. That gives n_eff ~ 1.0-1.5. Any rule that
    reads `n = 7` and acts is reading a sample size that does not exist.
    """
    clean = [[float(x) for x in _seq(g) if x is not None and not _isnan(x)]
             for g in _seq(groups)]
    clean = [g for g in clean if g]
    n = sum(len(g) for g in clean)
    k = len(clean)
    if n == 0:
        return {"n": 0, "n_clusters": 0, "mean_cluster_size": 0.0,
                "rho": 0.0, "deff": 1.0, "n_eff": 0.0}
    m_bar = n / k
    rho = intraclass_correlation(clean)
    deff = design_effect(m_bar, rho)
    return {"n": n, "n_clusters": k, "mean_cluster_size": round(m_bar, 3),
            "rho": round(rho, 4), "deff": round(deff, 3),
            "n_eff": round(max(1.0, n / deff), 2) if n else 0.0}


# ---------------------------------------------------------------------------
# 2. Multiple testing
# ---------------------------------------------------------------------------
def bonferroni_alpha(alpha: float, n_tests: int) -> float:
    """Per-test significance level to hold family-wise error at `alpha`."""
    try:
        n = max(1, int(n_tests))
        a = float(alpha)
    except (TypeError, ValueError, OverflowError):
        return 0.05
    return a / n


def fwer(alpha: float, n_tests: int) -> float:
    """P(at least one false positive) across independent tests at level alpha.

    The headline 0.9991 figure is fwer(0.337, 17), where 0.337 is the *measured
    size* of the old rule "n >= 5 and win_rate <= 0.25" under the bot's own 40%
    base win rate - i.e. P(at most 1 win in 5 | p = 0.40) = 0.3370. A gate firing
    on pure noise somewhere in the 17 buckets was not a risk, it was the expected
    outcome.

    Do not confuse the rule's size with its win-rate threshold: 0.25 is the
    threshold, not a per-test alpha, and fwer(0.25, 17) is 0.9925, not 0.9991.
    """
    try:
        n = max(0, int(n_tests))
        a = min(1.0, max(0.0, float(alpha)))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return 1.0 - (1.0 - a) ** n


def effective_trials(n_trials: int, mean_correlation: float) -> float:
    """N_eff = M / (1 + (M - 1) * rho_bar) - correlated strategy trials.

    Used when the "trials" are not independent because the candidate rules
    overlap (Lopez de Prado's correction for the Deflated Sharpe Ratio).
    """
    try:
        m = max(1, int(n_trials))
        r = max(0.0, min(1.0, float(mean_correlation)))
    except (TypeError, ValueError, OverflowError):
        return 1.0
    return m / (1.0 + (m - 1.0) * r)


def required_n_for_gate(p_null: float = 0.40, p_alt: float = 0.25,
                        alpha: float = 0.05, power: float = 0.80) -> int:
    """Samples needed to distinguish a genuinely-losing bucket from noise.

    One-sided test of a proportion. At the bot's own settings (null 40% win rate,
    alternative 25%, alpha 0.05, power 0.80) this returns 61. Under Bonferroni
    across 17 buckets (alpha = 0.0029) it returns 131. `MIN_SAMPLES_GATE = 5`
    is short of the uncorrected figure by a factor of about 12 and short of the
    corrected figure by about 26.

    The null is 40%, not 50%: the question a gate asks is "is this bucket worse
    than the rest of MY trades", and the bot's realised base rate is 3/7 = 0.43.
    Testing against a 50% coin instead understates the requirement by 2.65x
    (23 uncorrected / 49 corrected) and is the more flattering error, so it is
    the one to guard against.
    """
    try:
        p0, p1 = float(p_null), float(p_alt)
        a, pw = float(alpha), float(power)
    except (TypeError, ValueError, OverflowError):
        return 61
    if not (0 < p1 < p0 < 1):
        return 61
    # NaN comparisons are all False, so an unchecked NaN alpha sails through
    # norm_ppf's clamp and returns a *smaller* n (6, not 61) - the flattering
    # direction. Both of these also reject alpha/power outside (0, 1).
    if not (0 < a < 1) or not (0 < pw < 1):
        return 61
    z_a = norm_ppf(1 - a)
    z_b = norm_ppf(pw)
    num = (z_a * math.sqrt(p0 * (1 - p0)) + z_b * math.sqrt(p1 * (1 - p1))) ** 2
    return int(math.ceil(num / (p0 - p1) ** 2))


# ---------------------------------------------------------------------------
# 3. Collinearity: are two "independent" feature dimensions the same thing?
# ---------------------------------------------------------------------------
def cramers_v(labels_a: Sequence, labels_b: Sequence) -> float:
    """Cramer's V association between two categorical labellings, in [0, 1].

    V = sqrt( chi2 / (n * min(r-1, c-1)) ).

    V = 1.0 means the two dimensions carry identical information. In the bot's
    journal, `direction` and `trend_align` scored exactly 1.0000 - gating both
    was one hypothesis counted twice, and the "at least two buckets" guard could
    not see it because both dimensions genuinely had two populated buckets.
    """
    pairs = [(a, b) for a, b in zip(_seq(labels_a), _seq(labels_b))
             if a is not None and b is not None and a != "na" and b != "na"
             and _hashable(a) and _hashable(b)]
    n = len(pairs)
    if n == 0:
        return 0.0
    # key=repr so a mixed-type labelling (int and str in one dim) cannot raise.
    rows = sorted({p[0] for p in pairs}, key=repr)
    cols = sorted({p[1] for p in pairs}, key=repr)
    r, c = len(rows), len(cols)
    if r < 2 or c < 2:
        # A dimension that is constant carries no information. Report 0 (not
        # collinear) so the *constant* guard - not this one - handles it.
        return 0.0
    ri = {v: i for i, v in enumerate(rows)}
    ci = {v: i for i, v in enumerate(cols)}
    obs = [[0] * c for _ in range(r)]
    for a, b in pairs:
        obs[ri[a]][ci[b]] += 1
    row_tot = [sum(row) for row in obs]
    col_tot = [sum(obs[i][j] for i in range(r)) for j in range(c)]
    chi2 = 0.0
    for i in range(r):
        for j in range(c):
            exp = row_tot[i] * col_tot[j] / n
            if exp > 0:
                chi2 += (obs[i][j] - exp) ** 2 / exp
    denom = n * min(r - 1, c - 1)
    if denom <= 0:
        return 0.0
    return max(0.0, min(1.0, math.sqrt(chi2 / denom)))


def dedupe_dims(rows: Sequence[dict], dims: Sequence[str],
                threshold: float = 0.85) -> tuple:
    """Drop dimensions that duplicate an earlier one.

    `rows` are journal entries carrying a "buckets" dict. Returns
    (kept_dims, dropped) where `dropped` maps a dropped dim to
    (kept_dim, cramers_v) so the reason is reportable rather than silent.

    Order matters: the FIRST dimension in `dims` wins a tie, so pass them in
    order of interpretability. Threshold 0.85 rather than 1.0 because
    near-collinearity is just as capable of double-counting evidence as exact
    collinearity, and small samples rarely produce a clean 1.0.
    """
    rows = [r for r in _seq(rows) if isinstance(r, dict)]
    try:
        threshold = float(threshold)
    except (TypeError, ValueError, OverflowError):
        threshold = 0.85
    if _isnan(threshold):
        threshold = 0.85
    kept: list = []
    dropped: dict = {}
    for dim in _seq(dims):
        labels = [_bucket_of(r).get(dim) for r in rows]
        if len({l for l in labels if l and l != "na"}) < 2:
            continue  # constant or empty - no information to gate on
        collides_with = None
        for k in kept:
            v = cramers_v(labels, [_bucket_of(r).get(k) for r in rows])
            if v >= threshold:
                collides_with = (k, round(v, 4))
                break
        if collides_with:
            dropped[dim] = collides_with
        else:
            kept.append(dim)
    return kept, dropped


# ---------------------------------------------------------------------------
# 4. Shrinkage: pull thin-sample estimates toward the pooled mean
# ---------------------------------------------------------------------------
def eb_shrink(bucket_stats: Sequence[dict]) -> list:
    """DerSimonian-Laird random-effects empirical-Bayes shrinkage.

    Each input dict needs {"key", "mean", "var", "n"} where `var` is the
    within-bucket variance of the outcome. Returns the same dicts with
    "shrunk", "weight" (the shrinkage factor B in [0, 1]), "pooled" and "tau2"
    added.

    B = tau^2 / (tau^2 + v_i) where v_i = var_i / n_i. A bucket with 5 noisy
    trades gets B near 0 and is pulled almost entirely to the pooled mean; a
    bucket with 200 trades keeps its own estimate. This is what stops a 5-trade
    bucket from claiming a -21% expectancy that the pooled data does not
    support.

    DerSimonian & Laird (1986), "Meta-analysis in clinical trials".
    """
    items = []
    for b in _seq(bucket_stats):
        if not isinstance(b, dict):
            continue
        try:
            n = int(b.get("n") or 0)
            m = float(b.get("mean"))
            v = float(b.get("var"))
        except (TypeError, ValueError, OverflowError):
            continue
        if n < 1 or _isnan(m) or _isnan(v) or v < 0:
            continue
        # A zero within-bucket variance (e.g. n=1) would give infinite weight.
        vi = max(v / n, 1e-9)
        items.append({"key": b.get("key"), "mean": m, "var": v, "n": n, "_vi": vi})
    k = len(items)
    if k == 0:
        return []
    if k == 1:
        it = dict(items[0])
        it.update({"shrunk": it["mean"], "weight": 1.0,
                   "pooled": it["mean"], "tau2": 0.0})
        it.pop("_vi", None)
        return [it]

    w = [1.0 / it["_vi"] for it in items]
    sw = sum(w)
    pooled = sum(wi * it["mean"] for wi, it in zip(w, items)) / sw
    q = sum(wi * (it["mean"] - pooled) ** 2 for wi, it in zip(w, items))
    sw2 = sum(wi * wi for wi in w)
    denom = sw - sw2 / sw
    tau2 = max(0.0, (q - (k - 1)) / denom) if denom > 0 else 0.0

    out = []
    for it in items:
        b_factor = tau2 / (tau2 + it["_vi"]) if (tau2 + it["_vi"]) > 0 else 0.0
        row = dict(it)
        row.pop("_vi", None)
        row.update({"shrunk": round(pooled + b_factor * (it["mean"] - pooled), 6),
                    "weight": round(b_factor, 4),
                    "pooled": round(pooled, 6),
                    "tau2": round(tau2, 8)})
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 5. The gate itself - continuous, bounded, and never absorbing
# ---------------------------------------------------------------------------
GATE_FLOOR = 0.25    # a discouraged bucket still trades at quarter size
GATE_CEIL = 1.25     # a favoured bucket gets at most a 25% bonus
GATE_ACTIVATION_N = 30   # effective samples before the gate does anything at all


def gate_decision(shrunk_mean: float, n_eff: float, pooled: float = 0.0,
                  activation_n: float = GATE_ACTIVATION_N) -> dict:
    """Map evidence to a SIZE MULTIPLIER in [GATE_FLOOR, GATE_CEIL].

    The critical property: **the return value is never 0**. A hard gate is an
    absorbing state - it blocks the entries that would generate the samples
    needed to leave the gate, so P(exit) = 0 and the bucket is dead forever.
    That is exactly what happened: `call` was gated, so no calls were opened, so
    `call` never reached n=6, so every subsequent `learn()` re-fired the gate.
    The bot became permanently short-only from 5 clustered observations.

    A multiplier of 0.25 expresses the same belief - "this looks bad" - while
    leaving a path back. At quarter size a discouraged bucket still accumulates
    evidence, and if the evidence was noise the multiplier climbs back to 1.0.

    Below `activation_n` effective samples the multiplier is exactly 1.0.
    Confidence ramps in linearly from there, so the transition is continuous
    rather than a cliff at the sample threshold.
    """
    try:
        m = float(shrunk_mean)
        ne = float(n_eff)
        p = float(pooled)
        act = float(activation_n)
    except (TypeError, ValueError, OverflowError):
        return {"mult": 1.0, "confidence": 0.0, "reason": "unparseable stats"}
    # `pooled` is checked too: min(CEIL, nan) returns CEIL in Python, so an
    # unchecked NaN pooled mean would hand out the maximum *bonus*.
    if _isnan(m) or _isnan(ne) or _isnan(p) or _isnan(act):
        return {"mult": 1.0, "confidence": 0.0, "reason": "nan stats"}
    if not (math.isfinite(m) and math.isfinite(p)):
        return {"mult": 1.0, "confidence": 0.0, "reason": "non-finite stats"}
    # A non-positive activation threshold divides by zero below, and clamping it
    # to epsilon instead would silently mean "full confidence from one trade" -
    # the exact false-certainty this module exists to prevent. Refuse it.
    if not (act > 0) or not math.isfinite(act):
        return {"mult": 1.0, "confidence": 0.0,
                "reason": f"invalid activation threshold {act:g} — "
                          f"trading at full size"}
    if ne < act:
        return {"mult": 1.0, "confidence": 0.0,
                "reason": f"n_eff {ne:.1f} < activation {act:g} — "
                          f"no evidence yet, trading at full size"}

    # Confidence saturates at 4x the activation threshold.
    conf = min(1.0, max(0.0, (ne - act) / (3.0 * act)))
    edge = m - p          # how much worse (or better) than the pooled mean
    # Scale: a 10-percentage-point shortfall in per-trade return is a large
    # effect for options and maps to the full penalty at full confidence.
    raw = 1.0 + edge / 0.10
    raw = max(GATE_FLOOR, min(GATE_CEIL, raw))
    mult = 1.0 + conf * (raw - 1.0)
    mult = max(GATE_FLOOR, min(GATE_CEIL, mult))
    return {"mult": round(mult, 3), "confidence": round(conf, 3),
            "reason": f"shrunk {m:+.3f} vs pooled {p:+.3f} at n_eff {ne:.1f} "
                      f"(confidence {conf:.2f})"}


# ---------------------------------------------------------------------------
# 6. Track-record statistics - is a Sharpe ratio real?
# ---------------------------------------------------------------------------
def sharpe_variance(sr: float, n: int, skew: float = 0.0,
                    kurtosis: float = 3.0) -> float:
    """Var[SR_hat] = (1/(n-1)) * [1 - g3*SR + (g4-1)/4 * SR^2].

    `kurtosis` is RAW kurtosis (Normal = 3.0), not excess. Getting that wrong
    is the classic error and it makes every downstream number too optimistic.
    Mertens (2002); Bailey & Lopez de Prado (2012).
    """
    try:
        s, k = float(sr), float(kurtosis)
        g3 = float(skew)
        nn = int(n)
    except (TypeError, ValueError, OverflowError):
        return float("inf")
    if nn < 2:
        return float("inf")
    bracket = 1.0 - g3 * s + (k - 1.0) / 4.0 * s * s
    bracket = max(bracket, 1e-9)   # negative variance is not a thing
    return bracket / (nn - 1)


def psr(sr: float, n: int, sr_benchmark: float = 0.0,
        skew: float = 0.0, kurtosis: float = 3.0) -> float:
    """Probabilistic Sharpe Ratio: P(true SR > benchmark) given the sample."""
    v = sharpe_variance(sr, n, skew, kurtosis)
    if not math.isfinite(v) or v <= 0:
        return 0.5
    try:
        return norm_cdf((float(sr) - float(sr_benchmark)) / math.sqrt(v))
    except (TypeError, ValueError, OverflowError):
        return 0.5


def min_track_record_length(sr: float, sr_benchmark: float = 0.0,
                            skew: float = 0.0, kurtosis: float = 3.0,
                            confidence: float = 0.95) -> float:
    """MinTRL: observations needed before PSR clears `confidence`.

    MinTRL = 1 + [1 - g3*SR + (g4-1)/4 * SR^2] * (Z_c / (SR - SR*))^2

    Applied to the bot's own sleeve statistics this returns roughly **1,400
    trades** before one sleeve weight can honestly be moved off 1.0.
    `MIN_SAMPLES_SLEEVE = 4` is short by a factor of about 350.
    """
    try:
        s, sb = float(sr), float(sr_benchmark)
        g3, k = float(skew), float(kurtosis)
        c = float(confidence)
    except (TypeError, ValueError, OverflowError):
        return float("inf")
    # `s <= sb` is False when either is NaN, and max(nan, 1e-9) keeps the NaN,
    # so without this the function returns NaN instead of "unprovable".
    if _isnan(s) or _isnan(sb) or _isnan(g3) or _isnan(k) or _isnan(c):
        return float("inf")
    if s <= sb:
        return float("inf")     # no amount of data proves a worse thing better
    bracket = max(1.0 - g3 * s + (k - 1.0) / 4.0 * s * s, 1e-9)
    z = norm_ppf(c)
    return 1.0 + bracket * (z / (s - sb)) ** 2


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """SR_0: the Sharpe you expect from the BEST of N random strategies.

    SR_0 = sqrt(Var[SR]) * [ (1-g) * Z(1 - 1/N) + g * Z(1 - 1/(N*e)) ], g = Euler.

    This is the benchmark a "discovery" has to beat. Trying 20 variants and
    keeping the best one means the winner needs a Sharpe well above zero just to
    be indistinguishable from luck.
    """
    try:
        n = max(1, int(n_trials))
        v = max(0.0, float(sr_variance))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if n == 1:
        return 0.0
    return math.sqrt(v) * ((1 - EULER_GAMMA) * norm_ppf(1 - 1.0 / n) +
                           EULER_GAMMA * norm_ppf(1 - 1.0 / (n * math.e)))


def deflated_sharpe(sr: float, n: int, n_trials: int, sr_variance_across_trials: float,
                    skew: float = 0.0, kurtosis: float = 3.0) -> dict:
    """DSR = PSR(SR_0). A discovery requires DSR > 0.95.

    Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio".
    """
    sr0 = expected_max_sharpe(n_trials, sr_variance_across_trials)
    val = psr(sr, n, sr_benchmark=sr0, skew=skew, kurtosis=kurtosis)
    return {"sr0": round(sr0, 4), "dsr": round(val, 4), "is_discovery": bool(val > 0.95)}


# ---------------------------------------------------------------------------
# 7. Sizing and ruin arithmetic
# ---------------------------------------------------------------------------
def kelly_fraction(mu: float, sigma: float) -> float:
    """f* ~ mu / sigma^2 (continuous approximation). Negative edge -> 0."""
    try:
        m, s = float(mu), float(sigma)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if s <= 0 or _isnan(m) or _isnan(s):
        return 0.0
    return max(0.0, m / (s * s))


def kelly_growth_penalty(c: float) -> float:
    """g(c*f*) / g* = c * (2 - c).

    Half-Kelly keeps 75% of the growth rate for a quarter of the variance, which
    is why practitioners use it. c = 2 gives zero growth; c > 2 is negative
    growth even with a positive edge - overbetting a winning system loses.
    """
    try:
        cc = float(c)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if _isnan(cc):
        return 0.0      # never hand a NaN back into a sizing calculation
    return cc * (2.0 - cc)


def drawdown_probability(x: float, c: float) -> float:
    """P(equity ever falls to fraction x of its start) = x^(2/c - 1) at c-Kelly.

    At full Kelly (c=1) the probability of ever halving is 0.5. At c = 2 it is
    1.0 - certain ruin in the limit. This is the formula that makes "size up
    when you are winning" quantifiably dangerous.
    """
    try:
        xx, cc = float(x), float(c)
    except (TypeError, ValueError, OverflowError):
        return 1.0
    if not (0 < xx < 1) or cc <= 0:
        return 1.0
    if cc >= 2:
        return 1.0
    return min(1.0, xx ** (2.0 / cc - 1.0))


def time_to_multiple(multiple: float, sharpe: float) -> float:
    """Expected years to grow by `multiple` at optimal sizing: 2*ln(M)/SR^2.

    At full Kelly a Sharpe of SR gives log wealth drift SR^2/2 and log vol SR,
    so E[time to M] = ln(M)/(SR^2/2). 10x at Sharpe 1.0 takes ~4.6 years, and
    10x inside one year needs Sharpe 2.146 (= sqrt(2*ln 10)) on that basis.

    The softer "coin-flip" reading - a 50% chance of *touching* 10x at some point
    inside the year - is the first-passage probability of the same process and
    needs Sharpe 1.802, not 1.72: solve
        Phi((nu - b)/sigma) + exp(2*nu*b/sigma^2) * Phi((-b - nu)/sigma) = 0.5
    with nu = SR^2/2, sigma = SR, b = ln 10. Quote 2.146 or 1.802 and say which;
    1.72 is neither. For reference, Renaissance Medallion ran about 2.5 gross.
    """
    try:
        m, s = float(multiple), float(sharpe)
    except (TypeError, ValueError, OverflowError):
        return float("inf")
    if _isnan(m) or _isnan(s):
        return float("inf")     # NaN slips past both guards below
    if m <= 1 or s <= 0:
        return float("inf")
    return 2.0 * math.log(m) / (s * s)


def prob_reach_multiple(target: float, ruin: float, edge_per_trade: float = 0.0,
                        vol_per_trade: float = 1.0) -> float:
    """P(reach `target` x start before falling to `ruin` x start).

    Classic gambler's-ruin on log wealth with drift. `edge_per_trade` is the
    ARITHMETIC per-trade expected return and `vol_per_trade` its standard
    deviation, so by Ito the drift of LOG wealth is `mu - sigma^2/2` and

        lambda = 2*mu/sigma^2 - 1

    A fair game (mu = 0) therefore has lambda = -1 for any volatility, and the
    formula collapses to the exact martingale answer: from $10k, reaching $100k
    before $1k is (10 - 1)/(100 - 1) = 9.09%. That number is the honest benchmark
    for any "10x challenge" - it is what a coin flip gives you for free, and a
    strategy with negative expectancy scores strictly worse.

    The `-1` matters. Reading `edge_per_trade` as an already-logarithmic drift
    makes mu = 0 mean a wealth process that drifts *up* at sigma^2/2, and the
    same call then returns 50% - too generous by 5.5x, in a number quoted as a
    benchmark. The Ito term is the whole difference between "a coin flip 10x's
    one account in eleven" and "a coin flip 10x's every other account".
    """
    try:
        t, r = float(target), float(ruin)
        mu, sg = float(edge_per_trade), float(vol_per_trade)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if _isnan(t) or _isnan(r) or _isnan(mu) or _isnan(sg):
        return 0.0
    # An infinite edge is refused for the same reason an infinite volatility is:
    # the limit is 1.0 ("a guaranteed 10x"), which is the flattering answer to
    # give to garbage.
    if not (0 < r < 1 < t) or not (0 < sg < float("inf")) or not math.isfinite(mu):
        return 0.0
    a = math.log(1.0 / r)      # distance down to ruin, in logs
    b = math.log(t)            # distance up to target
    if not (math.isfinite(a) and math.isfinite(b)):
        return 0.0
    lam = 2.0 * mu / (sg * sg) - 1.0
    if abs(lam) < 1e-12:
        return a / (a + b)     # driftless IN LOGS: distance-proportional
    try:
        val = (1 - math.exp(-lam * a)) / (1 - math.exp(-lam * (a + b)))
    except (OverflowError, ZeroDivisionError):
        return 1.0 if lam > 0 else 0.0
    if _isnan(val):
        return 0.0
    return max(0.0, min(1.0, val))


# ---------------------------------------------------------------------------
def _isnan(x) -> bool:
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError, OverflowError):
        return True


def _seq(x) -> list:
    """Coerce anything into a list, without ever raising.

    Every function in this module is called from inside a trading loop, where an
    unhandled TypeError is a financial bug, not a stack trace. A scalar, a None
    or a bare string where a sequence was expected is not a sequence of anything
    - it is missing data - so it degrades to the empty list and the caller gets
    the module's documented empty-input answer instead of a crash.
    """
    if x is None or isinstance(x, (str, bytes, bytearray, int, float, bool)):
        return []
    if isinstance(x, dict):
        return list(x.values())
    try:
        return list(x)
    except TypeError:
        return []


def _bucket_of(row) -> dict:
    b = row.get("buckets") if isinstance(row, dict) else None
    return b if isinstance(b, dict) else {}


def _hashable(x) -> bool:
    try:
        hash(x)
        return True
    except TypeError:
        return False
