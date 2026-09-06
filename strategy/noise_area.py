"""
Intraday momentum with a time-of-day "noise area" — the primary strategy.

Lineage (see the research brief): Gao-Han-Li-Zhou 2018 (first/last half-hour
predictability), Baltussen-Da-Lammers-Martens 2021 (mechanism: gamma-hedging
demand, measured in 60+ futures), Zarattini-Aziz-Barbon 2024 (the "noise
area" implementation on SPY, Sharpe 1.33 net of costs 2007-2024), Quantitativo
2024 (ES/NQ replication, +6 bps/trade, 38% win rate, 2.25 payoff).

THE RULES WE IMPLEMENT (state them, then test them — a strategy you cannot
write down is one you cannot backtest):

  For session d with open O_d and previous session close C_{d-1}, decision
  bars of BAR_MINUTES (default 30) indexed by slot s = 0..12:

    move[d, s]  = | close[d, s] / O_d - 1 |          (move from open to bar close)
    sigma[d, s] = mean over the previous LOOKBACK sessions of move[., s]
    upper[d, s] = max(O_d, C_{d-1}) * (1 + MULT * sigma[d, s])
    lower[d, s] = min(O_d, C_{d-1}) * (1 - MULT * sigma[d, s])

  At the close of bar s:
    flat  -> long  if close > upper[d, s]     (momentum has left the noise)
          -> short if close < lower[d, s]     (unless LONG_ONLY)
    long  -> exit  if close < upper[d, s]     (TRAIL_MODE band: back inside noise)
                or close < VWAP_d(s)          (TRAIL_MODE vwap)
             then, if ALLOW_REVERSAL and close < lower: short
    short -> mirror image
  No entries after NO_NEW_ENTRY_AFTER; everything is flattened at FLATTEN_AT.
  At most MAX_ENTRIES_PER_DAY entries per session.

  The protective stop is NOT part of the signal. It is a resting broker order
  sized by the risk engine (risk/engine.py) so that a full stop-out costs
  RISK_PER_TRADE dollars. That is the prop-rule adaptation: the paper trusts
  the band as its stop; a prop account cannot afford an unbounded intrabar gap.

Everything here is pure: no clocks, no sockets. `SessionContext` is built once
per session from data and the same object drives the backtester and the live
runner, so what was tested is what trades.
"""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config as C
from data import sessions as S


@dataclass
class SessionContext:
    day: dt.date
    open_px: float
    prev_close: Optional[float]
    bars_1m: pd.DataFrame            # RTH 1-min bars, exchange-zone index
    bars_n: pd.DataFrame             # decision bars (BAR_MINUTES)
    sigma: np.ndarray                # per slot; NaN when lookback insufficient
    upper: np.ndarray
    lower: np.ndarray
    vwap_1m: pd.Series               # cumulative session VWAP per 1-min bar
    bar_range_est: float             # typical decision-bar range (points), for stops
    open_ts: pd.Timestamp = None
    close_ts: pd.Timestamp = None
    flatten_ts: pd.Timestamp = None
    no_entry_ts: pd.Timestamp = None

    @property
    def n_slots(self) -> int:
        return len(self.bars_n)

    def ready(self) -> bool:
        return bool(len(self.sigma)) and not np.isnan(self.sigma).all()


def _session_vwap(b1: pd.DataFrame) -> pd.Series:
    tp = (b1["high"] + b1["low"] + b1["close"]) / 3.0
    v = b1["volume"].clip(lower=0)
    cv = v.cumsum()
    num = (tp * v).cumsum()
    vwap = num / cv.replace(0, np.nan)
    return vwap.fillna(b1["close"])


def build_sessions(bars_1m_utc: pd.DataFrame, bar_minutes: int = C.BAR_MINUTES,
                   lookback: int = C.NOISE_LOOKBACK_DAYS, mult: float = C.NOISE_MULT
                   ) -> list[SessionContext]:
    """Turn a UTC 1-minute history into per-session contexts with bands.

    Sigma for session d uses ONLY sessions < d (no look-ahead). Sessions with
    fewer than 3 lookback observations for a slot get NaN there, and the
    strategy never trades a NaN slot.
    """
    rth = S.rth_only(bars_1m_utc)
    if rth.empty:
        return []
    days = sorted({d for d in rth.index.date})
    per_day_1m = {d: g for d, g in rth.groupby(rth.index.date)}
    contexts: list[SessionContext] = []
    # history of move[d, s] arrays, oldest first
    move_hist: list[np.ndarray] = []
    max_slots = 0
    prev_close = None
    for d in days:
        b1 = per_day_1m[d]
        if len(b1) < 20:                       # half-session / bad data: skip but keep prev_close
            prev_close = float(b1["close"].iloc[-1]) if len(b1) else prev_close
            continue
        bn = S.resample_bars(b1, bar_minutes)
        o = float(b1["open"].iloc[0])
        move = (bn["close"].to_numpy() / o - 1.0)
        move_abs = np.abs(move)
        n = len(bn); max_slots = max(max_slots, n)
        # sigma from previous sessions, slot-aligned; pad shorter days with NaN
        if move_hist:
            L = min(lookback, len(move_hist))
            past = np.full((L, n), np.nan)
            for i, h in enumerate(move_hist[-L:]):
                m = min(n, len(h)); past[i, :m] = h[:m]
            cnt = np.sum(~np.isnan(past), axis=0)
            sigma = np.where(cnt >= 3, np.nanmean(past, axis=0), np.nan)
        else:
            sigma = np.full(n, np.nan)
        ref_hi = max(o, prev_close) if prev_close else o
        ref_lo = min(o, prev_close) if prev_close else o
        upper = ref_hi * (1.0 + mult * sigma)
        lower = ref_lo * (1.0 - mult * sigma)
        rng = (bn["high"] - bn["low"]).to_numpy()
        # typical decision-bar range from the lookback (fallback: today's median)
        if move_hist and hasattr(build_sessions, "_ranges"):
            pass
        ob, cb, fb, eb = S.session_bounds(d)
        ctx = SessionContext(day=d, open_px=o, prev_close=prev_close, bars_1m=b1, bars_n=bn,
                             sigma=sigma, upper=upper, lower=lower, vwap_1m=_session_vwap(b1),
                             bar_range_est=float(np.nanmedian(rng)) if len(rng) else 0.0,
                             open_ts=pd.Timestamp(ob), close_ts=pd.Timestamp(cb),
                             flatten_ts=pd.Timestamp(fb), no_entry_ts=pd.Timestamp(eb))
        contexts.append(ctx)
        move_hist.append(move_abs)
        prev_close = float(b1["close"].iloc[-1])
    # bar_range_est should be lookback-based, not same-day: smooth it
    ranges = [c.bar_range_est for c in contexts]
    for i, c in enumerate(contexts):
        lo = max(0, i - lookback)
        past = ranges[lo:i]
        if past:
            c.bar_range_est = float(np.median(past))
    return contexts


@dataclass
class Decision:
    target: int                 # -1, 0, +1
    reason: str
    stop_points: float = 0.0    # protective stop distance in points (0 = none)


class NoiseAreaStrategy:
    """Stateless decision function over a SessionContext.

    `on_bar_close(ctx, slot, position, entries_today)` is called once per
    completed decision bar with the bar's close and the session VWAP at that
    time; it returns the TARGET position. The caller (backtester or live
    runner) owns fills, stops, costs and the clock.
    """

    def __init__(self, trail: str = C.TRAIL_MODE, allow_reversal: bool = C.ALLOW_REVERSAL,
                 long_only: bool = C.LONG_ONLY, max_entries: int = C.MAX_ENTRIES_PER_DAY,
                 stop_range_mult: float = C.STOP_RANGE_MULT, instrument=None):
        self.inst = instrument or C.INSTRUMENT
        self.trail = trail
        self.allow_reversal = allow_reversal
        self.long_only = long_only
        self.max_entries = max_entries
        self.stop_range_mult = stop_range_mult
        self.name = "noise_area"

    def stop_distance(self, ctx: SessionContext, slot: int | None = None) -> float:
        """Protective-stop distance in points.

        The band exit is the strategy's real stop; the resting broker stop is
        the disaster floor behind it. So it sits at STOP_RANGE_MULT x the full
        noise width at this slot (upper - lower, i.e. ~2 sigma), never inside
        the noise (a stop inside the noise just pays the spread to noise), and
        clamped to the instrument's [min, max] points so sizing stays sane.
        """
        inst = self.inst
        width = 0.0
        if slot is not None and 0 <= slot < ctx.n_slots and not np.isnan(ctx.sigma[slot]):
            width = float(ctx.upper[slot] - ctx.lower[slot])
        raw = self.stop_range_mult * max(width, ctx.bar_range_est, inst.tick_size)
        return float(min(max(raw, inst.min_stop_pts), inst.max_stop_pts))

    def on_bar_close(self, ctx: SessionContext, slot: int, position: int,
                     entries_today: int, bar_end_ts: pd.Timestamp) -> Decision:
        if slot < 0 or slot >= ctx.n_slots or np.isnan(ctx.sigma[slot]):
            return Decision(position, "no band (insufficient lookback)")
        close = float(ctx.bars_n["close"].iloc[slot])
        up, lo = float(ctx.upper[slot]), float(ctx.lower[slot])
        # VWAP at the end of this decision bar = last 1-min vwap value <= bar end
        v = ctx.vwap_1m[ctx.vwap_1m.index < bar_end_ts]
        vwap = float(v.iloc[-1]) if len(v) else close
        can_enter = (entries_today < self.max_entries) and (bar_end_ts <= ctx.no_entry_ts)
        sd = self.stop_distance(ctx, slot)

        def want_long():
            return close > up

        def want_short():
            return (not self.long_only) and close < lo

        if position == 0:
            if can_enter and want_long():
                return Decision(+1, f"long: close {close:.2f} > upper {up:.2f}", sd)
            if can_enter and want_short():
                return Decision(-1, f"short: close {close:.2f} < lower {lo:.2f}", sd)
            return Decision(0, "inside noise")

        if position > 0:
            exit_band = close < up
            exit_vwap = close < vwap
            ex = {"band": exit_band, "vwap": exit_vwap, "both": exit_band or exit_vwap,
                  "none": False}[self.trail]
            if ex:
                if self.allow_reversal and can_enter and want_short():
                    return Decision(-1, f"reverse to short: close {close:.2f} < lower {lo:.2f}", sd)
                return Decision(0, f"exit long: close {close:.2f} < "
                                   f"{'upper ' + format(up, '.2f') if exit_band else 'vwap ' + format(vwap, '.2f')}")
            return Decision(+1, "hold long")

        # short
        exit_band = close > lo
        exit_vwap = close > vwap
        ex = {"band": exit_band, "vwap": exit_vwap, "both": exit_band or exit_vwap,
              "none": False}[self.trail]
        if ex:
            if self.allow_reversal and can_enter and want_long():
                return Decision(+1, f"reverse to long: close {close:.2f} > upper {up:.2f}", sd)
            return Decision(0, f"exit short: close {close:.2f} > "
                               f"{'lower ' + format(lo, '.2f') if exit_band else 'vwap ' + format(vwap, '.2f')}")
        return Decision(-1, "hold short")
