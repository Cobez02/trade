"""
SPX-Beater / futures — single source of truth for every knob.

Everything here is env-overridable (SPXF_*) so the same code runs research,
paper (Alpaca SPY/QQQ proxy) and live (Tradovate / ProjectX) without edits.
Nothing in this file talks to a network.

Instruments are CME micro index futures. The strategy logic is expressed in
PRICE (index points) and the risk logic in DOLLARS, so switching MES <-> MNQ
<-> the SPY/QQQ share proxy is a config change, never a code change.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field


def _env(name: str, default, cast=None):
    raw = os.environ.get(f"SPXF_{name}")
    if raw is None or raw == "":
        return default
    try:
        if cast is bool:
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return (cast or type(default))(raw)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Instrument:
    symbol: str            # research/continuous symbol
    tick_size: float       # minimum price increment (index points)
    tick_value: float      # $ per tick per contract
    point_value: float     # $ per 1.00 index point per contract
    commission_side: float # all-in commission + exchange + NFA per contract per side ($)
    slippage_ticks: float  # assumed adverse slippage per side (ticks)
    kind: str = "future"   # "future" | "equity" (share proxy)
    databento_symbol: str = ""   # e.g. "MES.v.0" (continuous, highest volume)
    min_stop_pts: float = 2.0    # never rest a protective stop tighter than this
    max_stop_pts: float = 50.0   # ...or wider than this (sizing goes to 0 instead)

    @property
    def slippage_cost(self) -> float:
        return self.slippage_ticks * self.tick_value

    @property
    def roundtrip_cost(self) -> float:
        """$ per contract per round trip: 2 commissions + 2 slippages."""
        return 2 * (self.commission_side + self.slippage_cost)


# Micro E-mini S&P 500. Tick 0.25 = $1.25. Commission assumptions are
# conservative for a Tradovate/Rithmic prop account (~$0.35 comm + ~$0.35
# exchange + ~$0.02 NFA + clearing); override with SPXF_COMM_SIDE.
MES = Instrument("MES", 0.25, 1.25, 5.0,
                 _env("COMM_SIDE", 1.00, float), _env("SLIP_TICKS", 1.0, float),
                 databento_symbol="MES.v.0", min_stop_pts=2.0, max_stop_pts=60.0)
# Micro E-mini Nasdaq-100. Tick 0.25 = $0.50.
MNQ = Instrument("MNQ", 0.25, 0.50, 2.0,
                 _env("COMM_SIDE", 1.00, float), _env("SLIP_TICKS", 1.0, float),
                 databento_symbol="MNQ.v.0", min_stop_pts=8.0, max_stop_pts=250.0)
# Full-size, for reference / scaling plans only.
ES = Instrument("ES", 0.25, 12.50, 50.0, _env("COMM_SIDE", 2.50, float), 1.0,
                databento_symbol="ES.v.0", min_stop_pts=2.0, max_stop_pts=60.0)
NQ = Instrument("NQ", 0.25, 5.00, 20.0, _env("COMM_SIDE", 2.50, float), 1.0,
                databento_symbol="NQ.v.0", min_stop_pts=8.0, max_stop_pts=250.0)
# Share proxies for the Alpaca paper lab. 1 "contract" = PROXY_SHARES shares.
# SPY ~ 1/10 of the S&P index; 50 shares of SPY ~= 1 MES in $ exposure
# (MES = $5 x index ~= 50 x SPY price). Slippage 1c/share is generous for SPY.
PROXY_SHARES = _env("PROXY_SHARES", 50, int)
SPY = Instrument("SPY", 0.01, 0.01 * PROXY_SHARES, 1.0 * PROXY_SHARES,
                 0.0, _env("PROXY_SLIP_CENTS", 1.0, float), kind="equity",
                 min_stop_pts=0.20, max_stop_pts=6.0)
QQQ = Instrument("QQQ", 0.01, 0.01 * PROXY_SHARES, 1.0 * PROXY_SHARES,
                 0.0, _env("PROXY_SLIP_CENTS", 1.0, float), kind="equity",
                 min_stop_pts=0.20, max_stop_pts=6.0)

INSTRUMENTS = {"MES": MES, "MNQ": MNQ, "ES": ES, "NQ": NQ, "SPY": SPY, "QQQ": QQQ}
INSTRUMENT = INSTRUMENTS[_env("INSTRUMENT", "MES", str).upper()]

# ---------------------------------------------------------------------------
# Session (CME equity index "day session" == US cash equity hours)
# ---------------------------------------------------------------------------
SESSION_TZ = "America/Chicago"           # CME clock. NEVER hardcode UTC offsets.
RTH_OPEN = "08:30"                        # CT (= 09:30 ET)
RTH_CLOSE = "15:00"                       # CT (= 16:00 ET) — cash close / settlement
FLATTEN_AT = _env("FLATTEN_AT", "14:52", str)   # CT. Every prop firm force-flattens
                                               # later (Topstep 15:10, MFFU 15:10 CT);
                                               # we leave 8 min for a fill + retry.
NO_NEW_ENTRY_AFTER = _env("NO_ENTRY_AFTER", "14:00", str)  # CT

# ---------------------------------------------------------------------------
# Strategy: intraday momentum with a time-of-day "noise area"
# (Zarattini-Aziz-Barbon 2024 on SPY; Baltussen et al. 2021 mechanism;
#  Quantitativo 2024 ES/NQ replication). Our implementation, see strategy/.
# ---------------------------------------------------------------------------
BAR_MINUTES = _env("BAR_MINUTES", 30, int)          # decision bar
NOISE_LOOKBACK_DAYS = _env("NOISE_LOOKBACK", 14, int)
NOISE_MULT = _env("NOISE_MULT", 1.0, float)         # band = mult * mean |move|
TRAIL_MODE = _env("TRAIL_MODE", "band", str)        # "band" | "vwap" | "both" | "none"
ALLOW_REVERSAL = _env("ALLOW_REVERSAL", True, bool)
MAX_ENTRIES_PER_DAY = _env("MAX_ENTRIES_PER_DAY", 2, int)
LONG_ONLY = _env("LONG_ONLY", False, bool)

# ORB (secondary strategy)
ORB_MINUTES = _env("ORB_MINUTES", 5, int)
ORB_STOP_ATR_FRAC = _env("ORB_STOP_ATR_FRAC", 0.10, float)   # stop = max(range, 10% of 14d ATR)

# ---------------------------------------------------------------------------
# Risk (the prop-rule engine). Dollars, per account. See risk/rules.py for the
# firm presets these are derived from.
# ---------------------------------------------------------------------------
ACCOUNT_SIZE = _env("ACCOUNT_SIZE", 50_000.0, float)
RISK_PER_TRADE = _env("RISK_PER_TRADE", 200.0, float)      # $ at the protective stop
MAX_CONTRACTS = _env("MAX_CONTRACTS", 5, int)               # firm contract cap (micros)
DAILY_KILL_LOSS = _env("DAILY_KILL_LOSS", 700.0, float)     # stop entering for the day
DAILY_PROFIT_CAP = _env("DAILY_PROFIT_CAP", 1_200.0, float) # stop entering once reached
                                                            # (consistency-rule protection)
STOP_RANGE_MULT = _env("STOP_RANGE_MULT", 0.5, float)      # stop = mult x noise width (0.5 = one sigma,
                                                            # i.e. "back to the open" = momentum failed),
                                                            # clamped to the instrument's [min,max]_stop_pts

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("SPXF_CACHE", os.path.join(ROOT, "cache"))
STATE_PATH = os.environ.get("SPXF_STATE", os.path.join(ROOT, "state_futures.json"))


def describe() -> str:
    i = INSTRUMENT
    return (f"instrument={i.symbol} tick={i.tick_size}/${i.tick_value} point=${i.point_value} "
            f"rt_cost=${i.roundtrip_cost:.2f} | bars={BAR_MINUTES}m lookback={NOISE_LOOKBACK_DAYS}d "
            f"mult={NOISE_MULT} trail={TRAIL_MODE} | risk/trade=${RISK_PER_TRADE:.0f} "
            f"kill=${DAILY_KILL_LOSS:.0f} cap=${DAILY_PROFIT_CAP:.0f} maxq={MAX_CONTRACTS} | "
            f"flatten {FLATTEN_AT} CT, no entries after {NO_NEW_ENTRY_AFTER} CT")
