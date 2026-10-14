"""
Phase 0 data layer for the research program.

- Databento CME full-session minute bars (GLBX.MDP3, ohlcv-1m, continuous front month by volume).
- A SPEND LEDGER with a hard budget: every request is cost-estimated with the FREE metadata endpoint first,
  the cumulative spend is kept in research/program/databento_spend.json, and any request that would push the
  total past the budget is REFUSED with a clear message. Default budget = the $125 signup credit. Raising it
  is a human decision (pass budget= explicitly); the program's instruction is to stop and report.
- The SEALED HOLDOUT: nothing from 2024-01-01 onward is returned unless verdict=True. Development code that
  asks for it gets an exception, not data.

    from research.program.data import fetch_cme_minutes
    df = fetch_cme_minutes("ES.v.0", "2010-01-01", "2023-12-31")      # development
    df = fetch_cme_minutes("ES.v.0", "2024-01-01", "2026-10-01", verdict=True)   # one-shot verdict only
"""
from __future__ import annotations
import json, os, sys
import datetime as dt
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.path.join(ROOT, "cache", "research")
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "databento_spend.json")
HOLDOUT_START = "2024-01-01"
DEFAULT_BUDGET = 125.0


class HoldoutViolation(Exception):
    pass


class BudgetExceeded(Exception):
    pass


def _ledger() -> dict:
    try:
        return json.load(open(LEDGER))
    except Exception:
        return {"spent": 0.0, "requests": []}


def _save_ledger(l: dict):
    json.dump(l, open(LEDGER, "w"), indent=2)


def spent() -> float:
    return float(_ledger().get("spent", 0.0))


def fetch_cme_minutes(symbol: str, start: str, end: str, verdict: bool = False,
                      budget: float = DEFAULT_BUDGET, dataset: str = "GLBX.MDP3",
                      schema: str = "ohlcv-1m") -> pd.DataFrame:
    """Full-session 1-minute bars, UTC index, columns open/high/low/close/volume. Cached. Budgeted. Sealed."""
    if not verdict and end >= HOLDOUT_START:
        raise HoldoutViolation(f"{end} reaches into the sealed holdout (>= {HOLDOUT_START}); development requests must end before it")
    if verdict and start < HOLDOUT_START:
        raise HoldoutViolation("a verdict request must start inside the holdout; do not mix development and verdict data")
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f"{dataset}_{symbol.replace('.', '_')}_{schema}_{start}_{end}.parquet")
    if os.path.exists(p):
        return pd.read_parquet(p)
    import databento as db
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise RuntimeError("DATABENTO_API_KEY not set (it lives in ~/.spxf.env on the Mac)")
    client = db.Historical(key)
    cost = float(client.metadata.get_cost(dataset=dataset, symbols=[symbol], schema=schema,
                                          start=start, end=end, stype_in="continuous"))
    led = _ledger()
    if led["spent"] + cost > budget:
        raise BudgetExceeded(f"request would cost ${cost:.2f}; spent so far ${led['spent']:.2f}; budget ${budget:.2f}. "
                             f"STOP: report to Connor before continuing (PROGRAM.md §0 credit rule).")
    data = client.timeseries.get_range(dataset=dataset, symbols=[symbol], stype_in="continuous",
                                       schema=schema, start=start, end=end)
    df = data.to_df()
    df = df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df.index = df.index.tz_convert("UTC") if df.index.tz is not None else df.index.tz_localize("UTC")
    df.index.name = "ts"
    df.to_parquet(p)
    led["spent"] = round(led["spent"] + cost, 4)
    led["requests"].append({"when": dt.datetime.utcnow().isoformat(timespec="seconds"), "symbol": symbol, "schema": schema,
                            "start": start, "end": end, "cost": round(cost, 4), "rows": int(len(df)), "verdict": verdict})
    _save_ledger(led)
    print(f"databento: {symbol} {start}..{end} {len(df):,} rows, ${cost:.2f} (total ${led['spent']:.2f} of ${budget:.0f})", file=sys.stderr)
    return df


def estimate_phase0(symbols=("ES.v.0", "NQ.v.0", "RTY.v.0", "YM.v.0", "ZN.v.0", "ZB.v.0", "GC.v.0", "CL.v.0"),
                    start="2010-01-01", end="2023-12-31") -> float:
    """FREE: total estimated cost of the Phase 0 development download, before buying anything."""
    import databento as db
    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    total = 0.0
    for s in symbols:
        c = float(client.metadata.get_cost(dataset="GLBX.MDP3", symbols=[s], schema="ohlcv-1m", start=start, end=end, stype_in="continuous"))
        print(f"  {s}: ${c:.2f}"); total += c
    print(f"  TOTAL: ${total:.2f} (credit ${DEFAULT_BUDGET:.0f}, spent so far ${spent():.2f})")
    return total


if __name__ == "__main__":
    estimate_phase0()
