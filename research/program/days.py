"""
Shared DAY-SESSION table for the intraday 3A tests, built from the cached, holdout-sealed minute bars. One row per date with
a 08:30 and a 14:59 CT bar. Prices are opens at fixed CT times (o_HHMM), closes at fixed times (c_HHMM), the first-5 and
first-15-minute ranges, and two pre-computed opening-range-breakout outcomes (3A-12):
  orbN_dir  +1 if the first N-minute candle closed above its open, -1 below, 0 flat (no trade)
  orbN_entry open of the bar after the range; orbN_exit  exit price: the range's opposite extreme if touched (bar low/high),
  the bar's open if it gaps through, else the 14:59 close.
"""
from __future__ import annotations
import os
import numpy as np, pandas as pd
from research.program import data as D
from research.program.nights import START, END, MICRO, POINT, COST_SIDE, COST_BP

OPENS = ["08:00", "08:30", "08:35", "08:45", "09:00", "09:29", "09:30", "10:00", "10:30", "13:00", "14:00", "14:30"]
CLOSES = ["08:34", "08:44", "09:28", "13:29", "14:29", "14:59"]
_CACHE = {}


def _orb(g: pd.DataFrame, n_min: int):
    rng = g.iloc[:n_min]
    if len(rng) < n_min or len(g) <= n_min: return 0, np.nan, np.nan
    o, c = float(rng["open"].iloc[0]), float(rng["close"].iloc[-1]); hi, lo = float(rng["high"].max()), float(rng["low"].min())
    d = 1 if c > o else (-1 if c < o else 0)
    if d == 0: return 0, np.nan, np.nan
    path = g.iloc[n_min:]; entry = float(path["open"].iloc[0]); stop = lo if d == 1 else hi
    px = float(path["close"].iloc[-1])
    for op, hh, ll in path[["open", "high", "low"]].to_numpy():
        if d == 1 and op <= stop: px = op; break
        if d == 1 and ll <= stop: px = stop; break
        if d == -1 and op >= stop: px = op; break
        if d == -1 and hh >= stop: px = stop; break
    return d, entry, px


def days(root: str) -> pd.DataFrame:
    if root in _CACHE: return _CACHE[root]
    p = os.path.join(D.CACHE, f"days_{root}_{START}_{END}.parquet")
    if os.path.exists(p):
        _CACHE[root] = pd.read_parquet(p); return _CACHE[root]
    df = D.fetch_cme_minutes(f"{root}.v.0", START, END).tz_convert("America/Chicago")
    rows = []
    for d, g in df.groupby(df.index.date):
        hm = g.index.strftime("%H:%M"); g = g.copy(); g["hm"] = hm
        if "08:30" not in set(hm) or "14:59" not in set(hm): continue
        row = {"d": pd.Timestamp(d)}
        by = g.set_index("hm")
        for t in OPENS: row[f"o_{t.replace(':', '')}"] = float(by.loc[t, "open"]) if t in by.index else np.nan
        for t in CLOSES: row[f"c_{t.replace(':', '')}"] = float(by.loc[t, "close"]) if t in by.index else np.nan
        day = g[(hm >= "08:30") & (hm <= "14:59")]
        r5 = day.iloc[:5]; r15 = day.iloc[:15]
        row["h_0834"], row["l_0834"] = float(r5["high"].max()), float(r5["low"].min())
        row["h_0844"], row["l_0844"] = float(r15["high"].max()), float(r15["low"].min())
        row["orb5_dir"], row["orb5_entry"], row["orb5_exit"] = _orb(day, 5)
        row["orb15_dir"], row["orb15_entry"], row["orb15_exit"] = _orb(day, 15)
        row["n_bars"] = len(day)
        rows.append(row)
    out = pd.DataFrame(rows); out.to_parquet(p); _CACHE[root] = out
    return out


def trade_result(sub: pd.DataFrame, root: str, entry_col: str, exit_col: str, side_col=None, stop_bp_cap=None) -> dict:
    """Generic intraday trade scorer: P&L in the traded contract's $, plus gross/stopped bp for the §0b rule. side = +1/-1 (default +1)."""
    sub = sub.dropna(subset=[entry_col, exit_col]).sort_values("d")
    side = sub[side_col].values if side_col else np.ones(len(sub))
    pt, rt, cbp = POINT[MICRO[root]], 2 * COST_SIDE[MICRO[root]], COST_BP[MICRO[root]]
    ret = side * (sub[exit_col].values / sub[entry_col].values - 1)
    usd = side * (sub[exit_col].values - sub[entry_col].values) * pt
    daily = pd.Series(usd - rt, index=sub["d"].dt.strftime("%Y-%m-%d").values)
    return {"daily": daily, "trades": int(len(sub)), "gross": float(usd.sum()), "costs": len(sub) * rt,
            "gross_per_night": float(usd.mean()) if len(sub) else float("nan"), "rt_cost": rt, "bp_per_night": float(ret.mean() * 1e4) if len(sub) else float("nan"),
            "root": root, "gross_bp": ret * 1e4, "stopped_bp": ret * 1e4, "cost_bp": cbp, "sub": sub}
