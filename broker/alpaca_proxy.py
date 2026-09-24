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
import os, time
import datetime as dt

import pandas as pd

import config as C
from broker.base import Broker, Position, OrderResult
from data import sessions as S


def signed_qty(qty, side) -> int:
    """Alpaca reports SHORT positions with a NEGATIVE qty and side='short'. Negating a
    negative made shorts look long (paper lab, 2026-09-15). Sign comes from `side` only."""
    q = abs(int(float(qty)))
    return q if str(side).lower().endswith("long") else -q


def symbol_pnl(fills, qty, mark, prev_close) -> float:
    """Today's P&L for ONE symbol, independent of anything else in the account.

    fills      [(signed_shares, price)] filled today (+ bought, - sold)
    qty        signed shares held now; mark = its current price
    prev_close the symbol's previous close, used only for shares carried in from yesterday

    cash flow of today's fills + value of what is held now - yesterday's value of what was
    carried in. The account-wide day P&L used before mixed the overnight sleeve's QQQM into
    the intraday kill switch and records (9/24 logged +$375 on a day with zero entries).
    """
    cash = -sum(q * px for q, px in fills)
    net_today = sum(q for q, _ in fills)
    carried = qty - net_today
    return cash + qty * mark - carried * (prev_close or mark)


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
        q = signed_qty(p.qty, getattr(p.side, "value", str(p.side)))
        return Position(int(q / self.k), float(p.avg_entry_price))

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

    def _raw_qty(self, symbol) -> int:
        try:
            p = self.trading.get_open_position(symbol)
        except Exception:
            return 0
        return signed_qty(p.qty, getattr(p.side, "value", str(p.side)))

    def _open_orders(self, symbol) -> int:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        return len(self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])))

    def flatten(self, symbol) -> OrderResult:
        """Close the position and CONFIRM it is closed.

        2026-09-23: cancel_all() then close_position() in the same instant failed with
        "insufficient qty available ... held_for_orders 60": Alpaca releases shares held by a
        cancelled stop asynchronously. The failure was logged, nothing retried, and a short was
        carried overnight -- an account-ending event on a day-only prop rulebook. Now: cancel,
        wait until no order is open, close, verify flat, and retry with backoff. The last resort
        is a plain market order for the exact opposite quantity.
        """
        last = ""
        for attempt in range(6):
            q = self._raw_qty(symbol)
            if q == 0:
                return OrderResult("", True, "flat" if attempt == 0 else f"flat after {attempt} retries")
            self.cancel_all(symbol)
            for _ in range(24):                       # up to ~6 s for the cancels to release shares
                try:
                    if self._open_orders(symbol) == 0: break
                except Exception:
                    break
                time.sleep(0.25)
            try:
                if attempt < 4:
                    self.trading.close_position(symbol)
                else:
                    from alpaca.trading.requests import MarketOrderRequest
                    from alpaca.trading.enums import OrderSide, TimeInForce
                    self.trading.submit_order(MarketOrderRequest(symbol=symbol, qty=abs(q),
                        side=OrderSide.SELL if q > 0 else OrderSide.BUY, time_in_force=TimeInForce.DAY))
            except Exception as e:
                last = str(e)[:200]
            for _ in range(12):                       # up to ~6 s for the fill
                time.sleep(0.5)
                if self._raw_qty(symbol) == 0:
                    return OrderResult("", True, f"closed (attempt {attempt + 1})")
            time.sleep(1 + attempt)
        return OrderResult("", self._raw_qty(symbol) == 0, "FLATTEN NOT CONFIRMED: " + last)

    def _prev_close(self, symbol) -> float:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed
        o, _, _, _ = S.session_bounds(S.now_ct().date())
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                               start=(o - dt.timedelta(days=10)).astimezone(dt.timezone.utc),
                               end=(o - dt.timedelta(hours=1)).astimezone(dt.timezone.utc),
                               feed=DataFeed.SIP if self.feed == "sip" else DataFeed.IEX)
        df = self.data.get_stock_bars(req).df
        return float(df["close"].iloc[-1]) if df is not None and len(df) else 0.0

    def symbol_day_pnl(self, symbol) -> float:
        """This symbol's P&L since today's session open, in dollars (see symbol_pnl)."""
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        o, _, _, _ = S.session_bounds(S.now_ct().date())
        orders = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[symbol],
                                         after=(o - dt.timedelta(hours=5)).astimezone(dt.timezone.utc), limit=500))
        fills = []
        for od in orders:
            fq = float(od.filled_qty or 0)
            if fq > 0 and od.filled_avg_price:
                side = 1 if str(getattr(od.side, "value", od.side)).lower().endswith("buy") else -1
                fills.append((side * fq, float(od.filled_avg_price)))
        qty = self._raw_qty(symbol)
        mark = self.last_price(symbol) if qty else 0.0
        carried = qty - sum(q for q, _ in fills)
        prev = self._prev_close(symbol) if carried else 0.0
        return symbol_pnl(fills, qty, mark, prev)

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
