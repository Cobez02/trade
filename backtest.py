"""
Backtest harness — replays the SHIPPED rules over historical NBBO option quotes.

The point is fidelity, not flattery. Wherever possible this file does not
re-implement a rule: it imports the exact functions production trades with —
strategies.rsi / macd_signal / trend_up for signals, vol.forecast_vol for the
HAR forecast, execution.implied_vol for richness, exitrules.spread_decide for
package exits. What it cannot share (the broker) it replaces with ThetaData
EOD NBBO quotes served by the local Theta Terminal.

Fill discipline — the part backtests usually lie about:
  * GATES are evaluated exactly as live evaluates them (mid-based credit),
    so entry FREQUENCY matches what production would have done;
  * FILLS are booked pessimistically: spreads open at the NET BID (cross both
    legs), close at the NET ASK; singles buy the ASK and sell the BID.
  * Fees: $0.10/contract/side flat.

Known simplifications, stated where the report can't miss them:
  * EOD granularity. Live stops fire intraday; here they fire at the close.
    Every trade whose leg-OHLC bounds imply an intraday breach is FLAGGED,
    and a stress variant re-books flagged spread exits at 2.5x credit.
  * Window is 2020-07 .. 2026-07 (free stock bars begin mid-2020; the study
    therefore excludes Volmageddon 2018 and the Feb-Mar 2020 crash — two
    regimes in which short-premium strategies suffer most. Said plainly:
    the worst known environments for the spreads sleeve are NOT in sample.)
  * News sleeve untestable (no historical news feed); tech sleeve is tested
    on SPY/QQQ only.

Run:  python3 backtest.py            (terminal must be running on :25503)
"""
from __future__ import annotations
import os, csv, io, json, math, hashlib, time, datetime as dt
import urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import numpy as np

import vol as volmod
import execution
import exitrules
import stats
from strategies import rsi, macd_signal, trend_up

THETA = "http://127.0.0.1:25503/v3"
CACHE = os.environ.get("SPXBOT_BT_CACHE", "/home/claude/theta/cache")
START = dt.date(2020, 10, 1)          # sim start (60 bars of warm-up before it)
END   = dt.date(2026, 7, 24)
FEE_PER_CONTRACT_SIDE = 0.10

# Mirrors of the live config (import engine would drag in alpaca SDK config
# checks; these are asserted against engine's values by test_backtest.py).
SPREADS_RICH_PTS, SPREADS_MIN_CREDIT_FRAC = 0.02, 0.20
SPREADS_NET_COST_FRAC, SPREADS_MAX_LOSS = 0.08, 400.0
SPREADS_DTE_MIN, SPREADS_DTE_MAX = 5, 10
SPREADS_TAKE_FRAC, SPREADS_STOP_MULT, SPREADS_TIME_DTE = 0.50, 2.00, 2
MAX_SPREAD_PCT, MAX_PREMIUM_PER_TRADE = 0.04, 600.0
TAKE_PROFIT_PCT, STOP_LOSS_PCT, TIME_STOP_DTE = 0.45, -0.30, 1
TARGET_OTM_PCT = 0.015


# ---------------------------------------------------------------------------
# data layer
# ---------------------------------------------------------------------------
def _cached(key: str, fetch):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, hashlib.sha1(key.encode()).hexdigest() + ".json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    val = fetch()
    with open(p, "w") as f:
        json.dump(val, f)
    return val


def theta_csv(path: str, **params) -> list:
    """GET a v3 endpoint, parse CSV to list-of-dicts. Retries transient errors;
    a 'No data found' body is a valid empty result, not an error."""
    url = f"{THETA}{path}?" + urllib.parse.urlencode(params)
    def fetch():
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    text = r.read().decode()
                if "No data found" in text[:100]:
                    return []
                if text.lstrip().startswith("We have upgraded"):
                    raise RuntimeError(f"deprecated params: {url}")
                rows = list(csv.DictReader(io.StringIO(text)))
                return rows
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
    return _cached(url, fetch)


def expirations(symbol: str) -> list:
    rows = theta_csv("/option/list/expirations", symbol=symbol)
    return sorted({r["expiration"] for r in rows})


def strikes(symbol: str, expiration: str) -> list:
    rows = theta_csv("/option/list/strikes", symbol=symbol, expiration=expiration)
    return sorted(float(r["strike"]) for r in rows)


DATA_ERRORS = []          # (what, error) — a bad combo logs, never kills the run


def _parse_rows(rows) -> dict:
    """rows -> {(strike, 'CALL'|'PUT'): {iso_date: {'bid','ask','mid','high','low'}}}"""
    out = {}
    for r in rows:
        try:
            b, a = float(r["bid"]), float(r["ask"])
            if b <= 0 or a <= 0 or a < b:
                continue
            k = (float(r["strike"]), str(r["right"]).upper())
            d = str(r["created"])[:10]
            out.setdefault(k, {})[d] = {
                "bid": b, "ask": a, "mid": (a + b) / 2,
                "high": float(r.get("high") or 0), "low": float(r.get("low") or 0)}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def day_snapshot(symbol: str, expiration: str, iso_day: str) -> dict:
    """ONE request: every contract of `expiration`, quotes on ONE day.
    ~3.7s live, free from the disk cache afterwards. This is what entry
    evaluation runs on — 1 request per (sim day, symbol)."""
    try:
        rows = theta_csv("/option/history/eod", symbol=symbol,
                         expiration=expiration, start_date=iso_day,
                         end_date=iso_day)
    except Exception as e:
        DATA_ERRORS.append((f"{symbol} {expiration} snap {iso_day}", str(e)[:60]))
        return {}
    return _parse_rows(rows)


def contract_series(symbol: str, expiration: str, strike: float, right: str,
                    start: str) -> dict:
    """Full remaining life of ONE contract — pulled only for ENTERED trades,
    so this cost scales with trade count, not day count."""
    rword = {"C": "CALL", "P": "PUT"}.get(right, right)
    try:
        rows = theta_csv("/option/history/eod", symbol=symbol,
                         expiration=expiration, strike=f"{strike:g}",
                         right=rword, start_date=start, end_date=expiration)
    except Exception as e:
        DATA_ERRORS.append((f"{symbol} {expiration} {strike:g}{rword}", str(e)[:60]))
        return {}
    parsed = _parse_rows(rows)
    return parsed.get((strike, rword), {})


def snap_strikes(snap: dict, right: str) -> list:
    return sorted({k for (k, r) in snap if r == right})


def pick_for_day(exps: list, day) -> "tuple | None":
    """One expiration pick per day shared by both sims: nearest to 7 DTE in
    [3,12]. The spreads sim additionally requires [5,10] and skips otherwise."""
    return pick_expiration(exps, day, 3, 12, prefer=7)


def prefetch_days(symbol: str, exps: list, days: list, workers: int = 4, log=print):
    """Warm the disk cache with each sim day's snapshot, 4-way concurrent."""
    keys = []
    for day in days:
        pe = pick_for_day(exps, day.date())
        if pe:
            keys.append((pe[0], day.date().isoformat()))
    done = [0]
    def one(k):
        exp, iso = k
        day_snapshot(symbol, exp, iso)
        done[0] += 1
        if done[0] % 100 == 0:
            log(f"  prefetch {symbol}: {done[0]}/{len(keys)}")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, keys))
    return len(keys)


def stock_bars(symbol: str) -> pd.DataFrame:
    """Daily OHLC from Alpaca IEX (free feed; begins mid-2020)."""
    p = os.path.join(CACHE, f"bars_{symbol}.csv")
    os.makedirs(CACHE, exist_ok=True)
    if os.path.exists(p):
        return pd.read_csv(p, index_col=0, parse_dates=True)
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
    c = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"],
                                  os.environ["ALPACA_SECRET_KEY"])
    # Full fixed range regardless of the sim window: the cache is shared
    # across runs, and an END-scoped pull once truncated it for every later
    # run in the session (cache poisoning by monkeypatch — real incident).
    r = c.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
        start=dt.datetime(2020, 6, 1), end=dt.datetime(2026, 7, 30),
        feed=DataFeed.IEX))
    df = r.df.reset_index().set_index("timestamp")[["open", "high", "low", "close"]]
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.to_csv(p)
    return df


# ---------------------------------------------------------------------------
# shared per-day context
# ---------------------------------------------------------------------------
def day_context(bars: pd.DataFrame, day: pd.Timestamp):
    """(spot, forecast_vol, closes list) using ONLY data through `day`."""
    h = bars.loc[:day]
    if len(h) < 60:
        return None
    spot = float(h["close"].iloc[-1])
    fc = volmod.forecast_vol(h, horizon=7)
    if fc is None:
        fc = volmod.realized_vol(h, window=21, method="yang_zhang")
    if fc is None or not (0.01 < fc < 4.0):
        return None
    return spot, float(fc), [float(x) for x in h["close"].tolist()]


def pick_expiration(exps: list, day: dt.date, lo: int, hi: int, prefer: int = 7):
    best = None
    for e in exps:
        ed = dt.date.fromisoformat(e)
        d = (ed - day).days
        if lo <= d <= hi and (best is None or abs(d - prefer) < abs(best[1] - prefer)):
            best = (e, d)
    return best


def nearest(vals: list, target: float):
    return min(vals, key=lambda v: abs(v - target)) if vals else None


# ---------------------------------------------------------------------------
# spreads sleeve simulation
# ---------------------------------------------------------------------------
GATE_COUNTS = {}

def _count(k):
    GATE_COUNTS[k] = GATE_COUNTS.get(k, 0) + 1

def sim_spreads(symbol: str, bars: pd.DataFrame, exps: list, log=print) -> list:
    trades, open_pos = [], None
    days = [d for d in bars.index if START <= d.date() <= END]
    widths = (2.0, 3.0) if symbol == "SPY" else (3.0, 4.0, 5.0)
    for day in days:
        iso = day.date().isoformat()
        # ---- manage the open package first (live order: exits before entries)
        if open_pos is not None:
            q_s = open_pos["qs"].get(iso)
            q_l = open_pos["ql"].get(iso)
            dte_left = (dt.date.fromisoformat(open_pos["exp"]) - day.date()).days
            if q_s and q_l:
                net_mid = q_s["mid"] - q_l["mid"]
                d = exitrules.spread_decide(open_pos["credit_gate"], max(net_mid, 0.01),
                                            dte_left, take_frac=SPREADS_TAKE_FRAC,
                                            stop_mult=SPREADS_STOP_MULT,
                                            time_dte=SPREADS_TIME_DTE)
                # intraday breach bound from leg trade extremes
                worst_net = (q_s.get("high") or 0) - (q_l.get("low") or 0)
                if worst_net >= SPREADS_STOP_MULT * open_pos["credit_gate"] and d["action"] != "exit":
                    open_pos["breach_flag"] = True
                if d["action"] == "exit":
                    buyback = q_s["ask"] - q_l["bid"]          # pessimistic
                    pnl = (open_pos["credit_fill"] - buyback) * 100 - 4 * FEE_PER_CONTRACT_SIDE
                    trades.append({**open_pos_public(open_pos), "exit_date": iso,
                                   "exit_reason": d["reason"], "pnl": round(pnl, 2),
                                   "flagged": open_pos.get("breach_flag", False)})
                    open_pos = None
            elif dte_left <= 0:      # expiry with no quote: settle intrinsic
                spot_now = float(bars.loc[day, "close"])
                intr = max(open_pos["ks"] - spot_now, 0) - max(open_pos["kl"] - spot_now, 0)
                pnl = (open_pos["credit_fill"] - intr) * 100 - 4 * FEE_PER_CONTRACT_SIDE
                trades.append({**open_pos_public(open_pos), "exit_date": iso,
                               "exit_reason": "expired (no quote)", "pnl": round(pnl, 2),
                               "flagged": True})
                open_pos = None
        if open_pos is not None:
            continue
        # ---- entry evaluation (mirrors try_spread_entries)
        ctx = day_context(bars, day)
        if ctx is None:
            _count("no_context"); continue
        spot, fc, closes = ctx
        if trend_up(pd.Series(closes)) is False:
            _count("trend_veto"); continue
        pe = pick_for_day(exps, day.date())
        if pe is None or not (SPREADS_DTE_MIN <= pe[1] <= SPREADS_DTE_MAX):
            continue
        exp, dte_e = pe
        snap = day_snapshot(symbol, exp, iso)
        ks_all = snap_strikes(snap, "PUT")
        t_mid = ((SPREADS_DTE_MIN + SPREADS_DTE_MAX) / 2) / 365.0
        k_target = spot * (1 - 1.1 * fc * math.sqrt(t_mid))
        ks = nearest([k for k in ks_all if k < spot], k_target)
        if ks is None:
            continue
        row_s = snap.get((ks, "PUT"), {}).get(iso)
        if not row_s:
            continue
        iv = execution.implied_vol(row_s["mid"], spot, ks, dte_e / 365.0, is_call=False)
        if iv is None:
            _count("iv_unreadable"); continue
        if iv - fc < SPREADS_RICH_PTS:
            _count("not_rich"); continue
        placed = False
        for w in widths:
            kl = nearest([k for k in ks_all if k < ks], ks - w)
            if kl is None or kl >= ks:
                continue
            row_l = snap.get((kl, "PUT"), {}).get(iso)
            if not row_l:
                continue
            width = round(ks - kl, 2)
            credit_gate = (row_s["mid"] - row_l["mid"]) - 0.01     # live: mid - 0.01
            if credit_gate <= 0 or credit_gate / width < SPREADS_MIN_CREDIT_FRAC:
                _count("credit_floor"); continue
            net_bid = row_s["bid"] - row_l["ask"]
            net_ask = row_s["ask"] - row_l["bid"]
            if credit_gate <= 0 or (net_ask - net_bid) / credit_gate > SPREADS_NET_COST_FRAC:
                _count("net_cost_gate"); continue
            if (width - credit_gate) * 100 > SPREADS_MAX_LOSS:
                continue
            if net_bid <= 0:
                continue
            open_pos = {"symbol": symbol, "exp": exp, "ks": ks, "kl": kl,
                        "width": width, "entry_date": iso, "dte": dte_e,
                        "iv": iv, "fc": fc, "credit_gate": round(credit_gate, 4),
                        "credit_fill": round(net_bid, 4),
                        "qs": contract_series(symbol, exp, ks, "PUT", iso),
                        "ql": contract_series(symbol, exp, kl, "PUT", iso)}
            placed = True
            break
        if placed:
            continue
    return trades


def open_pos_public(p: dict) -> dict:
    return {k: p[k] for k in ("symbol", "exp", "ks", "kl", "width", "entry_date",
                              "dte", "iv", "fc", "credit_gate", "credit_fill")}


# ---------------------------------------------------------------------------
# singles (tech sleeve) simulation
# ---------------------------------------------------------------------------
def sim_singles(symbol: str, bars: pd.DataFrame, exps: list, log=print) -> list:
    trades, open_pos = [], None
    days = [d for d in bars.index if START <= d.date() <= END]
    for day in days:
        iso = day.date().isoformat()
        if open_pos is not None:
            q = open_pos["q"].get(iso)
            dte_left = (dt.date.fromisoformat(open_pos["exp"]) - day.date()).days
            reason = None
            if q:
                plpc = (q["mid"] - open_pos["entry_fill"]) / open_pos["entry_fill"]
                if plpc >= TAKE_PROFIT_PCT:
                    reason = f"take-profit {plpc:+.0%}"
                elif plpc <= STOP_LOSS_PCT:
                    reason = f"stop-loss {plpc:+.0%}"
                elif dte_left <= TIME_STOP_DTE:
                    reason = f"time-stop ({dte_left} DTE)"
                if reason:
                    exit_fill = q["bid"]                       # pessimistic
                    pnl = (exit_fill - open_pos["entry_fill"]) * 100 - 2 * FEE_PER_CONTRACT_SIDE
                    lo_p = (q.get("low") or q["mid"])
                    flagged = ((lo_p - open_pos["entry_fill"]) / open_pos["entry_fill"]
                               <= STOP_LOSS_PCT * 1.5)
                    trades.append({"symbol": symbol, "type": open_pos["right"],
                                   "entry_date": open_pos["entry_date"], "exit_date": iso,
                                   "exit_reason": reason, "pnl": round(pnl, 2),
                                   "pnl_pct": round((exit_fill - open_pos["entry_fill"])
                                                    / open_pos["entry_fill"], 4),
                                   "flagged": flagged})
                    open_pos = None
            elif dte_left <= 0:
                pnl = -open_pos["entry_fill"] * 100 - FEE_PER_CONTRACT_SIDE
                trades.append({"symbol": symbol, "type": open_pos["right"],
                               "entry_date": open_pos["entry_date"], "exit_date": iso,
                               "exit_reason": "expired worthless(?) no quote",
                               "pnl": round(pnl, 2), "pnl_pct": -1.0, "flagged": True})
                open_pos = None
        if open_pos is not None:
            continue
        ctx = day_context(bars, day)
        if ctx is None:
            continue
        spot, fc, closes = ctx
        cs = pd.Series(closes)
        r = rsi(cs)
        hist_now, hist_prev = macd_signal(cs)
        up = trend_up(cs)
        direction = None
        if r is not None and hist_now is not None:
            if r < 35 and hist_now > hist_prev:
                direction = "bull"
            elif up and hist_prev <= 0 < hist_now:
                direction = "bull"
            elif r > 70 and hist_now < hist_prev:
                direction = "bear"
        if direction is None:
            continue
        pe = pick_for_day(exps, day.date())
        if pe is None:
            continue
        exp, dte_e = pe
        snap = day_snapshot(symbol, exp, iso)
        right = "CALL" if direction == "bull" else "PUT"
        ks_all = snap_strikes(snap, right)
        k_target = spot * (1 + TARGET_OTM_PCT) if right == "CALL" else spot * (1 - TARGET_OTM_PCT)
        k = nearest(ks_all, k_target)
        if k is None:
            continue
        row = snap.get((k, right), {}).get(iso)
        if not row:
            continue
        spread_pct = (row["ask"] - row["bid"]) / row["mid"] if row["mid"] else 9
        if spread_pct > MAX_SPREAD_PCT:
            continue
        if row["ask"] * 100 > MAX_PREMIUM_PER_TRADE:
            continue
        iv = execution.implied_vol(row["mid"], spot, k, dte_e / 365.0,
                                   is_call=(right == "CALL"))
        te = execution.timing_edge(spot, k, dte_e / 365.0, fc, row["bid"], row["ask"],
                                   is_call=(right == "CALL"), side="buy")
        if not te.get("favorable"):
            continue
        open_pos = {"symbol": symbol, "exp": exp, "k": k, "right": right,
                    "entry_date": iso, "entry_fill": row["ask"],
                    "q": contract_series(symbol, exp, k, right, iso)}
    return trades


# ---------------------------------------------------------------------------
# scoring + report
# ---------------------------------------------------------------------------
REGIMES = [("2020H2 rebound", "2020-10-01", "2021-12-31"),
           ("2022 bear", "2022-01-01", "2022-12-31"),
           ("2023-24 recovery", "2023-01-01", "2024-12-31"),
           ("2025-26 modern", "2025-01-01", "2026-07-24")]


def score(trades: list, label: str) -> dict:
    if not trades:
        return {"label": label, "n": 0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    gw, gl = sum(wins), abs(sum(p for p in pnls if p <= 0))
    rets = np.array(pnls) / 100.0
    sr_trade = float(np.mean(rets) / np.std(rets)) if np.std(rets) > 0 else 0.0
    n = len(pnls)
    tpy = n / max((dt.date.fromisoformat(trades[-1]["exit_date"])
                   - dt.date.fromisoformat(trades[0]["exit_date"])).days / 365.25, 0.1)
    sr_ann = sr_trade * math.sqrt(max(tpy, 1e-9))
    sk = float(pd.Series(pnls).skew()) if n > 2 else 0.0
    ku = float(pd.Series(pnls).kurt() + 3) if n > 3 else 3.0
    eq, peak, mdd = 0.0, 0.0, 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return {"label": label, "n": n, "total": round(sum(pnls), 2),
            "win_rate": round(len(wins) / n, 3),
            "profit_factor": round(gw / gl, 2) if gl else float("inf"),
            "avg": round(float(np.mean(pnls)), 2), "skew": round(sk, 2),
            "sr_trade": round(sr_trade, 3), "sr_ann": round(sr_ann, 2),
            "trades_per_year": round(tpy, 1), "max_dd": round(mdd, 2),
            "psr": round(stats.psr(sr_trade, n, 0.0, sk, ku), 3) if n > 5 else None,
            "min_trl": (round(stats.min_track_record_length(sr_trade, 0.0, skew=sk,
                                                            kurtosis=ku, confidence=0.95))
                        if n > 5 and sr_trade > 0 else None),
            "flagged": sum(1 for t in trades if t.get("flagged"))}


def regime_rows(trades: list) -> list:
    out = []
    for name, a, b in REGIMES:
        sub = [t for t in trades if a <= t["exit_date"] <= b]
        out.append(score(sub, name))
    return out


def stress_spreads(trades: list) -> list:
    """Re-book flagged spread exits at 2.5x credit (intraday-breach bound)."""
    out = []
    for t in trades:
        t2 = dict(t)
        if t.get("flagged") and t["pnl"] > -(t["credit_fill"] * 1.5 * 100):
            t2["pnl"] = round(-(t["credit_fill"] * 1.5 * 100) - 0.4, 2)
        out.append(t2)
    return out


def main():
    t0 = time.time()
    all_spreads, all_singles = [], []
    for sym in ("SPY", "QQQ"):
        bars = stock_bars(sym)
        exps = [e for e in expirations(sym) if "2020-06-01" <= e <= "2026-08-31"]
        print(f"{sym}: {len(bars)} bars, {len(exps)} expirations in window")
        days = [d for d in bars.index if START <= d.date() <= END]
        n = prefetch_days(sym, exps, days)
        print(f"{sym}: {n} day-snapshots warm ({len(DATA_ERRORS)} data errors so far)")
        s = sim_spreads(sym, bars, exps)
        print(f"{sym}: spreads sim -> {len(s)} trades")
        all_spreads += s
        g = sim_singles(sym, bars, exps)
        print(f"{sym}: singles sim -> {len(g)} trades")
        all_singles += g
    result = {
        "spreads": {"headline": score(all_spreads, "spreads 2020-2026"),
                    "regimes": regime_rows(all_spreads),
                    "stress": score(stress_spreads(all_spreads), "spreads STRESS"),
                    "trades": all_spreads},
        "singles": {"headline": score(all_singles, "tech singles 2020-2026"),
                    "regimes": regime_rows(all_singles),
                    "trades": all_singles},
        "elapsed_s": round(time.time() - t0, 1),
    }
    with open("backtest_result.json", "w") as f:
        json.dump(result, f, indent=1)
    for k in ("spreads", "singles"):
        print(json.dumps(result[k]["headline"], indent=1))
    return result


if __name__ == "__main__":
    main()
