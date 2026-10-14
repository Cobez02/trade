"""
Shared overnight-night builder for the 3A family. One row per tradeable night, built ONLY from the cached, holdout-sealed
Databento bars (research.program.data). Every 3A test conditions on these rows; none of them re-derives the P&L.

Night = buy at the open of the 17:02 CT bar on day d0, sell at the open of the 08:30 CT bar on day d1 = d0 + 1 calendar day.
Friday nights are skipped (Globex closes 16:00 CT Friday); Sunday 17:02 -> Monday 08:30 is one night.

Columns (all in MICRO dollars for the root's micro: MES $5/pt, MNQ $2/pt, M2K $5/pt, MYM $0.50/pt):
  d0, d1        entry / exit calendar dates (America/Chicago)
  entry, exit   prices
  ret           exit/entry - 1 (no stop)
  gross         (exit - entry) * pt, no stop, no cost
  net_<stop>    P&L with the stop at entry*(1-stop) and the fixed round-trip cost, for stop in STOPS (0 = no stop)
  day_ret       the most recent DAY SESSION before entry: close of the 14:59 bar / open of the 08:30 bar - 1, same date as d0
                (for Sunday nights it is Friday's day session). NaN if either bar is missing.
  maxstep       largest one-minute |close/close - 1| inside the night (roll / crash diagnostic)
"""
from __future__ import annotations
import os
import numpy as np, pandas as pd
from research.program import data as D

STOPS = (0.0, 0.0075, 0.01, 0.015, 0.02)
# traded contract per data root, $/point, cost per side (PROGRAM.md §0: commission + 1 tick). Index roots trade the micro.
MICRO = {"ES": "MES", "NQ": "MNQ", "RTY": "M2K", "YM": "MYM", "ZN": "ZN", "ZB": "ZB", "GC": "GC", "CL": "CL"}
POINT = {"MES": 5, "MNQ": 2, "M2K": 5, "MYM": 0.5, "ZN": 1000, "ZB": 1000, "GC": 100, "CL": 1000}
COST_SIDE = {"MES": 1.00 + 1.25, "MNQ": 1.00 + 0.50, "M2K": 1.00 + 0.50, "MYM": 1.00 + 0.50,
             "ZN": 2.00 + 15.625, "ZB": 2.00 + 31.25, "GC": 2.50 + 10.00, "CL": 2.50 + 10.00}
START, END = "2010-06-06", "2023-12-31"
_CACHE = {}


def round_trip_cost(root: str) -> float:
    return 2 * COST_SIDE[MICRO[root]]


def nights(root: str) -> pd.DataFrame:
    if root in _CACHE:
        return _CACHE[root]
    p = os.path.join(D.CACHE, f"nights_{root}_{START}_{END}.parquet")
    if os.path.exists(p):
        _CACHE[root] = pd.read_parquet(p); return _CACHE[root]
    df = D.fetch_cme_minutes(f"{root}.v.0", START, END).tz_convert("America/Chicago")
    pt, rt = POINT[MICRO[root]], round_trip_cost(root)
    by = {d: g for d, g in df.groupby(df.index.date)}; days = sorted(by)
    # last completed day session on or before each date (for day_ret)
    day_ret = {}
    for d, g in by.items():
        o = g[(g.index.hour == 8) & (g.index.minute == 30)]; c = g[(g.index.hour == 14) & (g.index.minute == 59)]
        if len(o) == 1 and len(c) == 1:
            day_ret[d] = float(c["close"].iloc[0]) / float(o["open"].iloc[0]) - 1
    rows = []
    for i in range(len(days) - 1):
        d0, d1 = days[i], days[i + 1]
        if d0.weekday() == 4 or (d1 - d0).days != 1: continue
        g0, g1 = by[d0], by[d1]
        ent = g0[(g0.index.hour == 17) & (g0.index.minute == 2)]; ex = g1[(g1.index.hour == 8) & (g1.index.minute == 30)]
        if len(ent) != 1 or len(ex) != 1: continue
        entry, exit_px = float(ent["open"].iloc[0]), float(ex["open"].iloc[0])
        path = pd.concat([g0[g0.index >= ent.index[0]], g1[g1.index < ex.index[0]]])
        arr = path[["open", "low"]].to_numpy()
        row = {"d0": pd.Timestamp(d0), "d1": pd.Timestamp(d1), "entry": entry, "exit": exit_px, "ret": exit_px / entry - 1,
               "gross": (exit_px - entry) * pt, "maxstep": float((path["close"].pct_change().abs()).max())}
        # day_ret: the most recent completed day session before this entry (d0 itself, else the last earlier date with one)
        dr = day_ret.get(d0)
        if dr is None:
            j = i - 1
            while j >= 0 and days[j] not in day_ret and (d0 - days[j]).days <= 4: j -= 1
            dr = day_ret.get(days[j]) if j >= 0 and (d0 - days[j]).days <= 4 else np.nan
        row["day_ret"] = dr
        for s in STOPS:
            px = exit_px
            if s:
                lvl = entry * (1 - s)
                for o, lo in arr:
                    if o <= lvl: px = o; break
                    if lo <= lvl: px = lvl; break
            row[f"net_{s}"] = (px - entry) * pt - rt
        rows.append(row)
    out = pd.DataFrame(rows)
    out.to_parquet(p)
    _CACHE[root] = out
    return out


def summarize(sub: pd.DataFrame, root: str, stop: float = 0.01) -> dict:
    """What run_test.score needs, plus the ratio that matters: gross $/night vs the micro round trip."""
    rt = round_trip_cost(root)
    daily = pd.Series(sub[f"net_{stop}"].values, index=sub["d1"].dt.strftime("%Y-%m-%d").values).sort_index()
    gross = float(sub["gross"].sum())
    return {"daily": daily, "trades": int(len(sub)), "gross": gross, "costs": len(sub) * rt,
            "gross_per_night": gross / max(len(sub), 1), "rt_cost": rt, "bp_per_night": float(sub["ret"].mean() * 1e4)}
