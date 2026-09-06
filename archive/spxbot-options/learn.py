"""
Recursive learning layer, rebuilt on `stats.py`.

WHAT CHANGED AND WHY
--------------------
The first version of this file gated a feature bucket after 5 trades with a
win-rate at or below 25%, and paused a whole sleeve after 6. A Monte-Carlo
replay of that exact code against the bot's own journal put the probability
that the learning loop made the bot WORSE at 56-59%, costing 2.7-3.3
percentage points per trade. Four defects produced that, and each is now
handled by a function in `stats.py`:

  CLUSTERING     All 7 settled trades happened on one day, so they were not 7
                 independent observations. n_eff was 1.0, not 7.
                 -> every count here is now an EFFECTIVE count.

  MULTIPLE       The rule ran against 17 buckets at once. Its size is 0.337 and
  TESTING        the family-wise error rate across those buckets is 0.9991 - a
                 gate firing on noise was not a risk, it was the base case.
                 An honest single gate decision needs n ~ 61, or ~131 under
                 Bonferroni. The code used 5.
                 -> the gate now activates at n_eff >= 30 and ramps to full
                    strength at n_eff >= 120, and reports the shortfall.

  COLLINEARITY   `align_bucket()` returns `with_trend` iff
                 `(bull and trend_up) or (bear and not trend_up)`, and every
                 trade in the journal had `trend_up=False`. So
                 `counter_trend <=> bull <=> call`, Cramer's V = 1.0000.
                 Gating "call" and "counter_trend" was one hypothesis counted
                 twice, and the `if len(buckets) < 2: continue` guard could not
                 see it because both dimensions genuinely had two buckets.
                 -> dimensions are deduped by Cramer's V before anything is
                    learned from them, and the drop is reported, not silent.

  ABSORBING      The worst one. A hard gate blocks new entries in a bucket, so
  STATES         the bucket can never accumulate the samples that would clear
                 the gate, so `learn()` re-fires it forever. P(exit) = 0. The
                 bot became permanently short-only off 5 clustered trades, and
                 the gate did not even protect the four call positions already
                 open.
                 -> THERE ARE NO HARD BLOCKS ANYWHERE IN THIS FILE. Evidence
                    maps to a size MULTIPLIER in [0.25, 1.25] that is never
                    zero. A discouraged bucket trades at quarter size, which
                    still generates the evidence that can exonerate it.

The honest cost of this: the learner will now say "I do not know" for a long
time, because on this data it does not. Moving one sleeve weight off 1.0 with
95% confidence needs a Minimum Track Record Length of roughly 1,400 trades.
The old code moved sleeve weights on 4. Every conclusion below now ships with
the sample size it would actually need, so the gap is visible rather than
implied.
"""
from __future__ import annotations

import datetime as dt
import math

import stats
from engine import SLEEVES

# Retained for reporting continuity; neither gates anything any more.
MIN_SAMPLES_SLEEVE = 4
MIN_SAMPLES_GATE = 5

# The live thresholds.
GATE_FLOOR = stats.GATE_FLOOR              # 0.25 - most-discouraged size
GATE_CEIL = stats.GATE_CEIL                # 1.25 - most-encouraged size
GATE_ACTIVATION_N = stats.GATE_ACTIVATION_N  # 30 effective samples

# Dimensions, in order of interpretability. `dedupe_dims` keeps the FIRST of any
# collinear pair, so this order decides which survives: `direction` is the one a
# human can act on, so it leads.
DIMS = ["direction", "entry_rsi", "dte", "spread", "trend_align", "macd_rising"]


# ---- totality helpers -------------------------------------------------------
# The journal round-trips through JSON and survives crashes mid-write, so a row
# can arrive with a string where a number belongs, a null where a dict belongs,
# or a NaN that would poison every mean downstream. Nothing in this module may
# raise on that: an exception here kills the whole trading loop, and the loop is
# the thing that closes open positions.
#
# The uniform policy is FAIL TO UNKNOWN, never fail to a bucket. A value that
# cannot be read becomes "na", which is excluded from every statistic. Coercing
# junk into a real bucket would let corrupt rows vote on sizing.
def _num(v):
    """float(v) if it is a finite real number, else None. Never raises."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _dict(v) -> dict:
    """v if it is a dict, else {}. Lets `.get` chains be written flat."""
    return v if isinstance(v, dict) else {}


# ---- feature bucketers ------------------------------------------------------
def rsi_bucket(rsi):
    rsi = _num(rsi)
    if rsi is None: return "na"
    if rsi < 30: return "rsi<30"
    if rsi < 45: return "rsi30-45"
    if rsi < 55: return "rsi45-55"
    if rsi < 70: return "rsi55-70"
    return "rsi>70"

def dte_bucket(dte):
    dte = _num(dte)
    if dte is None: return "na"
    if dte <= 4: return "dte<=4"
    if dte <= 8: return "dte5-8"
    return "dte9+"

def spread_bucket(sp):
    sp = _num(sp)
    if sp is None: return "na"
    return "spread_tight" if sp <= 0.10 else ("spread_mid" if sp <= 0.25 else "spread_wide")

def align_bucket(feat):
    feat = _dict(feat)
    tu = feat.get("trend_up"); d = feat.get("direction")
    if tu is None or d is None: return "na"
    aligned = (d == "bull" and tu) or (d == "bear" and not tu)
    return "with_trend" if aligned else "counter_trend"

def feature_keys(feat: dict):
    """The bucket keys a trade belongs to, across every learned dimension."""
    feat = _dict(feat)
    return {
        "direction": ("call" if feat.get("direction") == "bull" else "put"),
        "entry_rsi": rsi_bucket(feat.get("rsi")),
        "dte": dte_bucket(feat.get("dte")),
        "spread": spread_bucket(feat.get("spread_pct")),
        "trend_align": align_bucket(feat),
        # NB: must distinguish "MACD was falling" from "we never recorded MACD".
        # Collapsing None into macd_down puts every trade in one fake bucket,
        # which would eventually discourage that bucket and shrink ALL trading.
        "macd_rising": ("na" if feat.get("macd_rising") is None
                        else ("macd_up" if feat.get("macd_rising") else "macd_down")),
    }


# ---- descriptive statistics -------------------------------------------------
def _rows(rows):
    """Only the dict-shaped entries of a journal-like sequence."""
    if not isinstance(rows, (list, tuple)):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _pnls(rows):
    """The finite pnl_pct values in `rows`, in order. Unreadable rows drop."""
    out = []
    for r in _rows(rows):
        v = _num(r.get("pnl_pct"))
        if v is not None:
            out.append(v)
    return out


def _day_of(row) -> str:
    """Cluster key. Trades that share a session share the market's move."""
    row = _dict(row)
    d = row.get("closed_on") or row.get("entry_date") or ""
    return str(d)[:10] or "unknown"


def _cluster(rows):
    """[[pnl_pct, ...], ...] grouped by settlement day, for stats.effective_n."""
    by_day = {}
    for r in _rows(rows):
        v = _num(r.get("pnl_pct"))
        if v is None:
            continue
        by_day.setdefault(_day_of(r), []).append(v)
    return list(by_day.values())


def _stat(rows) -> dict:
    """Descriptive stats PLUS the effective sample size after clustering."""
    vals = _pnls(rows)
    n = len(vals)
    if n == 0:
        return {"n": 0, "wins": 0, "win_rate": None, "avg_pnl_pct": None,
                "total_pnl": 0.0, "n_eff": 0.0, "n_days": 0,
                "rho": None, "deff": None, "var": 0.0}
    wins = sum(1 for v in vals if v > 0)
    avg = sum(vals) / n
    var = (sum((v - avg) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
    tot = sum((_num(r.get("pnl")) or 0.0) for r in _rows(rows))
    eff = stats.effective_n(_cluster(rows))
    return {"n": n, "wins": wins, "win_rate": round(wins / n, 3),
            "avg_pnl_pct": round(avg, 4), "total_pnl": round(tot, 2),
            "n_eff": eff["n_eff"], "n_days": eff["n_clusters"],
            "rho": eff["rho"], "deff": eff["deff"], "var": round(var, 6)}


def _sharpe(rows) -> float | None:
    """Per-trade Sharpe. Not annualized - the MinTRL below is in TRADES."""
    vals = _pnls(rows)
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))
    return (m / sd) if sd > 1e-12 else None


def _moments(rows):
    """(skew, raw kurtosis) of the P&L series - both feed the track-record math.

    Kurtosis is RAW (Normal = 3.0), not excess. Passing excess kurtosis here is
    the classic error and it makes every downstream number too optimistic.
    """
    vals = _pnls(rows)
    n = len(vals)
    if n < 4:
        return 0.0, 3.0
    m = sum(vals) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / n)
    if sd <= 1e-12:
        return 0.0, 3.0
    g3 = sum(((v - m) / sd) ** 3 for v in vals) / n
    g4 = sum(((v - m) / sd) ** 4 for v in vals) / n
    return round(g3, 4), round(g4, 4)


def record_lesson(state: dict, closed: dict):
    """Append a closed trade (with its entry features) to the journal."""
    if not isinstance(state, dict):
        return
    closed = _dict(closed)
    if not isinstance(state.get("journal"), list):
        state["journal"] = []
    feat = _dict(closed.get("features"))
    state["journal"].append({
        "symbol": closed.get("symbol"), "sleeve": closed.get("sleeve"),
        "pnl": closed.get("pnl"), "pnl_pct": closed.get("pnl_pct"),
        "exit_reason": closed.get("exit_reason"), "closed_on": closed.get("closed_on"),
        "features": feat, "buckets": feature_keys(feat),
    })
    state["journal"] = state["journal"][-500:]


# ---- the learner ------------------------------------------------------------
def learn(state: dict) -> dict:
    """Recompute weights, multipliers and lessons from the journal. Idempotent.

    Nothing this function returns can block a trade. The strongest statement it
    can make is "size this at a quarter", and it only makes that once the
    evidence survives clustering, deduplication and shrinkage.
    """
    if not isinstance(state, dict):
        state = {}
    journal = [j for j in _rows(state.get("journal"))
               if _num(j.get("pnl_pct")) is not None]
    overall = _stat(journal)
    pooled = overall["avg_pnl_pct"] or 0.0

    out = {
        "updated": dt.date.today().isoformat(),
        "n_trades": len(journal),
        "n_eff": overall["n_eff"],
        "n_days": overall["n_days"],
        "pooled_pnl_pct": overall["avg_pnl_pct"],
        "clustering": {"rho": overall.get("rho"), "deff": overall.get("deff")},
        "sleeve": {}, "features": {}, "gates": [], "lessons": [],
        "dims_kept": [], "dims_dropped": {}, "diagnostics": {},
    }

    # ---- 1. sleeves -----------------------------------------------------
    # Sleeve weights move on the same continuous, bounded, never-zero rule as
    # feature buckets. `weight = 0` - the old "pause a proven loser" - is gone:
    # a paused sleeve stops producing the evidence that would unpause it.
    sleeve_rows = {s: [j for j in journal if j.get("sleeve") == s] for s in SLEEVES}
    sleeve_inputs = []
    for s, rows in sleeve_rows.items():
        st = _stat(rows)
        if st["n"] > 0:
            sleeve_inputs.append({"key": s, "mean": st["avg_pnl_pct"],
                                  "var": max(st["var"], 1e-6), "n": st["n"]})
    shrunk_by_sleeve = {r["key"]: r for r in stats.eb_shrink(sleeve_inputs)}

    for s in SLEEVES:
        rows = sleeve_rows[s]
        st = _stat(rows)
        sh = shrunk_by_sleeve.get(s)
        shrunk = sh["shrunk"] if sh else None
        sleeve_pooled = sh["pooled"] if sh else 0.0
        decision = stats.gate_decision(
            shrunk if shrunk is not None else 0.0, st["n_eff"], sleeve_pooled)
        st["weight"] = decision["mult"]
        st["shrunk_pnl_pct"] = shrunk
        st["confidence"] = decision["confidence"]
        st["evidence"] = decision["reason"]

        # How much data would it take to say this honestly?
        sr = _sharpe(rows)
        g3, g4 = _moments(rows)
        if sr is not None and sr > 0:
            trl = stats.min_track_record_length(sr, 0.0, g3, g4, 0.95)
            st["min_track_record"] = (round(trl) if math.isfinite(trl) else None)
            st["psr"] = round(stats.psr(sr, max(st["n"], 2), 0.0, g3, g4), 4)
        else:
            st["min_track_record"] = None
            st["psr"] = None
        st["sharpe_per_trade"] = round(sr, 4) if sr is not None else None
        out["sleeve"][s] = st

    # ---- 2. deduplicate feature dimensions ------------------------------
    # Before ANY bucket statistic is trusted, drop dimensions that are restating
    # a dimension already counted. This is what the old `len(buckets) < 2` guard
    # was trying and failing to do.
    kept, dropped = stats.dedupe_dims(journal, DIMS, threshold=0.85)
    out["dims_kept"] = kept
    out["dims_dropped"] = {k: {"duplicates": v[0], "cramers_v": v[1]}
                           for k, v in dropped.items()}

    # ---- 3. per-bucket statistics, shrunk toward the pooled mean ---------
    for dim in DIMS:
        buckets = {}
        for j in journal:
            key = _dict(j.get("buckets")).get(dim)
            if not isinstance(key, str) or not key or key == "na":
                continue
            buckets.setdefault(key, []).append(j)
        if not buckets:
            out["features"][dim] = {}
            continue

        bstats = {k: _stat(v) for k, v in buckets.items()}
        shrink_in = [{"key": k, "mean": st["avg_pnl_pct"],
                      "var": max(st["var"], 1e-6), "n": st["n"]}
                     for k, st in bstats.items() if st["n"] > 0]
        shrunk_rows = {r["key"]: r for r in stats.eb_shrink(shrink_in)}

        for k, st in bstats.items():
            sh = shrunk_rows.get(k)
            st["shrunk_pnl_pct"] = sh["shrunk"] if sh else None
            st["shrink_weight"] = sh["weight"] if sh else None
            bucket_pooled = sh["pooled"] if sh else pooled
            if dim in kept:
                d = stats.gate_decision(st["shrunk_pnl_pct"] if sh else 0.0,
                                        st["n_eff"], bucket_pooled)
            else:
                # A deduplicated dimension is still REPORTED - it is often the
                # more readable description of what happened - but it must not
                # influence sizing, because the dimension it duplicates already
                # did. Counting it twice is exactly the old bug.
                d = {"mult": 1.0, "confidence": 0.0,
                     "reason": f"dimension deduplicated against "
                               f"'{dropped.get(dim, ('?',))[0]}' — reported, not acted on"}
            st["mult"] = d["mult"]
            st["confidence"] = d["confidence"]
            st["evidence"] = d["reason"]
            # Samples still needed for one honest decision on this bucket.
            st["n_needed"] = stats.required_n_for_gate(
                p_null=0.40, p_alt=0.25,
                alpha=stats.bonferroni_alpha(0.05, max(1, _n_buckets(bstats, kept, dim))),
                power=0.80)
        out["features"][dim] = bstats

    # ---- 4. "gates" - now DISCOURAGED buckets, never blocks --------------
    for dim in kept:
        for key, st in out["features"].get(dim, {}).items():
            if st.get("mult", 1.0) < 1.0:
                out["gates"].append({
                    "dim": dim, "bucket": key, "mult": st["mult"],
                    "n": st["n"], "n_eff": st["n_eff"],
                    "win_rate": st["win_rate"], "avg_pnl_pct": st["avg_pnl_pct"],
                    "shrunk_pnl_pct": st.get("shrunk_pnl_pct"),
                    "confidence": st.get("confidence"),
                })

    # ---- 5. diagnostics: what the old rule would have done ---------------
    n_buckets = sum(len(b) for b in out["features"].values())
    out["diagnostics"] = {
        "n_buckets_tested": n_buckets,
        "fwer_if_uncorrected": round(stats.fwer(0.337, max(1, n_buckets)), 4),
        "bonferroni_alpha": round(stats.bonferroni_alpha(0.05, max(1, n_buckets)), 5),
        "n_needed_uncorrected": stats.required_n_for_gate(0.40, 0.25, 0.05, 0.80),
        "n_needed_bonferroni": stats.required_n_for_gate(
            0.40, 0.25, stats.bonferroni_alpha(0.05, max(1, n_buckets)), 0.80),
        "activation_n_eff": GATE_ACTIVATION_N,
        "hard_gates_issued": 0,   # structurally impossible now; asserted by tests
    }

    out["lessons"] = _lessons(out)
    state["learning"] = out
    return out


def _n_buckets(bstats, kept, dim) -> int:
    try:
        return len(bstats) if dim in kept else 1
    except TypeError:
        return 1


def _lessons(learning: dict) -> list:
    """Human-readable conclusions, each carrying the evidence behind it.

    Every claim about a sleeve or a bucket is followed by how far the sample is
    from the size that would justify it. A lesson that cannot say that is not a
    lesson, it is a guess with a percentage sign on it.
    """
    learning = _dict(learning)
    lessons = []
    n_eff = _num(learning.get("n_eff")) or 0.0
    n = learning.get("n_trades") or 0
    if not isinstance(n, int):
        return ["No settled trades yet — nothing to learn from."]
    if not isinstance(learning.get("sleeve"), dict):
        return ["No settled trades yet — nothing to learn from."]

    if n == 0:
        return ["No settled trades yet — nothing to learn from."]

    # The headline: how much data is actually here.
    if n_eff < GATE_ACTIVATION_N:
        lessons.append(
            f"NOT ENOUGH EVIDENCE TO ACT: {n} settled trades across "
            f"{learning.get('n_days', 0)} session(s) is n_eff = {n_eff:.1f} after "
            f"correcting for same-day clustering (deff {learning['clustering'].get('deff')}). "
            f"Sizing stays neutral until n_eff ≥ {GATE_ACTIVATION_N}. "
            f"Trades on the same day share the market's move, so they are not "
            f"independent observations.")

    # Deduplication is a finding, not plumbing — it is the bug that gated the bot.
    for dim, info in _dict(learning.get("dims_dropped")).items():
        lessons.append(
            f"DEDUPLICATED '{dim}' — Cramér's V {info['cramers_v']} against "
            f"'{info['duplicates']}'. These are the same hypothesis; acting on "
            f"both would count one piece of evidence twice.")

    # Sleeve verdicts, with the track record each would need.
    ranked = sorted(learning["sleeve"].items(),
                    key=lambda kv: (kv[1]["avg_pnl_pct"] is not None,
                                    kv[1]["avg_pnl_pct"] or 0), reverse=True)
    for s, st in ranked:
        if st["n"] == 0:
            continue
        trl = st.get("min_track_record")
        need = (f" Honest confidence needs ~{trl:,} trades; there are {st['n']}."
                if trl else "")
        if st["weight"] == 1.0:
            lessons.append(
                f"'{s}': {st['win_rate']*100:.0f}% win, avg {st['avg_pnl_pct']*100:+.0f}% "
                f"over {st['n']} trades (n_eff {st['n_eff']:.1f}) — size unchanged, "
                f"evidence too thin to move it.{need}")
        else:
            lessons.append(
                f"'{s}' sized ×{st['weight']}: avg {st['avg_pnl_pct']*100:+.0f}% shrinks to "
                f"{(st.get('shrunk_pnl_pct') or 0)*100:+.1f}% against the pooled mean at "
                f"n_eff {st['n_eff']:.1f}.{need}")

    # Bucket verdicts — only ones the gate actually acted on.
    for g in (learning.get("gates") if isinstance(learning.get("gates"), list) else []):
        lessons.append(
            f"DISCOURAGED {g['bucket']} ({g['dim']}) — sized ×{g['mult']} at "
            f"{g['confidence']*100:.0f}% confidence, not blocked. "
            f"{g['win_rate']*100:.0f}% win over {g['n']} (n_eff {g['n_eff']:.1f}). "
            f"It keeps trading at reduced size so the evidence can keep accruing; "
            f"a hard block would have made this permanent.")

    # The multiple-testing footnote.
    d = _dict(learning.get("diagnostics"))
    if d.get("n_buckets_tested"):
        lessons.append(
            f"Testing {d['n_buckets_tested']} buckets at once: an uncorrected rule of "
            f"this size fires on noise with probability {d['fwer_if_uncorrected']:.4f}. "
            f"One honest decision needs n ≈ {d['n_needed_uncorrected']} "
            f"({d['n_needed_bonferroni']} corrected). No bucket has been blocked.")

    return lessons


# ---- helpers main() uses to APPLY what was learned --------------------------
def sleeve_weight(state: dict, sleeve: str) -> float:
    """Sizing multiplier for a sleeve, in [0.25, 1.25]. Never 0."""
    sl = _dict(_dict(_dict(state).get("learning")).get("sleeve"))
    try:
        w = _dict(sl.get(sleeve)).get("weight", 1.0)
    except TypeError:          # unhashable sleeve key
        return 1.0
    w = _num(w)
    if w is None:
        return 1.0
    return max(GATE_FLOOR, min(GATE_CEIL, w))


def size_multiplier(state: dict, feat: dict) -> tuple:
    """(multiplier, [reasons]) for a candidate trade's feature buckets.

    Replaces `is_gated()`. Returns a number in [GATE_FLOOR, GATE_CEIL] - never
    zero, so no candidate is ever structurally unable to generate evidence.

    Combination rule: the MINIMUM of the per-dimension multipliers, not the
    product. Even after deduplication the surviving dimensions remain
    correlated, and multiplying correlated evidence compounds it - six
    dimensions at 0.25 would multiply to 0.0002, which is a hard block wearing a
    disguise. Taking the minimum means the single most-discouraging dimension
    sets the size and no amount of restating it can push size lower.
    """
    lr = _dict(_dict(state).get("learning"))
    feats = _dict(lr.get("features"))
    kept = lr.get("dims_kept")
    if not isinstance(kept, list) or not kept:
        kept = list(feats.keys())
    if not feats:
        return 1.0, []
    bk = feature_keys(feat)
    mults, reasons = [], []
    for dim in kept:
        key = bk.get(dim) if isinstance(dim, str) else None
        if not key or key == "na":
            continue
        st = _dict(_dict(feats.get(dim)).get(key))
        if not st:
            continue
        m = _num(st.get("mult", 1.0))
        if m is None:
            continue
        mults.append(m)
        if m < 1.0:
            reasons.append(f"{key} ×{m:.2f} ({st.get('n', 0)} trades, "
                           f"n_eff {_num(st.get('n_eff')) or 0.0:.1f})")
    if not mults:
        return 1.0, []
    mult = max(GATE_FLOOR, min(GATE_CEIL, min(mults)))
    return round(mult, 3), reasons


def is_gated(state: dict, feat: dict):
    """Deprecated. Always returns None.

    Kept so that any caller not yet migrated fails OPEN rather than blocking.
    A hard gate is an absorbing state: it removes the entries that would produce
    the samples needed to leave it, so P(exit) = 0. Use `size_multiplier()`.
    """
    return None
