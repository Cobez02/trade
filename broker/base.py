"""
Broker adapters. One interface, four implementations:

  SimBroker        in-memory, deterministic — the dry-run and integration tests
  AlpacaProxyBroker Alpaca PAPER trading SPY/QQQ shares as a stand-in for MES/MNQ
                   (Alpaca has no futures; this is the free forward-test lab)
  TradovateBroker  MyFundedFutures / Tradeify / Take Profit Trader accounts     [UNTESTED]
  ProjectXBroker   TopstepX gateway                                             [UNTESTED]

The runner only ever calls the methods on Broker. "qty" is always in
CONTRACTS; the equity proxy multiplies by config.PROXY_SHARES internally.

The two live futures adapters were written from the vendors' public API docs
without an account to test against. They are skeletons with the right shape,
not certified code: run them against a demo/eval account with qty=1 and a
human watching before trusting them. Both tag orders as automated, which CME
Rule 575 / the firms' fair-play policies expect.
"""
from __future__ import annotations
import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

import config as C


@dataclass
class Position:
    qty: int = 0            # signed contracts
    avg_px: float = 0.0


@dataclass
class OrderResult:
    order_id: str
    ok: bool
    detail: str = ""


class Broker(ABC):
    name = "abstract"

    @abstractmethod
    def account(self) -> dict:
        """{'balance': float, 'day_pnl': float, 'equity': float}"""

    @abstractmethod
    def position(self, symbol: str) -> Position: ...

    @abstractmethod
    def place_market(self, symbol: str, side: int, qty: int, tag: str) -> OrderResult: ...

    @abstractmethod
    def place_stop(self, symbol: str, side: int, qty: int, stop_px: float, tag: str) -> OrderResult:
        """Resting stop-market order to EXIT (side = -position sign)."""

    @abstractmethod
    def cancel_all(self, symbol: str) -> int: ...

    @abstractmethod
    def resting_stop_qty(self, symbol: str) -> int:
        """Total quantity of resting stop orders on the symbol (0 = no floor)."""

    @abstractmethod
    def flatten(self, symbol: str) -> OrderResult: ...

    @abstractmethod
    def last_price(self, symbol: str) -> float: ...

    @abstractmethod
    def bars_today(self, symbol: str) -> pd.DataFrame:
        """1-minute bars (UTC index) since the session open, columns open/high/low/close/volume."""

    def front_month(self, root: str) -> str:
        """Tradable contract symbol for a root (MES -> MESZ6). Default: caller passes it."""
        return root


# ---------------------------------------------------------------------------
class SimBroker(Broker):
    """Deterministic simulator driven by a 1-minute bar feed you push into it.

    Fills: market at the current bar's close +/- slippage; stops trigger on the
    NEXT bars' high/low (checked in `advance`). Good enough to exercise the
    runner end to end without a network.
    """
    name = "sim"

    def __init__(self, instrument=C.INSTRUMENT):
        self.inst = instrument
        self.pos: dict[str, Position] = {}
        self.stops: dict[str, tuple[int, int, float, str]] = {}   # symbol -> (side, qty, px, tag)
        self.realized = 0.0
        self.bars: dict[str, pd.DataFrame] = {}
        self.log: list[str] = []
        self._oid = 0

    def feed(self, symbol: str, bars_1m: pd.DataFrame):
        """Set the full day's bars; `advance(i)` reveals them one at a time."""
        self._full = bars_1m; self.bars[symbol] = bars_1m.iloc[0:0]; self._i = 0

    def advance(self, symbol: str) -> bool:
        """Reveal the next bar; check resting stops against it."""
        if self._i >= len(self._full):
            return False
        bar = self._full.iloc[self._i]
        self.bars[symbol] = self._full.iloc[: self._i + 1]
        self._i += 1
        st = self.stops.get(symbol)
        if st:
            side, qty, px, tag = st
            hit = (side < 0 and bar["low"] <= px) or (side > 0 and bar["high"] >= px)
            if hit:
                gapped = (side < 0 and bar["open"] <= px) or (side > 0 and bar["open"] >= px)
                fill = bar["open"] if gapped else px
                self._fill(symbol, side, qty, float(fill), f"stop:{tag}")
                self.stops.pop(symbol, None)
        return True

    def _fill(self, symbol, side, qty, px, tag):
        px = px + side * self.inst.slippage_ticks * self.inst.tick_size
        self.log.append(f"fill {symbol} {side:+d}x{qty} @ {px:.2f} [{tag}]")
        p = self.pos.setdefault(symbol, Position())
        if p.qty and (p.qty > 0) != (side > 0):          # closing
            close_q = min(qty, abs(p.qty))
            self.realized += (px - p.avg_px) * (1 if p.qty > 0 else -1) * close_q * self.inst.point_value
            self.realized -= close_q * self.inst.commission_side
            p.qty += side * close_q
            qty -= close_q
            if p.qty == 0:
                p.avg_px = 0.0
        if qty:
            new_q = p.qty + side * qty
            p.avg_px = (p.avg_px * abs(p.qty) + px * qty) / (abs(p.qty) + qty) if p.qty else px
            p.qty = new_q
            self.realized -= qty * self.inst.commission_side

    def account(self) -> dict:
        unreal = 0.0
        for s, p in self.pos.items():
            if p.qty and s in self.bars and len(self.bars[s]):
                unreal += (float(self.bars[s]["close"].iloc[-1]) - p.avg_px) * p.qty * self.inst.point_value
        return {"balance": self.realized, "day_pnl": self.realized + unreal, "equity": self.realized + unreal}

    def position(self, symbol):
        p = self.pos.get(symbol, Position())
        return Position(p.qty, p.avg_px)          # copy: callers must not alias broker state

    def resting_stop_qty(self, symbol):
        st = self.stops.get(symbol)
        return st[1] if st else 0

    def place_market(self, symbol, side, qty, tag):
        self._oid += 1
        self._fill(symbol, side, qty, self.last_price(symbol), tag)
        return OrderResult(str(self._oid), True)

    def place_stop(self, symbol, side, qty, stop_px, tag):
        self._oid += 1
        self.stops[symbol] = (side, qty, stop_px, tag)
        return OrderResult(str(self._oid), True)

    def cancel_all(self, symbol):
        return 1 if self.stops.pop(symbol, None) else 0

    def flatten(self, symbol):
        p = self.position(symbol)
        self.cancel_all(symbol)
        if p.qty:
            return self.place_market(symbol, -1 if p.qty > 0 else 1, abs(p.qty), "flatten")
        return OrderResult("", True, "flat")

    def last_price(self, symbol):
        b = self.bars.get(symbol)
        return float(b["close"].iloc[-1]) if b is not None and len(b) else 0.0

    def bars_today(self, symbol):
        return self.bars.get(symbol, pd.DataFrame())
