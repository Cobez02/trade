"""
Sleeve 3 — intraday relative value between the two index futures (ES vs NQ),
proxied by SPY vs QQQ. Market-neutral by construction, so its P&L should be
nearly uncorrelated with the directional sleeves — which is the point.

THE RULE (pre-registered; one entry per session):
  On the 30-minute grid, spread s_t = ln(Q_t / Q_open) - ln(S_t / S_open):
  how far the Nasdaq has out/under-performed the S&P since today's open.
  sigma_t = mean over the previous LOOKBACK sessions of |s_t| at the same slot
  (same construction as the noise band, no look-ahead).
  Entry (flat, slot >= 1, before NO_NEW_ENTRY_AFTER): |s_t| >= Z_ENTRY * sigma_t
    -> bet on reversion: s_t > 0  => short QQQ / long SPY;  s_t < 0 => long QQQ / short SPY
  Exit at a later 30-minute close when |s_t| <= Z_EXIT * sigma_t, or when the
  spread moves a further STOP_SIGMA * sigma_t against the position (checked at
  bar closes — intrabar stops are not modelled, stated as a limitation), or at
  the flatten. Dollar-neutral legs: notional L each, with L chosen so a full
  stop-out costs RISK_PER_TRADE. Fills at the next 1-minute open with per-leg
  slippage; commission per share per side per leg.

Output is the same DayResult list the single-leg backtester produces, so the
rule simulator and the portfolio script treat it like any other sleeve.
"""
from __future__ import annotations
import math
import statistics as st
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

import config as C
from data import sessions as S
from strategy.noise_area import build_sessions
from backtest.simulate import DayResult, Report


@dataclass
class PairTrade:
    day: str
    side: int              # +1 = long QQQ / short SPY ; -1 = short QQQ / long SPY
    notional: float
    entry_ts: str
    entry_s: float
    exit_ts: str
    exit_s: float
    pnl: float
    reason_out: str
    costs: float = 0.0
    qty: int = 1


class PairReport(Report):
    def summary(self) -> dict:
        out = super().summary()
        out["costs_total"] = round(sum(t.costs for t in self.trades), 2)
        out.pop("stop_outs", None); out.pop("flattens", None)
        out["exits"] = {k: sum(1 for t in self.trades if t.reason_out == k) for k in ("reverted", "stop", "flatten", "daily kill")}
        return out


class PairBacktester:
    def __init__(self, z_entry: float = 1.5, z_exit: float = 0.5, stop_sigma: float = 1.0,
                 risk: float = C.RISK_PER_TRADE, max_notional: float = 60_000.0,
                 lookback: int = C.NOISE_LOOKBACK_DAYS, bar_minutes: int = C.BAR_MINUTES,
                 slip_a: float = 0.045, slip_b: float = 0.019, comm_a: float = 0.0, comm_b: float = 0.0,
                 daily_kill: float = C.DAILY_KILL_LOSS):
        """slip_/comm_ are $ per SHARE per side for leg A (SPY) and leg B (QQQ)."""
        self.z_entry, self.z_exit, self.stop_sigma = z_entry, z_exit, stop_sigma
        self.risk, self.max_notional = risk, max_notional
        self.lookback, self.bar_minutes = lookback, bar_minutes
        self.slip_a, self.slip_b, self.comm_a, self.comm_b = slip_a, slip_b, comm_a, comm_b
        self.daily_kill = daily_kill
        self.trades: list[PairTrade] = []
        self.days: list[DayResult] = []
        self.name = "pair_spread"

    def run(self, bars_a: pd.DataFrame, bars_b: pd.DataFrame, session_filter: dict | None = None) -> Report:
        ca = {c.day: c for c in build_sessions(bars_a, self.bar_minutes, self.lookback, 1.0)}
        cb = {c.day: c for c in build_sessions(bars_b, self.bar_minutes, self.lookback, 1.0)}
        days = sorted(set(ca) & set(cb))
        hist: list[np.ndarray] = []                     # |s_t| per slot, previous sessions
        for d in days:
            A, B = ca[d], cb[d]
            if not (A.ready() and B.ready()):
                continue
            n = min(A.n_slots, B.n_slots)
            s = np.log(B.bars_n["close"].to_numpy()[:n] / B.open_px) - np.log(A.bars_n["close"].to_numpy()[:n] / A.open_px)
            if session_filter is not None and not session_filter.get(d, True):
                self.days.append(DayResult(str(d), 0.0, 0.0, 0.0, 0, "regime off")); hist.append(np.abs(s)); continue
            sigma = np.full(n, np.nan)
            if len(hist) >= 3:
                L = hist[-self.lookback:]
                past = np.full((len(L), n), np.nan)
                for i, h in enumerate(L):
                    m = min(n, len(h)); past[i, :m] = h[:m]
                cnt = np.sum(~np.isnan(past), axis=0)
                sigma = np.where(cnt >= 3, np.nanmean(past, axis=0), np.nan)
            res = self._run_day(A, B, s, sigma, n)
            self.days.append(res)
            hist.append(np.abs(s))
        return PairReport(self.trades, self.days, C.SPY)

    def _leg_px(self, ctx, ts):
        """1-minute open at/after ts, in exchange zone."""
        b = ctx.bars_1m[ctx.bars_1m.index >= ts]
        return (float(b["open"].iloc[0]), b.index[0]) if len(b) else (float(ctx.bars_1m["close"].iloc[-1]), ctx.bars_1m.index[-1])

    def _run_day(self, A, B, s, sigma, n) -> DayResult:
        bar_len = pd.Timedelta(minutes=self.bar_minutes)
        pos = 0; notional = 0.0; sh_a = sh_b = 0.0; pa0 = pb0 = 0.0; s_entry = 0.0; entry_ts = None
        cum = 0.0; low = 0.0; high = 0.0; trades = 0; entered = False; halt = ""

        def close_at(ts, s_now, why):
            nonlocal pos, cum, trades, notional, sh_a, sh_b
            pa, ta = self._leg_px(A, ts); pb, tb = self._leg_px(B, ts)
            # closing: reverse each leg with adverse slippage
            pa_fill = pa + (self.slip_a if pos < 0 else -self.slip_a)   # pos<0 => we are LONG SPY -> sell at bid
            pb_fill = pb + (self.slip_b if pos > 0 else -self.slip_b)   # pos>0 => we are LONG QQQ -> sell at bid
            pnl = (pb_fill - pb0) * sh_b * pos + (pa_fill - pa0) * sh_a * (-pos)
            comm = (abs(sh_a) * self.comm_a + abs(sh_b) * self.comm_b) * 2
            slip = (abs(sh_a) * self.slip_a + abs(sh_b) * self.slip_b) * 2
            pnl -= comm
            self.trades.append(PairTrade(str(A.day), pos, round(notional), str(entry_ts), round(s_entry, 5), str(ta), round(s_now, 5), round(pnl, 2), why, round(comm + slip, 2)))
            cum += pnl; trades += 1; pos = 0

        for t in range(n):
            ts_close = A.open_ts + bar_len * (t + 1)             # end of decision bar t
            if ts_close >= A.flatten_ts:
                break
            sig = sigma[t]
            if pos == 0:
                if entered or t < 1 or np.isnan(sig) or sig <= 0 or ts_close > A.no_entry_ts:
                    continue
                z = s[t] / sig
                if abs(z) >= self.z_entry and cum > -self.daily_kill:
                    pos = -1 if s[t] > 0 else +1                  # revert: fade the outperformer
                    stop_frac = self.stop_sigma * sig
                    notional = min(self.risk / max(stop_frac, 1e-4), self.max_notional)
                    pa0, ta = self._leg_px(A, ts_close); pb0, tb = self._leg_px(B, ts_close)
                    pa0 = pa0 + (self.slip_a if pos < 0 else -self.slip_a)   # buying SPY pays the ask
                    pb0 = pb0 + (self.slip_b if pos > 0 else -self.slip_b)
                    sh_a = notional / pa0; sh_b = notional / pb0
                    s_entry = s[t]; entry_ts = ta; entered = True
            else:
                mtm = notional * (s[t] - s_entry) * pos          # mark-to-market (exact at fills)
                low = min(low, cum + mtm); high = max(high, cum + mtm)
                z_now = s[t] / sig if (not np.isnan(sig) and sig > 0) else 0.0
                adverse = (s[t] - s_entry) * pos < -self.stop_sigma * (sig if not np.isnan(sig) else abs(s_entry))
                if adverse:
                    close_at(ts_close, s[t], "stop"); continue
                if abs(z_now) <= self.z_exit:
                    close_at(ts_close, s[t], "reverted"); continue
                if cum + mtm <= -self.daily_kill:
                    close_at(ts_close, s[t], "daily kill"); halt = "daily kill"; continue
        if pos != 0:
            close_at(A.flatten_ts, s[min(n - 1, len(s) - 1)], "flatten")
        return DayResult(str(A.day), round(cum, 2), round(min(low, cum, 0.0), 2), round(max(high, cum, 0.0), 2), trades, halt)
