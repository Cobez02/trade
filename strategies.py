"""
Signal sleeves. Each returns a list of Signal dicts:
    {underlying, direction ('bull'|'bear'), thesis, score}

Sources are deliberately distinct so the horse-race is meaningful:
  wsb  -> RETIRED as an entry source; survives as `crowd_veto` (see below)
  news -> headline catalysts (Alpaca market news + keyword sentiment)
  tech -> price-based indicators (RSI / MACD / trend)
  flow -> RETIRED (open-interest skew is unsigned — see engine.RETIRED_SLEEVES)

The retirement mechanics live in engine.RETIRED_SLEEVES / ACTIVE_SLEEVES:
retired sleeves keep their name, their open positions, their journal history
and their dashboard row; they just stop producing entry signals. The functions
below are kept intact so the record of what was tried stays runnable and a
future signed-volume feed could revive `flow` without archaeology.
"""
from __future__ import annotations
import datetime as dt
import requests

from engine import (
    Broker, rsi, macd_signal, trend_up,
    ContractType, GetOptionContractsRequest, AssetStatus,
    MIN_OPEN_INTEREST, SLEEVES, RETIRED_SLEEVES,
)

# Liquid, optionable universe for tech/flow scans
WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA",
             "META", "AMZN", "GOOGL", "NFLX", "IWM", "COIN", "PLTR"]

# ---------------------------------------------------------------------------
# Tradeable-options universe for the NEWS sleeve.
#
# WHY THIS EXISTS. `sleeve_news` reads whatever tickers appear in the Alpaca
# news feed and filtered them on exactly two things: alphabetic, and <=5 chars.
# It then handed the top `max_signals` to the entry path, where most of them
# died at engine.find_contracts() on MIN_OPEN_INTEREST. Week of 2026-08-24:
# 62 "no liquid contract" rejections across 38 distinct names -- the largest
# single rejection bucket, 3x the next one. The sleeve has 4 signal slots per
# run and was routinely burning 3 of them on names that could never trade,
# which is why the bot averaged 3.4 trades/session against caps allowing 8.
#
# WHAT THIS IS NOT. It is not a loosening of anything. Every gate downstream
# is untouched: the >=250 open-interest floor, the 4% spread cap, the vol-edge
# gate, the lottery/MAX screen, the expiration screen. This only stops the
# sleeve spending its slots on candidates that provably cannot clear them.
#
# HOW THE LIST WAS CHOSEN -- read this before editing it. Membership is by
# OPTIONS LIQUIDITY, never by realised P/L. Selecting names on 85 trades of
# realised P/L would be curve-fitting to noise: NVDA has 16 trades and -$26,
# VZ has ONE trade and +$876. A P/L-ranked universe would drop NVDA and keep
# Verizon, which is obviously backwards. Liquidity is a stable property of a
# name; five weeks of P/L is not.
#
# Tier 1 -- the 24 underlyings that actually cleared every gate and traded at
#           least once (empirical proof the chain supports 3-12 DTE at ~1.5%
#           OTM with OI >= 250).
# Tier 2 -- additional names with deep weekly chains, untested here. The OI
#           floor remains the arbiter; if one of these cannot quote, it is
#           rejected exactly as before, just without having cost a slot.
# EXCLUDED -- the 38 names observed failing find_contracts (OKTA, VEEV, CRM,
#           ESTC, NTNX, BOX, JAZZ, ACAD, DKS, ARGX, BBY, GAP, RBRK and the
#           rest). Note CRM: intuition says Salesforce is liquid, but it
#           failed 3 of 3 attempts in this DTE/moneyness window. The data
#           wins over the prior.
OPTIONABLE_UNIVERSE = {
    # Tier 1 -- proven tradeable by this bot
    "SPY", "QQQ", "IWM", "DIA", "GLD", "IBIT",
    "NVDA", "TSLA", "AAPL", "GOOGL", "GOOG", "META", "AMZN", "NFLX",
    "COIN", "MSTR", "INTC", "CSCO", "MRVL", "SLB", "VZ", "RKLB", "AI", "DRAM",
    # Tier 2 -- deep weekly chains, untested here
    "MSFT", "AMD", "PLTR", "AVGO", "MU", "SMCI", "ARM", "QCOM", "TSM",
    "TXN", "LRCX", "AMAT", "KLAC", "ON", "DELL", "SMH",
    "SLV", "TLT", "XLF", "XLE", "XLK", "EEM", "HYG", "USO", "ARKK",
    "JPM", "BAC", "WFC", "XOM", "CVX", "WMT", "DIS", "BA", "PFE",
    "T", "KO", "MCD", "HD", "UNH", "V", "MA", "COST",
    "MARA", "RIOT", "SOFI", "HOOD", "NIO", "RIVN", "LCID",
    "SNAP", "U", "DKNG", "ROKU", "F",
}

# Names we will NOT trade from noisy crowd feeds (too illiquid / weird tickers get
# validated at execution anyway, but this trims obvious junk)
MIN_UNDERLYING_PRICE = 8.0
MAX_UNDERLYING_PRICE = 1500.0


# ---------------------------------------------------------------------------
# 1) WSB / Reddit sentiment
# ---------------------------------------------------------------------------
def sleeve_wsb(broker: Broker, max_signals: int = 4):
    signals = []
    try:
        r = requests.get("https://tradestie.com/api/v1/apps/reddit", timeout=15)
        data = r.json()
    except Exception:
        return signals
    # sort by discussion volume, keep names with a clear sentiment lean
    data = [d for d in data if d.get("no_of_comments", 0) >= 5]
    data.sort(key=lambda d: d.get("no_of_comments", 0), reverse=True)
    for d in data:
        if len(signals) >= max_signals:
            break
        tkr = d.get("ticker", "").upper()
        score = float(d.get("sentiment_score", 0))
        if not tkr.isalpha() or len(tkr) > 5:
            continue
        if abs(score) < 0.05:      # too neutral to bet on
            continue
        spot = broker.stock_price(tkr)
        if spot is None or spot < MIN_UNDERLYING_PRICE or spot > MAX_UNDERLYING_PRICE:
            continue
        direction = "bull" if score > 0 else "bear"
        signals.append({
            "underlying": tkr, "direction": direction,
            "thesis": f"WSB {d.get('sentiment')} ({d.get('no_of_comments')} comments, "
                      f"score {score:+.2f})",
            "score": abs(score) + d.get("no_of_comments", 0) / 1000.0,
        })
    return signals


# ---------------------------------------------------------------------------
# 2) News catalyst (Alpaca market news + keyword sentiment)
# ---------------------------------------------------------------------------
_BULL = {"beats", "beat", "surge", "surges", "upgrade", "upgraded", "soars", "soar",
         "jumps", "jump", "record", "wins", "win", "approval", "approved", "raises",
         "raised", "tops", "rally", "rallies", "buy", "outperform", "strong", "growth",
         "expands", "boosts", "wins", "gains", "high"}
_BEAR = {"miss", "misses", "plunge", "plunges", "downgrade", "downgraded", "falls",
         "fall", "drops", "drop", "cuts", "cut", "probe", "lawsuit", "warning",
         "warns", "recall", "halts", "sinks", "slumps", "layoffs", "bankruptcy",
         "investigation", "fraud", "weak", "slashes", "plummet", "plunged", "low"}

def _headline_score(text: str) -> int:
    words = {w.strip(".,!?:;'\"()").lower() for w in text.split()}
    return len(words & _BULL) - len(words & _BEAR)

def sleeve_news(broker: Broker, api_key: str, secret_key: str, max_signals: int = 4):
    signals = []
    try:
        end = dt.datetime.now(dt.timezone.utc)
        start = end - dt.timedelta(days=2)
        headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}
        params = {"limit": 50, "start": start.isoformat(), "sort": "desc"}
        r = requests.get("https://data.alpaca.markets/v1beta1/news",
                         headers=headers, params=params, timeout=15)
        news = r.json().get("news", [])
    except Exception:
        return signals
    # aggregate sentiment per symbol
    agg = {}
    for art in news:
        text = (art.get("headline", "") + " " + art.get("summary", ""))
        s = _headline_score(text)
        if s == 0:
            continue
        for sym in art.get("symbols", []):
            sym = sym.upper()
            if not sym.isalpha() or len(sym) > 5:
                continue
            agg.setdefault(sym, {"score": 0, "n": 0})
            agg[sym]["score"] += s
            agg[sym]["n"] += 1
    # Rank only among names whose option chains can actually support an entry.
    # Filtering BEFORE the ranking (not after) is the whole point: the sleeve
    # has `max_signals` slots, and a candidate that cannot quote should never
    # have consumed one. Every downstream gate is unchanged.
    agg = {k: v for k, v in agg.items() if k in OPTIONABLE_UNIVERSE}
    ranked = sorted(agg.items(), key=lambda kv: abs(kv[1]["score"]), reverse=True)
    for sym, v in ranked:
        if len(signals) >= max_signals:
            break
        if abs(v["score"]) < 2:      # need a couple of confirming headlines
            continue
        spot = broker.stock_price(sym)
        if spot is None or spot < MIN_UNDERLYING_PRICE or spot > MAX_UNDERLYING_PRICE:
            continue
        direction = "bull" if v["score"] > 0 else "bear"
        signals.append({
            "underlying": sym, "direction": direction,
            "thesis": f"News flow {v['score']:+d} across {v['n']} articles (keyword sentiment)",
            "score": abs(v["score"]),
        })
    return signals


# ---------------------------------------------------------------------------
# 3) Technical indicators (RSI / MACD / trend)
# ---------------------------------------------------------------------------
def sleeve_tech(broker: Broker, max_signals: int = 4):
    signals = []
    scored = []
    for sym in WATCHLIST:
        df = broker.daily_bars(sym, days=120)
        if df is None or len(df) < 55:
            continue
        close = df["close"]
        r = rsi(close)
        hist_now, hist_prev = macd_signal(close)
        up = trend_up(close)
        direction, reason, sc = None, "", 0.0
        # Bullish: oversold bounce OR momentum turning up in an uptrend
        if r < 35 and hist_now > hist_prev:
            direction, reason, sc = "bull", f"RSI {r:.0f} oversold + MACD turning up", (35 - r) + 5
        elif up and hist_prev <= 0 < hist_now:
            direction, reason, sc = "bull", f"MACD bull cross in uptrend (RSI {r:.0f})", 8
        # Bearish: overbought rollover
        elif r > 70 and hist_now < hist_prev:
            direction, reason, sc = "bear", f"RSI {r:.0f} overbought + MACD turning down", (r - 70) + 5
        if direction:
            scored.append({"underlying": sym, "direction": direction,
                           "thesis": f"Technical: {reason}", "score": sc})
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:max_signals]


# ---------------------------------------------------------------------------
# 4) Options flow (call vs put open-interest skew = "smart money" positioning)
# ---------------------------------------------------------------------------
def _oi_by_type(broker: Broker, underlying: str, ctype, exp_lo, exp_hi) -> int:
    try:
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying], status=AssetStatus.ACTIVE,
            expiration_date_gte=exp_lo, expiration_date_lte=exp_hi,
            type=ctype, limit=500)
        resp = broker.trading.get_option_contracts(req)
        contracts = getattr(resp, "option_contracts", None) or []
    except Exception:
        return 0
    total = 0
    for c in contracts:
        try:
            total += int(c.open_interest) if c.open_interest is not None else 0
        except Exception:
            pass
    return total

# Broad-market ETFs are excluded from flow: their put OI is structurally inflated
# by portfolio hedging, so call/put skew reads bearish regardless of direction.
_FLOW_EXCLUDE = {"SPY", "QQQ", "IWM", "DIA"}

def sleeve_flow(broker: Broker, max_signals: int = 3):
    signals = []
    today = dt.date.today()
    exp_lo = (today + dt.timedelta(days=5)).isoformat()
    exp_hi = (today + dt.timedelta(days=45)).isoformat()
    scored = []
    for sym in WATCHLIST:
        if sym in _FLOW_EXCLUDE:
            continue
        call_oi = _oi_by_type(broker, sym, ContractType.CALL, exp_lo, exp_hi)
        put_oi = _oi_by_type(broker, sym, ContractType.PUT, exp_lo, exp_hi)
        if call_oi + put_oi < 5000:      # need real liquidity to read positioning
            continue
        ratio = call_oi / max(put_oi, 1)
        # Strong skew either way = a positioning bet worth following
        if ratio >= 1.8:
            scored.append({"underlying": sym, "direction": "bull",
                           "thesis": f"Options flow: call/put OI skew {ratio:.1f} (bullish positioning)",
                           "score": ratio})
        elif ratio <= 0.55:
            scored.append({"underlying": sym, "direction": "bear",
                           "thesis": f"Options flow: call/put OI skew {ratio:.2f} (bearish positioning)",
                           "score": 1 / ratio})
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:max_signals]


# ---------------------------------------------------------------------------
# Crowd veto — the one thing the WSB feed is still trusted to know
# ---------------------------------------------------------------------------
# The retail-options literature's most consistent finding is that
# attention-driven lottery demand overprices options on exactly the names the
# crowd is loudest about (Han & Kumar's retail-concentration result; Boyer &
# Vorkink's ex-ante skewness premium; Byun & Kim's lottery-option pricing).
# Buying options — either direction — on a name at peak crowd attention means
# paying that inflated premium, so heavy attention becomes a VETO on new buys.
#
# Two hard properties, both load-bearing:
#   * DEFENSIVE ONLY. The veto can only remove a candidate buy. It never
#     creates a position and is never inverted into a short — fading the crowd
#     by writing the overpriced option is a different, margin-intensive
#     strategy this account is not equipped to run.
#   * FAIL-OPEN. If the feed is down, the veto is empty and trading proceeds:
#     a third-party API's uptime must not gate the bot, and every entry still
#     passes the Tier-1 screens, which catch the same lottery profile from the
#     option's own measurable shape (ex-ante skew, moneyness, DTE).
CROWD_VETO_MIN_COMMENTS = 50     # ~an order of magnitude above ordinary chatter
CROWD_VETO_MAX_NAMES = 15        # a broken feed must not veto the whole book


def crowd_veto() -> dict:
    """{ticker: reason} for names at peak retail attention right now."""
    try:
        r = requests.get("https://tradestie.com/api/v1/apps/reddit", timeout=15)
        data = r.json()
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    crowded = []
    for d in data:
        if not isinstance(d, dict):
            continue
        tkr = str(d.get("ticker", "")).upper()
        try:
            n = int(d.get("no_of_comments") or 0)
        except Exception:
            continue
        if tkr.isalpha() and len(tkr) <= 5 and n >= CROWD_VETO_MIN_COMMENTS:
            crowded.append((n, tkr, str(d.get("sentiment") or "?")))
    crowded.sort(reverse=True)
    return {tkr: f"{n} WSB comments ({senti}) — attention-inflated premium"
            for n, tkr, senti in crowded[:CROWD_VETO_MAX_NAMES]}


def all_signals(broker: Broker, api_key: str, secret_key: str) -> dict:
    """Return {sleeve: [signals]} — each wrapped so one failure can't kill the run.

    Retired sleeves are present with an empty list, never absent: downstream
    code (dashboard state, the entry loop, the learner) iterates the full
    sleeve set and an absent key would read as a feed failure rather than a
    decision."""
    entry_fns = {
        "wsb":  lambda: sleeve_wsb(broker),
        "news": lambda: sleeve_news(broker, api_key, secret_key),
        "tech": lambda: sleeve_tech(broker),
        "flow": lambda: sleeve_flow(broker),
    }
    out = {}
    for name in SLEEVES:
        if name in RETIRED_SLEEVES:
            out[name] = []
            continue
        try:
            out[name] = entry_fns[name]()
        except Exception:
            out[name] = []
    return out
