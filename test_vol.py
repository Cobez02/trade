"""
Volatility module test harness. No network, no broker. Run: python3 test_vol.py

Every test drives a data generator whose TRUE volatility is known by
construction, so the assertions are about measured bias, not about whether the
arithmetic runs. Three of these sections are documentation as much as
verification:

  * section 2 measures the gap-blindness of Parkinson/Garman-Klass and prints
    the vol-point shortfall, because that shortfall is exactly the fake
    "options are always rich" signal the module exists to prevent;
  * section 4 asserts that every malformed input returns None rather than NaN,
    because a NaN reaching a position sizer is a live financial bug;
  * section 8 reproduces the IEX microstructure-noise arithmetic that rules out
    1-minute sampling on the free data feed.
"""
from __future__ import annotations
import sys
import warnings

import numpy as np
import pandas as pd

import vol as V

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")


def banner(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


# --------------------------------------------------------------------------
# generators — the true volatility is an input, so bias is measurable
# --------------------------------------------------------------------------
def ohlc_from_variance(daily_var, gap_share=0.0, steps=390, seed=11, s0=100.0):
    """Daily OHLC bars from a known DAILY VARIANCE path.

    gap_share = fraction of each day's TOTAL variance delivered as an
    overnight jump between yesterday's close and today's open; the remaining
    (1 - gap_share) is a `steps`-point GBM path inside the session. So true
    full-day vol is sqrt(252*v) regardless of gap_share, while true
    OPEN-TO-CLOSE vol is that times sqrt(1-gap_share) — which is the ceiling
    on what a gap-blind estimator can possibly report.

    steps=390 is one price per minute, the realistic count for a liquid name.
    The high and low of a 390-point sample understate the continuous extremes,
    which is a real and documented downward bias in every range estimator
    (note 08 §11.3) — section 1 measures it and then shows it away.
    """
    rng = np.random.default_rng(seed)
    path_var = np.asarray(daily_var, dtype=float)
    rows, prev = [], s0
    for dv in path_var:
        o = prev * np.exp(rng.normal(0.0, np.sqrt(gap_share * dv)))
        p = o * np.exp(np.cumsum(rng.normal(0.0, np.sqrt((1 - gap_share) * dv / steps), steps)))
        rows.append((o, max(p.max(), o), min(p.min(), o), p[-1]))
        prev = p[-1]
    idx = pd.date_range("2015-01-01", periods=len(rows), freq="B")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def gbm_ohlc(n_days=1300, sigma=0.20, gap_share=0.0, steps=390, seed=11):
    """Constant-volatility special case of ohlc_from_variance."""
    return ohlc_from_variance(np.full(n_days, sigma ** 2 / V.TRADING_DAYS),
                              gap_share=gap_share, steps=steps, seed=seed)


def har_variance_path(n=2600, target_vol=0.19, shock_sd=0.35, seed=3):
    """log RV_t = c + 0.42*d + 0.30*w + 0.18*m + eps, in the SAME
    non-overlapping lag geometry vol.har_features uses (d = t-1,
    w = mean t-2..t-5, m = mean t-6..t-22). Slopes sum to 0.90 so
    c = 0.10*log(target daily variance) pins the unconditional level.
    Returns DAILY VARIANCE."""
    rng = np.random.default_rng(seed)
    base = np.log(target_vol ** 2 / V.TRADING_DAYS)
    lrv = np.full(n, base)
    for t in range(22, n):
        lrv[t] = (0.10 * base + 0.42 * lrv[t - 1] + 0.30 * lrv[t - 5:t - 1].mean()
                  + 0.18 * lrv[t - 22:t - 5].mean() + rng.normal(0.0, shock_sd))
    return pd.Series(np.exp(lrv))


def avg_over_slices(fn, df, window, stride=None):
    """Mean of an estimator over NON-OVERLAPPING slices of df.

    The public API returns one scalar (the most recent window), and a single
    21-day window has a standard error near sigma/sqrt(2n) ~ 3 vol points at a
    20 vol level. Averaging k independent slices cuts that by sqrt(k) and lets
    the test assert on bias rather than on one lucky draw.
    """
    stride = stride or (window + 1)
    vals = [fn(df.iloc[i:i + window + 1], window)
            for i in range(0, len(df) - window - 1, stride)]
    vals = [v for v in vals if v is not None]
    return float(np.mean(vals)), len(vals)


ESTIMATORS = [("close_to_close", V.close_to_close), ("parkinson", V.parkinson),
              ("garman_klass", V.garman_klass), ("rogers_satchell", V.rogers_satchell),
              ("yang_zhang", V.yang_zhang)]


# --------------------------------------------------------------------------
banner("1. Range estimators recover a known sigma (GBM, no overnight gap)")
# With gap_share=0 there is nothing for a gap-aware estimator to add, so ALL
# five should land on sigma. Statistic: the mean of ~20 INDEPENDENT 63-day
# windows, which cuts the ~1.8 vol point standard error of a single 63-day
# close-to-close estimate to about 0.4 points.
#
# TOLERANCES, and why they differ:
#   close_to_close  0.5 pts — it is unbiased, so only sampling error remains.
#   range estimators 1.5 pts — plus a genuine DOWNWARD bias, because the high
#     and low of 390 discrete observations understate the continuous supremum
#     and infimum the estimators' scaling constants assume. Note 08 §11.3 puts
#     this at 0.7-0.9 points at a 20 vol level; measured here it is 0.9-1.3.
#     The follow-up test at 4,000 ticks/session shows it away, which is the
#     proof that the bias is the sampling grid and not the implementation.
TRUE_SIGMA = 0.20
TOL_C2C, TOL_RANGE = 0.005, 0.015
df_nogap = gbm_ohlc(n_days=1300, sigma=TRUE_SIGMA, gap_share=0.0, seed=11)
print(f"  1300 daily bars, true annualized vol {TRUE_SIGMA:.2%}, 390 ticks/session")
for name, fn in ESTIMATORS:
    tol = TOL_C2C if name == "close_to_close" else TOL_RANGE
    got, k = avg_over_slices(fn, df_nogap, 63)
    check(f"{name} recovers {TRUE_SIGMA:.0%} within {100*tol:.1f} vol pts",
          abs(got - TRUE_SIGMA) <= tol,
          f"mean of {k} windows = {got:.4f} ({100*(got-TRUE_SIGMA):+.2f} pts)")

# Same true vol, 4000 ticks/session instead of 390. If the range estimators are
# implemented correctly, their bias is a property of the observation grid and
# must shrink toward the close-to-close sampling error as the grid gets finer.
df_fine = gbm_ohlc(n_days=1300, sigma=TRUE_SIGMA, gap_share=0.0, steps=4000, seed=13)
print("  ...and again with 4000 ticks/session (finer high/low sampling):")
for name, fn in ESTIMATORS:
    got, _ = avg_over_slices(fn, df_fine, 63)
    coarse, _ = avg_over_slices(fn, df_nogap, 63)
    print(f"    {name:<16} {got:.4f} ({100*(got-TRUE_SIGMA):+.2f} pts), "
          f"was {100*(coarse-TRUE_SIGMA):+.2f} pts at 390 ticks")
    check(f"{name} is within 0.6 vol pts once the grid is fine",
          abs(got - TRUE_SIGMA) <= 0.006,
          f"{got:.4f} ({100*(got-TRUE_SIGMA):+.2f} pts)")

check("realized_vol() defaults to yang_zhang",
      V.realized_vol(df_nogap, 21) == V.yang_zhang(df_nogap, 21),
      f"{V.realized_vol(df_nogap, 21):.4f}")
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    V.realized_vol(df_nogap, 21, method="parkinson")
    check("realized_vol(method='parkinson') warns about gap-blindness",
          any("GAP-BLIND" in str(w.message) for w in caught))
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    got = V.realized_vol(df_nogap, 21, method="not_an_estimator")
    check("unknown method -> None + warning, never raises",
          got is None and len(caught) >= 1)


# --------------------------------------------------------------------------
banner("2. GAP-BLINDNESS — the reason yang_zhang is the default")
# Same true full-day vol as section 1, but now 25% of each day's variance
# arrives as an overnight jump. Nothing about the full-day risk of an
# overnight options position has changed. Parkinson/GK/RS cannot see the jump,
# so their ceiling is sigma*sqrt(1-0.25) = 0.866*sigma = a -2.68 vol point
# shortfall BEFORE the discrete-sampling bias measured in section 1.
GAP_SHARE = 0.25
df_gap = gbm_ohlc(n_days=1300, sigma=TRUE_SIGMA, gap_share=GAP_SHARE, seed=11)
ceiling = TRUE_SIGMA * np.sqrt(1 - GAP_SHARE)
print(f"  true full-day vol {TRUE_SIGMA:.2%}; {GAP_SHARE:.0%} of daily variance "
      f"is overnight")
print(f"  => an open-to-close estimator can at best report {ceiling:.4f} "
      f"({100*(ceiling-TRUE_SIGMA):+.2f} pts)")
measured = {}
for name, fn in ESTIMATORS:
    got, _ = avg_over_slices(fn, df_gap, 63)
    measured[name] = got
    print(f"    {name:<16} {got:.4f}   {100*(got-TRUE_SIGMA):+6.2f} vol points")

for name in ("parkinson", "garman_klass"):
    shortfall = 100 * (TRUE_SIGMA - measured[name])
    check(f"{name} materially UNDER-reports full-day vol",
          shortfall >= 2.0,
          f"reports {measured[name]:.4f} vs true {TRUE_SIGMA:.4f} — a "
          f"{shortfall:.2f} vol point shortfall, i.e. a phantom {shortfall:.2f} "
          f"point variance risk premium handed to any IV comparison")

# YZ is held to the SAME tolerance as section 1, where there was no gap at all:
# what is left is the discrete-sampling bias it inherits through its
# Rogers-Satchell component, not gap blindness. The gap costs it nothing.
yz_err = 100 * (measured["yang_zhang"] - TRUE_SIGMA)
check(f"yang_zhang stays within {100*TOL_RANGE:.1f} vol pts of truth despite the gap",
      abs(yz_err) <= 100 * TOL_RANGE, f"{measured['yang_zhang']:.4f} ({yz_err:+.2f} pts)")
check("yang_zhang is no worse WITH a 25% gap than with none",
      abs(measured["yang_zhang"] - TRUE_SIGMA)
      <= abs(avg_over_slices(V.yang_zhang, df_nogap, 63)[0] - TRUE_SIGMA) + 0.003,
      f"gap {measured['yang_zhang']:.4f} vs no-gap "
      f"{avg_over_slices(V.yang_zhang, df_nogap, 63)[0]:.4f}")

for name in ("parkinson", "garman_klass"):
    edge = 100 * (measured["yang_zhang"] - measured[name])
    check(f"yang_zhang - {name} gap = the fake premium ({edge:.2f} pts)",
          edge >= 1.5,
          f"selling premium on this alone would be selling a measurement error")

check("all three gap-blind estimators are flagged in GAP_BLIND_METHODS",
      V.GAP_BLIND_METHODS == {"parkinson", "garman_klass", "rogers_satchell"})
check("the module docstring carries the warning and its magnitude",
      "GAP-BLIND" in V.__doc__ and "-2.45" in V.__doc__)
for name in ("parkinson", "garman_klass"):
    doc = getattr(V, name).__doc__
    check(f"{name}.__doc__ warns with a vol-point magnitude",
          "GAP-BLIND" in doc and "vol\n    points" in doc.replace("vol points", "vol\n    points"))


# --------------------------------------------------------------------------
banner("3. A gap-blind estimator invents a variance risk premium")
# The end-to-end consequence, stated in the units the bot trades. IV is set
# exactly equal to true realized vol, so the honest VRP is zero.
iv_fair = TRUE_SIGMA
for name in ("parkinson", "garman_klass", "yang_zhang"):
    fake = V.vrp_vol_points(iv_fair, measured[name])
    print(f"    vrp_vol_points(IV={iv_fair:.2f}, {name}) = {100*fake:+.2f} vol points")
check("gap-blind estimators show a premium where there is none",
      V.vrp_vol_points(iv_fair, measured["parkinson"]) > 0.02
      and V.vrp_vol_points(iv_fair, measured["garman_klass"]) > 0.02)
check("yang_zhang reports ~zero premium on a fairly-priced option",
      abs(V.vrp_vol_points(iv_fair, measured["yang_zhang"])) < 0.01)


# --------------------------------------------------------------------------
banner("4. Bad input returns None — never NaN, never an exception")
good = gbm_ohlc(n_days=200, sigma=0.20, seed=5)
BAD = {
    "insufficient rows (10 bars, window 21)": good.iloc[:10],
    "exactly window rows (need window+1)": good.iloc[:21],
    "missing 'open' column": good.drop(columns=["open"]),
    "missing 'close' column": good.drop(columns=["close"]),
    "high below low": good.assign(high=good["low"] * 0.5),
    "one zero price": good.assign(close=good["close"].mask(
        good.index == good.index[100], 0.0)),
    "one negative price": good.assign(low=good["low"].mask(
        good.index == good.index[50], -1.0)),
    "all NaN": good * np.nan,
    "all-NaN single column": good.assign(high=np.nan),
    "empty frame": good.iloc[:0],
    "non-numeric strings": good.astype(str).assign(close="oops"),
    "inf price": good.assign(open=good["open"].mask(
        good.index == good.index[7], np.inf)),
    "not a DataFrame": [1, 2, 3],
    "None": None,
}
for label, bad in BAD.items():
    results = {}
    for name, fn in ESTIMATORS + [("realized_vol", V.realized_vol)]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            results[name] = fn(bad, 21)
    all_none = all(v is None for v in results.values())
    no_nan = not any(isinstance(v, float) and v != v for v in results.values())
    check(f"{label} -> None from every estimator", all_none and no_nan,
          "" if all_none else f"got {results}")

for label, bad in list(BAD.items())[:6]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cone, pct = V.vol_cone(bad), V.cone_percentile(bad, 21, 0.20)
        lo, hi = V.cone_ci(bad, 21, 90)
    # .get(), not [], is the contract: a window with too little history is
    # OMITTED from the cone rather than filled with a fabricated shape.
    check(f"{label} -> no 21d cone entry / None percentile / (None, None) CI",
          cone.get(21) is None and pct is None and (lo, hi) == (None, None),
          f"cone windows present: {sorted(cone)}")

for w in (0, 1, -5, 2.5, "21", None, True):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        check(f"window={w!r} -> None", V.yang_zhang(good, w) is None)

check("a flat (zero-range) series yields None, not a zero divisor",
      V.yang_zhang(pd.DataFrame({c: [100.0] * 60 for c in
                                 ("open", "high", "low", "close")}), 21) is None)


# --------------------------------------------------------------------------
banner("5. HAR-RV recovers a known HAR process")
rv = har_variance_path(n=2600, target_vol=0.19, shock_sd=0.35, seed=3)
TRUE_SLOPES = (0.42, 0.30, 0.18)
model = V.har_fit(rv)
check("har_fit returns a model on 2600 days", isinstance(model, dict),
      "" if model else "got None")
if model:
    b = model["beta"]
    print(f"  beta = [{', '.join(f'{x:.4f}' for x in b)}]   n={model['n']}  "
          f"r2={model['r2']:.4f}  resid_std={model['resid_std']:.4f}")
    check("returns the documented keys",
          set(model) >= {"beta", "n", "r2", "resid_std"} and len(b) == 4)
    # Individual HAR slopes are poorly identified (the regressors are lagged
    # averages of one another); the SUM is what the literature reports and what
    # the forecast depends on. The reference run recovered 0.887 vs 0.900.
    check("sum of slopes recovers the DGP's 0.900 within 0.06",
          abs(sum(b[1:]) - 0.90) <= 0.06, f"{sum(b[1:]):.4f}")
    for lab, got, want in zip("dwm", b[1:], TRUE_SLOPES):
        check(f"slope {lab} within 0.08 of {want:.2f}", abs(got - want) <= 0.08,
              f"{got:.4f}")
    check("in-sample R2 is in Corsi's plausible band (0.35-0.75)",
          0.35 <= model["r2"] <= 0.75, f"{model['r2']:.4f}")

    uncond = float(np.sqrt(rv.mean() * V.TRADING_DAYS))
    for h in (1, 5, 21, 63):
        f = V.har_forecast(model, rv, horizon=h)
        ok = f is not None and np.isfinite(f) and f > 0.0
        check(f"har_forecast(horizon={h}) is finite and positive", ok,
              f"{f:.4f} annualized vol" if ok else f"got {f!r}")
    f1, f63 = V.har_forecast(model, rv, 1), V.har_forecast(model, rv, 63)
    check("long-horizon forecast mean-reverts toward the unconditional level",
          abs(f63 - uncond) < abs(f1 - uncond),
          f"h=1 {f1:.4f}, h=63 {f63:.4f}, unconditional {uncond:.4f}")
    check("forecasts land in a sane vol range (5%-100%)",
          all(0.05 < V.har_forecast(model, rv, h) < 1.0 for h in (1, 5, 21, 63)))

    # The Jensen correction: exp(mu + s^2/2) > exp(mu). Dropping it is the
    # commonest retail HAR bug and it biases every forecast LOW.
    flat = dict(model, resid_std=0.0)
    check("Jensen correction raises the forecast above exp(mu)",
          V.har_forecast(model, rv, 1) > V.har_forecast(flat, rv, 1),
          f"with {V.har_forecast(model, rv, 1):.4f} vs without "
          f"{V.har_forecast(flat, rv, 1):.4f} — omitting it under-forecasts by "
          f"{100*(V.har_forecast(model, rv, 1) - V.har_forecast(flat, rv, 1)):.2f} pts")

check("har_features returns d/w/m spanning 1/5/22 days",
      list(V.har_features(rv).columns) == ["d", "w", "m"]
      and V.har_features(rv).dropna().index[0] >= 21)
check("har_fit on <60 usable observations -> None",
      V.har_fit(rv.iloc[:70]) is None, f"n available ~ {70-22}")
check("har_fit on a constant series (rank-deficient) -> None",
      V.har_fit(pd.Series([1.6e-4] * 400)) is None)
check("har_fit on all-NaN -> None", V.har_fit(pd.Series([np.nan] * 400)) is None)
check("har_fit on garbage -> None", V.har_fit("not a series") is None)
check("har_forecast with a bad model -> None",
      V.har_forecast({"beta": [1, 2]}, rv) is None
      and V.har_forecast(None, rv) is None
      and V.har_forecast(model, rv.iloc[:5]) is None
      and V.har_forecast(model, rv, horizon=0) is None)
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    V.har_fit(pd.Series(np.linspace(0.03, 0.05, 400)))     # annualized-vol units
    check("passing the wrong units warns loudly",
          any("DAILY VARIANCE" in str(w.message) for w in caught))


# --------------------------------------------------------------------------
banner("6. Volatility cone")
# The research note's exact reference setup (§11.1): 2600 days of the HAR
# variance path from section 5, 15% of each day's variance overnight, 390
# ticks/session. A cone is only meaningful on a series that HAS vol-of-vol, so
# this section deliberately does not reuse the constant-sigma GBM.
df_cone = ohlc_from_variance(rv, gap_share=0.15, steps=390, seed=7)
cone = V.vol_cone(df_cone)
ref_neff = {10: 259, 21: 122, 42: 60, 63: 40, 126: 19, 252: 9}
got_neff = {w: r["n_eff"] for w, r in
            V.vol_cone(df_cone, windows=tuple(ref_neff)).items()}
check("n_eff reproduces the note's §11.2 table exactly", got_neff == ref_neff,
      f"{got_neff} — 2348 nominal 252-day observations are 9 independent ones")
print(f"  {'win':>4} {'p10':>8} {'p25':>8} {'p50':>8} {'p75':>8} {'p90':>8} "
      f"{'n_obs':>7} {'n_eff':>6}")
for w, rec in sorted(cone.items()):
    print(f"  {w:>4} " + " ".join(f"{rec[f'p{p}']:>8.4f}" for p in (10, 25, 50, 75, 90))
          + f" {rec['n_obs']:>7} {rec['n_eff']:>6}")
check("every requested window is present", set(cone) == {5, 10, 21, 42, 63})
for w, rec in cone.items():
    ps = [rec[f"p{p}"] for p in (10, 25, 50, 75, 90)]
    check(f"window {w}: percentiles are monotone non-decreasing",
          all(a <= b for a, b in zip(ps, ps[1:])) and rec["min"] <= ps[0]
          and ps[-1] <= rec["max"])
    check(f"window {w}: n_eff == n_obs // window (overlap discounted)",
          rec["n_eff"] == max(1, rec["n_obs"] // w),
          f"{rec['n_obs']} nominal -> {rec['n_eff']} independent")
check("longer windows compress the cone (vol-of-vol falls with horizon)",
      cone[63]["p90"] - cone[63]["p10"] < cone[5]["p90"] - cone[5]["p10"],
      f"5d spread {100*(cone[5]['p90']-cone[5]['p10']):.2f} pts vs 63d "
      f"{100*(cone[63]['p90']-cone[63]['p10']):.2f} pts")

for w in (10, 21, 63):
    for p in (10, 50, 90):
        got = V.cone_percentile(df_cone, w, cone[w][f"p{p}"])
        check(f"cone_percentile round-trips vol_cone p{p} at window {w}",
              got is not None and abs(got - p) <= 2.0, f"got {got:.2f}")
check("cone_percentile is monotone in its argument",
      V.cone_percentile(df_cone, 21, 0.12) < V.cone_percentile(df_cone, 21, 0.20)
      < V.cone_percentile(df_cone, 21, 0.35))
check("an absurdly high IV sits at the top of the cone",
      V.cone_percentile(df_cone, 21, 5.0) == 100.0)
check("cone_percentile rejects a non-positive / non-finite value",
      V.cone_percentile(df_cone, 21, 0.0) is None
      and V.cone_percentile(df_cone, 21, np.nan) is None
      and V.cone_percentile(df_cone, 21, "0.2") is None)

# ★ The point of cone_ci: the band is the same size as the edge being hunted.
print(f"  {'win':>4} {'p90':>8} {'95% CI':>18} {'width':>8} {'p90-p10':>9} "
      f"{'CI/spread':>10} {'n_eff':>6}")
ci_w, ci_ratio = {}, {}
for w in sorted(cone):
    lo_w, hi_w = V.cone_ci(df_cone, w, 90, n_boot=500, seed=0)
    spread = cone[w]["p90"] - cone[w]["p10"]
    ci_w[w], ci_ratio[w] = 100 * (hi_w - lo_w), (hi_w - lo_w) / spread
    print(f"  {w:>4} {cone[w]['p90']:>8.4f} [{lo_w:.4f}, {hi_w:.4f}] "
          f"{ci_w[w]:>7.2f}p {100*spread:>8.2f}p {ci_ratio[w]:>10.3f} "
          f"{cone[w]['n_eff']:>6}")
    check(f"window {w}: cone_ci brackets its own point estimate",
          lo_w < cone[w]["p90"] < hi_w)
    check(f"window {w}: p90 CI is at least 1 vol point wide", ci_w[w] >= 1.0,
          f"{ci_w[w]:.2f} pts — an edge smaller than this is not measurable here")

check("21-day p90 CI reproduces the note's 2.18 vol points (+/-0.4)",
      abs(ci_w[21] - 2.18) <= 0.4,
      f"{ci_w[21]:.2f} pts wide on {cone[21]['n_obs']} nominal observations "
      f"(n_eff {cone[21]['n_eff']}) — the signal sits inside its own error bar")
check("the CI is a bigger fraction of the cone as n_eff falls",
      ci_ratio[63] > ci_ratio[5],
      f"63d CI is {100*ci_ratio[63]:.1f}% of the cone's p10-p90 spread vs "
      f"{100*ci_ratio[5]:.1f}% at 5d")
check("cone_ci is deterministic given a seed",
      V.cone_ci(df_cone, 21, 90, seed=0) == V.cone_ci(df_cone, 21, 90, seed=0))
check("a different seed moves the interval a little, not a lot",
      abs(V.cone_ci(df_cone, 21, 90, seed=7)[0]
          - V.cone_ci(df_cone, 21, 90, seed=0)[0]) < 0.01)
check("cone_ci on too little history -> (None, None)",
      V.cone_ci(df_cone.iloc[:40], 21, 90) == (None, None))
check("cone_ci rejects an out-of-range percentile / bad n_boot",
      V.cone_ci(df_cone, 21, 900) == (None, None)
      and V.cone_ci(df_cone, 21, -1) == (None, None)
      and V.cone_ci(df_cone, 21, 90, n_boot=1) == (None, None))
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    V.vol_cone(df_cone, windows=(21,), percentiles=(0.10, 0.90))
    check("percentiles passed as fractions warn (0-100, not 0-1)",
          any("0-100" in str(w.message) for w in caught))


# --------------------------------------------------------------------------
banner("7. Variance risk premium")
check("vrp is in VARIANCE units: 0.20^2 - 0.16^2 = 0.0144",
      abs(V.vrp(0.20, 0.16) - 0.0144) < 1e-12, f"{V.vrp(0.20, 0.16):.6f}")
check("vrp_vol_points is the vol difference as a decimal (0.04 = 4 pts)",
      abs(V.vrp_vol_points(0.20, 0.16) - 0.04) < 1e-12)
check("positive VRP means the option is rich", V.vrp(0.30, 0.20) > 0)
check("negative VRP means the option is cheap", V.vrp(0.15, 0.20) < 0)
check("VRP is zero when IV equals the forecast",
      V.vrp(0.22, 0.22) == 0.0 and V.vrp_vol_points(0.22, 0.22) == 0.0)
check("variance and vol conventions genuinely differ (Jensen)",
      abs(V.vrp(0.40, 0.20) - V.vrp_vol_points(0.40, 0.20)) > 0.05,
      f"variance {V.vrp(0.40,0.20):.4f} vs vol {V.vrp_vol_points(0.40,0.20):.4f}")
for bad in (None, np.nan, np.inf, -0.1, "0.2"):
    check(f"vrp with {bad!r} -> None (a None forecast must not become NaN)",
          V.vrp(0.20, bad) is None and V.vrp(bad, 0.20) is None
          and V.vrp_vol_points(0.20, bad) is None)
check("a None from har_forecast flows through as None, not a crash",
      V.vrp(0.25, V.har_forecast(None, rv)) is None)


# --------------------------------------------------------------------------
banner("8. IEX microstructure-noise guard")
# Parameters, stated explicitly (note 08 §C.3):
#   noise_std = 1e-4  == 1 basis point of log price, representative of a
#     SINGLE-VENUE quote for a liquid name. Consolidated NBBO noise is smaller;
#     IEX's 350us speed bump makes its quote staler, so 1bp is not pessimistic.
#   M = 390 bars/day at 1-minute sampling, 78 at 5-minute, 26 at 15-minute.
#   Bias in daily variance = 2*M*noise_std^2; reported as sqrt(252 * bias).
NOISE_STD = 1e-4
EXPECT = {390: 0.0443, 78: 0.0198, 26: 0.0114}     # 1-min, 5-min, 15-min
for m, want in EXPECT.items():
    got = V.iex_noise_bias(m, NOISE_STD)
    check(f"M={m} bars/day, 1bp noise -> +{100*want:.1f} vol points",
          abs(got - want) <= 0.0005,
          f"{got:.4f} ({100*got:+.2f} pts); 2*{m}*1e-8 = "
          f"{2*m*NOISE_STD**2:.3g} daily variance")
check("the 1-minute penalty exceeds the 5-minute one by >2 vol points",
      100 * (V.iex_noise_bias(390, NOISE_STD) - V.iex_noise_bias(78, NOISE_STD)) > 2.0,
      f"{100*(V.iex_noise_bias(390, NOISE_STD)-V.iex_noise_bias(78, NOISE_STD)):.2f} "
      f"pts — larger than any edge documented in the research")
check("bias grows linearly in variance, i.e. as sqrt(M) in vol",
      abs(V.iex_noise_bias(4 * 78, NOISE_STD) / V.iex_noise_bias(78, NOISE_STD) - 2.0) < 1e-9)
check("zero noise -> zero bias", V.iex_noise_bias(390, 0.0) == 0.0)

# The additive form: variances add and sqrt is concave, so the +4.4 headline
# is the bias in isolation, not the inflation of a measurement on a 20-vol name.
add1, add5 = (V.iex_noise_bias(390, NOISE_STD, true_vol=0.20),
              V.iex_noise_bias(78, NOISE_STD, true_vol=0.20))
print(f"  additive inflation at a 20% true vol: 1-min {100*add1:+.2f} pts, "
      f"5-min {100*add5:+.2f} pts")
check("true_vol= gives the smaller additive inflation, correctly ordered",
      0 < add5 < add1 < V.iex_noise_bias(390, NOISE_STD),
      f"{100*add1:.2f} pts additive vs {100*V.iex_noise_bias(390, NOISE_STD):.2f} "
      f"pts in isolation")
for bad in (0, -1, None, "390"):
    check(f"iex_noise_bias({bad!r}, ...) -> None",
          V.iex_noise_bias(bad, NOISE_STD) is None)
check("iex_noise_bias with bad noise_std -> None",
      V.iex_noise_bias(390, None) is None and V.iex_noise_bias(390, np.nan) is None)

check("recommended_sampling_minutes() == 5",
      V.recommended_sampling_minutes() == 5)
doc = V.recommended_sampling_minutes.__doc__.lower()
missing = [k for k in ("4.4", "iex", "speed bump", "quarticity") if k not in doc]
check("...and its docstring says why (noise, staleness, speed bump)",
      not missing, f"missing: {missing}" if missing else "")


# --------------------------------------------------------------------------
print()
print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
print("=" * 74)
sys.exit(1 if FAIL else 0)
