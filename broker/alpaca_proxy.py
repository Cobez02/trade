"""
Alpaca PAPER adapter — trades SPY/QQQ SHARES as a stand-in for MES/MNQ.

Why keep Alpaca at all: it is free, it already has your keys, and it lets the
futures logic forward-test for weeks on a live tape with real (simulated)
fills before you pay a prop firm. 1 "contract" = config.PROXY_SHARES shares
(50 SPY shares ~= 1 MES in dollar exposure). Alpaca does not offer futures,
so this adapter can never be the live path.

Uses SIP data if your key has Algo Trader Plus, otherwise IEX (set
SPXF_ALPACA_FEED=iex). Stop orders are real Alpaca stop orders that survive
this process dying — the same design principle as the options bot's
broker-side floor.
"""
from __future__ import annotations
import os
import datetime as dt

import pandas as pd

import config as C
from broker.base import Broker, Position, OrderResult
from data import sessions as S


class AlpacaProxyBroker(Broker):
    name = "alpaca_proxy"

    def __init__(self, shares_per_contract: int = C.PROXY_SHARES, feed: str | None = None):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        key = os.environ["ALPACA_API_KEY"]; sec = os.environ["ALPACA_SECRET_KEY"]
        self.trading = TradingClient(key, sec, paper=True)     # PAPER, always
        self.data = StockHistoricalDataClient(key, sec)
        self.k = shares_per_contract
        self.feed = (feed or os.environ.get("SPXF_ALPACA_FEED", "sip")).lower()

    def account(self) -> dict:
        a = self.trading.get_account()
        eq = float(a.equity); last = float(a.last_equity)
        return {"balance": eq, "day_pnl": eq - last, "equity": eq}

    def position(self, symbol) -> Position:
        try:
            p = self.trading.get_open_position(symbol)
        except Exception:
            return Position()
        q = int(float(p.qty)); q = q if p.side.value == "long" else -q
        return Position(q // self.k, float(p.avg_entry_price))

    def place_market(self, symbol, side, qty, tag) -> OrderResult:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        req = MarketOrderRequest(symbol=symbol, qty=qty * self.k,
                                 side=OrderSide.BUY if side > 0 else OrderSide.SELL,
                                 time_in_force=TimeInForce.DAY, client_order_id=f"SPXF-{tag}-{int(dt.datetime.now().timestamp())}"[:48])
        o = self.trading.submit_order(req)
        return OrderResult(str(o.id), True, str(o.status))

    def place_stop(self, symbol, side, qty, stop_px, tag) -> OrderResult:
        from alpaca.trading.requests import StopOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        req = StopOrderRequest(symbol=symbol, qty=qty * self.k, stop_price=round(stop_px, 2),
                               side=OrderSide.BUY if side > 0 else OrderSide.SELL,
                               time_in_force=TimeInForce.DAY, client_order_id=f"SPXF-stop-{tag}-{int(dt.datetime.now().timestamp())}"[:48])
        o = self.trading.submit_order(req)
        return OrderResult(str(o.id), True, str(o.status))

    def cancel_all(self, symbol) -> int:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        n = 0
        for o in self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])):
            try:
                self.trading.cancel_order_by_id(o.id); n += 1
            except Exception:
                pass
        return n

    def resting_stop_qty(self, symbol) -> int:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        q = 0
        for o in self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])):
            if str(o.order_type).lower().endswith("stop"):
                q += int(float(o.qty))
        return q // self.k

    def flatten(self, symbol) -> OrderResult:
        self.cancel_all(symbol)
        try:
            o = self.trading.close_position(symbol)
            return OrderResult(str(getattr(o, "id", "")), True, "closed")
        except Exception as e:
            return OrderResult("", "position does not exist" in str(e).lower(), str(e))

    def last_price(self, symbol) -> float:
        from alpaca.data.requests import StockLatestTradeRequest
        from alpaca.data.enums import DataFeed
        t = self.data.get_stock_latest_trade(StockLatestTradeRequest(
            symbol_or_symbols=symbol, feed=DataFeed.SIP if self.feed == "sip" else DataFeed.IEX))
        return float(t[symbol].price)

    def bars_today(self, symbol) -> pd.DataFrame:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.enums import DataFeed
        o, _, _, _ = S.session_bounds(S.now_ct().date())
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                               start=o.astimezone(dt.timezone.utc), feed=DataFeed.SIP if self.feed == "sip" else DataFeed.IEX)
        df = self.data.get_stock_bars(req).df
        if df is None or not len(df):
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index(level=0, drop=True)
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
        return df
