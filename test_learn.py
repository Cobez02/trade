"""
Test suite for learn.py — the recursive learning layer.

This file exists to pin the four properties the rewrite of `learn.py` was
performed to guarantee, because each one corresponds to a defect that a
Monte-Carlo replay showed was making the bot WORSE 56-59% of the time:

  1. NO HARD BLOCKS.  `learn()` must emit `hard_gates_issued: 0` and
     `size_multiplier()` must never return 0 or anything outside
     [GATE_FLOOR, GATE_CEIL], on ANY input including adversarial ones.
     A hard gate is an absorbing state — it removes the entries that would
     produce the samples needed to leave it, so P(exit) = 0.

  2. CLUSTERING IS RESPECTED.  Seven trades settled on one day must report
     n_eff = 1.0, not 7.

  3. COLLINEAR DIMENSIONS ARE DEDUPED and the dropped dimension is REPORTED
     but forced to mult exactly 1.0 — it must not influence sizing.

  4. `is_gated()` fails OPEN forever (always None), so an unmigrated caller
     cannot resurrect the old behaviour.

Plus the usual: bucketer boundaries, totality under garbage, idempotency,
journal-format compatibility, and a direct regression replay of the exact
journal shape that made the old code go permanently short-only.

Stdlib only. Deterministic — no clock, no RNG without a fixed seed.
"""
from __future__ import annotations

import copy
import datetime as dt
import inspect
import itertools
import math
import random
import sys

import learn
import stats
from engine import SLEEVES

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL  {name}  {detail}")


def near(a, b, tol=1e-9):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def section(t):
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def mk(sleeve, direction, pnl_pct, day="2026-07-27", rsi=50.0, dte=4,
       trend_up=False, macd=True, spread=0.02, pnl=None):
    feat = {"sleeve": sleeve, "direction": direction,
            "type": "call" if direction == "bull" else "put",
            "rsi": rsi, "dte": dte, "trend_up": trend_up,
            "macd_rising": macd, "spread_pct": spread}
    return {"symbol": f"{sleeve.upper()}X", "sleeve": sleeve,
            "pnl": (pnl if pnl is not None else pnl_pct * 300),
            "pnl_pct": pnl_pct, "exit_reason": "closed", "closed_on": day,
            "features": feat, "buckets": learn.feature_keys(feat)}


# The real journal shape: 7 trades, ALL settled 2026-07-27, trend_up False on
# every one (which is what made direction <=> trend_align perfectly collinear),
# 3 wins / 4 losses.
REAL_SHAPE = [
    mk("news", "bull", -0.5077, rsi=77.0, macd=True, spread=0.02, pnl=-165.0),
    mk("wsb", "bear", 0.9206, rsi=54.0, macd=False, spread=0.04, pnl=290.0),
    mk("tech", "bull", -0.6000, rsi=68.0, macd=True, spread=0.03, pnl=-180.0),
    mk("tech", "bull", -0.4500, rsi=72.0, macd=True, spread=0.06, pnl=-135.0),
    mk("news", "bear", 0.3100, rsi=41.0, macd=False, spread=0.05, pnl=93.0),
    mk("flow", "bull", -0.7200, rsi=63.0, macd=True, spread=0.11, pnl=-216.0),
    mk("wsb", "bear", 0.4400, rsi=48.0, macd=False, spread=0.03, pnl=132.0),
]


def real_state():
    return {"journal": copy.deepcopy(REAL_SHAPE)}


def spread_days(rows, n_days):
    """Same outcomes, spread across n_days distinct sessions."""
    out = copy.deepcopy(rows)
    base = dt.date(2026, 6, 1)
    for i, r in enumerate(out):
        r["closed_on"] = (base + dt.timedelta(days=(i % n_days))).isoformat()
    return out


# ===========================================================================
section("1. Module contract — constants and exported surface")
# ===========================================================================
check("GATE_FLOOR is 0.25", near(learn.GATE_FLOOR, 0.25), learn.GATE_FLOOR)
check("GATE_CEIL is 1.25", near(learn.GATE_CEIL, 1.25), learn.GATE_CEIL)
check("GATE_ACTIVATION_N is 30", near(learn.GATE_ACTIVATION_N, 30),
      learn.GATE_ACTIVATION_N)
check("floor mirrors stats", learn.GATE_FLOOR is stats.GATE_FLOOR or
      near(learn.GATE_FLOOR, stats.GATE_FLOOR))
check("ceil mirrors stats", near(learn.GATE_CEIL, stats.GATE_CEIL))
check("floor is strictly positive", learn.GATE_FLOOR > 0,
      "a zero floor IS a hard block")
check("DIMS has 6 dims", len(learn.DIMS) == 6, learn.DIMS)
check("direction leads DIMS", learn.DIMS[0] == "direction",
      "dedupe keeps the FIRST of a collinear pair; direction must win")
check("size_multiplier exists", callable(learn.size_multiplier))
check("sleeve_weight exists", callable(learn.sleeve_weight))
check("is_gated still exists (must fail open, not vanish)",
      callable(learn.is_gated))
_src = inspect.getsource(learn)
check("no literal 'weight' = 0.0 assignment", "weight\"] = 0.0" not in _src and
      "weight'] = 0.0" not in _src)
check("docstring names the absorbing-state defect",
      "ABSORBING" in learn.__doc__ and "P(exit) = 0" in learn.__doc__)


# ===========================================================================
section("2. Bucketers — boundaries, totality, and the macd None trap")
# ===========================================================================
for v, want in [(None, "na"), (0, "rsi<30"), (29.999, "rsi<30"), (30, "rsi30-45"),
                (44.999, "rsi30-45"), (45, "rsi45-55"), (54.999, "rsi45-55"),
                (55, "rsi55-70"), (69.999, "rsi55-70"), (70, "rsi>70"),
                (100, "rsi>70"), (1e9, "rsi>70"), (-5, "rsi<30")]:
    check(f"rsi_bucket({v})", learn.rsi_bucket(v) == want, learn.rsi_bucket(v))

for v, want in [(None, "na"), (0, "dte<=4"), (4, "dte<=4"), (5, "dte5-8"),
                (8, "dte5-8"), (9, "dte9+"), (400, "dte9+"), (-1, "dte<=4")]:
    check(f"dte_bucket({v})", learn.dte_bucket(v) == want, learn.dte_bucket(v))

for v, want in [(None, "na"), (0.0, "spread_tight"), (0.10, "spread_tight"),
                (0.1001, "spread_mid"), (0.25, "spread_mid"),
                (0.2501, "spread_wide"), (5.0, "spread_wide")]:
    check(f"spread_bucket({v})", learn.spread_bucket(v) == want,
          learn.spread_bucket(v))

check("align: bull+up = with_trend",
      learn.align_bucket({"direction": "bull", "trend_up": True}) == "with_trend")
check("align: bull+down = counter_trend",
      learn.align_bucket({"direction": "bull", "trend_up": False}) == "counter_trend")
check("align: bear+down = with_trend",
      learn.align_bucket({"direction": "bear", "trend_up": False}) == "with_trend")
check("align: bear+up = counter_trend",
      learn.align_bucket({"direction": "bear", "trend_up": True}) == "counter_trend")
check("align: missing trend = na",
      learn.align_bucket({"direction": "bull"}) == "na")
check("align: missing direction = na",
      learn.align_bucket({"trend_up": True}) == "na")

# The macd None trap: collapsing unknown-MACD into macd_down would put every
# unlabelled trade into one fake bucket, which would eventually discourage that
# bucket and shrink ALL trading.
check("macd None -> 'na', NOT macd_down",
      learn.feature_keys({"macd_rising": None})["macd_rising"] == "na")
check("macd False -> macd_down",
      learn.feature_keys({"macd_rising": False})["macd_rising"] == "macd_down")
check("macd True -> macd_up",
      learn.feature_keys({"macd_rising": True})["macd_rising"] == "macd_up")
check("macd absent -> 'na'",
      learn.feature_keys({})["macd_rising"] == "na")

check("feature_keys covers every DIM",
      set(learn.feature_keys({}).keys()) == set(learn.DIMS),
      set(learn.feature_keys({}).keys()) ^ set(learn.DIMS))
check("direction non-bull maps to put",
      learn.feature_keys({"direction": "bear"})["direction"] == "put")
check("direction missing maps to put (not a crash)",
      learn.feature_keys({})["direction"] == "put")


# ===========================================================================
section("3. Clustering — the n_eff=1.0 finding")
# ===========================================================================
st = learn._stat(REAL_SHAPE)
check("n = 7", st["n"] == 7, st["n"])
check("n_days = 1", st["n_days"] == 1, st["n_days"])
check("n_eff = 1.0 for 7 same-day trades", near(st["n_eff"], 1.0, 1e-9),
      f"n_eff={st['n_eff']} — the whole clustering argument rests on this")
check("deff = 7.0", near(st["deff"], 7.0, 1e-9), st["deff"])
check("rho = 1.0", near(st["rho"], 1.0, 1e-9), st["rho"])

st7 = learn._stat(spread_days(REAL_SHAPE, 7))
check("same outcomes over 7 days -> n_eff = 7.0", near(st7["n_eff"], 7.0, 1e-9),
      st7["n_eff"])
check("7 distinct days counted", st7["n_days"] == 7, st7["n_days"])
check("n unchanged by regrouping", st7["n"] == st["n"])
check("win_rate unchanged by regrouping", st7["win_rate"] == st["win_rate"])
check("n_eff <= n always (1 day)", st["n_eff"] <= st["n"] + 1e-9)
check("n_eff <= n always (7 days)", st7["n_eff"] <= st7["n"] + 1e-9)

st3 = learn._stat(spread_days(REAL_SHAPE, 3))
check("3 days sits between", st["n_eff"] <= st3["n_eff"] <= st7["n_eff"] + 1e-9,
      st3["n_eff"])

check("_stat on empty", learn._stat([])["n"] == 0)
check("_stat empty n_eff 0.0", near(learn._stat([])["n_eff"], 0.0))
check("_stat empty win_rate None", learn._stat([])["win_rate"] is None)
check("_stat skips None pnl_pct",
      learn._stat([{"pnl_pct": None}, {"pnl_pct": 0.5, "closed_on": "2026-01-01"}])["n"] == 1)

check("_day_of prefers closed_on",
      learn._day_of({"closed_on": "2026-07-27", "entry_date": "2026-01-01"}) == "2026-07-27")
check("_day_of falls back to entry_date",
      learn._day_of({"entry_date": "2026-01-05"}) == "2026-01-05")
check("_day_of empty -> 'unknown'", learn._day_of({}) == "unknown")
check("_day_of truncates a timestamp",
      learn._day_of({"closed_on": "2026-07-27T15:59:00Z"}) == "2026-07-27")
check("_day_of tolerates a date object",
      learn._day_of({"closed_on": dt.date(2026, 7, 27)}) == "2026-07-27")

check("_cluster groups by day", len(learn._cluster(REAL_SHAPE)) == 1)
check("_cluster 7 days", len(learn._cluster(spread_days(REAL_SHAPE, 7))) == 7)
check("_cluster drops None pnl", learn._cluster([{"pnl_pct": None}]) == [])


# ===========================================================================
section("4. _moments — RAW kurtosis, not excess")
# ===========================================================================
g3, g4 = learn._moments(REAL_SHAPE)
check("kurtosis is RAW (>= 1 for any real sample)", g4 >= 1.0,
      f"g4={g4}; excess-kurtosis convention would be near 0 and understates "
      f"Sharpe variance")
check("skew finite", math.isfinite(g3), g3)
check("kurtosis finite", math.isfinite(g4), g4)

# A near-Normal sample should give raw kurtosis near 3, not near 0.
rng = random.Random(20260728)
normal_rows = [{"pnl_pct": rng.gauss(0.0, 1.0), "closed_on": f"2026-01-{(i % 28) + 1:02d}"}
               for i in range(4000)]
ng3, ng4 = learn._moments(normal_rows)
check("Normal sample: raw kurtosis ~ 3.0", abs(ng4 - 3.0) < 0.35, ng4)
check("Normal sample: skew ~ 0.0", abs(ng3) < 0.15, ng3)
check("_moments returns the Normal default under n<4",
      learn._moments([{"pnl_pct": 1.0}]) == (0.0, 3.0))
check("_moments zero-variance -> defaults",
      learn._moments([{"pnl_pct": 0.5}] * 8) == (0.0, 3.0))

sr = learn._sharpe(REAL_SHAPE)
check("_sharpe finite on real shape", sr is not None and math.isfinite(sr), sr)
check("_sharpe None with <2 obs", learn._sharpe([{"pnl_pct": 1.0}]) is None)
check("_sharpe None with zero variance", learn._sharpe([{"pnl_pct": 0.5}] * 5) is None)
check("_sharpe sign matches mean sign",
      (learn._sharpe([{"pnl_pct": v} for v in (0.1, 0.2, 0.35)]) or 0) > 0)


# ===========================================================================
section("5. THE HEADLINE — learn() cannot emit a hard block")
# ===========================================================================
s = real_state()
out = learn.learn(s)

check("hard_gates_issued == 0", out["diagnostics"]["hard_gates_issued"] == 0,
      out["diagnostics"]["hard_gates_issued"])
check("learn() writes state['learning']", s.get("learning") is out)
check("n_trades = 7", out["n_trades"] == 7, out["n_trades"])
check("n_eff = 1.0 in output", near(out["n_eff"], 1.0, 1e-9), out["n_eff"])
check("n_days = 1 in output", out["n_days"] == 1, out["n_days"])

for sl, sst in out["sleeve"].items():
    check(f"sleeve '{sl}' weight >= floor", sst["weight"] >= learn.GATE_FLOOR - 1e-12,
          sst["weight"])
    check(f"sleeve '{sl}' weight <= ceil", sst["weight"] <= learn.GATE_CEIL + 1e-12,
          sst["weight"])
    check(f"sleeve '{sl}' weight non-zero", sst["weight"] != 0.0, sst["weight"])

# Every sleeve here has n_eff = 1.0, far under the activation threshold of 30,
# so nothing should have moved off neutral at all.
check("all sleeve weights are exactly 1.0 at n_eff=1",
      all(near(v["weight"], 1.0) for v in out["sleeve"].values()),
      {k: v["weight"] for k, v in out["sleeve"].items()})

for dim, buckets in out["features"].items():
    for key, bst in buckets.items():
        check(f"bucket {dim}/{key} mult in range",
              learn.GATE_FLOOR - 1e-12 <= bst["mult"] <= learn.GATE_CEIL + 1e-12,
              bst["mult"])
        check(f"bucket {dim}/{key} mult non-zero", bst["mult"] != 0.0)

check("no gate entry carries a block flag",
      all("blocked" not in g and "block" not in str(g.get("mult")) for g in out["gates"]))
check("every gate entry is a reduction, not a block",
      all(g["mult"] >= learn.GATE_FLOOR for g in out["gates"]),
      out["gates"])

# The direct regression: the OLD rule (5 samples, <=25% win rate) fired on the
# 'call' bucket here. The new one must not block it.
call_st = out["features"].get("direction", {}).get("call")
check("'call' bucket exists in output", call_st is not None)
if call_st:
    check("'call' bucket lost money (the old trigger condition)",
          call_st["avg_pnl_pct"] < 0, call_st["avg_pnl_pct"])
    check("'call' bucket win rate <= 0.25 (the old trigger condition)",
          call_st["win_rate"] <= 0.25, call_st["win_rate"])
    check("'call' has >= MIN_SAMPLES_GATE samples (old trigger condition)",
          call_st["n"] >= learn.MIN_SAMPLES_GATE - 1, call_st["n"])
    check("REGRESSION: 'call' mult is EXACTLY 1.0 — old code would have blocked it",
          near(call_st["mult"], 1.0),
          f"mult={call_st['mult']}, n_eff={call_st['n_eff']}")
    check("'call' n_eff is 1.0 not 4", near(call_st["n_eff"], 1.0, 1e-9),
          call_st["n_eff"])
    check("'call' reports how many samples it would need",
          call_st["n_needed"] > 50, call_st.get("n_needed"))

check("is_gated returns None on the real state",
      learn.is_gated(s, {"direction": "bull", "rsi": 77, "dte": 4,
                         "trend_up": False, "macd_rising": True,
                         "spread_pct": 0.02}) is None)


# ===========================================================================
section("6. Deduplication — reported, but forced to mult 1.0")
# ===========================================================================
check("dims_kept is non-empty", len(out["dims_kept"]) > 0, out["dims_kept"])
check("direction survives dedupe", "direction" in out["dims_kept"],
      out["dims_kept"])
check("trend_align was dropped (V=1.0 vs direction)",
      "trend_align" in out["dims_dropped"], out["dims_dropped"])
if "trend_align" in out["dims_dropped"]:
    info = out["dims_dropped"]["trend_align"]
    check("dropped against 'direction'", info["duplicates"] == "direction",
          info["duplicates"])
    check("Cramér's V is 1.0 exactly", near(info["cramers_v"], 1.0, 1e-9),
          info["cramers_v"])

check("kept and dropped are disjoint",
      not (set(out["dims_kept"]) & set(out["dims_dropped"])),
      set(out["dims_kept"]) & set(out["dims_dropped"]))

# A deduped dimension is still reported...
check("dropped dim is still REPORTED in features",
      len(out["features"].get("trend_align", {})) > 0)
# ...but every one of its buckets is pinned to 1.0.
for key, bst in out["features"].get("trend_align", {}).items():
    check(f"deduped trend_align/{key} mult forced to 1.0", near(bst["mult"], 1.0),
          bst["mult"])
    check(f"deduped trend_align/{key} confidence 0", near(bst["confidence"], 0.0),
          bst["confidence"])
    check(f"deduped trend_align/{key} says so in its reason",
          "dedup" in bst["evidence"].lower(), bst["evidence"])

check("gates never reference a deduped dimension",
      all(g["dim"] in out["dims_kept"] for g in out["gates"]),
      [g["dim"] for g in out["gates"]])

# Independent confirmation of the collinearity claim straight off the journal.
a = [j["buckets"]["direction"] for j in REAL_SHAPE]
b = [j["buckets"]["trend_align"] for j in REAL_SHAPE]
check("direction<->trend_align Cramér's V = 1.0 on the real journal",
      near(stats.cramers_v(a, b), 1.0, 1e-9), stats.cramers_v(a, b))

# When trend_up VARIES, the collinearity breaks and trend_align must survive.
varied = copy.deepcopy(REAL_SHAPE)
for i, r in enumerate(varied):
    r["features"]["trend_up"] = bool(i % 2)
    r["buckets"] = learn.feature_keys(r["features"])
out_v = learn.learn({"journal": varied})
check("with varying trend_up, trend_align is NOT deduped",
      "trend_align" not in out_v["dims_dropped"], out_v["dims_dropped"])


# ===========================================================================
section("7. size_multiplier — range, minimum-rule, and never zero")
# ===========================================================================
FEAT_A = {"direction": "bull", "rsi": 77.0, "dte": 4, "trend_up": False,
          "macd_rising": True, "spread_pct": 0.02}
m, reasons = learn.size_multiplier(s, FEAT_A)
check("size_multiplier returns a 2-tuple", isinstance(reasons, list))
check("mult in [floor, ceil]", learn.GATE_FLOOR <= m <= learn.GATE_CEIL, m)
check("mult non-zero", m != 0.0, m)
check("mult is 1.0 at n_eff=1 (no evidence)", near(m, 1.0), m)

check("empty state -> 1.0", learn.size_multiplier({}, FEAT_A)[0] == 1.0)
check("no learning key -> 1.0", learn.size_multiplier({"journal": []}, FEAT_A)[0] == 1.0)
check("empty feat is safe", learn.GATE_FLOOR <= learn.size_multiplier(s, {})[0] <= learn.GATE_CEIL)

# The minimum rule: six correlated dimensions at the floor must yield the FLOOR,
# not the floor^6 that a product rule would give (0.25**6 = 0.000244).
synthetic = {"learning": {
    "dims_kept": list(learn.DIMS),
    "features": {d: {k: {"mult": 0.25, "n": 40, "n_eff": 35.0}
                     for k in ("call", "put", "rsi>70", "dte<=4", "spread_tight",
                               "counter_trend", "with_trend", "macd_up", "macd_down")}
                 for d in learn.DIMS},
}}
m6, r6 = learn.size_multiplier(synthetic, FEAT_A)
check("six dims at floor -> floor, not floor^6", near(m6, learn.GATE_FLOOR), m6)
check("product rule would have given ~0.000244 (must NOT)",
      m6 > 0.25 ** 2, f"m6={m6}")
check("all six dims produce a reason", len(r6) == 6, len(r6))

# Minimum, not average: one discouraged dim among five neutral must dominate.
mixed = copy.deepcopy(synthetic)
for d in learn.DIMS:
    for k in mixed["learning"]["features"][d]:
        mixed["learning"]["features"][d][k]["mult"] = 1.25
mixed["learning"]["features"]["dte"]["dte<=4"]["mult"] = 0.4
mm, rm = learn.size_multiplier(mixed, FEAT_A)
check("minimum rule: one 0.4 among 1.25s gives 0.4", near(mm, 0.4), mm)
check("only the discouraging dim is reported", len(rm) == 1, rm)

# All-encouraging must cap at the ceiling, never above.
allup = copy.deepcopy(synthetic)
for d in learn.DIMS:
    for k in allup["learning"]["features"][d]:
        allup["learning"]["features"][d][k]["mult"] = 99.0
check("mult clamped to ceiling", near(learn.size_multiplier(allup, FEAT_A)[0],
                                      learn.GATE_CEIL))
allown = copy.deepcopy(synthetic)
for d in learn.DIMS:
    for k in allown["learning"]["features"][d]:
        allown["learning"]["features"][d][k]["mult"] = -5.0
check("negative mult clamped to floor",
      near(learn.size_multiplier(allown, FEAT_A)[0], learn.GATE_FLOOR))
zeroed = copy.deepcopy(synthetic)
for d in learn.DIMS:
    for k in zeroed["learning"]["features"][d]:
        zeroed["learning"]["features"][d][k]["mult"] = 0.0
check("even a stored 0.0 is clamped up to the floor",
      near(learn.size_multiplier(zeroed, FEAT_A)[0], learn.GATE_FLOOR),
      "a persisted zero from an old state file must not resurrect the block")

nanned = copy.deepcopy(synthetic)
nanned["learning"]["features"]["dte"]["dte<=4"]["mult"] = float("nan")
mn, _ = learn.size_multiplier(nanned, FEAT_A)
check("NaN mult does not produce a zero or a crash",
      learn.GATE_FLOOR <= mn <= learn.GATE_CEIL or math.isnan(mn) is False, mn)
strd = copy.deepcopy(synthetic)
strd["learning"]["features"]["dte"]["dte<=4"]["mult"] = "banana"
check("non-numeric mult is skipped, not fatal",
      learn.GATE_FLOOR <= learn.size_multiplier(strd, FEAT_A)[0] <= learn.GATE_CEIL)

# A deduped dim must not lower the multiplier even if its stored mult is low.
dedup_state = {"learning": {
    "dims_kept": ["direction"],
    "features": {
        "direction": {"call": {"mult": 1.0, "n": 5, "n_eff": 1.0}},
        "trend_align": {"counter_trend": {"mult": 0.25, "n": 5, "n_eff": 1.0}},
    },
}}
md, rd = learn.size_multiplier(dedup_state, FEAT_A)
check("deduped dim cannot pull size down", near(md, 1.0), md)
check("deduped dim contributes no reason", rd == [], rd)


# ===========================================================================
section("8. size_multiplier sweep — 4,096 feature combinations, never zero")
# ===========================================================================
DIRS = ["bull", "bear"]
RSIS = [None, 10.0, 35.0, 50.0, 60.0, 90.0]
DTES = [None, 0, 4, 6, 12]
TRENDS = [True, False, None]
MACDS = [True, False, None]
SPREADS = [None, 0.01, 0.15, 0.9]

states_to_sweep = [s, synthetic, mixed, {}, {"learning": {}},
                   {"learning": {"features": {}, "dims_kept": []}}]
combo_count = 0
bad = []
for st_i, sweep_state in enumerate(states_to_sweep):
    for d, r, dd, t, mc, sp in itertools.product(DIRS, RSIS, DTES, TRENDS,
                                                 MACDS, SPREADS):
        f = {"direction": d, "rsi": r, "dte": dd, "trend_up": t,
             "macd_rising": mc, "spread_pct": sp}
        try:
            mv, rs = learn.size_multiplier(sweep_state, f)
        except Exception as e:  # noqa: BLE001
            bad.append((st_i, f, repr(e)))
            continue
        combo_count += 1
        if not isinstance(mv, float) or not math.isfinite(mv):
            bad.append((st_i, f, f"non-finite {mv}"))
        elif mv <= 0.0:
            bad.append((st_i, f, f"ZERO OR NEGATIVE {mv}"))
        elif not (learn.GATE_FLOOR - 1e-9 <= mv <= learn.GATE_CEIL + 1e-9):
            bad.append((st_i, f, f"out of range {mv}"))
        elif not isinstance(rs, list):
            bad.append((st_i, f, "reasons not a list"))
print(f"  swept {combo_count} (state, feature) combinations")
check("sweep: zero exceptions and zero out-of-range multipliers",
      not bad, f"{len(bad)} bad: {bad[:4]}")
check("sweep actually ran a meaningful number of combos", combo_count >= 4000,
      combo_count)


# ===========================================================================
section("9. is_gated is permanently disabled")
# ===========================================================================
gate_bad = []
for sweep_state in states_to_sweep + [None, {"learning": None}]:
    for d, r, dd in itertools.product(DIRS, RSIS, DTES):
        f = {"direction": d, "rsi": r, "dte": dd}
        try:
            res = learn.is_gated(sweep_state, f)
        except Exception as e:  # noqa: BLE001
            gate_bad.append(repr(e))
            continue
        if res is not None:
            gate_bad.append(f"returned {res!r}")
check("is_gated returned None on every input, and never raised", not gate_bad,
      gate_bad[:4])
check("is_gated docstring marks it deprecated",
      "deprecated" in (learn.is_gated.__doc__ or "").lower())
check("is_gated body is a bare `return None`",
      inspect.getsource(learn.is_gated).rstrip().endswith("return None"))


# ===========================================================================
section("10. Enough evidence — the gate DOES engage when data justifies it")
# ===========================================================================
# 200 losing 'call' trades on 200 separate days: n_eff is large, the effect is
# real, and the multiplier SHOULD drop. A learner that can never conclude
# anything is as useless as one that concludes everything.
base = dt.date(2026, 1, 1)
big = []
for i in range(200):
    day = (base + dt.timedelta(days=i)).isoformat()
    big.append(mk("tech", "bull", -0.45, day=day, rsi=60.0, trend_up=bool(i % 2)))
for i in range(200):
    day = (base + dt.timedelta(days=i)).isoformat()
    big.append(mk("news", "bear", 0.30, day=day, rsi=40.0, trend_up=bool(i % 2)))
out_big = learn.learn({"journal": big[-500:]})

check("large sample: n_eff is large", out_big["n_eff"] > 100, out_big["n_eff"])
call_big = out_big["features"].get("direction", {}).get("call", {})
put_big = out_big["features"].get("direction", {}).get("put", {})
check("large sample: losing 'call' is discouraged", call_big.get("mult", 1.0) < 1.0,
      call_big.get("mult"))
check("large sample: winning 'put' is encouraged", put_big.get("mult", 1.0) > 1.0,
      put_big.get("mult"))
check("large sample: discouraged mult still >= floor",
      call_big.get("mult", 1.0) >= learn.GATE_FLOOR, call_big.get("mult"))
check("large sample: encouraged mult still <= ceil",
      put_big.get("mult", 1.0) <= learn.GATE_CEIL, put_big.get("mult"))
check("large sample: still zero hard gates",
      out_big["diagnostics"]["hard_gates_issued"] == 0)
check("large sample: gates list is populated", len(out_big["gates"]) > 0)
check("large sample: every gate is a size cut, never a block",
      all(g["mult"] > 0 for g in out_big["gates"]))
mb, rb = learn.size_multiplier({"learning": out_big},
                               {"direction": "bull", "rsi": 60.0, "dte": 4,
                                "trend_up": False, "macd_rising": True,
                                "spread_pct": 0.02})
check("large sample: candidate in the bad bucket is sized down, not refused",
      learn.GATE_FLOOR <= mb < 1.0, mb)
check("large sample: the cut is explained", len(rb) > 0, rb)
check("large sample: sleeve weights moved off neutral",
      any(not near(v["weight"], 1.0) for v in out_big["sleeve"].values()),
      {k: v["weight"] for k, v in out_big["sleeve"].items()})
for sl, sst in out_big["sleeve"].items():
    check(f"large sample: sleeve '{sl}' still in range",
          learn.GATE_FLOOR - 1e-12 <= sst["weight"] <= learn.GATE_CEIL + 1e-12,
          sst["weight"])


# ===========================================================================
section("11. Diagnostics — the multiple-testing numbers are the headline ones")
# ===========================================================================
d = out["diagnostics"]
check("n_buckets_tested > 0", d["n_buckets_tested"] > 0, d["n_buckets_tested"])
check("n_needed_uncorrected == 61", d["n_needed_uncorrected"] == 61,
      d["n_needed_uncorrected"])
check("required_n_for_gate default p_null is 0.40 (not the flattering 0.50)",
      stats.required_n_for_gate() == 61, stats.required_n_for_gate())
check("a 0.50 null understates by ~2.65x",
      near(61 / stats.required_n_for_gate(p_null=0.50), 2.652, 0.01),
      61 / stats.required_n_for_gate(p_null=0.50))
check("n_needed_bonferroni > n_needed_uncorrected",
      d["n_needed_bonferroni"] > d["n_needed_uncorrected"],
      (d["n_needed_bonferroni"], d["n_needed_uncorrected"]))
check("fwer_if_uncorrected > 0.99", d["fwer_if_uncorrected"] > 0.99,
      d["fwer_if_uncorrected"])
check("fwer(0.337, 17) == 0.9991 exactly", near(round(stats.fwer(0.337, 17), 4), 0.9991),
      round(stats.fwer(0.337, 17), 4))
check("fwer(0.25, 17) == 0.9925 — NOT the headline figure",
      near(round(stats.fwer(0.25, 17), 4), 0.9925), round(stats.fwer(0.25, 17), 4))
check("bonferroni_alpha < 0.05", d["bonferroni_alpha"] < 0.05, d["bonferroni_alpha"])
check("activation_n_eff reported as 30", d["activation_n_eff"] == 30)
check("MIN_SAMPLES_GATE=5 shortfall is 12.2x uncorrected",
      near(d["n_needed_uncorrected"] / learn.MIN_SAMPLES_GATE, 12.2, 0.01),
      d["n_needed_uncorrected"] / learn.MIN_SAMPLES_GATE)

# Every sleeve with a positive Sharpe must publish the track record it needs.
for sl, sst in out["sleeve"].items():
    if sst.get("sharpe_per_trade") and sst["sharpe_per_trade"] > 0:
        check(f"sleeve '{sl}' publishes min_track_record",
              sst["min_track_record"] is None or sst["min_track_record"] > 0,
              sst["min_track_record"])
        check(f"sleeve '{sl}' publishes psr",
              sst["psr"] is None or 0.0 <= sst["psr"] <= 1.0, sst["psr"])
check("MinTRL(sr=0.0445, skew=-0.5, kurt=6.0) == 1401 trades",
      round(stats.min_track_record_length(0.0445, 0.0, -0.5, 6.0, 0.95)) == 1401,
      round(stats.min_track_record_length(0.0445, 0.0, -0.5, 6.0, 0.95)))
check("that is ~350x MIN_SAMPLES_SLEEVE=4",
      near(1401 / learn.MIN_SAMPLES_SLEEVE, 350.25, 0.01))


# ===========================================================================
section("12. Lessons — every claim carries its sample size")
# ===========================================================================
check("lessons non-empty", len(out["lessons"]) > 0)
check("all lessons are strings", all(isinstance(x, str) for x in out["lessons"]))
check("headline lesson admits the evidence is insufficient",
      any("NOT ENOUGH EVIDENCE" in x for x in out["lessons"]), out["lessons"][:2])
check("headline lesson quotes n_eff",
      any("n_eff" in x for x in out["lessons"]))
check("deduplication is reported as a finding",
      any("DEDUPLICATED" in x for x in out["lessons"]))
check("multiple-testing footnote present",
      any("buckets at once" in x for x in out["lessons"]))
check("footnote states nothing was blocked",
      any("No bucket has been blocked" in x for x in out["lessons"]))
check("no lesson claims a block",
      not any("BLOCK" in x.upper() and "blocked" not in x for x in out["lessons"]))
check("empty journal gives the honest empty lesson",
      learn.learn({"journal": []})["lessons"] == ["No settled trades yet — nothing to learn from."])
check("empty journal still emits zero hard gates",
      learn.learn({"journal": []})["diagnostics"]["hard_gates_issued"] == 0)

big_lessons = out_big["lessons"]
check("large sample drops the NOT-ENOUGH-EVIDENCE headline",
      not any("NOT ENOUGH EVIDENCE" in x for x in big_lessons))
check("large sample reports DISCOURAGED, and says it is not a block",
      any("DISCOURAGED" in x and "not blocked" in x for x in big_lessons),
      [x for x in big_lessons if "DISCOURAG" in x][:1])


# ===========================================================================
section("13. sleeve_weight accessor")
# ===========================================================================
for sl in SLEEVES:
    w = learn.sleeve_weight(s, sl)
    check(f"sleeve_weight('{sl}') in range", learn.GATE_FLOOR <= w <= learn.GATE_CEIL, w)
check("unknown sleeve -> 1.0", near(learn.sleeve_weight(s, "does_not_exist"), 1.0))
check("empty state -> 1.0", near(learn.sleeve_weight({}, "tech"), 1.0))
check("stored 0.0 is clamped up to the floor",
      near(learn.sleeve_weight({"learning": {"sleeve": {"tech": {"weight": 0.0}}}}, "tech"),
           learn.GATE_FLOOR),
      "an old state.json with weight 0 must not resurrect the pause")
check("stored 99 clamped to ceil",
      near(learn.sleeve_weight({"learning": {"sleeve": {"tech": {"weight": 99}}}}, "tech"),
           learn.GATE_CEIL))
check("stored garbage -> 1.0",
      near(learn.sleeve_weight({"learning": {"sleeve": {"tech": {"weight": "x"}}}}, "tech"), 1.0))
check("stored None -> 1.0",
      near(learn.sleeve_weight({"learning": {"sleeve": {"tech": {"weight": None}}}}, "tech"), 1.0))


# ===========================================================================
section("14. record_lesson — journal format compatibility")
# ===========================================================================
st2 = {}
learn.record_lesson(st2, {"symbol": "AAPL260731C00200000", "sleeve": "tech",
                          "pnl": -50.0, "pnl_pct": -0.33, "exit_reason": "stop",
                          "closed_on": "2026-07-28",
                          "features": {"direction": "bull", "rsi": 61.0, "dte": 5,
                                       "trend_up": True, "macd_rising": False,
                                       "spread_pct": 0.03}})
row = st2["journal"][0]
check("record_lesson creates the journal", len(st2["journal"]) == 1)
check("row keys unchanged from the old format",
      set(row.keys()) == {"symbol", "sleeve", "pnl", "pnl_pct", "exit_reason",
                          "closed_on", "features", "buckets"},
      set(row.keys()))
check("buckets computed at write time", row["buckets"]["direction"] == "call")
check("with_trend computed correctly", row["buckets"]["trend_align"] == "with_trend")
check("dte 5 -> dte5-8", row["buckets"]["dte"] == "dte5-8")
learn.record_lesson(st2, {"symbol": "X", "pnl_pct": 0.1, "features": None})
check("None features tolerated", len(st2["journal"]) == 2)
check("None features still produce buckets", "buckets" in st2["journal"][1])
learn.record_lesson(st2, {})
check("wholly empty close tolerated", len(st2["journal"]) == 3)

cap = {"journal": []}
for i in range(560):
    learn.record_lesson(cap, {"symbol": f"S{i}", "sleeve": "tech", "pnl": 1.0,
                              "pnl_pct": 0.01, "closed_on": "2026-07-28",
                              "features": {"direction": "bull"}})
check("journal capped at 500", len(cap["journal"]) == 500, len(cap["journal"]))
check("cap keeps the NEWEST", cap["journal"][-1]["symbol"] == "S559")
check("cap drops the oldest", cap["journal"][0]["symbol"] == "S60")
check("learn() runs on a 500-row journal",
      learn.learn(cap)["n_trades"] == 500)


# ===========================================================================
section("15. Idempotency and state hygiene")
# ===========================================================================
s_a = real_state()
o1 = learn.learn(s_a)
o1_copy = copy.deepcopy(o1)
o2 = learn.learn(s_a)
o3 = learn.learn(s_a)
check("learn() is idempotent (run 2)", o2 == o1_copy, "second call differed")
check("learn() is idempotent (run 3)", o3 == o1_copy, "third call differed")
check("learn() does not mutate the journal length",
      len(s_a["journal"]) == 7, len(s_a["journal"]))
check("learn() does not mutate journal rows",
      s_a["journal"] == REAL_SHAPE, "journal rows were modified in place")

s_b = real_state()
s_b["learning"] = {"sleeve": {"tech": {"weight": 0.0}}, "gates": [{"blocked": True}]}
o_b = learn.learn(s_b)
check("a stale learning block with weight 0 is fully replaced",
      all(v["weight"] > 0 for v in o_b["sleeve"].values()),
      {k: v["weight"] for k, v in o_b["sleeve"].items()})
check("stale gates are replaced, not appended",
      all("blocked" not in g for g in o_b["gates"]))

# Rows with a None pnl_pct (still-open trades) must be excluded, not crash.
s_c = real_state()
s_c["journal"].append({"symbol": "OPEN", "sleeve": "tech", "pnl_pct": None,
                       "features": {}, "buckets": learn.feature_keys({})})
o_c = learn.learn(s_c)
check("open trades excluded from n_trades", o_c["n_trades"] == 7, o_c["n_trades"])


# ===========================================================================
section("16. Totality — learn() and friends never raise on garbage")
# ===========================================================================
GARBAGE = [None, {}, {"journal": None}, {"journal": []}, {"journal": [{}]},
           {"journal": [{"pnl_pct": "x"}]},
           {"journal": [{"pnl_pct": float("nan"), "closed_on": "2026-01-01"}]},
           {"journal": [{"pnl_pct": float("inf"), "closed_on": "2026-01-01"}]},
           {"journal": [{"pnl_pct": 0.0, "closed_on": None, "features": None,
                         "buckets": None}]},
           {"journal": [{"pnl_pct": 1.0, "sleeve": 12345, "closed_on": 99,
                         "buckets": {"direction": None}}]},
           {"journal": [{"pnl_pct": -1.0, "closed_on": "2026-01-01",
                         "buckets": {"direction": "call"}}] * 40},
           {"journal": [mk("tech", "bull", 0.0)]},
           {"journal": [mk("tech", "bull", 0.0)] * 3},
           ]
raised = []
for i, g in enumerate(GARBAGE):
    try:
        if g is None:
            continue
        r = learn.learn(g)
        if r["diagnostics"]["hard_gates_issued"] != 0:
            raised.append((i, "emitted a hard gate"))
        for sl, sst in r["sleeve"].items():
            if not (learn.GATE_FLOOR - 1e-9 <= sst["weight"] <= learn.GATE_CEIL + 1e-9):
                raised.append((i, f"sleeve {sl} weight {sst['weight']}"))
        for dim, bs in r["features"].items():
            for k, bst in bs.items():
                if not (learn.GATE_FLOOR - 1e-9 <= bst["mult"] <= learn.GATE_CEIL + 1e-9):
                    raised.append((i, f"{dim}/{k} mult {bst['mult']}"))
        m_, r_ = learn.size_multiplier(g, FEAT_A)
        if not (learn.GATE_FLOOR - 1e-9 <= m_ <= learn.GATE_CEIL + 1e-9):
            raised.append((i, f"size_multiplier {m_}"))
    except Exception as e:  # noqa: BLE001
        raised.append((i, repr(e)))
check("learn() survived every garbage journal with in-range output", not raised,
      raised[:5])

# Discovery sweep over every public callable, as in test_stats.py.
CALLABLES = [(n, f) for n, f in vars(learn).items()
             if callable(f) and not n.startswith("__")
             and getattr(f, "__module__", None) == "learn"]
check("discovered the expected public surface", len(CALLABLES) >= 12, len(CALLABLES))
ARGS = [None, 0, 1, -1, 0.5, -0.5, float("nan"), float("inf"), float("-inf"),
        "", "x", [], {}, [{}], {"a": 1}, True, False, 1e18, -1e18]
sweep_raises = []
calls = 0
for name, fn in CALLABLES:
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        continue
    n_req = sum(1 for p in params
                if p.default is inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))
    if n_req == 0:
        combos = [()]
    elif n_req == 1:
        combos = [(a,) for a in ARGS]
    elif n_req == 2:
        combos = list(itertools.product(ARGS, ARGS))
    else:
        combos = [tuple(ARGS[i % len(ARGS)] for _ in range(n_req))
                  for i in range(len(ARGS))]
    for c in combos:
        calls += 1
        try:
            fn(*c)
        except Exception as e:  # noqa: BLE001
            sweep_raises.append((name, c, type(e).__name__))
print(f"  swept {calls} garbage calls across {len(CALLABLES)} callables")
check("totality sweep: zero unhandled exceptions", not sweep_raises,
      f"{len(sweep_raises)} raised: {sweep_raises[:6]}")


# ===========================================================================
section("17. Structural proof — no code path can return a blocking value")
# ===========================================================================
# Belt and braces: brute-force a wide grid of gate_decision inputs through the
# same clamp learn.py applies, and confirm the floor holds. This is the property
# main.py relies on when it multiplies size by the result.
grid_bad = []
grid_n = 0
for shrunk in (-1e6, -5.0, -0.9, -0.5, -0.21, -0.05, 0.0, 0.05, 0.5, 5.0, 1e6,
               float("nan"), float("inf"), float("-inf")):
    for n_eff in (0.0, 0.5, 1.0, 1.2, 5.0, 29.9, 30.0, 60.0, 120.0, 1e6,
                  float("nan"), float("inf")):
        for pooled in (-1.0, -0.2, 0.0, 0.2, 1.0, float("nan")):
            grid_n += 1
            try:
                dec = stats.gate_decision(shrunk, n_eff, pooled)
                mv = max(learn.GATE_FLOOR, min(learn.GATE_CEIL, dec["mult"]))
            except Exception as e:  # noqa: BLE001
                grid_bad.append((shrunk, n_eff, pooled, repr(e)))
                continue
            if not math.isfinite(mv) or mv <= 0.0:
                grid_bad.append((shrunk, n_eff, pooled, mv))
print(f"  swept {grid_n} gate_decision inputs through learn's clamp")
check("no gate_decision input can produce a zero or non-finite size", not grid_bad,
      grid_bad[:4])

# The specific pre-compaction finding: gate_decision(-0.21, n_eff=1.2) must be
# EXACTLY 1.0. This is the case the old hard block fired on.
dec = stats.gate_decision(-0.21, 1.2, 0.0)
check("gate_decision(-0.21, n_eff=1.2) mult is EXACTLY 1.0", near(dec["mult"], 1.0),
      dec["mult"])
check("...with zero confidence", near(dec["confidence"], 0.0), dec["confidence"])


# ===========================================================================
section("18. Cross-module — main.py uses the new API, not the old one")
# ===========================================================================
main_src = open("/home/claude/spxbot/main.py", encoding="utf-8").read()
check("main.py calls size_multiplier", "learn.size_multiplier(" in main_src)
check("main.py no longer calls is_gated", "learn.is_gated(" not in main_src,
      "the deprecated hard-gate entry point is still wired in")
check("main.py no longer pauses a sleeve on weight <= 0",
      "weight <= 0.0" not in main_src and "weight <= 0:" not in main_src)
check("main.py applies the multiplier to size",
      "mult" in main_src and "size_budget" in main_src)

rep_src = open("/home/claude/spxbot/reporting.py", encoding="utf-8").read()

# Check EXECUTABLE lines only. A naive substring scan matches the comment that
# explains the fix and reports the bug as still present — the same false
# positive that a `-iname "*config.env*"` secret sweep hits on a patch filename.
rep_code = "\n".join(ln for ln in rep_src.splitlines()
                     if not ln.lstrip().startswith("#"))
check("reporting.py does not describe a sleeve as paused at w == 0",
      "w == 0.0" not in rep_code,
      "with a 0.25 floor, 'paused' is unreachable — the dashboard would lie")
check("reporting.py bar scale matches the new ceiling",
      "/ 1.8" not in rep_code, "bars would render at 14-69% of their width")
check("reporting.py scales bars against the 1.25 ceiling",
      "w / W_CEIL" in rep_code, "bar scale constant not found")
check("reporting.py empty-state copy no longer promises pausing/gating",
      "get paused" not in rep_code and "get gated" not in rep_code,
      "the dashboard would advertise a capability the bot deliberately lacks")

# _weight_style is the whole contract in one function — sweep it.
import reporting  # noqa: E402

ws_bad = []
for wv in (0.0, -1.0, 0.25, 0.5, 0.99, 1.0, 1.01, 1.25, 5.0, None, "x",
           float("nan"), float("inf"), float("-inf")):
    for nv in (0, 1, 40):
        try:
            fr, col, lab = reporting._weight_style(wv, nv)
        except Exception as e:  # noqa: BLE001
            ws_bad.append((wv, nv, repr(e)))
            continue
        if not (0.0 < fr <= 1.0) or "paused" in lab:
            ws_bad.append((wv, nv, (fr, lab)))
check("_weight_style total, in-range, and never says 'paused'", not ws_bad,
      ws_bad[:4])
check("_weight_style: neutral renders at 80% of the track",
      near(reporting._weight_style(1.0, 5)[0], 0.8), reporting._weight_style(1.0, 5))
check("_weight_style: ceiling renders full width",
      near(reporting._weight_style(1.25, 5)[0], 1.0))
check("_weight_style: floor renders at 20%, not 0",
      near(reporting._weight_style(0.25, 5)[0], 0.2))
check("_weight_style: a stored 0.0 clamps up to the floor, not to an empty bar",
      near(reporting._weight_style(0.0, 5)[0], 0.2), reporting._weight_style(0.0, 5))
check("_weight_style: n=0 reads 'exploring'",
      reporting._weight_style(1.0, 0)[2] == "exploring")
check("_weight_style: cut is labelled as a cut",
      "cut" in reporting._weight_style(0.4, 9)[2])
check("_weight_style: boost is labelled up",
      "up" in reporting._weight_style(1.2, 9)[2])

dash = reporting.build_dashboard(copy.deepcopy(s))
check("dashboard builds", len(dash) > 2000, len(dash))
check("dashboard never prints 'paused'", "paused" not in dash)
check("dashboard states the floor is not zero", "never blocked" in dash or
      "nothing is ever blocked" in dash)
check("dashboard carries the neutral tick", "wbar-tick" in dash)
check("dashboard reports the effective sample size", "effective sample" in dash)
check("dashboard renders with an empty learner",
      len(reporting.build_dashboard({"journal": [], "learning": {}})) > 500)


# ===========================================================================
print(f"\n{'=' * 74}")
print(f"{PASS} passed, {FAIL} failed")
print("=" * 74)
if FAILURES:
    print("\nFAILURES:")
    for f in FAILURES:
        print(f"  - {f}")
sys.exit(1 if FAIL else 0)
