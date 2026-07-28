"""
Volatility estimation and forecasting for the options bot. numpy/pandas only.

Every decision the bot makes about an option is, underneath, a bet that implied
vol is wrong. That bet is only as good as the realized-vol number it is compared
against, so the measurement IS the strategy, not a detail of it.

★ THE ONE THING TO GET RIGHT: PARKINSON AND GARMAN-KLASS ARE GAP-BLIND.
They and Rogers-Satchell measure OPEN-TO-CLOSE variation only and cannot see the
overnight gap — roughly a fifth of daily variation for US equities (Hansen &
Lunde). Measured bias against a known vol path, 2,600 days, in annualized vol
points (note 08 §11.2): parkinson -2.16, garman_klass -2.45, rogers_satchell
-2.47, yang_zhang -0.62 (the only near-unbiased one), close_to_close -0.01
(unbiased, 2.7x noisier than YZ).

A systematic 2.2-2.5 point UNDER-estimate manufactures a fake "IV is above HV,
options are always rich" signal out of thin air. A bot acting on it sells premium
against a measurement artifact, permanently, and the losses look like ordinary
bad luck. It is the commonest silent error in retail vol-cone code. So:
yang_zhang is the default everywhere including realized_vol(), and parkinson /
garman_klass stay as DIAGNOSTICS (genuinely lower-variance, and correct for
intraday-only exposure) but warn when chosen through the dispatcher.

Units: every vol-returning function returns ANNUALIZED DECIMAL volatility
(0.20 == 20%) on 252 days — never variance, never percent. HAR is the labelled
exception: it eats and returns DAILY VARIANCE, because variance is what is
additive in time. Nothing returns NaN — insufficient or impossible data returns
None, so misuse raises a TypeError at the point of use instead of silently
sizing a position to nan.

Sources: /home/claude/research/notes/08-volatility-forecasting.md §11 (formulas
+ verified output), §C.3 (IEX constraint), §D.3 (cone error bars);
01-volatility-risk-premium.md §E.1 (VRP sign conventions).
"""
from __future__ import annotations

import functools
import math
import warnings
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

TRADING_DAYS = 252
_OHLC = ("open", "high", "low", "close")

# These three see the session only, never the gap. Bias measured vs true
# full-day vol in note 08 §11.2 (2,600-day simulation, 15% of daily variance
# arriving overnight — real single names gap MORE than that).
GAP_BLIND_METHODS = frozenset({"parkinson", "garman_klass", "rogers_satchell"})
GAP_BLIND_BIAS_VOL_POINTS = (2.2, 2.5)

HAR_LAGS = 22          # Corsi (2009): daily / weekly / monthly cascade
HAR_MIN_OBS = 60       # below this the 4-parameter fit is not worth trusting


# --- plumbing --------------------------------------------------------------
def _never_raises(fallback: Any):
    """Fail closed. This runs unattended inside a live trading loop, so an
    unanticipated exception must degrade to 'no signal', not kill the loop. The
    warning keeps genuine bugs visible in the log rather than swallowed."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as exc:               # noqa: BLE001 - deliberate
                warnings.warn(f"vol.{fn.__name__} failed: {exc!r}",
                              RuntimeWarning, stacklevel=2)
                return fallback
        return wrapper
    return deco

def _prepare(df: pd.DataFrame | None, window: int) -> pd.DataFrame | None:
    """Validate a daily-bar frame. None means 'refuse to produce a number'.

    Rejects, rather than repairs, anything meaning the data is WRONG rather than
    merely awkward: missing column, non-positive price, high < low, or fewer than
    window+1 usable rows (yang_zhang and close_to_close need the prior close). A
    NaN row is dropped instead — that is how a feed says "no bar", and dropping
    it just lengthens the overnight gap the way a weekend does, which YZ handles.
    An inf price can only be upstream corruption, so it rejects the frame.
    high < max(open, close) is CLAMPED: a single-venue-tape consistency artifact,
    not an impossible bar, and clamping only widens the range slightly.
    """
    if not isinstance(df, pd.DataFrame):
        return None
    if not isinstance(window, (int, np.integer)) or isinstance(window, bool):
        return None
    if window < 2:                 # ddof=1 variance and the YZ k factor need n>=2
        return None
    if any(c not in df.columns for c in _OHLC):
        return None
    out = df.loc[:, list(_OHLC)].apply(pd.to_numeric, errors="coerce").astype(float)
    if bool(np.isinf(out.to_numpy()).any()):
        return None
    out = out.dropna()
    if len(out) < window + 1:
        return None
    o, h, lo, c = (out[k].to_numpy() for k in _OHLC)
    if min(o.min(), h.min(), lo.min(), c.min()) <= 0.0 or bool((h < lo).any()):
        return None
    out["high"] = np.maximum(h, np.maximum(o, c))
    out["low"] = np.minimum(lo, np.minimum(o, c))
    return out


# --- 1. range-based estimators (daily OHLC) --------------------------------
def _s_close_to_close(f: pd.DataFrame, n: int) -> pd.Series:
    r = np.log(f["close"]).diff()
    return r.rolling(n).std(ddof=1) * math.sqrt(TRADING_DAYS)

def _s_parkinson(f: pd.DataFrame, n: int) -> pd.Series:
    hl = np.log(f["high"] / f["low"]) ** 2
    # 1/(4 ln 2) is Parkinson (1980)'s scaling of E[range^2] to variance.
    return np.sqrt(hl.rolling(n).mean() / (4.0 * math.log(2.0)) * TRADING_DAYS)

def _s_garman_klass(f: pd.DataFrame, n: int) -> pd.Series:
    hl = np.log(f["high"] / f["low"]) ** 2
    co = np.log(f["close"] / f["open"]) ** 2
    # Garman-Klass (1980) eq. 20, the practical 4-point form.
    var = (0.5 * hl - (2.0 * math.log(2.0) - 1.0) * co).rolling(n).mean()
    return np.sqrt(var.clip(lower=0.0) * TRADING_DAYS)   # GK goes negative on tiny ranges

def _s_rogers_satchell(f: pd.DataFrame, n: int) -> pd.Series:
    u, d = np.log(f["high"] / f["open"]), np.log(f["low"] / f["open"])
    c = np.log(f["close"] / f["open"])
    rs = u * (u - c) + d * (d - c)         # Rogers & Satchell (1991)
    return np.sqrt(rs.rolling(n).mean().clip(lower=0.0) * TRADING_DAYS)

def _s_yang_zhang(f: pd.DataFrame, n: int) -> pd.Series:
    o = np.log(f["open"] / f["close"].shift(1))     # overnight return
    c = np.log(f["close"] / f["open"])              # open-to-close return
    u, d = np.log(f["high"] / f["open"]), np.log(f["low"] / f["open"])
    rs = u * (u - c) + d * (d - c)
    # Yang & Zhang (2000) eq. 20. k minimizes the estimator's variance; it is a
    # function of the window only, and -> 0.34/2.34 as n -> inf.
    k = 0.34 / (1.34 + (n + 1.0) / (n - 1.0))
    var = (o.rolling(n).var(ddof=1)                 # ddof=1 == the paper's (n-1)
           + k * c.rolling(n).var(ddof=1)
           + (1.0 - k) * rs.rolling(n).mean())
    return np.sqrt(var.clip(lower=0.0) * TRADING_DAYS)

_ESTIMATORS = {"close_to_close": _s_close_to_close, "parkinson": _s_parkinson,
               "garman_klass": _s_garman_klass, "rogers_satchell": _s_rogers_satchell,
               "yang_zhang": _s_yang_zhang}

def _series(df, window: int, method: str) -> pd.Series | None:
    """Full rolling annualized-vol series, or None. Shared with the cone code."""
    f = _prepare(df, window)
    if f is None:
        return None
    fn = _ESTIMATORS.get(method)
    if fn is None:
        warnings.warn(f"vol: unknown estimator {method!r}; returning no signal. "
                      f"Valid: {sorted(_ESTIMATORS)}", RuntimeWarning, stacklevel=3)
        return None
    s = fn(f, int(window)).replace([np.inf, -np.inf], np.nan).dropna()
    return s if len(s) else None

def _last(df, window: int, method: str) -> float | None:
    """Most recent window's estimate. Zero/negative vol is treated as no signal:
    it is degenerate (flat prices) and divides badly downstream."""
    s = _series(df, window, method)
    if s is None:
        return None
    v = float(s.iloc[-1])
    return v if math.isfinite(v) and v > 0.0 else None

@_never_raises(None)
def close_to_close(df: pd.DataFrame, window: int = 21) -> float | None:
    """Plain historical vol: stdev of daily log close returns, annualized.
    Unbiased but noisy — ~2.7x the RMSE of Yang-Zhang (§11.2). Assumes clean
    closes and i.i.d. returns; ddof=1 subtracts the sample mean, so drift is
    removed empirically. Needs window+1 bars. None on any invalid input.
    """
    return _last(df, window, "close_to_close")

@_never_raises(None)
def parkinson(df: pd.DataFrame, window: int = 21) -> float | None:
    """Parkinson (1980) high-low range estimator. DIAGNOSTIC ONLY.

    ★ GAP-BLIND: it sees intraday range only, so on equities it under-reports
    full-day vol by ~2.2 annualized vol points (measured, §11.2). Differenced
    against an implied vol that DOES price the overnight, it fabricates a
    permanent 2-point "options are rich" signal — never feed it to a VRP or
    cone-percentile signal, use yang_zhang. It is legitimate for exactly one
    thing: variance you will only ever be exposed to intraday. Also assumes zero
    drift and continuous monitoring; discretely observed extremes understate the
    true ones, costing another ~0.7-0.9 points at 390 ticks/day and much more on
    thin names. None on invalid input.
    """
    return _last(df, window, "parkinson")

@_never_raises(None)
def garman_klass(df: pd.DataFrame, window: int = 21) -> float | None:
    """Garman-Klass (1980) OHLC estimator. DIAGNOSTIC ONLY.

    ★ GAP-BLIND: minimum-variance among analytic open-to-close estimators (~7.4x
    the theoretical efficiency of close-to-close) but blind to the overnight gap,
    under-reporting full-day vol by ~2.5 annualized vol points on equities
    (measured, §11.2). Same trap as parkinson: differenced against implied vol it
    manufactures a fake variance risk premium about the size of the premium you
    are hunting. Use yang_zhang for anything held overnight — which an options
    position always is. Assumes zero drift; the efficiency gain is eaten by that
    bias in practice (1.44x RMSE ratio, §11.2). None on invalid input.
    """
    return _last(df, window, "garman_klass")

@_never_raises(None)
def rogers_satchell(df: pd.DataFrame, window: int = 21) -> float | None:
    """Rogers-Satchell (1991). Drift-INDEPENDENT, still gap-blind (~-2.5 vol
    points, §11.2). Preferred over Parkinson/GK for strongly trending names
    because it does not assume zero drift. None on invalid input.
    """
    return _last(df, window, "rogers_satchell")

@_never_raises(None)
def yang_zhang(df: pd.DataFrame, window: int = 21) -> float | None:
    """Yang-Zhang (2000). ★ THE DEFAULT. Drift- AND gap-independent.

        sigma^2_YZ = sigma^2_overnight + k*sigma^2_open_to_close + (1-k)*RS
        k = 0.34 / (1.34 + (n+1)/(n-1))

    The only estimator here that measures FULL-DAY variance, which is the
    variance an overnight options position is actually exposed to. Measured bias
    -0.6 vol points with 2.7x lower RMSE than close-to-close (§11.2) — it wins by
    modelling the gap, not through variance reduction. Assumes one session per
    row, split/dividend-adjusted prices, and independent overnight and intraday
    components (the paper's derivation of k). It is a BACKWARD-LOOKING window
    estimator, not a forecast: after a spike yang_zhang(10) overstates the next
    10 days — use har_fit for that, or blend a short and a long window. Needs
    window+1 bars. None on invalid input.
    """
    return _last(df, window, "yang_zhang")

@_never_raises(None)
def realized_vol(df: pd.DataFrame, window: int = 21,
                 method: str = "yang_zhang") -> float | None:
    """Dispatcher. Defaults to yang_zhang and you should leave it there.

    method: close_to_close | parkinson | garman_klass | rogers_satchell |
    yang_zhang. A gap-blind choice warns here, because this is the path a config
    string or a typo travels down; call parkinson() directly when the
    intraday-only answer is genuinely what you want. Unknown -> warning + None.
    """
    if method in GAP_BLIND_METHODS:
        lo, hi = GAP_BLIND_BIAS_VOL_POINTS
        warnings.warn(
            f"vol.realized_vol(method={method!r}) is GAP-BLIND: it ignores the "
            f"overnight move and under-reports full-day vol by ~{lo}-{hi} "
            f"annualized vol points on equities. Comparing it to an implied vol "
            f"invents a variance risk premium that is not there. Use "
            f"'yang_zhang' unless you are forecasting intraday-only variance.",
            RuntimeWarning, stacklevel=2)
    return _last(df, window, method)


# --- 2. HAR-RV (Corsi 2009) — the one place we work in DAILY VARIANCE -------
def _clean_rv(rv_series) -> pd.Series:
    """Coerce to a positive, finite DAILY VARIANCE series. Empty on garbage."""
    s = pd.Series(rv_series, dtype="float64") if not isinstance(rv_series, pd.Series) \
        else rv_series.astype("float64")
    s = s.replace([np.inf, -np.inf], np.nan).dropna()
    s = s[s > 0.0]
    if len(s) and float(s.median()) > (3.0 ** 2) / TRADING_DAYS:
        # 0.0357 daily variance == 300% annualized. Almost always means the
        # caller passed annualized variance or vol instead of daily variance.
        warnings.warn("vol: rv_series median implies >300% annualized vol — "
                      "har_* expects DAILY VARIANCE (e.g. yz**2/252), not "
                      "annualized vol or variance.", RuntimeWarning, stacklevel=3)
    return s

def har_features(rv_series, non_overlapping: bool = True) -> pd.DataFrame:
    """HAR design matrix in LOG daily variance: columns d (1 day), w (5), m (22).

    Default is Salt Financial's NON-OVERLAPPING cascade, spanning the same
    1/5/22-day horizons as Corsi but partitioning them instead of nesting:
    d = log RV_t, w = mean(log RV_{t-1..t-4}), m = mean(log RV_{t-5..t-21}).
    Materially less collinear than the nested form and forecasts at least as well
    (§A.8). non_overlapping=False gives Corsi's mean(t..t-4) / mean(t..t-21).

    rv_series is DAILY VARIANCE. Logs, because realized variance is roughly
    lognormal and fitting in levels lets a handful of crisis days own the loss
    function. Non-positive and non-finite entries are dropped, which shifts the
    lag structure across the hole, so pass a contiguous series. Returns an empty
    frame (never None, never raises) on unusable input.
    """
    try:
        rv = _clean_rv(rv_series)
    except Exception:                                   # noqa: BLE001
        rv = pd.Series(dtype="float64")
    if not len(rv):
        return pd.DataFrame(columns=["d", "w", "m"], dtype="float64")
    x = np.log(rv)
    if non_overlapping:
        w, m = x.shift(1).rolling(4).mean(), x.shift(5).rolling(17).mean()
    else:
        w, m = x.rolling(5).mean(), x.rolling(22).mean()
    return pd.DataFrame({"d": x, "w": w, "m": m})

@_never_raises(None)
def har_fit(rv_series, non_overlapping: bool = True) -> dict | None:
    """Fit HAR-RV by OLS on LOG daily variance, one-day-ahead target.

    Returns {"beta": [b0, bd, bw, bm], "n", "r2", "resid_std", "non_overlapping"}
    or None. beta and resid_std are in LOG-VARIANCE units — har_forecast owns the
    conversion back to annualized vol, Jensen included. r2 is in-sample on log RV
    (Corsi's S&P 500 range is 0.52-0.71; a much higher number means you are
    fitting overlapping windows, not RV).

    Numerically safe by construction: solved with numpy.linalg.lstsq (SVD), never
    a normal-equation inverse, and returns None when lstsq reports the design
    rank-deficient (< 4) — which is what a constant or duplicated RV series
    produces. Requires >= 60 usable observations.
    """
    X = har_features(rv_series, non_overlapping=non_overlapping)
    if not len(X):
        return None
    frame = pd.concat([X, X["d"].shift(-1).rename("y")], axis=1)
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < HAR_MIN_OBS:
        return None
    A = np.column_stack([np.ones(len(frame)), frame[["d", "w", "m"]].to_numpy()])
    b = frame["y"].to_numpy()
    beta, _, rank, _ = np.linalg.lstsq(A, b, rcond=None)
    if rank < A.shape[1] or not np.all(np.isfinite(beta)):
        return None
    resid = b - A @ beta
    dof, tot = len(frame) - A.shape[1], float(np.var(b))
    if dof <= 0 or tot <= 0.0:
        return None
    return {"beta": [float(x) for x in beta], "n": int(len(frame)),
            "r2": float(1.0 - np.var(resid) / tot),
            "resid_std": float(math.sqrt(float(resid @ resid) / dof)),
            "non_overlapping": bool(non_overlapping)}

def _har_lag_weights(beta: Sequence[float], non_overlapping: bool) -> np.ndarray:
    """Flatten the d/w/m cascade into 22 plain AR coefficients so the forecast can
    be iterated forward one day at a time."""
    c = np.zeros(HAR_LAGS)
    c[0] += beta[1]
    if non_overlapping:
        c[1:5] += beta[2] / 4.0
        c[5:HAR_LAGS] += beta[3] / 17.0
    else:
        c[0:5] += beta[2] / 5.0
        c[0:HAR_LAGS] += beta[3] / 22.0
    return c

@_never_raises(None)
def har_forecast(model: dict, rv_series, horizon: int = 1) -> float | None:
    """Forecast ANNUALIZED VOL (decimal) over the next `horizon` trading days.

    The fit is one-day-ahead, so multi-day horizons iterate it: because the model
    is linear in LOGS, plugging the conditional mean back in gives the exact
    conditional mean of future log RV. The value returned is the mean daily
    variance over days t+1..t+h, converted with sqrt(v*252).

    ★ JENSEN CORRECTION. The fit is in logs, so exp(mu) is the MEDIAN, not the
    mean. Each day is converted with exp(mu_h + sigma_h^2 / 2), where sigma_h^2 is
    the accumulated h-step forecast variance from the model's MA representation.
    Omitting this — the most commonly dropped line in retail HAR code — biases
    every forecast LOW, the dangerous direction for anyone ever short volatility.

    Also clips the daily-variance forecast to [min(rv), 3*max(rv)] of the history
    supplied, so a broken input cannot hand a 400-vol forecast to a position
    sizer. rv_series is DAILY VARIANCE, the same units har_fit saw, and needs at
    least 22 usable days. None on any invalid input.
    """
    if not isinstance(model, dict):
        return None
    beta = model.get("beta")
    if not isinstance(beta, (list, tuple, np.ndarray)) or len(beta) != 4:
        return None
    beta = [float(x) for x in beta]
    resid_std = float(model.get("resid_std", 0.0) or 0.0)
    if not all(math.isfinite(x) for x in beta) or not math.isfinite(resid_std):
        return None
    if not isinstance(horizon, (int, np.integer)) or horizon < 1:
        return None
    rv = _clean_rv(rv_series)
    if len(rv) < HAR_LAGS:
        return None
    c = _har_lag_weights(beta, bool(model.get("non_overlapping", True)))
    s2 = resid_std ** 2
    # buf[0] is log RV_t, buf[k] is log RV_{t-k}. psi holds the MA coefficients of
    # the log process: psi[j] weights the shock j days before the forecast date,
    # so var(log RV_{t+h}) = s2 * sum(psi[:h]**2).
    buf = np.log(rv.to_numpy()[-HAR_LAGS:])[::-1].copy()
    psi = [1.0]
    for j in range(1, horizon):
        psi.append(float(sum(c[i - 1] * psi[j - i] for i in range(1, min(j, HAR_LAGS) + 1))))
    total, ss = 0.0, 0.0
    for h in range(1, horizon + 1):
        mu = beta[0] + float(c @ buf)
        ss += psi[h - 1] ** 2
        total += math.exp(mu + 0.5 * s2 * ss)          # Jensen correction
        buf = np.concatenate(([mu], buf[:-1]))
    hist = rv.to_numpy()
    daily_var = float(np.clip(total / horizon, hist.min(), hist.max() * 3.0))
    if not math.isfinite(daily_var) or daily_var <= 0.0:
        return None
    return math.sqrt(daily_var * TRADING_DAYS)


# --- 3. volatility cone (Burghardt & Lane 1990) ----------------------------
def _cone_sample(df, window: int, method: str, gap: bool) -> np.ndarray | None:
    s = _series(df, window, method)
    if s is None:
        return None
    if gap and len(s) > window:
        # strictly-trailing: drop windows overlapping the decision date so the
        # reference distribution is not correlated with the value being scored.
        s = s.iloc[:-window]
    v = s.to_numpy(dtype=float)
    return v if len(v) else None

@_never_raises({})
def vol_cone(df: pd.DataFrame, windows: Iterable[int] = (5, 10, 21, 42, 63),
             percentiles: Iterable[float] = (10, 25, 50, 75, 90),
             method: str = "yang_zhang", gap: bool = False) -> dict:
    """Volatility cone: the historical distribution of realized vol by horizon.

    Returns {window: {"p10".."p90", "min", "max", "n_obs", "n_eff"}}, with
    percentiles on 0-100. Windows with too little history are OMITTED — use
    .get(window), not [window].

    ★ READ n_eff BEFORE ACTING ON THIS. The percentiles come from OVERLAPPING
    windows, so n_obs is a fiction: consecutive 63-day windows share 62 days. The
    honest sample size is n_eff = n_obs / window, so two years of daily bars gives
    a 63-day cone an n_eff of about 8. The 21-day p90 in the reference study
    carried a bootstrap 95% CI 2.18 VOL POINTS WIDE (§11.2) — the same order as
    the entire edge the bot is hunting. Call cone_ci() and refuse the trade when
    your threshold sits inside the band.

    gap=False (default) uses every window in df; a live df ends at the last
    completed session, so there is no look-ahead. gap=True also drops the most
    recent `window` observations, right for research where df runs past the
    decision date.
    """
    pcts = [float(p) for p in percentiles]
    pcts = [p for p in pcts if math.isfinite(p) and 0.0 <= p <= 100.0]
    if pcts and max(pcts) <= 1.0:
        warnings.warn("vol.vol_cone: percentiles are 0-100, not 0-1 — "
                      f"{pcts} will be read as sub-1st-percentile.",
                      RuntimeWarning, stacklevel=2)
    out: dict[int, dict] = {}
    for w in windows:
        if not isinstance(w, (int, np.integer)) or w < 2:
            continue
        v = _cone_sample(df, int(w), method, gap)
        if v is None or len(v) < int(w) + 5:      # too thin to describe a shape
            continue
        rec = {f"p{p:g}": float(np.quantile(v, p / 100.0)) for p in pcts}
        rec["min"], rec["max"] = float(v.min()), float(v.max())
        rec["n_obs"] = int(len(v))
        rec["n_eff"] = max(1, int(len(v) // int(w)))   # the honest sample size
        out[int(w)] = rec
    return out

@_never_raises(None)
def cone_percentile(df: pd.DataFrame, window: int, value: float,
                    method: str = "yang_zhang", gap: bool = False) -> float | None:
    """Where `value` (annualized decimal vol, e.g. an ATM IV) sits in the cone.

    Returns 0-100: the percent of historical `window`-day realized-vol
    observations strictly below `value`. Round-trips against vol_cone with the
    same method/gap. Requires >= 20 observations, else None. The percentile is
    only as certain as the cone behind it — "IV is at the 90th percentile" off an
    n_eff of 8 is not a signal. See cone_ci and n_eff.
    """
    if not isinstance(value, (int, float, np.floating, np.integer)) or isinstance(value, bool):
        return None
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        return None
    v = _cone_sample(df, window, method, gap)
    if v is None or len(v) < 20:
        return None
    return float(100.0 * np.mean(v < value))

@_never_raises((None, None))
def cone_ci(df: pd.DataFrame, window: int, percentile: float,
            n_boot: int = 500, seed: int = 0,
            method: str = "yang_zhang", gap: bool = False) -> tuple:
    """Block-bootstrap 95% CI for one cone percentile, as (lo, hi) in annualized
    decimal vol, or (None, None).

    Block length = window, the minimum honest choice: overlapping window
    estimates are autocorrelated for exactly `window` days, so an i.i.d. bootstrap
    understates the interval by roughly sqrt(window). In the reference run the
    21-day p90 came out 0.2528 with a CI of [0.2420, 0.2638] — 2.18 vol points
    wide on 2,579 nominal observations (n_eff = 122). If your entry threshold is
    inside that interval you do not have a signal, you have a sample. Requires
    >= 3*window observations.
    """
    if not isinstance(percentile, (int, float, np.floating, np.integer)) or isinstance(percentile, bool):
        return (None, None)
    p = float(percentile)
    if not math.isfinite(p) or not 0.0 <= p <= 100.0:
        return (None, None)
    if not isinstance(n_boot, (int, np.integer)) or n_boot < 2:
        return (None, None)
    v = _cone_sample(df, window, method, gap)
    w = int(window)
    if v is None or len(v) < 3 * w:
        return (None, None)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(v) - w, size=(int(n_boot), max(1, len(v) // w)))
    idx = starts[:, :, None] + np.arange(w)[None, None, :]
    draws = np.quantile(v[idx].reshape(int(n_boot), -1), p / 100.0, axis=1)
    return (float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975)))


# --- 4. variance risk premium ----------------------------------------------
def _as_vol(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float, np.floating, np.integer)):
        return None
    v = float(x)
    return v if math.isfinite(v) and v >= 0.0 else None

@_never_raises(None)
def vrp(implied_vol: float, forecast_vol: float) -> float | None:
    """VRP in VARIANCE units: iv**2 - fv**2. Positive == options are rich.

    Both inputs are ANNUALIZED DECIMAL vol, and fv must be a FORECAST of realized
    vol over the option's life, not trailing realized vol. Subtracting a trailing
    number is the commonest VRP bug in circulation and it fails in the worst
    possible way: right after a vol spike trailing RV is high, VRP looks negative,
    and the naive rule buys volatility into the collapse (note 01 §E.1).
    har_forecast() produces the right input.

    Watch the horizon too: VIX is 30 CALENDAR days annualized on 365, realized vol
    here is 21 TRADING days on 252, and mismatching them is a permanent 1-2 vol
    point bias. Returns None (not NaN) if either input is missing or non-finite —
    the path that catches har_forecast() having returned None.
    """
    a, b = _as_vol(implied_vol), _as_vol(forecast_vol)
    return None if a is None or b is None else a * a - b * b

@_never_raises(None)
def vrp_vol_points(implied_vol: float, forecast_vol: float) -> float | None:
    """VRP in vol units: iv - fv, as a DECIMAL (0.04 == 4 vol points).
    Report this alongside vrp(): a straddle is closer to a vol exposure and a
    variance swap to a variance exposure, and Jensen makes the two disagree. Same
    forecast-not-trailing rule as vrp(). None on invalid input.
    """
    a, b = _as_vol(implied_vol), _as_vol(forecast_vol)
    return None if a is None or b is None else a - b


# --- 5. the IEX noise guard ------------------------------------------------
@_never_raises(None)
def iex_noise_bias(n_bars_per_day: int, noise_std: float,
                   true_vol: float | None = None) -> float | None:
    """Upward bias in realized variance from microstructure noise, as vol.

    Zhang, Mykland & Ait-Sahalia (2005): with observed log price Y = X + eps,
    E[RV] = integrated variance + 2 * M * E[eps^2], so the bias grows LINEARLY in
    sampling frequency. Sampled finely enough, RV estimates the noise.

    n_bars_per_day (M): 390 at 1-minute, 78 at 5-minute, 26 at 15-minute.
    noise_std: per-observation noise sd in log price. 1e-4 (one basis point) is
    representative of a SINGLE-VENUE quote on a liquid name; consolidated NBBO
    noise is smaller, and IEX's 350us speed bump makes its quote staler than the
    NBBO, so 1bp is not pessimistic.

    Returns sqrt(252 * 2*M*noise_std^2): M=390 at 1bp -> 0.0443 (+4.4 vol points),
    M=78 -> 0.0198 (+2.0). ★ That is the vol equivalent of the VARIANCE bias in
    isolation, which is the note's convention and the right number for comparing
    sampling schemes. It is NOT the additive inflation of a measurement on a name
    that actually has vol, because variances add and the square root is concave —
    pass true_vol for that instead (at a 20% level: +0.49 points at 1-minute,
    +0.10 at 5-minute).
    """
    if not isinstance(n_bars_per_day, (int, float, np.integer, np.floating)) \
            or isinstance(n_bars_per_day, bool):
        return None
    m, sd = float(n_bars_per_day), _as_vol(noise_std)
    if sd is None or not math.isfinite(m) or m <= 0.0:
        return None
    bias_daily_var = 2.0 * m * sd * sd
    if true_vol is None:
        return math.sqrt(TRADING_DAYS * bias_daily_var)
    tv = _as_vol(true_vol)
    if tv is None:
        return None
    return math.sqrt(TRADING_DAYS * (tv * tv / TRADING_DAYS + bias_daily_var)) - tv

def recommended_sampling_minutes() -> int:
    """Return 5. Never sample IEX minute bars at 1 minute.

    Three independent reasons, any one of which is sufficient (note 08 §C.2-3):
    1. NOISE BIAS. At M=390 with 1bp single-venue noise the RV bias is +4.4
       annualized vol points, against +2.0 at M=78 (see iex_noise_bias) — bigger
       than any edge documented anywhere in the research. The 5-minute residual is
       roughly a constant level shift, so it largely cancels in a HAR intercept
       and in RV-vs-own-average comparisons, but NOT when you compare RV to IV.
    2. STALENESS. Alpaca's free tier is IEX-only, a few percent of consolidated
       volume, so many 1-minute bars for anything but a big ETF contain no trade
       at all and carry the last price forward. Little bias, but badly inflated
       variance, and realized quarticity and every jump test become simply wrong
       (so: no HARQ on this data).
    3. THE SPEED BUMP. IEX's 350-microsecond delay makes its quote stale relative
       to the NBBO. Irrelevant at 5 minutes, fatal below one.

    Liu, Patton & Sheppard (2015) tested 400+ realized measures across 31 assets
    and could not significantly beat 5-minute RV, so there is no upside to being
    clever here either. Subsample (average the five 5-minute grids at offsets 0-4)
    — it is free and reduces variance.
    """
    return 5
