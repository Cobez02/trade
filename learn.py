"""
Recursive learning layer.

Every position stores the FEATURES present at entry (which sleeve, signal score,
underlying RSI/MACD/trend, DTE, moneyness, spread, call/put, trend-alignment).
When it closes, the outcome (pnl%, win/loss, exit reason) is joined to those
features and appended to the trade journal.

`learn()` then aggregates the journal into:
  - per-sleeve expectancy  -> a WEIGHT (proven sleeves sized up, losers paused)
  - per-feature win-rates   -> GATES (feature buckets that reliably lose are blocked)
  - human-readable LESSONS  -> "what told us it was a good/bad trade"

`main` reads the weights/gates to steer the next round of trades. This is the
recursive loop: trade -> observe -> attribute -> adjust -> trade again.
"""
from __future__ import annotations
import datetime as dt
from engine import SLEEVES

MIN_SAMPLES_SLEEVE = 4      # trades before a sleeve's weight moves off 1.0
MIN_SAMPLES_GATE   = 5      # trades before a feature bucket can be gated
PAUSE_WINRATE      = 0.25   # sleeve win-rate below this (with enough n) -> paused
GATE_WINRATE       = 0.25   # feature bucket win-rate below this -> gated


# ---- feature bucketers ------------------------------------------------------
def rsi_bucket(rsi):
    if rsi is None: return "na"
    if rsi < 30: return "rsi<30"
    if rsi < 45: return "rsi30-45"
    if rsi < 55: return "rsi45-55"
    if rsi < 70: return "rsi55-70"
    return "rsi>70"

def dte_bucket(dte):
    if dte is None: return "na"
    if dte <= 4: return "dte<=4"
    if dte <= 8: return "dte5-8"
    return "dte9+"

def spread_bucket(sp):
    if sp is None: return "na"
    return "spread_tight" if sp <= 0.10 else ("spread_mid" if sp <= 0.25 else "spread_wide")

def align_bucket(feat):
    tu = feat.get("trend_up"); d = feat.get("direction")
    if tu is None or d is None: return "na"
    aligned = (d == "bull" and tu) or (d == "bear" and not tu)
    return "with_trend" if aligned else "counter_trend"

def feature_keys(feat: dict):
    """The bucket keys a trade belongs to, across every learned dimension."""
    return {
        "direction": ("call" if feat.get("direction") == "bull" else "put"),
        "entry_rsi": rsi_bucket(feat.get("rsi")),
        "dte": dte_bucket(feat.get("dte")),
        "spread": spread_bucket(feat.get("spread_pct")),
        "trend_align": align_bucket(feat),
        "macd_rising": "macd_up" if feat.get("macd_rising") else "macd_down",
    }


def _stat(rows):
    n = len(rows)
    if n == 0:
        return {"n": 0, "wins": 0, "win_rate": None, "avg_pnl_pct": None, "total_pnl": 0.0}
    wins = sum(1 for r in rows if (r.get("pnl_pct") or 0) > 0)
    avg = sum((r.get("pnl_pct") or 0) for r in rows) / n
    tot = sum((r.get("pnl") or 0) for r in rows)
    return {"n": n, "wins": wins, "win_rate": round(wins / n, 3),
            "avg_pnl_pct": round(avg, 4), "total_pnl": round(tot, 2)}


def _sleeve_weight(stat):
    """Map realized expectancy to a sizing/gating weight in [0, 1.8]."""
    if stat["n"] < MIN_SAMPLES_SLEEVE or stat["win_rate"] is None:
        return 1.0                                   # explore until we have data
    if stat["n"] >= 6 and stat["win_rate"] < PAUSE_WINRATE:
        return 0.0                                   # pause a proven loser
    w = 1.0 + 2.5 * stat["avg_pnl_pct"]              # expectancy-scaled
    return round(max(0.15, min(1.8, w)), 2)


def record_lesson(state: dict, closed: dict):
    """Append a closed trade (with its entry features) to the journal."""
    state.setdefault("journal", [])
    feat = closed.get("features", {}) or {}
    state["journal"].append({
        "symbol": closed.get("symbol"), "sleeve": closed.get("sleeve"),
        "pnl": closed.get("pnl"), "pnl_pct": closed.get("pnl_pct"),
        "exit_reason": closed.get("exit_reason"), "closed_on": closed.get("closed_on"),
        "features": feat, "buckets": feature_keys(feat),
    })
    state["journal"] = state["journal"][-500:]


def learn(state: dict) -> dict:
    """Recompute weights, gates, and lessons from the journal. Idempotent."""
    journal = [j for j in state.get("journal", []) if j.get("pnl_pct") is not None]
    out = {"updated": dt.date.today().isoformat(), "n_trades": len(journal),
           "sleeve": {}, "features": {}, "gates": [], "lessons": []}

    # per-sleeve
    for s in SLEEVES:
        rows = [j for j in journal if j.get("sleeve") == s]
        st = _stat(rows)
        st["weight"] = _sleeve_weight(st)
        out["sleeve"][s] = st

    # per-feature dimensions
    dims = ["direction", "entry_rsi", "dte", "spread", "trend_align", "macd_rising"]
    for dim in dims:
        buckets = {}
        for j in journal:
            key = j.get("buckets", {}).get(dim)
            if not key or key == "na":
                continue
            buckets.setdefault(key, []).append(j)
        out["features"][dim] = {k: _stat(v) for k, v in buckets.items()}

    # gates: feature buckets that reliably lose
    for dim, buckets in out["features"].items():
        for key, st in buckets.items():
            if st["n"] >= MIN_SAMPLES_GATE and st["win_rate"] is not None \
               and st["win_rate"] <= GATE_WINRATE and (st["avg_pnl_pct"] or 0) < 0:
                out["gates"].append({"dim": dim, "bucket": key,
                                     "n": st["n"], "win_rate": st["win_rate"],
                                     "avg_pnl_pct": st["avg_pnl_pct"]})

    out["lessons"] = _lessons(out)
    state["learning"] = out
    return out


def _lessons(learning: dict):
    lessons = []
    # sleeve verdicts
    ranked = sorted(learning["sleeve"].items(),
                    key=lambda kv: (kv[1]["avg_pnl_pct"] is not None, kv[1]["avg_pnl_pct"] or 0),
                    reverse=True)
    for s, st in ranked:
        if st["n"] == 0:
            continue
        if st["weight"] == 0.0:
            lessons.append(f"PAUSED '{s}': {st['win_rate']*100:.0f}% win over {st['n']} trades "
                           f"(avg {st['avg_pnl_pct']*100:+.0f}%) — stopped opening new {s} trades.")
        elif st["n"] >= MIN_SAMPLES_SLEEVE:
            verdict = "leading" if st["avg_pnl_pct"] > 0 else "lagging"
            lessons.append(f"'{s}' {verdict}: {st['win_rate']*100:.0f}% win, "
                           f"avg {st['avg_pnl_pct']*100:+.0f}% over {st['n']} trades "
                           f"(weight {st['weight']}).")
    # strongest feature signals (both good and bad)
    flat = []
    for dim, buckets in learning["features"].items():
        for key, st in buckets.items():
            if st["n"] >= 4 and st["avg_pnl_pct"] is not None:
                flat.append((dim, key, st))
    flat.sort(key=lambda t: t[2]["avg_pnl_pct"], reverse=True)
    for dim, key, st in flat[:2]:
        lessons.append(f"Best signal so far — {key}: {st['win_rate']*100:.0f}% win, "
                       f"avg {st['avg_pnl_pct']*100:+.0f}% ({st['n']}).")
    for dim, key, st in [t for t in flat if t[2]['avg_pnl_pct'] < 0][-2:]:
        lessons.append(f"Weak signal — {key}: {st['win_rate']*100:.0f}% win, "
                       f"avg {st['avg_pnl_pct']*100:+.0f}% ({st['n']}).")
    for g in learning["gates"]:
        lessons.append(f"GATED {g['bucket']} ({g['dim']}): {g['win_rate']*100:.0f}% win "
                       f"over {g['n']} — blocking new entries that match.")
    return lessons


# ---- helpers main() uses to APPLY what was learned --------------------------
def sleeve_weight(state: dict, sleeve: str) -> float:
    return state.get("learning", {}).get("sleeve", {}).get(sleeve, {}).get("weight", 1.0)

def is_gated(state: dict, feat: dict) -> str | None:
    """Return the gate reason if this trade's features match a gated bucket."""
    gates = state.get("learning", {}).get("gates", [])
    if not gates:
        return None
    bk = feature_keys(feat)
    for g in gates:
        if bk.get(g["dim"]) == g["bucket"]:
            return f"{g['bucket']} ({g['win_rate']*100:.0f}% win over {g['n']})"
    return None
