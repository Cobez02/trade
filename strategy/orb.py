"""
Opening-range breakout — the secondary strategy (Zarattini & Aziz 2023,
"Can Day Trading Really Be Profitable?", QQQ 2016-2023).

Published rule: if the first 5-minute bar closes up, buy at the open of the
second bar; if down, sell short; doji = no trade. Stop at the opposite end of
the opening range (the paper uses 10% of 14-day ATR as the stop distance and
a 10x-reward target; leverage via TQQQ). Exit at the close.

Independent replication (giovannibrusco/ORB) found the QQQ edge breaks even at
about 2.2 cents/share of slippage, i.e. it is thin, and the leverage the paper
uses is not available in a prop account. We keep only the directional core:
one un-leveraged entry per day, sized by the risk engine like everything else.

Same interface as NoiseAreaStrategy so the backtester and live runner do not
care which one they are running. Decisions happen on ORB_MINUTES bars for the
first bar and then the strategy only holds (exits are stop / flatten).
"""
from __future__ import annotations
import numpy as np
import pandas as pd

import config as C
from strategy.noise_area import SessionContext, Decision


class ORBStrategy:
    def __init__(self, orb_minutes: int = C.ORB_MINUTES, long_only: bool = C.LONG_ONLY,
                 atr_frac: float = C.ORB_STOP_ATR_FRAC, instrument=None):
        self.orb_minutes = orb_minutes
        self.long_only = long_only
        self.atr_frac = atr_frac
        self.inst = instrument or C.INSTRUMENT
        self.name = f"orb{orb_minutes}"

    def _opening_range(self, ctx: SessionContext):
        b1 = ctx.bars_1m.iloc[: self.orb_minutes]
        if len(b1) < self.orb_minutes:
            return None
        return float(b1["open"].iloc[0]), float(b1["high"].max()), float(b1["low"].min()), float(b1["close"].iloc[-1])

    def stop_distance(self, ctx: SessionContext, orng=None) -> float:
        orng = orng or self._opening_range(ctx)
        if not orng:
            return self.inst.min_stop_pts
        _, hi, lo, _ = orng
        # opposite side of the opening range, floored by a fraction of the typical range
        raw = max(hi - lo, self.atr_frac * 13 * max(ctx.bar_range_est, self.inst.tick_size))
        return float(min(max(raw, self.inst.min_stop_pts), self.inst.max_stop_pts))

    def on_bar_close(self, ctx: SessionContext, slot: int, position: int,
                     entries_today: int, bar_end_ts: pd.Timestamp) -> Decision:
        """Called on the decision-bar grid (BAR_MINUTES). The ORB decision is
        taken at the close of the FIRST decision bar if BAR_MINUTES == ORB_MINUTES;
        the runner should be configured with SPXF_BAR_MINUTES=5 for this strategy."""
        if position != 0:
            return Decision(position, "hold (orb: exits are stop/flatten)")
        if slot != 0 or entries_today > 0:
            return Decision(0, "orb: one decision per day, at the first bar")
        orng = self._opening_range(ctx)
        if not orng or not ctx.ready():
            return Decision(0, "orb: no range / no lookback")
        o, hi, lo, c = orng
        sd = self.stop_distance(ctx, orng)
        if c > o:
            return Decision(+1, f"orb long: first bar {o:.2f}->{c:.2f}", sd)
        if c < o and not self.long_only:
            return Decision(-1, f"orb short: first bar {o:.2f}->{c:.2f}", sd)
        return Decision(0, "orb: doji")
