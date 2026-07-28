"""
SPX-Beater: autonomous options paper-trading engine (Alpaca paper account).

Design goals
------------
- Conservative $10k bankroll, options only, LONG defined-risk positions.
- Four independent "sleeves" (strategies), each with its own logical bankroll,
  so we can see which method actually beats the S&P 500:
      wsb   : Reddit / r/wallstreetbets sentiment
      news  : news-catalyst driven
      tech  : technical indicators (RSI / MACD / trend)
      flow  : "copy the smart money" unusual options activity
- Alpaca holds the real (paper) positions & cash. We keep a small state file
  that TAGS each option position with the sleeve that opened it and tracks the
  SPX benchmark, so per-sleeve P&L and "vs S&P" can be reported.

The engine is deliberately robust to the free-tier data feed: option selection
is driven by moneyness / days-to-expiry / open-interest, NOT by greeks, which
may be missing on free accounts.
"""

from __future__ import annotations
import os, json, math, datetime as dt, traceback
from dataclasses import dataclass, field
from typing import Optional

# ---- Alpaca SDK -------------------------------------------------------------
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, StopOrderRequest,
    GetOptionContractsRequest, GetOrdersRequest,
)
from alpaca.trading.enums import (
    OrderSide, TimeInForce, ContractType, AssetStatus, QueryOrderStatus, OrderStatus,
)
import time, re as _re
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, OptionLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed, OptionsFeed   # free tier: IEX stocks / indicative options

import pandas as pd
import numpy as np

# ============================================================================
# CONFIG
# ============================================================================

STATE_PATH = os.environ.get("SPXBOT_STATE", os.path.join(os.path.dirname(__file__), "state.json"))
BENCHMARK_SYMBOL = "SPY"          # proxy for S&P 500 buy-and-hold
START_EQUITY = 10_000.0

SLEEVES = ["wsb", "news", "tech", "flow"]
SLEEVE_ALLOCATION = START_EQUITY / len(SLEEVES)   # ~$2,500 logical bankroll each

# Risk controls. Conservative in DOLLARS (small size), but short-dated + tighter
# exits so trades cycle fast and feed the learner quickly. Several knobs are
# env-overridable so the recursive-learning step can retune them run-to-run.
def _envf(name, default):
    try: return float(os.environ.get(name, default))
    except Exception: return default

# A $150/trade cap sounds conservative but adverse-selects: it prices out every
# liquid quality contract (SPY/NVDA/GOOGL run $180-340) and leaves only sub-$1
# wide-spread junk -- which is exactly what lost money. Raising the per-trade cap
# while cutting slots per sleeve keeps total deployment conservative:
#   new  $350 x 2 x 4 sleeves = $2,800 of $10k (28%)
#   old  $150 x 3 x 4 sleeves = $1,800 of $10k (18%)
MAX_PREMIUM_PER_TRADE = _envf("SPXBOT_MAX_PREM", 350.0)  # max $ risked per position
MAX_OPEN_PER_SLEEVE   = int(_envf("SPXBOT_MAX_OPEN", 2)) # cap concurrent positions per sleeve
MIN_OPEN_INTEREST     = 100        # liquidity filter
MAX_SPREAD_PCT        = _envf("SPXBOT_MAX_SPREAD", 0.15)  # skip if bid/ask spread wider than this
TARGET_DTE_MIN = int(_envf("SPXBOT_DTE_MIN", 3))
TARGET_DTE_MAX = int(_envf("SPXBOT_DTE_MAX", 12))        # short-dated: high gamma, fast feedback
TARGET_OTM_PCT = _envf("SPXBOT_OTM", 0.015)              # ~1.5% OTM (near the money -> moves)

# Exit rules (tighter -> trades realize P&L intraday, generating lessons same day)
TAKE_PROFIT_PCT = _envf("SPXBOT_TP", 0.45)   # +45% -> take profit
STOP_LOSS_PCT   = _envf("SPXBOT_SL", -0.30)  # -30% -> cut loss
TIME_STOP_DTE   = int(_envf("SPXBOT_TIME_STOP", 1))  # close at <= 1 DTE


# ============================================================================
# BROKER WRAPPER
# ============================================================================

class Broker:
    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.trading = TradingClient(api_key, secret_key, paper=paper)
        self.stock_data = StockHistoricalDataClient(api_key, secret_key)
        self.opt_data = OptionHistoricalDataClient(api_key, secret_key)

    # ---- account / market state -------------------------------------------
    def account(self):
        return self.trading.get_account()

    def clock(self):
        return self.trading.get_clock()

    def positions(self):
        try:
            return self.trading.get_all_positions()
        except Exception:
            return []

    def stock_price(self, symbol: str) -> Optional[float]:
        """Latest daily close for an underlying (robust; uses recent bars)."""
        try:
            end = dt.datetime.now(dt.timezone.utc)
            start = end - dt.timedelta(days=15)
            req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                                   start=start, end=end, feed=DataFeed.IEX)
            bars = self.stock_data.get_stock_bars(req).df
            if bars is None or len(bars) == 0:
                return None
            return float(bars["close"].iloc[-1])
        except Exception:
            return None

    def daily_bars(self, symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
        try:
            end = dt.datetime.now(dt.timezone.utc)
            start = end - dt.timedelta(days=days * 2)  # calendar buffer for weekends
            req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                                   start=start, end=end, feed=DataFeed.IEX)
            df = self.stock_data.get_stock_bars(req).df
            if df is None or len(df) == 0:
                return None
            if isinstance(df.index, pd.MultiIndex):
                df = df.reset_index(level=0, drop=True)
            return df
        except Exception:
            return None

    # ---- option chain ------------------------------------------------------
    def find_contract(self, underlying: str, direction: str, spot: float):
        """Pick a liquid, conservative contract in the target DTE/moneyness window."""
        ctype = ContractType.CALL if direction == "bull" else ContractType.PUT
        today = dt.date.today()
        exp_lo = (today + dt.timedelta(days=TARGET_DTE_MIN)).isoformat()
        exp_hi = (today + dt.timedelta(days=TARGET_DTE_MAX)).isoformat()
        if direction == "bull":
            target_strike = spot * (1 + TARGET_OTM_PCT)
            slo, shi = spot * 0.99, spot * 1.10
        else:
            target_strike = spot * (1 - TARGET_OTM_PCT)
            slo, shi = spot * 0.90, spot * 1.01
        try:
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying],
                status=AssetStatus.ACTIVE,
                expiration_date_gte=exp_lo,
                expiration_date_lte=exp_hi,
                type=ctype,
                strike_price_gte=str(round(slo, 2)),
                strike_price_lte=str(round(shi, 2)),
                limit=200,
            )
            resp = self.trading.get_option_contracts(req)
            contracts = getattr(resp, "option_contracts", None) or []
        except Exception:
            return None
        # liquidity filter + nearest to target strike
        best, best_dist = None, 1e18
        for c in contracts:
            try:
                oi = int(c.open_interest) if c.open_interest is not None else 0
            except Exception:
                oi = 0
            if oi < MIN_OPEN_INTEREST:
                continue
            strike = float(c.strike_price)
            dist = abs(strike - target_strike)
            if dist < best_dist:
                best, best_dist = c, dist
        # fallback: ignore OI filter if nothing liquid enough
        if best is None and contracts:
            for c in contracts:
                strike = float(c.strike_price)
                dist = abs(strike - target_strike)
                if dist < best_dist:
                    best, best_dist = c, dist
        return best

    def option_ask(self, occ_symbol: str) -> Optional[float]:
        try:
            q = self.opt_data.get_option_latest_quote(
                OptionLatestQuoteRequest(symbol_or_symbols=occ_symbol,
                                         feed=OptionsFeed.INDICATIVE))
            quote = q.get(occ_symbol)
            if quote is None:
                return None
            ask = float(quote.ask_price or 0)
            bid = float(quote.bid_price or 0)
            if ask > 0 and bid > 0:
                return round((ask + bid) / 2, 2)   # mid
            return ask or bid or None
        except Exception:
            return None

    def option_quote(self, occ_symbol: str):
        """Return {bid, ask, mid, spread_pct} or None."""
        try:
            q = self.opt_data.get_option_latest_quote(
                OptionLatestQuoteRequest(symbol_or_symbols=occ_symbol,
                                         feed=OptionsFeed.INDICATIVE))
            quote = q.get(occ_symbol)
            if quote is None:
                return None
            bid = float(quote.bid_price or 0); ask = float(quote.ask_price or 0)
            if bid <= 0 or ask <= 0:
                return None
            mid = (bid + ask) / 2
            return {"bid": bid, "ask": ask, "mid": round(mid, 2),
                    "spread_pct": round((ask - bid) / mid, 3) if mid else None}
        except Exception:
            return None

    def indicators(self, underlying: str):
        """RSI / MACD histogram / trend snapshot for feature logging."""
        df = self.daily_bars(underlying, days=120)
        if df is None or len(df) < 55:
            return {}
        close = df["close"]
        h_now, h_prev = macd_signal(close)
        return {"rsi": round(rsi(close), 1),
                "macd_hist": round(h_now, 3),
                "macd_rising": bool(h_now > h_prev),
                "trend_up": trend_up(close)}

    # ---- orders ------------------------------------------------------------
    def buy_to_open(self, occ_symbol: str, qty: int, tag: str = "SPXB-unknown",
                    limit_price: Optional[float] = None):
        # `tag` encodes the sleeve AND the entry-features fingerprint, so the whole
        # learning journal can be rebuilt from Alpaca's order history alone (no
        # external state store needed). Format built by main.build_tag(); we append
        # an epoch-ms suffix for the uniqueness Alpaca requires.
        coid = f"{tag}-{int(time.time()*1000)}"
        if limit_price:   # capped marketable limit -> avoids pathological fills
            order = LimitOrderRequest(symbol=occ_symbol, qty=qty, side=OrderSide.BUY,
                                      time_in_force=TimeInForce.DAY, client_order_id=coid,
                                      limit_price=round(float(limit_price), 2))
        else:
            order = MarketOrderRequest(symbol=occ_symbol, qty=qty, side=OrderSide.BUY,
                                       time_in_force=TimeInForce.DAY, client_order_id=coid)
        return self.trading.submit_order(order)

    def sell_to_close(self, occ_symbol: str, qty: int):
        order = MarketOrderRequest(symbol=occ_symbol, qty=qty, side=OrderSide.SELL,
                                   time_in_force=TimeInForce.DAY)
        return self.trading.submit_order(order)

    def cancel(self, order_id):
        """Pull a working order (e.g. a limit that has gone stale as the spread widened)."""
        return self.trading.cancel_order_by_id(order_id)

    # -- protective stops ----------------------------------------------------
    # The prefix is deliberately NOT "SPXB-": sleeve_map_from_orders() reads any
    # SPXB-tagged closed order and would file a filled stop under a sleeve named
    # "stop", quietly corrupting the attribution the learner runs on.
    STOP_TAG = "PSTOP"

    def rest_stop(self, occ_symbol: str, qty: int, stop_price: float):
        """Place a resting protective stop.

        This is the only piece of risk control that keeps working when nothing
        of ours is running — no cron, no watcher, no container. Alpaca evaluates
        it continuously on its own side. Options accept `stop` but reject
        `trailing_stop` and OCO, so a trail has to be emulated by replacing it.
        """
        return self.trading.submit_order(StopOrderRequest(
            symbol=occ_symbol, qty=qty, side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            stop_price=round(max(float(stop_price), 0.01), 2),
            client_order_id=f"{self.STOP_TAG}-{int(time.time()*1000)}"))

    def open_sell_orders(self, occ_symbol: str = None):
        """Working SELL orders — protective stops, plus anything else resting.

        A resting sell reserves the contracts, so a close order placed while one
        is live is rejected for insufficient quantity. Anything that intends to
        sell must clear these first.
        """
        out = []
        for o in self.open_orders():
            if "SELL" not in str(getattr(o, "side", "")).upper():
                continue
            if occ_symbol and o.symbol != occ_symbol:
                continue
            out.append(o)
        return out

    def closed_orders(self, limit: int = 500):
        """All filled/closed orders (both sides), newest first."""
        try:
            req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=limit)
            return list(self.trading.get_orders(req))
        except Exception:
            return []

    def open_orders(self, limit: int = 200):
        """Working (unfilled) orders — used to avoid re-submitting the same name."""
        try:
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=limit)
            return list(self.trading.get_orders(req))
        except Exception:
            return []

    def sleeve_map_from_orders(self) -> dict:
        """Rebuild {occ_symbol: sleeve} from filled BUY orders' client_order_id."""
        out = {}
        for o in self.closed_orders():
            coid = getattr(o, "client_order_id", "") or ""
            if coid.startswith("SPXB-") and o.symbol:
                parts = coid.split("-")
                if len(parts) > 1:
                    out[o.symbol] = parts[1]
        return out


# ============================================================================
# STATE
# ============================================================================

def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {
        "started": None,
        "start_equity": START_EQUITY,
        "benchmark_start_price": None,
        "positions": {},          # occ_symbol -> position dict (our sleeve tag + entry)
        "closed": [],             # list of closed trade dicts
        "equity_history": [],     # [{date, equity, benchmark_equity}]
        "run_log": [],            # [{date, notes}]
    }

def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ============================================================================
# INDICATORS (for the 'tech' sleeve)
# ============================================================================

def rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = -delta.clip(upper=0).rolling(period).mean()
    rs = up / down.replace(0, np.nan)
    val = 100 - (100 / (1 + rs))
    return float(val.iloc[-1]) if not math.isnan(val.iloc[-1]) else 50.0

def macd_signal(series: pd.Series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    return float(hist.iloc[-1]), float(hist.iloc[-2]) if len(hist) > 1 else 0.0

def trend_up(series: pd.Series) -> bool:
    sma20 = series.rolling(20).mean().iloc[-1]
    sma50 = series.rolling(50).mean().iloc[-1]
    return bool(series.iloc[-1] > sma20 > sma50)
