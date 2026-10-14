"""
The referee: statistics that gate every test in the program.

deflated_sharpe(sr, T, n_trials, skew, kurt)  -> probability the observed Sharpe beats the best of n_trials null
                                                  strategies (Bailey & López de Prado 2014). Use the SCOREBOARD's
                                                  running trial count for n_trials.
pbo(returns_matrix, n_blocks=16)              -> probability of backtest overfitting via CSCV (Bailey, Borwein,
                                                  López de Prado, Zhu 2017). returns_matrix: T x N (trials).
purged_folds(n, k, embargo)                   -> (train_idx, test_idx) pairs with an embargo gap, for walk-forward.
"""
from __future__ import annotations
import itertools, math
import numpy as np

EULER = 0.5772156649


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p):
    # Acklam's approximation, adequate here
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    p = min(max(p, 1e-12), 1 - 1e-12)
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > 1 - 0.02425:
        q = math.sqrt(-2 * math.log(1 - p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5; r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def deflated_sharpe(sr: float, T: int, n_trials: int, skew: float = 0.0, kurt: float = 3.0,
                    var_sr_trials: float | None = None) -> float:
    """sr is the PER-PERIOD Sharpe (daily if returns are daily). Returns P(true SR > 0 | best of n_trials)."""
    n = max(int(n_trials), 1)
    if var_sr_trials is None:
        var_sr_trials = (1 + 0.5 * sr * sr) / max(T, 2)       # sampling variance of one SR estimate
    sr0 = math.sqrt(var_sr_trials) * ((1 - EULER) * _norm_ppf(1 - 1 / n) + EULER * _norm_ppf(1 - 1 / (n * math.e))) if n > 1 else 0.0
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr * sr, 1e-9))
    z = (sr - sr0) * math.sqrt(max(T - 1, 1)) / denom
    return _norm_cdf(z)


def pbo(returns: np.ndarray, n_blocks: int = 16) -> dict:
    """CSCV. returns: T x N matrix (rows = periods, cols = strategy variants). Returns PBO and the logits.
    Calibration note (2026-10-14): on pure noise this implementation averages ~0.55-0.6 rather than 0.5 — it
    errs toward calling things overfit. For a referee that is the right direction to err; do not "fix" it by
    loosening the gate."""
    R = np.asarray(returns, dtype=float)
    T, N = R.shape
    if N < 2 or T < 2 * n_blocks:
        return {"pbo": float("nan"), "n_combos": 0}
    blocks = np.array_split(np.arange(T), n_blocks)
    logits = []
    for train in itertools.combinations(range(n_blocks), n_blocks // 2):
        tr = np.concatenate([blocks[i] for i in train]); te = np.concatenate([blocks[i] for i in range(n_blocks) if i not in train])
        def sharpe(x):
            m = x.mean(axis=0); s = x.std(axis=0, ddof=0) + 1e-12; return m / s
        best = int(np.argmax(sharpe(R[tr])))
        oos = sharpe(R[te]); rank = (oos < oos[best]).sum() / (N - 1)        # relative rank of the IS-best OOS
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(rank / (1 - rank)))
    logits = np.array(logits)
    return {"pbo": float((logits <= 0).mean()), "n_combos": int(len(logits)), "mean_logit": float(logits.mean())}


def purged_folds(n: int, k: int = 5, embargo: int = 5):
    """Walk-forward-ish K folds: each test fold is contiguous; training excludes the fold plus an embargo on each side."""
    idx = np.arange(n); folds = np.array_split(idx, k); out = []
    for te in folds:
        lo, hi = te[0] - embargo, te[-1] + embargo
        tr = idx[(idx < lo) | (idx > hi)]
        out.append((tr, te))
    return out


def sharpe_ann(daily: np.ndarray, periods: int = 252) -> float:
    d = np.asarray(daily, dtype=float); s = d.std(ddof=0)
    return float(d.mean() / s * math.sqrt(periods)) if s > 0 else 0.0
