"""
Sleeve 2 — last-half-hour momentum, and the volatility-regime switch.

LAST HALF HOUR (Gao, Han, Li & Zhou, JFE 2018, "Market Intraday Momentum").
Published finding: the first half-hour return (from the previous close to the
end of the first half hour) predicts the last half-hour return, with the
second-to-last half hour adding power. Mechanism (Baltussen et al. 2021):
option-hedging and leveraged-ETF rebalancing flows push the day's move into
the close.

THE RULE WE IMPLEMENT (one decision per session):
  r_first = close[slot 0] / prev_close - 1        (prev close -> 09:00 CT)
  r_late  = close[s-1]  / close[s-2] - 1           (the second-to-last decision bar)
  at the close of the second-to-last decision bar (14:30 CT on a normal day):
    long  if r_first > +MIN_MOVE and (not CONFIRM or r_late >= 0)
    short if r_first < -MIN_MOVE and (not CONFIRM or r_late <= 0)
  hold to the session flatten (set SPXF_FLATTEN_AT=14:58 for this sleeve so the
  position rides the last half hour; the prop force-flat is 15:10 CT).
  Disaster stop = STOP_MULT x typical decision-bar range (risk engine sizes it).

VOLATILITY REGIME SWITCH.
Zarattini et al. report Sharpe rising with VIX; our own year table agreed. A
regime filter is a different animal from a signal-strength filter (which
failed): it removes whole sessions from dead tape rather than skimming the
strongest breakouts. Rule: session d is ON if the trailing 14-day mean |daily
return| — measured through the PREVIOUS session's close, never d's own — is at
or above the EXPANDING median of that statistic over all prior sessions (first
60 sessions always ON). Batch 4 (2026-09-09) shipped with d's own close inside
the window; that leaked the day's move into the day's flag and was withdrawn.
"""
from __future__ import annotations
import datetime as dt
import numpy as np
import pandas as pd

import config as C
from strategy.noise_area import SessionContext, Decision


class LastHalfHourStrategy:
    def __init__(self, min_move: float = 0.0005, confirm: bool = True, long_only: bool = C.LONG_ONLY,
                 stop_mult: float = 1.0, instrument=None):
        self.min_move = min_move
        self.confirm = confirm
        self.long_only = long_only
        self.stop_mult = stop_mult
        self.inst = instrument or C.INSTRUMENT
        self.name = "lasthalf"

    def stop_distance(self, ctx: SessionContext) -> float:
        raw = self.stop_mult * max(ctx.bar_range_est, self.inst.tick_size)
        return float(min(max(raw, self.inst.min_stop_pts), self.inst.max_stop_pts))

    def on_bar_close(self, ctx: SessionContext, slot: int, position: int,
                     entries_today: int, bar_end_ts: pd.Timestamp) -> Decision:
        if position != 0:
            return Decision(position, "hold to close")
        decision_slot = ctx.n_slots - 2                 # bar ending 30 min before the close
        if slot != decision_slot or entries_today > 0 or decision_slot < 2:
            return Decision(0, "not the decision bar")
        if not ctx.prev_close:
            return Decision(0, "no previous close")
        closes = ctx.bars_n["close"].to_numpy()
        r_first = closes[0] / ctx.prev_close - 1.0
        r_late = closes[slot] / closes[slot - 1] - 1.0
        sd = self.stop_distance(ctx)
        if r_first > self.min_move and (not self.confirm or r_late >= 0):
            return Decision(+1, f"lasthalf long: r_first {r_first:+.2%}, r_late {r_late:+.2%}", sd)
        if r_first < -self.min_move and not self.long_only and (not self.confirm or r_late <= 0):
            return Decision(-1, f"lasthalf short: r_first {r_first:+.2%}, r_late {r_late:+.2%}", sd)
        return Decision(0, f"no signal: r_first {r_first:+.2%}, r_late {r_late:+.2%}")


def vol_regime(contexts: list[SessionContext], lookback: int = 14, min_obs: int = 60,
               pct: float = 0.5) -> dict:
    """day -> bool. ON when trailing mean |daily return| >= expanding percentile of
    its own history (prior sessions only). Also returns the statistic for reporting."""
    days = [c.day for c in contexts]
    closes = np.array([float(c.bars_1m["close"].iloc[-1]) for c in contexts])
    rets = np.abs(np.diff(closes) / closes[:-1])          # rets[k] = |return INTO session k+1|
    stat = np.full(len(days), np.nan)
    for i in range(len(days)):
        # for session i use only returns into sessions <= i-1, i.e. rets[.. i-2]: the last
        # usable term is rets[i-2]; session i's own close must never enter its own flag
        if i - 1 >= lookback:
            stat[i] = np.mean(rets[i - 1 - lookback:i - 1])
    out = {}; hist = []
    for i, d in enumerate(days):
        if np.isnan(stat[i]) or len(hist) < min_obs:
            out[d] = True
        else:
            out[d] = bool(stat[i] >= np.quantile(hist, pct))
        if not np.isnan(stat[i]):
            hist.append(stat[i])
    return out
