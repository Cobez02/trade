"""Alpaca-backed data layer for backtest.py — replaces ThetaData.

WHY
---
backtest.py was written against a local Theta Terminal serving EOD NBBO
quotes. ThetaData died with a workspace wipe and its key is not recoverable,
which left every open study blocked. The Algo Trader Plus subscription exposes
historical option bars back to Feb 2024, so this module reimplements the four
functions backtest.py actually calls, keeping their exact signatures and
return shapes:

    expirations(symbol)                              -> [iso_date, ...]
    strikes(symbol, expiration)                      -> [float, ...]
    day_snapshot(symbol, expiration, iso_day)        -> {(strike, RIGHT): {date: quote}}
    contract_series(symbol, expiration, strike, right, start) -> {date: quote}

where quote = {"bid", "ask", "mid", "high", "low"}.

THE SUBSTITUTION THAT MATTERS -- READ BEFORE TRUSTING ANY RESULT
----------------------------------------------------------------
ThetaData served BID and ASK. Alpaca serves **no historical option quotes at
all** -- verified against six endpoint paths, all 404 except live-only
`quotes/latest` and `snapshots`, with `bars` as a working control. So the
NBBO this backtest used to screen on cannot be reconstructed at any price.

What replaces it: `mid` = the bar's **VWAP** (volume-weighted traded price,
falling back to close), which is where trades actually printed. That is a
good proxy for a transactable level and a poor proxy for the spread, because
it has no width at all.

Consequences, stated plainly:

  1. bid == ask == mid, so `spread_pct` is 0 and the 4%-spread screen PASSES
     EVERYTHING. That screen exists to reject trades whose round trip eats the
     edge. Disabling it makes the backtest strictly optimistic.
  2. The vol-edge gate compares implied vol from the mid against a forecast.
     It still runs, but on a VWAP mid rather than an NBBO mid.
  3. Round-trip cost cannot be measured here at all. That question needs real
     fills and is what the live arm is for.

So: EVERY NUMBER THIS PRODUCES IS AN UPPER BOUND. To keep that from quietly
becoming a measurement, set SPXBOT_BT_RT_COST to an assumed round-trip cost
(as a fraction of premium) and re-run. A conclusion that survives 0%, 2% and
4% is robust; one that only holds at 0% is an artifact of the missing spread.

COVERAGE: options from 2024-02 (Alpaca's stated start; empirically the first
expired-contract listings appear 2024-01). Equities reach 2016 via SIP, so
underlying-only studies have a much longer window than option studies.
"""
import datetime as dt
import json
import os
import time
from typing import Optional

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.trading.enums import AssetStatus

CACHE = os.environ.get("SPXBOT_BT_CACHE", "/home/claude/btcache")
os.makedirs(CACHE, exist_ok=True)

# Loud module-level facts, so a caller can assert on them rather than trusting
# a comment. backtest.py prints these in its banner.
SPREADS_AVAILABLE = False
MID_SOURCE = "vwap"
DATA_START = dt.date(2024, 2, 1)
ASSUMED_RT_COST = float(os.environ.get("SPXBOT_BT_RT_COST", "0.0"))

DATA_ERRORS = []

_K = os.environ.get("ALPACA_API_KEY", "")
_S = os.environ.get("ALPACA_SECRET_KEY", "")
_opt: Optional[OptionHistoricalDataClient] = None
_trade: Optional[TradingClient] = None


def _clients():
    global _opt, _trade
    if _opt is None:
        if not _K or not _S:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
        _opt = OptionHistoricalDataClient(_K, _S)
        _trade = TradingClient(_K, _S, paper=True)
    return _opt, _trade


def _cached(key: str, fetch):
    """Disk cache. Same contract as backtest._cached: a miss calls `fetch`,
    a hit is free. Empty results ARE cached -- an expiry with no contracts is
    a real, stable answer, and re-asking every run wastes the rate limit."""
    path = os.path.join(CACHE, key.replace("/", "_") + ".json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    val = fetch()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(val, f)
    os.replace(tmp, path)          # atomic: a killed run cannot leave a torn cache
    return val


def _contracts(symbol: str, exp: str) -> list:
    """Every contract for one underlying+expiry, as plain dicts.

    status=INACTIVE is the important part: a backtest needs the contracts that
    EXISTED on a past date, and essentially all of them are now expired.
    ACTIVE returns zero for any historical expiry."""
    def go():
        _, tr = _clients()
        out, token = [], None
        while True:
            req = GetOptionContractsRequest(
                underlying_symbols=[symbol], status=AssetStatus.INACTIVE,
                expiration_date_gte=exp, expiration_date_lte=exp,
                limit=10000, page_token=token)
            try:
                r = tr.get_option_contracts(req)
            except Exception as e:
                DATA_ERRORS.append((f"{symbol} {exp} contracts", str(e)[:80]))
                break
            cs = getattr(r, "option_contracts", None) or []
            for c in cs:
                out.append({
                    "symbol": c.symbol,
                    "strike": float(c.strike_price),
                    "right": "CALL" if str(c.type).upper().endswith("CALL") else "PUT",
                    "oi": int(c.open_interest) if c.open_interest is not None else 0,
                })
            token = getattr(r, "next_page_token", None)
            if not token:
                break
        return out
    return _cached(f"ct_{symbol}_{exp}", go)


def expirations(symbol: str) -> list:
    """Expiries with listed contracts, from DATA_START to today.

    Alpaca has no 'list expirations' endpoint, so this walks candidate dates
    (every Friday, plus month-end) and keeps the ones that list contracts."""
    def go():
        found, d = [], DATA_START
        today = dt.date.today()
        while d <= today:
            if d.weekday() == 4:            # weeklies expire Friday
                if _contracts(symbol, d.isoformat()):
                    found.append(d.isoformat())
            d += dt.timedelta(days=1)
        return found
    return _cached(f"exp_{symbol}", go)


def strikes(symbol: str, expiration: str) -> list:
    return sorted({c["strike"] for c in _contracts(symbol, expiration)})


def open_interest(symbol: str, expiration: str) -> dict:
    """{(strike, RIGHT): oi} — lets the backtest apply the live MIN_OPEN_INTEREST
    floor rather than silently skipping a screen the live bot enforces."""
    return {(c["strike"], c["right"]): c["oi"] for c in _contracts(symbol, expiration)}


def _bars(occ: str, start: str, end: str) -> dict:
    """Daily bars for one contract -> {iso_date: quote}. VWAP is the mid."""
    def go():
        op, _ = _clients()
        try:
            df = op.get_option_bars(OptionBarsRequest(
                symbol_or_symbols=occ, timeframe=TimeFrame.Day,
                start=dt.date.fromisoformat(start),
                end=dt.date.fromisoformat(end) + dt.timedelta(days=1))).df
        except Exception as e:
            DATA_ERRORS.append((occ, str(e)[:80]))
            return {}
        if df is None or len(df) == 0:
            return {}
        out = {}
        for idx, row in df.iterrows():
            ts = idx[-1] if isinstance(idx, tuple) else idx
            day = str(ts)[:10]
            px = row.get("vwap")
            if px is None or px != px or px <= 0:      # NaN-safe
                px = row.get("close")
            if px is None or px != px or px <= 0:
                continue
            px = float(px)
            # No historical NBBO exists. bid == ask == mid is the honest
            # encoding of "we do not know the width", not a claim of zero
            # spread -- SPREADS_AVAILABLE says so and backtest.py banners it.
            out[day] = {"bid": px, "ask": px, "mid": px,
                        "high": float(row.get("high") or px),
                        "low": float(row.get("low") or px),
                        "volume": float(row.get("volume") or 0)}
        return out
    return _cached(f"bars_{occ}_{start}_{end}", go)


def _bars_multi(occs: list, start: str, end: str) -> dict:
    """Batch version of _bars: {occ: {iso_date: quote}} in ONE request.

    This is the difference between a 15-minute backtest and a 12-hour one.
    Alpaca's bars endpoint accepts a symbol LIST, so a 3%-moneyness band that
    cost 82 sequential calls costs 1. Results are written into the same
    per-contract cache files _bars() reads, so the two are interchangeable and
    a partially-warm cache costs nothing extra.
    """
    need, out = [], {}
    for occ in occs:
        path = os.path.join(CACHE, f"bars_{occ}_{start}_{end}".replace("/", "_") + ".json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    out[occ] = json.load(f)
                continue
            except (OSError, ValueError):
                pass
        need.append(occ)
    if not need:
        return out

    op, _ = _clients()
    CHUNK = 200                      # keep URLs well inside any length limit
    fetched = {}
    for i in range(0, len(need), CHUNK):
        part = need[i:i + CHUNK]
        try:
            df = op.get_option_bars(OptionBarsRequest(
                symbol_or_symbols=part, timeframe=TimeFrame.Day,
                start=dt.date.fromisoformat(start),
                end=dt.date.fromisoformat(end) + dt.timedelta(days=1))).df
        except Exception as e:
            DATA_ERRORS.append((f"batch[{len(part)}]", str(e)[:80]))
            continue
        if df is None or len(df) == 0:
            continue
        for idx, row in df.iterrows():
            occ = idx[0] if isinstance(idx, tuple) else None
            ts = idx[-1] if isinstance(idx, tuple) else idx
            if occ is None:
                continue
            px = row.get("vwap")
            if px is None or px != px or px <= 0:
                px = row.get("close")
            if px is None or px != px or px <= 0:
                continue
            px = float(px)
            fetched.setdefault(occ, {})[str(ts)[:10]] = {
                "bid": px, "ask": px, "mid": px,
                "high": float(row.get("high") or px),
                "low": float(row.get("low") or px),
                "volume": float(row.get("volume") or 0)}

    # Cache every requested contract, including the ones with no bars --
    # "this contract never traded that day" is a real and stable answer.
    for occ in need:
        val = fetched.get(occ, {})
        path = os.path.join(CACHE, f"bars_{occ}_{start}_{end}".replace("/", "_") + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(val, f)
        os.replace(tmp, path)
        out[occ] = val
    return out


def day_snapshot(symbol: str, expiration: str, iso_day: str) -> dict:
    """{(strike, RIGHT): {iso_day: quote}} for one expiry on one day.

    ThetaData answered this in ONE request. Alpaca has no chain-history
    endpoint, so it costs one bars call per contract -- which is why the
    caller should restrict strikes to a moneyness band before asking, and why
    every result is cached."""
    cs = _contracts(symbol, expiration)
    got = _bars_multi([c["symbol"] for c in cs], iso_day, iso_day)
    out = {}
    for c in cs:
        b = got.get(c["symbol"], {})
        if iso_day in b:
            out[(c["strike"], c["right"])] = {iso_day: b[iso_day]}
    return out


def day_snapshot_near(symbol: str, expiration: str, iso_day: str,
                      spot: float, band: float = 0.12) -> dict:
    """day_snapshot restricted to strikes within `band` of spot.

    The bot targets ~1.5% OTM, so a 12% band covers every strike it could
    pick with room for the strike-distance study to widen. Without this a
    single SPY expiry is 318 contracts and the full sim is millions of calls."""
    lo, hi = spot * (1 - band), spot * (1 + band)
    cs = [c for c in _contracts(symbol, expiration) if lo <= c["strike"] <= hi]
    got = _bars_multi([c["symbol"] for c in cs], iso_day, iso_day)
    out = {}
    for c in cs:
        b = got.get(c["symbol"], {})
        if iso_day in b:
            out[(c["strike"], c["right"])] = {iso_day: b[iso_day]}
    return out


def contract_series(symbol: str, expiration: str, strike: float, right: str,
                    start: str) -> dict:
    """Full remaining life of ONE contract. Pulled only for entered trades."""
    rword = {"C": "CALL", "P": "PUT"}.get(right, right).upper()
    for c in _contracts(symbol, expiration):
        if abs(c["strike"] - strike) < 1e-9 and c["right"] == rword:
            return _bars(c["symbol"], start, expiration)
    DATA_ERRORS.append((f"{symbol} {expiration} {strike:g}{rword}", "no such contract"))
    return {}


def banner() -> str:
    return (
        "  DATA LAYER: Alpaca (historical option bars)\n"
        f"  mid source        : {MID_SOURCE}\n"
        f"  spreads available : {SPREADS_AVAILABLE}  <-- 4% spread screen CANNOT run\n"
        f"  assumed RT cost   : {ASSUMED_RT_COST:.1%}  (SPXBOT_BT_RT_COST)\n"
        f"  option data from  : {DATA_START}\n"
        "  => every P/L here is an UPPER BOUND. Re-run at 0%/2%/4% assumed\n"
        "     cost; a conclusion that only survives 0% is an artifact.\n")


if __name__ == "__main__":
    print(banner())
    sym = os.environ.get("BTD_SYM", "SPY")
    exps = [e for e in expirations(sym) if e >= "2026-05-01"][:3]
    print(f"  {sym}: {len(exps)} sample expiries -> {exps}")
    if exps:
        e = exps[0]
        ks = strikes(sym, e)
        oi = open_interest(sym, e)
        print(f"  {e}: {len(ks)} strikes  {min(ks):.0f}..{max(ks):.0f}")
        liq = sum(1 for v in oi.values() if v >= 250)
        print(f"       {liq} contracts with OI >= 250 (the live floor)")
