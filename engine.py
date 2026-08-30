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
    GetOptionContractsRequest, GetOrdersRequest, OptionLegRequest,
)
from alpaca.trading.enums import (
    OrderSide, TimeInForce, ContractType, AssetStatus, QueryOrderStatus, OrderStatus,
    OrderClass, PositionIntent,
)
import time, re as _re
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, OptionLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed, OptionsFeed

# ---------------------------------------------------------------------------
# DATA FEED SELECTION -- this is a correctness knob, not a preference.
#
# Until 2026-08-30 both feeds were hardcoded to the free tier and the whole
# five-week record was built on them:
#
#   OptionsFeed.INDICATIVE -- NOT OPRA. Alpaca staff describe it as "a
#     derivative of the original OPRA feed: the quotes are not actual OPRA
#     quotes, they're just 'indicative' derivatives", provided "to reduce the
#     cost burden during algo design". Every spread screen, vol-edge gate and
#     entry price in the first 85 trades was computed against synthetic quotes.
#
#   DataFeed.IEX -- IEX is a single venue carrying a low single-digit share of
#     consolidated volume. `daily_bars()` feeds RSI, MACD and trend, so the
#     entire tech sleeve has been reading indicators off a partial tape.
#
# With an Algo Trader Plus subscription both upgrade: OPRA for options, SIP
# (full consolidated tape) for equities. The subscription alone changes
# nothing -- the code has to ask. Defaults below are the PAID tier; set
# SPXBOT_OPT_FEED=indicative / SPXBOT_STOCK_FEED=iex to fall back.
_OPT_FEED_NAME = os.environ.get("SPXBOT_OPT_FEED", "opra").strip().lower()
_STK_FEED_NAME = os.environ.get("SPXBOT_STOCK_FEED", "sip").strip().lower()
OPT_FEED = OptionsFeed.OPRA if _OPT_FEED_NAME == "opra" else OptionsFeed.INDICATIVE
STOCK_FEED = DataFeed.SIP if _STK_FEED_NAME == "sip" else DataFeed.IEX

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

# Sleeves that no longer OPEN positions. They stay in SLEEVES because exit
# management, journal attribution, learning and the dashboard all key on the
# sleeve name, and a position opened under a sleeve must remain manageable and
# attributable for as long as it (or its history) exists.
#
# Why these two, specifically — the research phase's two clearest verdicts:
#
#   flow  RETIRED. The sleeve reads call/put OPEN INTEREST skew as "smart money
#         positioning," but open interest has no trade direction in it: every
#         contract has a buyer and a seller, and the literature that does find
#         signal in options activity (Pan & Poteshman 2006) needs signed,
#         volume-based put-call ratios from proprietary open/close data — an
#         input this bot cannot get from its feed. What it is actually reading
#         is mostly hedging structure. No study supports the deployed signal.
#
#   wsb   RETIRED as an ENTRY source, retained as a defensive VETO
#         (strategies.crowd_veto). On the entry side the sleeve buys what the
#         crowd is loudest about, which the retail-options literature
#         identifies as the single most reliably overpriced corner of the
#         market: attention-driven lottery demand inflates exactly those
#         premia (Han & Kumar; Boyer & Vorkink ex-ante skewness). The veto
#         keeps the feed's one usable property — it knows where the crowd is —
#         and only ever BLOCKS a buy on a crowded name, never places or
#         inverts one.
#
# Retiring a sleeve retires its capital. SLEEVE_ALLOCATION deliberately still
# divides by len(SLEEVES): the verdict on these sleeves is "this class of trade
# is negative-EV," which is not an argument that the survivors deserve double
# stakes — at any Kelly fraction, a doubtful edge argues for LESS deployment,
# not redistribution. Flagged in the strategy spec as an owner decision.
RETIRED_SLEEVES = {"wsb", "flow"}
ACTIVE_SLEEVES = [s for s in SLEEVES if s not in RETIRED_SLEEVES]

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
MAX_PREMIUM_PER_TRADE = _envf("SPXBOT_MAX_PREM", 600.0)  # max $ risked per position
# 350 -> 600 on 2026-07-30, Connor's call after the TOO-RICH postmortem: 6% of
# the $10k bankroll per trade. Removing the cap entirely was considered and
# rejected — the sleeve allocation alone would permit 25% of the bankroll on
# one contract, and a 3-stop cluster (which happened THIS week) would cost
# 15-25%% of the account. The cap is the survivability governor.
MAX_OPEN_PER_SLEEVE   = int(_envf("SPXBOT_MAX_OPEN", 2)) # cap concurrent positions per sleeve
# Liquidity. Raised from 100: open interest is the cheapest available proxy for
# the spread we will actually pay, and the thin-OI tail is where Bryzgalova et
# al. measure a 28.4% effective spread on deep-OTM retail flow.
MIN_OPEN_INTEREST     = int(_envf("SPXBOT_MIN_OI", 250))

# THE SINGLE MOST EXPENSIVE NUMBER IN THIS FILE. It shipped at 0.15.
#
# The round-trip identity (research note 07 §H) is
#     RT = S(2 - c_e - c_x) / (2 + S(1 - c_e)) + fees/premium
# At S = 15% with a realistic capture profile that is 10.93% of premium, per
# round trip. The best bias-corrected option anomaly in the published
# literature is about 0.5% per month at Sharpe ~0.5 (Duarte, Jones, Khorram &
# Mo). A 15% gate therefore admitted trades that had to be held ~22 months at
# the best documented edge simply to break even on execution.
#
# At 4% the same identity gives 3.05%, and the hurdle drops to ~6.1 months.
# That is still a hard game. It is no longer an arithmetically impossible one.
#
# Caveat that must not be lost: Alpaca's free options feed is INDICATIVE, not
# OPRA. This gate is applied to an indicative quote, so it is a proxy for the
# NBBO spread and not a measurement of it.
MAX_SPREAD_PCT        = _envf("SPXBOT_MAX_SPREAD", 0.04)

# Floor of 2 enforced downstream by screens.MIN_DTE: below ~2 DTE vega is so
# small that a correct volatility view cannot pay for the spread, which turns
# the trade into a pure direction bet with a fee attached. 0DTE retail fills run
# ~4.7pp worse than the already-negative retail average.
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
                                   start=start, end=end, feed=STOCK_FEED)
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
                                   start=start, end=end, feed=STOCK_FEED)
            df = self.stock_data.get_stock_bars(req).df
            if df is None or len(df) == 0:
                return None
            if isinstance(df.index, pd.MultiIndex):
                df = df.reset_index(level=0, drop=True)
            return df
        except Exception:
            return None

    # ---- option chain ------------------------------------------------------
    def find_contracts(self, underlying: str, direction: str, spot: float,
                       n: int = 3):
        """Up to `n` liquid contracts in the target DTE/moneyness window,
        ranked by strike distance to target (ties toward the lower strike).

        Returning several candidates instead of one exists because of the
        day-2 drought post-mortem: each signal was judged on exactly ONE
        strike's quote at ONE moment, on an indicative (non-NBBO) feed whose
        spreads flutter. Adjacent strikes on the same liquid name routinely
        quote very differently at any instant; letting the entry path compare
        2-3 and take the tightest is a measurement fix, not a discipline
        change — every candidate still clears the same OI floor and the same
        spread/premium/skew/lottery gates."""
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
        # liquidity filter + rank by distance to target strike
        passing = []
        for c in contracts:
            try:
                oi = int(c.open_interest) if c.open_interest is not None else 0
            except Exception:
                oi = 0
            if oi < MIN_OPEN_INTEREST:
                continue
            strike = float(c.strike_price)
            passing.append((abs(strike - target_strike), strike, c))
        # NO FALLBACK. This function used to re-scan ignoring MIN_OPEN_INTEREST
        # whenever the liquidity filter emptied the list, which inverted its own
        # purpose: the filter fired precisely when the chain was illiquid, and
        # the fallback then guaranteed a trade in the least liquid contract
        # available. That is adverse selection by construction, and it is the
        # single most expensive habit in the retail options literature — the
        # measured round-trip cost is 10.93% of premium at a 15% spread against
        # a best-case forecasting edge worth well under 1% per trade.
        #
        # "No liquid contract exists right now" is a correct and useful answer.
        # Returning None here costs one skipped signal; the fallback cost real
        # money on every signal it rescued.
        passing.sort(key=lambda t: (t[0], t[1]))
        return [c for _, _, c in passing[:n]]

    def find_contract(self, underlying: str, direction: str, spot: float):
        """Single nearest liquid contract (back-compat wrapper)."""
        cands = self.find_contracts(underlying, direction, spot, n=1)
        return cands[0] if cands else None

    def option_mid(self, occ_symbol: str) -> Optional[float]:
        """Quote MIDPOINT, or the single live side if only one is quoted.

        Named for what it returns. It was previously called `option_ask` while
        returning the mid, and main.py bound the result to a variable named
        `ask` and priced marketable orders off it — so every fallback fill was
        priced half a spread too tight and silently missed. At the old 15%
        spread ceiling that is a 7.5%-of-premium mispricing on the fallback
        path. Use `option_quote()` when you need the two sides separately;
        this is only a last-resort scalar.
        """
        try:
            q = self.opt_data.get_option_latest_quote(
                OptionLatestQuoteRequest(symbol_or_symbols=occ_symbol,
                                         feed=OPT_FEED))
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

    # Back-compat alias. Deliberately points at the same (mid-returning) code so
    # no caller silently changes behaviour; the name is the thing that was wrong.
    option_ask = option_mid

    def option_quote(self, occ_symbol: str):
        """Return {bid, ask, mid, spread_pct} or None."""
        try:
            q = self.opt_data.get_option_latest_quote(
                OptionLatestQuoteRequest(symbol_or_symbols=occ_symbol,
                                         feed=OPT_FEED))
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

    # ---- fill audit --------------------------------------------------------
    # Every order the bot places funnels through buy_to_open / sell_to_close,
    # so snapshotting the NBBO here covers the hourly runner, the tick watcher
    # and the EOD flatten with one hook instead of three.
    #
    # What this exists to answer: the paper engine models no market impact, no
    # latency slippage and no queue position, and does not check order size
    # against available liquidity ("you can submit and receive a fill for an
    # order that is much larger than the actual available liquidity" --
    # Alpaca's own paper-trading docs). So the five-week P&L is a record of
    # what a simulator paid, not of what a market would have charged. The
    # measured edge is $13.53/trade; one round trip at the 4% spread cap is
    # ~$20. Until fill quality is measured against real OPRA quotes, the sign
    # of the edge is unknown.
    #
    # Nothing here can affect an order. It records and returns.
    FILL_AUDIT_MAX = 2000

    def _audit_quote(self, occ_symbol: str, side: str, qty: int,
                     limit_price: Optional[float] = None):
        """Snapshot the NBBO immediately before submission. Never raises."""
        try:
            q = self.option_quote(occ_symbol)
            if not q:
                return None
            return {"symbol": occ_symbol, "side": side, "qty": int(qty),
                    "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "feed": OPT_FEED.value,
                    "bid": q["bid"], "ask": q["ask"], "mid": q["mid"],
                    "spread_pct": q.get("spread_pct"),
                    "limit": round(float(limit_price), 2) if limit_price else None}
        except Exception:
            return None

    # ---- orders ------------------------------------------------------------
    def buy_to_open(self, occ_symbol: str, qty: int, tag: str = "SPXB-unknown",
                    limit_price: Optional[float] = None):
        # `tag` encodes the sleeve AND the entry-features fingerprint, so the whole
        # learning journal can be rebuilt from Alpaca's order history alone (no
        # external state store needed). Format built by main.build_tag(); we append
        # an epoch-ms suffix for the uniqueness Alpaca requires.
        coid = f"{tag}-{int(time.time()*1000)}"
        self.last_audit = self._audit_quote(occ_symbol, "buy", qty, limit_price)
        if limit_price:   # capped marketable limit -> avoids pathological fills
            order = LimitOrderRequest(symbol=occ_symbol, qty=qty, side=OrderSide.BUY,
                                      time_in_force=TimeInForce.DAY, client_order_id=coid,
                                      limit_price=round(float(limit_price), 2))
        else:
            order = MarketOrderRequest(symbol=occ_symbol, qty=qty, side=OrderSide.BUY,
                                       time_in_force=TimeInForce.DAY, client_order_id=coid)
        return self.trading.submit_order(order)

    def sell_to_close(self, occ_symbol: str, qty: int):
        self.last_audit = self._audit_quote(occ_symbol, "sell", qty, None)
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

    # -- defined-risk spreads (sleeve "spreads") ------------------------------
    def spread_quote(self, short_sym: str, long_sym: str):
        """Net quote for a put credit spread (short leg sold, long leg bought).

        Convention: values are the package's NET CREDIT, positive numbers.
          bid = credit received CROSSING both legs (short.bid - long.ask)
          ask = cost to buy the package back crossing (short.ask - long.bid)
          mid = short.mid - long.mid
        The package "cost gate" compares (ask - bid) to the credit: that
        difference is the round trip the market charges for the pair.
        Returns {"bid","ask","mid","spread_pct"} or None if either leg lacks
        a two-sided quote (same principle as singles: no invented numbers).
        """
        try:
            q = self.opt_data.get_option_latest_quote(OptionLatestQuoteRequest(
                symbol_or_symbols=[short_sym, long_sym],
                feed=OPT_FEED))
            s, l = q.get(short_sym), q.get(long_sym)
            if s is None or l is None:
                return None
            sb, sa = float(s.bid_price or 0), float(s.ask_price or 0)
            lb, la = float(l.bid_price or 0), float(l.ask_price or 0)
            if min(sb, sa, lb, la) <= 0 or sa < sb or la < lb:
                return None
            bid = sb - la
            ask = sa - lb
            mid = (sb + sa) / 2.0 - (lb + la) / 2.0
            if mid <= 0 or ask <= 0:
                return None            # a "credit" spread quoting at a debit is unusable
            return {"bid": round(bid, 4), "ask": round(ask, 4),
                    "mid": round(mid, 4),
                    "spread_pct": round((ask - bid) / mid, 4) if mid else None}
        except Exception:
            return None

    def find_put_spread(self, underlying: str, spot: float, sigma_ann: float):
        """Select a put credit spread: short strike ~one expected move down,
        long strike one preferred width below it, both legs OI-passing.

        Returns {"short": contract, "long": contract, "width", "dte",
                 "expiry_ymd"} or None. No fallback past the OI floor — the
        same no-adverse-selection rule as singles."""
        try:
            if not spot or spot <= 0 or not sigma_ann or sigma_ann <= 0:
                return None
            today = dt.date.today()
            exp_lo = (today + dt.timedelta(days=SPREADS_DTE_MIN)).isoformat()
            exp_hi = (today + dt.timedelta(days=SPREADS_DTE_MAX)).isoformat()
            t_mid = ((SPREADS_DTE_MIN + SPREADS_DTE_MAX) / 2.0) / 365.0
            k_target = spot * (1.0 - 1.1 * sigma_ann * math.sqrt(t_mid))
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying], status=AssetStatus.ACTIVE,
                expiration_date_gte=exp_lo, expiration_date_lte=exp_hi,
                type=ContractType.PUT,
                strike_price_gte=str(round(spot * 0.80, 2)),
                strike_price_lte=str(round(spot * 1.00, 2)),
                limit=300)
            resp = self.trading.get_option_contracts(req)
            contracts = getattr(resp, "option_contracts", None) or []
        except Exception:
            return None
        by_exp = {}
        for c in contracts:
            try:
                oi = int(c.open_interest) if c.open_interest is not None else 0
                if oi < MIN_OPEN_INTEREST:
                    continue
                parts = _occ_parts(c.symbol)
                if parts is None:
                    continue
                by_exp.setdefault(parts["ymd"], []).append((parts["strike"], c))
            except Exception:
                continue
        widths = (2.0, 3.0) if underlying == "SPY" else (3.0, 4.0, 5.0)
        best = None
        for ymd, lst in by_exp.items():
            dte = spread_dte(ymd)
            if dte is None or not (SPREADS_DTE_MIN <= dte <= SPREADS_DTE_MAX):
                continue
            lst.sort()
            strikes = [s for s, _ in lst]
            # short: OI-passing strike nearest the expected-move target
            si = min(range(len(lst)), key=lambda i: abs(strikes[i] - k_target))
            s_strike, s_c = lst[si]
            for w in widths:
                lt = s_strike - w
                li = min(range(len(lst)), key=lambda i: abs(strikes[i] - lt))
                l_strike, l_c = lst[li]
                if l_strike >= s_strike:
                    continue
                width = round(s_strike - l_strike, 2)
                if (width - 0.0) * 100 > SPREADS_MAX_LOSS + 50:
                    continue          # even before credit, hopeless vs the loss cap
                cand = {"short": s_c, "long": l_c, "width": width,
                        "dte": dte, "expiry_ymd": ymd}
                # prefer the nearest-to-target expiry ~7 DTE, then first width
                score = abs(dte - 7)
                if best is None or score < best[0]:
                    best = (score, cand)
                break
        return best[1] if best else None

    def submit_spread(self, short_sym, long_sym, qty, net_credit,
                      client_order_id: str = None):
        return submit_spread_order(self.trading, short_sym, long_sym, qty,
                                   net_credit, client_order_id)

    def close_spread(self, short_sym, long_sym, qty, net_debit_limit,
                     client_order_id: str = None):
        return close_spread_order(self.trading, short_sym, long_sym, qty,
                                  net_debit_limit, client_order_id)

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


# ============================================================================
# DEFINED-RISK SPREADS (sleeve "spreads" — see SPREADS_DESIGN.md)
# ============================================================================
# Put credit spreads on SPY/QQQ only: sell a put ~one expected move down,
# buy a put 2-5 dollars further down. Max loss is fixed at entry
# (width - credit): that bound, not any resting order, is the disaster floor.
# Connor granted these (and only these) the overnight/weekend exemption from
# the flat-by-close rule on 2026-07-30.

SPREADS_ALLOCATION  = _envf("SPXBOT_SPREADS_ALLOC", 1000.0)  # dedicated, NOT from SLEEVE_ALLOCATION
SPREADS_MAX_OPEN    = int(_envf("SPXBOT_SPREADS_MAX_OPEN", 2))
SPREADS_MAX_LOSS    = _envf("SPXBOT_SPREADS_MAX_LOSS", 400.0)   # per spread, width - credit
SPREADS_MIN_CREDIT_FRAC = _envf("SPXBOT_SPREADS_MIN_CRED", 0.20)  # credit >= 20% of width
SPREADS_NET_COST_FRAC   = _envf("SPXBOT_SPREADS_NET_COST", 0.08)  # package RT cost <= 8% of credit
SPREADS_RICH_PTS    = _envf("SPXBOT_SPREADS_RICH", 0.02)     # implied - forecast >= 2 vol pts
SPREADS_DTE_MIN     = int(_envf("SPXBOT_SPREADS_DTE_MIN", 5))
SPREADS_DTE_MAX     = int(_envf("SPXBOT_SPREADS_DTE_MAX", 10))
SPREADS_TAKE_FRAC   = _envf("SPXBOT_SPREADS_TP", 0.50)       # buy back at 50% of credit
SPREADS_STOP_MULT   = _envf("SPXBOT_SPREADS_STOP", 2.00)     # buy back at 2x credit
SPREADS_TIME_DTE    = int(_envf("SPXBOT_SPREADS_TIME_DTE", 2))
SPREADS_UNDERLYINGS = ("SPY", "QQQ")

_OCC_SPREAD_RE = _re.compile(r'^([A-Z]+)(\d{6})([CP])(\d{8})$')

def _occ_parts(sym: str):
    m = _OCC_SPREAD_RE.match(sym or "")
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    return {"underlying": root, "ymd": ymd, "cp": cp, "strike": int(strike) / 1000.0}

def detect_spreads(positions):
    """Group raw Alpaca option positions into put-credit-spread packages.

    Stateless by design, like everything else: a package is derivable from
    the book alone — a SHORT put (qty < 0) paired with the nearest LOWER-
    strike LONG put of the same underlying and expiry, quantity-matched.
    Anything that does not pair stays a single and is managed (and
    flattened) by the ordinary rules — an orphan short leg would mean
    assignment or a broken entry, and treating it as a normal position is
    the safe default because the ordinary rules will close it.

    Returns (packages, member_syms):
      packages: [{short, long, qty, underlying, expiry_ymd,
                  short_strike, long_strike, width}]
      member_syms: set of every symbol consumed by a package.
    Total function: unreadable positions are skipped, never raised on.
    """
    shorts, longs = [], []
    for p in positions or []:
        try:
            sym = getattr(p, "symbol", None) or ""
            parts = _occ_parts(sym)
            if parts is None or parts["cp"] != "P":
                continue
            q = int(float(getattr(p, "qty", 0) or 0))
            if q < 0:
                shorts.append((sym, parts, -q))
            elif q > 0:
                longs.append((sym, parts, q))
        except Exception:
            continue
    packages, used = [], set()
    for s_sym, s, s_qty in sorted(shorts, key=lambda t: t[0]):
        best = None
        for l_sym, l, l_qty in longs:
            if l_sym in used or l_sym == s_sym:
                continue
            if (l["underlying"] != s["underlying"] or l["ymd"] != s["ymd"]
                    or l["strike"] >= s["strike"] or l_qty < s_qty):
                continue
            if best is None or l["strike"] > best[1]["strike"]:
                best = (l_sym, l, l_qty)
        if best is None:
            continue
        l_sym, l, _ = best
        used.add(l_sym)
        packages.append({
            "short": s_sym, "long": l_sym, "qty": s_qty,
            "underlying": s["underlying"], "expiry_ymd": s["ymd"],
            "short_strike": s["strike"], "long_strike": l["strike"],
            "width": round(s["strike"] - l["strike"], 2),
        })
    member_syms = {p["short"] for p in packages} | {p["long"] for p in packages}
    return packages, member_syms


def spread_dte(expiry_ymd: str):
    """Days to expiry from an OCC yymmdd, or None if unreadable."""
    try:
        e = dt.date(2000 + int(expiry_ymd[:2]), int(expiry_ymd[2:4]),
                    int(expiry_ymd[4:6]))
        return (e - dt.date.today()).days
    except Exception:
        return None


def submit_spread_order(trading, short_sym: str, long_sym: str, qty: int,
                        net_credit: float, client_order_id: str = None):
    """Open a put credit spread at a NET CREDIT limit (module-level so the
    watcher can use it with its own TradingClient).

    Alpaca's mleg convention (verified in the SDK reference): limit_price
    NEGATIVE signifies a credit received. `net_credit` here is the positive
    credit we require; the sign flip happens exactly once, HERE, so no caller
    ever reasons about signed prices."""
    legs = [
        OptionLegRequest(symbol=short_sym, ratio_qty=1, side=OrderSide.SELL,
                         position_intent=PositionIntent.SELL_TO_OPEN),
        OptionLegRequest(symbol=long_sym, ratio_qty=1, side=OrderSide.BUY,
                         position_intent=PositionIntent.BUY_TO_OPEN),
    ]
    req = LimitOrderRequest(
        qty=qty, order_class=OrderClass.MLEG, time_in_force=TimeInForce.DAY,
        limit_price=-abs(round(float(net_credit), 2)), legs=legs,
        client_order_id=client_order_id)
    return trading.submit_order(req)


def close_spread_order(trading, short_sym: str, long_sym: str, qty: int,
                       net_debit_limit: float, client_order_id: str = None):
    """Buy a put credit spread back (BTC short / STC long) at a net debit."""
    legs = [
        OptionLegRequest(symbol=short_sym, ratio_qty=1, side=OrderSide.BUY,
                         position_intent=PositionIntent.BUY_TO_CLOSE),
        OptionLegRequest(symbol=long_sym, ratio_qty=1, side=OrderSide.SELL,
                         position_intent=PositionIntent.SELL_TO_CLOSE),
    ]
    req = LimitOrderRequest(
        qty=qty, order_class=OrderClass.MLEG, time_in_force=TimeInForce.DAY,
        limit_price=abs(round(float(net_debit_limit), 2)), legs=legs,
        client_order_id=client_order_id)
    return trading.submit_order(req)
