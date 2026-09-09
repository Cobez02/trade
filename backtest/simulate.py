"""
Backtester — replays the strategy minute by minute with the same risk engine
the live runner uses.

Fill model (deliberately pessimistic):
  * decisions are made on the CLOSE of a decision bar and executed at the OPEN
    of the next 1-minute bar, plus SLIPPAGE_TICKS against you
  * the protective stop is a resting stop-market order: if a 1-minute bar's
    low (long) / high (short) touches it, you are filled at the stop price
    minus slippage — or at the bar's open if the bar opened through the stop
    (gap), which is what happens to real stop orders
  * commission is charged per side, every side
  * the flatten is a market order at the open of the first bar at/after
    FLATTEN_AT, plus slippage

Outputs: per-trade log, per-day P&L (with the day's intraday low of cumulative
P&L, which the prop simulator needs), summary metrics, and P(pass) under any
RuleSet. Everything is deterministic.
"""
from __future__ import annotations
import math
import statistics as st
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

import config as C
from data import sessions as S
from strategy.noise_area import SessionContext, NoiseAreaStrategy, Decision
from risk.engine import RiskEngine, DayState, AccountState
from risk import rules as R


@dataclass
class Trade:
    day: str
    side: int
    qty: int
    entry_ts: str
    entry_px: float
    exit_ts: str
    exit_px: float
    stop_px: float
    pnl: float
    reason_in: str
    reason_out: str

    @property
    def points(self) -> float:
        return (self.exit_px - self.entry_px) * self.side


@dataclass
class DayResult:
    day: str
    pnl: float
    low: float          # worst intraday cumulative P&L (<= 0)
    high: float
    trades: int
    halted: str


class Backtester:
    def __init__(self, strategy: NoiseAreaStrategy, risk: RiskEngine, instrument=C.INSTRUMENT,
                 bar_minutes: int = C.BAR_MINUTES, verbose: bool = False):
        self.strat = strategy
        self.risk = risk
        self.inst = instrument
        self.bar_minutes = bar_minutes
        self.verbose = verbose
        self.trades: list[Trade] = []
        self.days: list[DayResult] = []

    # ------------------------------------------------------------------
    def _fill(self, px: float, side: int) -> float:
        """Adverse slippage: buying pays more, selling gets less."""
        return px + side * self.inst.slippage_ticks * self.inst.tick_size

    def _round_tick(self, px: float) -> float:
        t = self.inst.tick_size
        return round(px / t) * t

    # ------------------------------------------------------------------
    def run_session(self, ctx: SessionContext, acct: AccountState) -> DayResult:
        b1 = ctx.bars_1m
        day = DayState()
        pos = 0; qty = 0; entry_px = 0.0; entry_ts = None; stop_px = 0.0; reason_in = ""
        cum = 0.0; low = 0.0; high = 0.0
        pending: Decision | None = None
        n = len(b1)
        idx = b1.index
        opens = b1["open"].to_numpy(); highs = b1["high"].to_numpy(); lows = b1["low"].to_numpy()
        closes = b1["close"].to_numpy()
        bar_len = pd.Timedelta(minutes=self.bar_minutes)
        next_decision_end = ctx.open_ts + bar_len
        slot = 0
        # data resolution (1 min normally; 5 min for the yfinance smoke test)
        step = pd.Timedelta(minutes=1) if n < 2 else pd.Series(idx[1:] - idx[:-1]).median()

        def close_position(i: int, px: float, why: str):
            nonlocal pos, qty, cum, entry_px, entry_ts, stop_px, reason_in
            fill = self._fill(px, -pos)
            pnl = (fill - entry_px) * pos * qty * self.inst.point_value - 2 * qty * self.inst.commission_side
            self.trades.append(Trade(str(ctx.day), pos, qty, str(entry_ts), entry_px, str(idx[i]),
                                     fill, stop_px, round(pnl, 2), reason_in, why))
            cum += pnl
            day.realized += pnl; day.unrealized = 0.0; day.trades += 1
            pos = 0; qty = 0; entry_px = 0.0; entry_ts = None; stop_px = 0.0

        def open_position(i: int, side: int, q: int, sp: float, why: str):
            nonlocal pos, qty, entry_px, entry_ts, stop_px, reason_in
            fill = self._fill(opens[i], side)
            pos = side; qty = q; entry_px = fill; entry_ts = idx[i]; reason_in = why
            stop_px = self._round_tick(fill - side * sp)
            day.entries += 1

        for i in range(n):
            ts = idx[i]
            # --- 1. act on a decision made at the previous bar close --------
            if pending is not None:
                d = pending; pending = None
                if d.target != pos:
                    if pos != 0:
                        close_position(i, opens[i], d.reason)
                    if d.target != 0 and ts < ctx.no_entry_ts:
                        sz = self.risk.size(d.stop_points, day, acct)
                        if sz.ok:
                            open_position(i, d.target, sz.qty, sz.stop_points, d.reason)
                        elif self.verbose:
                            print(f"  {ts} skip entry: {sz.reason}")
            # --- 2. flatten ---------------------------------------------------
            if ts >= ctx.flatten_ts:
                if pos != 0:
                    close_position(i, opens[i], "flatten")
                # keep scanning for the day-low bookkeeping only
            # --- 3. resting stop check within this minute ---------------------
            if pos != 0:
                hit = (pos > 0 and lows[i] <= stop_px) or (pos < 0 and highs[i] >= stop_px)
                if hit:
                    gapped = (pos > 0 and opens[i] <= stop_px) or (pos < 0 and opens[i] >= stop_px)
                    px = opens[i] if gapped else stop_px
                    close_position(i, px, "stop")
            # --- 4. mark to market, day-low bookkeeping ---------------------
            if pos != 0:
                day.unrealized = (closes[i] - entry_px) * pos * qty * self.inst.point_value
            low = min(low, cum + day.unrealized); high = max(high, cum + day.unrealized)
            # --- 5. daily kill: close on the kill, not just stop entering -----
            if pos != 0 and day.pnl <= -self.risk.daily_kill:
                close_position(i, closes[i], "daily kill")
                day.halted = True; day.halt_reason = "daily kill"
            # --- 6. decision bar boundary -----------------------------------
            bar_end = ts + step
            if bar_end >= next_decision_end and ts < ctx.flatten_ts:
                # the decision bar that just completed is `slot`
                if slot < ctx.n_slots:
                    pending = self.strat.on_bar_close(ctx, slot, pos, day.entries, bar_end)
                slot += 1
                next_decision_end = ctx.open_ts + bar_len * (slot + 1)
        if pos != 0:   # data ended before flatten (early close without bars) — close at last
            close_position(n - 1, closes[-1], "session end")
        res = DayResult(str(ctx.day), round(cum, 2), round(low, 2), round(high, 2), day.trades,
                        day.halt_reason)
        self.days.append(res)
        return res

    # ------------------------------------------------------------------
    def run(self, contexts: list[SessionContext], max_drawdown: float = 2_000.0,
            session_filter: dict | None = None) -> "Report":
        """Research replay: the account has an UNBOUNDED buffer.

        The trailing-drawdown floor and the buffer gate are live-account
        mechanics; applying them here freezes the replay after any losing
        stretch (entries refused forever), which is exactly what happened in
        the first proxy run. Evaluation geometry is scored separately by
        risk/rules.py on the daily P&L this replay produces. The per-day gates
        (kill, cap, max entries) still apply — they are part of the strategy.
        """
        acct = AccountState(0.0, 0.0, -1e12)
        for ctx in contexts:
            if not ctx.ready():
                continue
            if session_filter is not None and not session_filter.get(ctx.day, True):
                self.days.append(DayResult(str(ctx.day), 0.0, 0.0, 0.0, 0, "regime off"))
                continue
            r = self.run_session(ctx, acct)
            acct.balance += r.pnl
        return Report(self.trades, self.days, self.inst)


@dataclass
class Report:
    trades: list[Trade]
    days: list[DayResult]
    inst: object

    @property
    def daily(self) -> list[float]:
        return [d.pnl for d in self.days]

    @property
    def daily_lows(self) -> list[float]:
        return [d.low for d in self.days]

    def summary(self) -> dict:
        t = self.trades; d = self.daily
        if not t:
            return {"trades": 0}
        pnls = [x.pnl for x in t]
        wins = [x for x in pnls if x > 0]; losses = [x for x in pnls if x <= 0]
        gp = sum(wins); gl = -sum(losses)
        mean_d = st.mean(d); sd_d = st.pstdev(d) if len(d) > 1 else 0.0
        eq = np.cumsum(d); peak = np.maximum.accumulate(eq); dd = eq - peak
        out = {
            "sessions": len(d), "trades": len(t), "trades_per_day": round(len(t) / max(len(d), 1), 2),
            "net_pnl": round(sum(pnls), 2), "mean_trade": round(st.mean(pnls), 2),
            "win_rate": round(len(wins) / len(t), 3),
            "avg_win": round(st.mean(wins), 2) if wins else 0.0,
            "avg_loss": round(st.mean(losses), 2) if losses else 0.0,
            "payoff": round((st.mean(wins) / -st.mean(losses)), 2) if wins and losses and st.mean(losses) else None,
            "profit_factor": round(gp / gl, 2) if gl else None,
            "mean_day": round(mean_d, 2), "sd_day": round(sd_d, 2),
            "sharpe_daily_ann": round(mean_d / sd_d * math.sqrt(252), 2) if sd_d else None,
            "best_day": round(max(d), 2), "worst_day": round(min(d), 2),
            "worst_intraday_low": round(min(self.daily_lows), 2),
            "max_drawdown": round(float(dd.min()), 2),
            "days_below_-1000": sum(1 for x in self.daily_lows if x <= -1000),
            "days_below_-700": sum(1 for x in self.daily_lows if x <= -700),
            "stop_outs": sum(1 for x in t if x.reason_out == "stop"),
            "flattens": sum(1 for x in t if x.reason_out == "flatten"),
            "costs_total": round(sum(2 * getattr(x, "qty", 1) * (self.inst.commission_side + self.inst.slippage_cost) for x in t), 2),
        }
        # t-stat of mean daily P&L (day-clustered by construction)
        if sd_d and len(d) > 1:
            out["t_stat_daily"] = round(mean_d / (sd_d / math.sqrt(len(d))), 2)
        return out

    def by_year(self) -> pd.DataFrame:
        df = pd.DataFrame([asdict(x) for x in self.days])
        if df.empty:
            return df
        df["year"] = df["day"].str[:4]
        g = df.groupby("year")["pnl"]
        return pd.DataFrame({"days": g.size(), "trades": df.groupby("year")["trades"].sum(), "pnl": g.sum().round(0),
                             "mean_day": g.mean().round(1), "sd_day": g.std().round(1),
                             "worst": g.min().round(0), "best": g.max().round(0)})

    def prop_score(self, rules: R.RuleSet, n: int = 10_000, scale: float = 1.0) -> dict:
        """P(pass) by block-bootstrapping this report's own daily P&L."""
        # use the measured intraday lows: frac = mean(|low - min(pnl,0)|)/mean|pnl|
        d = self.daily; lo = self.daily_lows
        extra = [abs(l - min(x, 0.0)) for x, l in zip(d, lo)]
        frac = (st.mean(extra) / st.mean([abs(x) for x in d])) if d and st.mean([abs(x) for x in d]) else 0.0
        mc = R.monte_carlo(rules, d, n=n, scale=scale, intraday_low_frac=min(frac, 1.0))
        actual = R.simulate_path(rules, [x * scale for x in d], [l * scale for l in lo])
        mc["this_history"] = {"result": actual.result, "days": actual.days, "reason": actual.reason,
                              "balance": round(actual.balance, 0)}
        mc["intraday_low_frac_used"] = round(frac, 3)
        return mc

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(x) for x in self.trades])
