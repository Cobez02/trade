"""
1-minute bar loaders with one shape: a DataFrame indexed by UTC timestamp
(bar START) with columns open, high, low, close, volume. Everything downstream
(sessions.rth_only, resample_bars, the backtester, the live runner) consumes
exactly this, so the data source is swappable.

Sources
  databento  MES/MNQ/ES/NQ from CME Globex MDP3 (GLBX.MDP3), continuous
             front-month by volume (".v.0"). Needs DATABENTO_API_KEY.
             This is the data the real bot must be validated on.
  alpaca     SPY/QQQ SIP minute bars (Algo Trader Plus) — the share PROXY for
             research before you buy futures data, and for the paper lab.
  yfinance   SPY/QQQ, last 60 days of 5-minute bars, free, no key. Only for
             smoke-testing the pipeline; never for conclusions.

All loaders cache to parquet under config.CACHE_DIR keyed by source/symbol/
start/end so a re-run is free.
"""
from __future__ import annotations
import os, sys, time
import datetime as dt
from typing import Optional

import pandas as pd

import config as C

COLS = ["open", "high", "low", "close", "volume"]


def _cache_path(source: str, symbol: str, start: str, end: str, tf: str) -> str:
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    return os.path.join(C.CACHE_DIR, f"{source}_{symbol}_{tf}_{start}_{end}.parquet")


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df = df[COLS].astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df.index.name = "ts"
    return df


# ---------------------------------------------------------------------------
def load_databento(symbol: str, start: str, end: str, dataset: str = "GLBX.MDP3",
                   api_key: Optional[str] = None) -> pd.DataFrame:
    """Continuous front-month 1-minute OHLCV for a CME product.

    `symbol` is the databento continuous symbol, e.g. "MES.v.0" (highest
    volume). The volume-ranked roll switches ~the Thursday before expiry, so
    roll gaps land between sessions, which is harmless for a flat-by-close
    strategy (each day is independent; the noise band is in % terms).
    Cost: ohlcv-1m for one product over 5 years is on the order of a few
    dollars of Databento credit. Databento's own timestamps are ts_event (UTC).
    """
    p = _cache_path("databento", symbol.replace(".", "_"), start, end, "1m")
    if os.path.exists(p):
        return pd.read_parquet(p)
    import databento as db
    key = api_key or os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise RuntimeError("DATABENTO_API_KEY is not set")
    client = db.Historical(key)
    data = client.timeseries.get_range(
        dataset=dataset, symbols=[symbol], stype_in="continuous",
        schema="ohlcv-1m", start=start, end=end)
    df = data.to_df()
    # to_df() indexes by ts_event (UTC) and gives float prices already scaled
    df = df.rename(columns={c: c for c in COLS})
    df = _finish(df)
    df.to_parquet(p)
    return df


def load_alpaca(symbol: str, start: str, end: str, feed: str | None = None,
                api_key: Optional[str] = None, secret: Optional[str] = None) -> pd.DataFrame:
    """SPY/QQQ 1-minute bars from Alpaca (SIP with Algo Trader Plus, else IEX).

    Paginates month by month because alpaca-py's page handling on multi-year
    minute ranges is slow and memory-hungry; each month is cached separately
    so an interrupted download resumes.
    """
    feed = (feed or os.environ.get("SPXF_ALPACA_FEED", "sip")).lower()
    p = _cache_path("alpaca", symbol, start, end, feed + "_1m")
    if os.path.exists(p):
        return pd.read_parquet(p)
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.data.enums import DataFeed, Adjustment
    key = api_key or os.environ.get("ALPACA_API_KEY")
    sec = secret or os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    client = StockHistoricalDataClient(key, sec)
    fd = DataFeed.SIP if feed.lower() == "sip" else DataFeed.IEX
    s = pd.Timestamp(start, tz="UTC"); e = pd.Timestamp(end, tz="UTC")
    parts = []
    cur = s
    while cur < e:
        nxt = min((cur + pd.offsets.MonthBegin(1)).normalize(), e)
        mp = _cache_path("alpaca", symbol, cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d"), "1m")
        if os.path.exists(mp):
            parts.append(pd.read_parquet(mp)); cur = nxt; continue
        req = StockBarsRequest(symbol_or_symbols=symbol,
                               timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                               start=cur.to_pydatetime(), end=nxt.to_pydatetime(),
                               feed=fd, adjustment=Adjustment.RAW)
        df = None
        for attempt in range(6):
            try:
                df = client.get_stock_bars(req).df
                break
            except Exception as ex:                      # 429 rate limit, 5xx, network
                wait = 20 * (attempt + 1)
                print(f"  {symbol} {cur.date()}..{nxt.date()}: {type(ex).__name__}: {str(ex)[:120]} — retry in {wait}s",
                      file=sys.stderr, flush=True)
                time.sleep(wait)
        else:
            raise RuntimeError(f"alpaca fetch failed for {symbol} {cur.date()}..{nxt.date()} after 6 attempts")
        print(f"  {symbol} {cur.date()}..{nxt.date()}: {0 if df is None else len(df):,} bars", file=sys.stderr, flush=True)
        time.sleep(0.4)          # stay under the basic plan's 200 req/min even with pagination
        if df is not None and len(df):
            if isinstance(df.index, pd.MultiIndex):
                df = df.reset_index(level=0, drop=True)
            df = _finish(df)
            df.to_parquet(mp)
            parts.append(df)
        cur = nxt
    out = _finish(pd.concat(parts)) if parts else pd.DataFrame(columns=COLS)
    out.to_parquet(p)
    return out


def load_yfinance(symbol: str, period: str = "60d", interval: str = "5m") -> pd.DataFrame:
    """Free smoke-test data. 60 days max at 5m; 7 days at 1m. NOT for research."""
    p = _cache_path("yf", symbol, period, dt.date.today().isoformat(), interval)
    if os.path.exists(p):
        return pd.read_parquet(p)
    import yfinance as yf
    df = yf.download(symbol, period=period, interval=interval, auto_adjust=False,
                     progress=False, prepost=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    df = _finish(df)
    df.to_parquet(p)
    return df


def load(source: str, symbol: str, start: str = None, end: str = None, **kw) -> pd.DataFrame:
    if source == "databento":
        return load_databento(symbol, start, end, **kw)
    if source == "alpaca":
        return load_alpaca(symbol, start, end, **kw)
    if source == "yfinance":
        return load_yfinance(symbol, **kw)
    raise ValueError(f"unknown source {source}")
