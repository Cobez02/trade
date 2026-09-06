"""
Session clock — every time-of-day rule in the project goes through here.

WHY THIS FILE EXISTS. The options bot's flatten crons were written in fixed
UTC. From the first Monday of November they would have fired an hour early
(EST), the watcher job would have exited before the open and been killed
before the close, and every position would have been carried overnight. On a
prop account that is not a bug, it is a closed account. So: all session times
are expressed in the exchange's own zone (America/Chicago for CME) and
converted with zoneinfo, which knows the DST calendar. UTC never appears in a
rule.
"""
from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

import config as C

TZ = ZoneInfo(C.SESSION_TZ)
UTC = dt.timezone.utc

# CME Globex equity index holidays are not in a library; the practical rule is
# "no cash session => no day session for us". We treat any calendar day with
# no bars as closed (data-driven), and expose a small hard list for the live
# runner so it does not sit polling on a holiday.
US_MARKET_HOLIDAYS_2026 = {
    dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16), dt.date(2026, 4, 3),
    dt.date(2026, 5, 25), dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7),
    dt.date(2026, 11, 26), dt.date(2026, 12, 25),
}
# Early closes (13:00 ET = 12:00 CT): day after Thanksgiving, Christmas Eve.
EARLY_CLOSE_2026 = {dt.date(2026, 11, 27): "12:00", dt.date(2026, 12, 24): "12:00"}


def _hm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def session_bounds(day: dt.date):
    """(open, close, flatten, no_entry) as tz-aware datetimes in the exchange zone."""
    close_s = EARLY_CLOSE_2026.get(day, C.RTH_CLOSE)
    o = dt.datetime.combine(day, _hm(C.RTH_OPEN), TZ)
    c = dt.datetime.combine(day, _hm(close_s), TZ)
    # flatten/no-entry are offsets from the close so early-close days work
    flat_off = (dt.datetime.combine(day, _hm(C.RTH_CLOSE), TZ)
                - dt.datetime.combine(day, _hm(C.FLATTEN_AT), TZ))
    entry_off = (dt.datetime.combine(day, _hm(C.RTH_CLOSE), TZ)
                 - dt.datetime.combine(day, _hm(C.NO_NEW_ENTRY_AFTER), TZ))
    return o, c, c - flat_off, c - entry_off


def is_trading_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in US_MARKET_HOLIDAYS_2026


def now_ct() -> dt.datetime:
    return dt.datetime.now(UTC).astimezone(TZ)


def to_ct(ts) -> pd.DatetimeIndex | pd.Timestamp:
    """UTC (or naive-UTC) timestamps -> exchange zone."""
    idx = pd.DatetimeIndex(ts) if not isinstance(ts, (pd.DatetimeIndex, pd.Timestamp)) else ts
    if isinstance(idx, pd.Timestamp):
        return (idx.tz_localize("UTC") if idx.tzinfo is None else idx).tz_convert(C.SESSION_TZ)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(C.SESSION_TZ)


def rth_only(bars: pd.DataFrame) -> pd.DataFrame:
    """Keep only bars whose timestamp (bar START) falls inside the day session.

    Bars are labelled by their start; a 1-minute bar starting 14:59 is the last
    RTH bar. Early-close days are handled by the per-day bounds.
    """
    if bars.empty:
        return bars
    idx = to_ct(bars.index)
    out = bars.copy()
    out.index = idx
    keep = []
    for day, chunk in out.groupby(out.index.date):
        o, c, _, _ = session_bounds(day)
        m = (chunk.index >= o) & (chunk.index < c)
        keep.append(chunk[m])
    res = pd.concat(keep) if keep else out.iloc[0:0]
    return res


def resample_bars(bars_1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate 1-minute RTH bars into N-minute bars anchored on the session open.

    Anchoring matters: a 30-minute grid anchored at midnight would put bar
    boundaries at :00/:30, which happen to coincide with 08:30 CT — but an
    early-close day or a different bar size would not. We anchor per day.
    """
    if bars_1m.empty:
        return bars_1m
    parts = []
    for day, chunk in bars_1m.groupby(bars_1m.index.date):
        o, _, _, _ = session_bounds(day)
        agg = chunk.resample(f"{minutes}min", origin=o, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        agg = agg.dropna(subset=["open"])
        parts.append(agg)
    return pd.concat(parts)


def bar_slot(ts: pd.Timestamp, minutes: int) -> int:
    """0-based index of the N-minute bar within its session (time-of-day key)."""
    ts = to_ct(ts) if ts.tzinfo is None or str(ts.tzinfo) != C.SESSION_TZ else ts
    o, _, _, _ = session_bounds(ts.date())
    return int((ts - o).total_seconds() // (minutes * 60))
