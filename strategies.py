"""
Four independent signal sleeves. Each returns a list of Signal dicts:
    {underlying, direction ('bull'|'bear'), thesis, score}

Sources are deliberately distinct so the horse-race is meaningful:
  wsb  -> retail crowd chatter (r/wallstreetbets sentiment feed)
  news -> headline catalysts (Alpaca market news + keyword sentiment)
  tech -> price-based indicators (RSI / MACD / trend)
  flow -> options-market positioning (call vs put open-interest skew)
"""
from __future__ import annotations
import datetime as dt
import requests

from engine import (
    Broker, rsi, macd_signal, trend_up,
    ContractType, GetOptionContractsRequest, AssetStatus,
    MIN_OPEN_INTEREST,
)

# Liquid, optionable universe for tech/flow scans
WATCHLIST = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA",
             "META", "AMZN", "GOOGL", "NFLX", "IWM", "COIN", "PLTR"]

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


def all_signals(broker: Broker, api_key: str, secret_key: str) -> dict:
    """Return {sleeve: [signals]} — each wrapped so one failure can't kill the run."""
    out = {}
    for name, fn in [
        ("wsb",  lambda: sleeve_wsb(broker)),
        ("news", lambda: sleeve_news(broker, api_key, secret_key)),
        ("tech", lambda: sleeve_tech(broker)),
        ("flow", lambda: sleeve_flow(broker)),
    ]:
        try:
            out[name] = fn()
        except Exception:
            out[name] = []
    return out
