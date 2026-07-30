"""
Test harness for execution.py. No network, no broker, no clock. Run: python3 test_execution.py

What is being tested here is arithmetic that will size and price real orders, so the
bar is "matches a published reference to the digit," not "looks about right." Three
things in this module are silent killers if they are wrong:

  * vega's units. Per-1.00-vol vs per-1-vol-point differ by 100x and both numbers look
    plausible in a log line. Section 4 pins the convention against Hull's own worked
    example and against a finite difference.
  * implied_vol on a deep-OTM short-dated contract, which is most of what this bot
    trades. Vega there is ~1e-12; a solver that returns a number instead of None hands
    a garbage vol straight to the timing filter. Section 3 asserts None.
  * the cost identity. It decides whether a trade is taken at all. Section 5
    reproduces research note 07 §H.1's worked example digit for digit.

Every public function also goes through a garbage battery (section 10), because this
module runs unattended: an exception in a pricing helper means an unmanaged position.
"""
from __future__ import annotations
import sys, math

import execution as E

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")


def clean(v) -> bool:
    """True if v contains no NaN anywhere. Requirement 1: no public function may
    return NaN, because NaN compares False against every threshold and therefore
    sneaks past every gate rather than being stopped by one."""
    if isinstance(v, dict):
        return all(clean(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return all(clean(x) for x in v)
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return True
    if isinstance(v, (int, float)):
        return not math.isnan(float(v))
    return True


# --------------------------------------------------------------------------
print("=" * 74)
print("1. bs_price against published Black-Scholes reference values")
print("=" * 74)
# Source: John C. Hull, "Options, Futures, and Other Derivatives," the worked example
# in the Black-Scholes-Merton chapter. S=42, K=40, r=10%, sigma=20%, T=0.5.
# Hull's published answers: call = $4.76, put = $0.81.
c = E.bs_price(42, 40, 0.5, 0.20, 0.10, True)
p = E.bs_price(42, 40, 0.5, 0.20, 0.10, False)
check("Hull S=42 K=40 r=10% s=20% T=0.5 call = 4.76", abs(c - 4.76) < 0.005, f"{c:.6f}")
check("Hull same put = 0.81", abs(p - 0.81) < 0.005, f"{p:.6f}")

# Source: Hull, end-of-chapter problem. S=60, K=65, r=8%, sigma=30%, T=0.25.
# Hull's published answer: call = $2.13.
c2 = E.bs_price(60, 65, 0.25, 0.30, 0.08, True)
check("Hull S=60 K=65 r=8% s=30% T=0.25 call = 2.13", abs(c2 - 2.13) < 0.005, f"{c2:.6f}")

# Source: Hull, "The Greek Letters" chapter, running example
# S=49, K=50, r=5%, sigma=20%, T=20 weeks. Hull's published call value: $2.40.
c3 = E.bs_price(49, 50, 20 / 52, 0.20, 0.05, True)
check("Hull S=49 K=50 r=5% s=20% T=20wk call = 2.40", abs(c3 - 2.40) < 0.006, f"{c3:.6f}")

# Zero-rate ATM: the closed form C = S*(2*N(sigma*sqrt(T)/2) - 1) is exact.
S, T, v = 100.0, 0.25, 0.30
exact = S * (2 * E.norm_cdf(v * math.sqrt(T) / 2) - 1)
check("zero-rate ATM matches its exact closed form",
      abs(E.bs_price(S, S, T, v, 0.0, True) - exact) < 1e-12, f"{exact:.10f}")

check("norm_cdf(0) = 0.5", abs(E.norm_cdf(0.0) - 0.5) < 1e-15)
check("norm_cdf(1.96) = 0.975", abs(E.norm_cdf(1.959964) - 0.975) < 1e-6)
check("norm_cdf symmetric to 1e-15",
      abs(E.norm_cdf(0.7) + E.norm_cdf(-0.7) - 1.0) < 1e-15)

print()
print("=" * 74)
print("2. Put-call parity, t<=0 intrinsic, and monotonicity")
print("=" * 74)
worst = 0.0
for s in (80, 95, 100, 105, 130):
    for k in (90, 100, 110):
        for t in (1 / 365, 0.25, 1.0):
            for vol in (0.10, 0.35, 0.90):
                for r in (0.0, 0.05):
                    lhs = (E.bs_price(s, k, t, vol, r, True)
                           - E.bs_price(s, k, t, vol, r, False))
                    worst = max(worst, abs(lhs - (s - k * math.exp(-r * t))))
check("put-call parity holds to 1e-9 over 90 cells", worst < 1e-9, f"max err {worst:.3e}")

check("t_years=0 -> call intrinsic, no ZeroDivisionError",
      E.bs_price(105, 100, 0.0, 0.30, 0.05, True) == 5.0)
check("t_years=0 -> OTM call is worth 0", E.bs_price(95, 100, 0.0, 0.30) == 0.0)
check("t_years<0 -> put intrinsic (clock skew must not crash the loop)",
      E.bs_price(95, 100, -1.0, 0.30, 0.0, False) == 5.0)
check("vol=0 -> forward intrinsic (the zero-vol limit)",
      abs(E.bs_price(105, 100, 1.0, 0.0, 0.05, True) - (105 - 100 * math.exp(-0.05)))
      < 1e-12)
mono = all(E.bs_price(100, 100, 0.5, x) < E.bs_price(100, 100, 0.5, x + 0.01)
           for x in [i / 100 for i in range(1, 300)])
check("price strictly increasing in vol (bisection depends on this)", mono)

print()
print("=" * 74)
print("3. implied_vol round-trips bs_price; returns None where vega collapses")
print("=" * 74)
MONEY = [0.7, 0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.3]
MATS = [("1d", 1 / 365), ("7d", 7 / 365), ("30d", 30 / 365),
        ("90d", 90 / 365), ("1y", 1.0)]
VOLS = [0.15, 0.30, 0.60]
recovered, refused, worst_iv, bad = 0, 0, 0.0, []
for m in MONEY:
    for _lbl, t in MATS:
        for tv in VOLS:
            for call in (True, False):
                px = E.bs_price(100.0, 100.0 * m, t, tv, 0.0, call)
                got = E.implied_vol(px, 100.0, 100.0 * m, t, 0.0, call)
                if got is None:
                    refused += 1
                    # Refusal must be justified: either no price signal at all, or
                    # vega below the documented MIN_VEGA_FOR_IV floor.
                    vg = E.bs_greeks(100.0, 100.0 * m, t, tv, 0.0, call)["vega"]
                    if vg >= E.MIN_VEGA_FOR_IV and px > 1e-6:
                        intrinsic = max(100.0 - 100.0 * m, 0.0) if call \
                            else max(100.0 * m - 100.0, 0.0)
                        if px - intrinsic > 1e-9:
                            bad.append((m, t, tv, call, vg, px))
                else:
                    recovered += 1
                    worst_iv = max(worst_iv, abs(got - tv))
total = len(MONEY) * len(MATS) * len(VOLS) * 2
check(f"grid of {total} cells: {recovered} recovered, {refused} refused, 0 garbage",
      not bad, f"unjustified refusals: {bad[:3]}")
check("every recovered vol is within 1e-6 of truth", worst_iv < 1e-6,
      f"max |iv - true| = {worst_iv:.3e}")
check("a healthy majority of the grid recovers", recovered > total * 0.5,
      f"{recovered}/{total}")

# The specific case the bot trades and the case that breaks Newton solvers.
otm_1d = E.bs_price(100, 130, 1 / 365, 0.30)
check("deep-OTM 1-day call: implied_vol returns None, not a number",
      E.implied_vol(otm_1d, 100, 130, 1 / 365) is None,
      f"price={otm_1d:.3e}, vega={E.bs_greeks(100,130,1/365,0.30)['vega']:.3e}")
check("deep-ITM 1-day call (pure intrinsic) also returns None",
      E.implied_vol(E.bs_price(100, 70, 1 / 365, 0.30), 100, 70, 1 / 365) is None)
check("price below intrinsic -> None (no-arb violation)",
      E.implied_vol(3.0, 100, 90, 0.5, 0.0, True) is None)
check("price above spot -> None (no-arb upper bound for a call)",
      E.implied_vol(101.0, 100, 90, 0.5, 0.0, True) is None)
check("put priced above K*exp(-rT) -> None",
      E.implied_vol(95.0, 100, 90, 0.5, 0.05, False) is None)
check("true vol above the 5.0 bracket -> None, not 5.0",
      E.implied_vol(E.bs_price(100, 100, 1.0, 6.0), 100, 100, 1.0) is None)
check("implied_vol terminates (IV_MAX_ITER is a hard cap)", E.IV_MAX_ITER <= 256,
      f"cap={E.IV_MAX_ITER}, bracket=[{E.IV_VOL_LO}, {E.IV_VOL_HI}]")
check("recovers vol with a nonzero rate too",
      abs(E.implied_vol(E.bs_price(100, 105, 0.5, 0.28, 0.04, True),
                        100, 105, 0.5, 0.04, True) - 0.28) < 1e-6)

print()
print("=" * 74)
print("4. bs_greeks — bounds, signs, and the per-1.00-vol convention")
print("=" * 74)
# Hull, "The Greek Letters": S=49, K=50, r=5%, sigma=20%, T=20 weeks.
# Published: delta 0.522, gamma 0.066, vega 12.1, theta -4.31 per year.
g = E.bs_greeks(49, 50, 20 / 52, 0.20, 0.05, True)
check("Hull delta = 0.522", abs(g["delta"] - 0.522) < 0.001, f"{g['delta']:.5f}")
check("Hull gamma = 0.066", abs(g["gamma"] - 0.066) < 0.001, f"{g['gamma']:.5f}")
check("Hull theta = -4.31 per YEAR", abs(g["theta"] + 4.31) < 0.01, f"{g['theta']:.5f}")
check("Hull theta_per_day = -0.0118", abs(g["theta_per_day"] + 0.0118) < 0.0001,
      f"{g['theta_per_day']:.6f}")

# --- THE OFF-BY-100 PIN. Hull's vega for this contract is 12.1, meaning a move from
# 20 vol to 21 vol (0.20 -> 0.21) changes the option by 0.121. If this module ever
# returns 0.121 here instead of 12.1, every vol-scaled size is 100x wrong and nothing
# else in the test suite would notice.
check("Hull vega = 12.1 PER 1.00 OF VOL (not 0.121 per vol point)",
      abs(g["vega"] - 12.1) < 0.05, f"{g['vega']:.5f}")
check("vega is unambiguously the per-1.00 convention (>1, not <1)",
      g["vega"] > 1.0,
      f"vega={g['vega']:.4f}; the per-point convention would give {g['vega']/100:.4f}")
dv = (E.bs_price(49, 50, 20 / 52, 0.21, 0.05) - E.bs_price(49, 50, 20 / 52, 0.20, 0.05))
check("a 1-vol-POINT move changes value by vega/100", abs(dv - g["vega"] / 100) < 0.002,
      f"actual {dv:.5f} vs vega/100 {g['vega']/100:.5f}")

# Finite-difference vega across the surface. Restricted to cells where vega clears
# MIN_VEGA_FOR_IV: below that floor a central difference of two option prices is pure
# floating-point cancellation (at vega ~1.5e-7 the FD is only good to 3 decimal places
# relatively), which is the same reason the module refuses to report an implied vol
# there. Checking the FD outside that region would test IEEE754, not this code.
worst_fd, checked_fd = 0.0, 0
for m in (0.85, 1.0, 1.15):
    for t in (7 / 365, 0.25, 1.0):
        for vol in (0.20, 0.50):
            an = E.bs_greeks(100, 100 * m, t, vol, 0.03)["vega"]
            if an < E.MIN_VEGA_FOR_IV:
                continue
            h = 1e-5
            fd = (E.bs_price(100, 100 * m, t, vol + h, 0.03)
                  - E.bs_price(100, 100 * m, t, vol - h, 0.03)) / (2 * h)
            worst_fd = max(worst_fd, abs(fd - an) / an)
            checked_fd += 1
check("analytic vega matches central finite difference to 1e-6 relative",
      worst_fd < 1e-6 and checked_fd >= 12,
      f"{checked_fd} cells, max rel err {worst_fd:.3e}")

ok_bounds = True
for m in (0.7, 0.9, 1.0, 1.1, 1.4):
    for t in (1 / 365, 0.1, 1.0):
        gc = E.bs_greeks(100, 100 * m, t, 0.30, 0.02, True)
        gp = E.bs_greeks(100, 100 * m, t, 0.30, 0.02, False)
        ok_bounds &= 0.0 <= gc["delta"] <= 1.0
        ok_bounds &= -1.0 <= gp["delta"] <= 0.0
        ok_bounds &= gc["gamma"] >= 0.0 and gp["gamma"] >= 0.0
        ok_bounds &= gc["vega"] >= 0.0 and gp["vega"] >= 0.0
        ok_bounds &= abs(gc["gamma"] - gp["gamma"]) < 1e-12   # gamma is side-agnostic
        ok_bounds &= abs(gc["vega"] - gp["vega"]) < 1e-12     # so is vega
check("call delta in [0,1], put delta in [-1,0], gamma>=0, vega>=0", ok_bounds)
check("gamma strictly positive for a live ATM option",
      E.bs_greeks(100, 100, 0.25, 0.30)["gamma"] > 0)
check("theta negative for a long ATM option (time decay)",
      E.bs_greeks(100, 100, 0.25, 0.30, 0.0)["theta"] < 0)
check("t=0 -> delta is the expiry step function, second-order greeks are 0",
      E.bs_greeks(105, 100, 0.0, 0.3)["delta"] == 1.0
      and E.bs_greeks(105, 100, 0.0, 0.3)["vega"] == 0.0
      and E.bs_greeks(95, 100, 0.0, 0.3)["delta"] == 0.0)

print()
print("=" * 74)
print("5. roundtrip_cost_frac — note 07 §H.1's identity and worked examples")
print("=" * 74)


def quote(mid, spread_frac):
    return mid * (1 - spread_frac / 2), mid * (1 + spread_frac / 2)


b15, a15 = quote(1.00, 0.15)
rt = E.roundtrip_cost_frac(b15, a15, capture_entry=0.5, capture_exit=0.0)
# Note 07 §H.2: "Half-spread capture on entry, forced exit (realistic):
#                10.87% + 0.085% ~= 11.0% of premium."
check("S=15%, c_e=0.5, c_x=0.0 -> ~11.0% of premium (note 07 §H.2)",
      abs(rt - 0.110) < 0.002, f"{rt:.4%}")

# The closed form, isolated from fees by making the fee fraction vanish. The note's
# own drop-in code gives 0.1125/1.0375 = 10.8434%; its summary TABLE prints 10.87%,
# which is a rounding slip in the note — the identity and the note's code agree.
spread_only = E.roundtrip_cost_frac(b15, a15, 0.5, 0.0, notional_per_contract=1e12)
closed = 0.15 * (2 - 0.5 - 0.0) / (2 + 0.15 * (1 - 0.5))
check("matches the closed form S(2-c_e-c_x)/(2+S(1-c_e)) exactly",
      abs(spread_only - closed) < 1e-12, f"{spread_only:.8f} vs {closed:.8f}")
check("closed form = 10.8434% before fees", abs(closed - 0.108434) < 1e-6,
      f"{closed:.6%}")

# The rest of note 07 §H.1's table, at S=15% with fees at a $1.00 mid.
for label, ce, cx, want in (("cross both ways      ", 0.0, 0.0, 0.1404),
                            ("bot's realistic case ", 0.5, 0.0, 0.1100),
                            ("both sides patient   ", 0.5, 0.5, 0.0731)):
    got = E.roundtrip_cost_frac(b15, a15, ce, cx)
    check(f"S=15% {label} c_e={ce} c_x={cx} -> {want:.2%}", abs(got - want) < 0.002,
          f"{got:.4%}")
check("c_e=c_x=1.0 (both fills at the mid) -> fees only",
      E.roundtrip_cost_frac(b15, a15, 1.0, 1.0) < 0.001,
      f"{E.roundtrip_cost_frac(b15, a15, 1.0, 1.0):.5%}")

# Note 07 §H.2 recommendation 2: "The gate needs to move to roughly 2-4% to bring
# required edge into the 1.5-3.5% range."
band = {}
for s in (0.02, 0.03, 0.04):
    band[s] = E.roundtrip_cost_frac(*quote(1.00, s))
check("2-4% spread gate lands required edge in note 07's 1.5-3.5% band",
      all(0.015 <= v <= 0.035 for v in band.values()),
      "  ".join(f"S={k:.0%}->{v:.2%}" for k, v in band.items()))
check("cost is monotone increasing in the spread",
      band[0.02] < band[0.03] < band[0.04] < rt)
check("required_gross_edge is identical to roundtrip_cost_frac",
      E.required_gross_edge(b15, a15) == E.roundtrip_cost_frac(b15, a15))

# Fees are small but never zero, and they bite hardest on cheap contracts. Same 5% spread
# both times, so the ONLY difference between the two costs is the fixed per-contract fee.
cheap = E.roundtrip_cost_frac(*quote(0.20, 0.05))
rich = E.roundtrip_cost_frac(*quote(3.00, 0.05))
fee_cheap = E.REG_FEES_PER_CONTRACT_ROUNDTRIP / (0.20 * 100)
fee_rich = E.REG_FEES_PER_CONTRACT_ROUNDTRIP / (3.00 * 100)
check("the fixed $0.0854 fee is ~0.43% of a $0.20 contract, ~0.03% of a $3.00 one",
      abs(fee_cheap - 0.00427) < 1e-5 and abs(fee_rich - 0.000285) < 1e-5
      and (cheap - rich) > 0.003,
      f"fee alone: $0.20 -> {fee_cheap:.3%}, $3.00 -> {fee_rich:.4%}; "
      f"same 5% spread, total cost {cheap:.3%} vs {rich:.3%}")

v_gate = E.cost_verdict(b15, a15, 1.00)
v_ok = E.cost_verdict(*quote(1.00, 0.03), 1.00)
v_mid = E.cost_verdict(*quote(1.00, 0.08), 1.00)
check("cost_verdict: 15% gate is 'prohibitive'", v_gate["verdict"] == "prohibitive",
      f"{v_gate['rt_cost']:.2%} — {v_gate['note']}")
check("cost_verdict: 3% spread is 'ok'", v_ok["verdict"] == "ok",
      f"{v_ok['rt_cost']:.2%}")
check("cost_verdict: 8% spread is 'expensive'", v_mid["verdict"] == "expensive",
      f"{v_mid['rt_cost']:.2%}")
check("cost_verdict on a crossed quote is prohibitive, not a crash",
      E.cost_verdict(1.10, 0.90, 1.00)["verdict"] == "prohibitive")

print()
print("=" * 74)
print("6. timing_edge — the Muravyev-Pearson sign convention")
print("=" * 74)
# ATM 30-day, 20 vol, spot 100 -> BSM value 2.2872. Straddle the quote around it.
SPOT, STRIKE, TT, RV = 100.0, 100.0, 30 / 365, 0.20
BSM = E.bs_price(SPOT, STRIKE, TT, RV)
cheap_q = (BSM - 0.15, BSM - 0.05)      # mid BELOW the BSM value: quote is stale-cheap
rich_q = (BSM + 0.05, BSM + 0.15)       # mid ABOVE the BSM value: quote is stale-rich

r = E.timing_edge(SPOT, STRIKE, TT, RV, *cheap_q, True, "buy")
check("BUY is favorable when BSM > mid", r["favorable"] and r["edge"] > 0, r["reason"])
r = E.timing_edge(SPOT, STRIKE, TT, RV, *rich_q, True, "buy")
check("BUY is UNfavorable when BSM < mid", (not r["favorable"]) and r["edge"] < 0,
      r["reason"])
r = E.timing_edge(SPOT, STRIKE, TT, RV, *rich_q, True, "sell")
check("SELL is favorable when BSM < mid  (symmetric)",
      r["favorable"] and r["edge"] > 0, r["reason"])
r = E.timing_edge(SPOT, STRIKE, TT, RV, *cheap_q, True, "sell")
check("SELL is UNfavorable when BSM > mid  (symmetric)",
      (not r["favorable"]) and r["edge"] < 0, r["reason"])

rb = E.timing_edge(SPOT, STRIKE, TT, RV, *cheap_q, True, "buy")
rs = E.timing_edge(SPOT, STRIKE, TT, RV, *cheap_q, True, "sell")
check("buy and sell edges are exact negatives of each other",
      abs(rb["edge"] + rs["edge"]) < 1e-15, f"{rb['edge']:+.6f} / {rs['edge']:+.6f}")
check("edge is expressed as a fraction of the mid",
      abs(rb["edge"] - (rb["bsm"] - rb["mid"]) / rb["mid"]) < 1e-15)
check("bsm and mid are reported, not just the verdict",
      abs(rb["bsm"] - BSM) < 1e-12 and abs(rb["mid"] - (cheap_q[0] + cheap_q[1]) / 2) < 1e-12)

# The mechanism, end to end: the underlying moves, the option quote has not yet.
stale = (BSM - 0.02, BSM + 0.02)
check("underlying ticks UP, option quote unchanged -> buy becomes favorable",
      E.should_cross(100.6, STRIKE, TT, RV, *stale, True, "buy"))
check("underlying ticks DOWN, option quote unchanged -> buy is refused",
      not E.should_cross(99.4, STRIKE, TT, RV, *stale, True, "buy"))
check("...and the same down-tick makes the SELL favorable",
      E.should_cross(99.4, STRIKE, TT, RV, *stale, True, "sell"))
# A +0.05% underlying tick produces a +1.12% option edge — real, but thin. It clears
# M&P's naive filter (min_edge_frac=0) and fails a 2% bar.
check("min_edge_frac raises the bar: a 1.1% edge passes 0% and fails 2%",
      E.should_cross(100.05, STRIKE, TT, RV, *stale, True, "buy", min_edge_frac=0.0)
      and not E.should_cross(100.05, STRIKE, TT, RV, *stale, True, "buy",
                             min_edge_frac=0.02),
      f"edge = {E.timing_edge(100.05, STRIKE, TT, RV, *stale, True, 'buy')['edge']:+.2%}")
check("a dead-even quote (edge exactly 0) does NOT cross",
      not E.should_cross(SPOT, STRIKE, TT, RV, BSM - 0.10, BSM + 0.10, True, "buy"))
check("expired contract refuses to trade on staleness",
      not E.should_cross(SPOT, STRIKE, 0.0, RV, *cheap_q, True, "buy")
      and "expired" in E.timing_edge(SPOT, STRIKE, 0.0, RV, *cheap_q, True, "buy")["reason"])
check("unknown side is refused, not guessed",
      not E.should_cross(SPOT, STRIKE, TT, RV, *cheap_q, True, "BUY_TO_OPEN"))

print()
print("=" * 74)
print("7. stop_election_risk — Finding 2, the headline")
print("=" * 74)
# BPS retail average quoted spread: 13.7% (note 07 §A.4). Entry at the ask of a
# 13.7%-wide quote around a $1.00 mid.
SPREAD = 0.137
b, a = quote(1.00, SPREAD)                      # 0.9315 / 1.0685
drop_to_bid = (a - b) / a                       # 12.82% below the price you paid

# Note 07 §E.2(1) verbatim: "A sell-stop set 10% below your entry can be elected by a
# single print at the bid while the midpoint has not moved at all."
r10 = E.stop_election_risk(b, a, a * 0.90)
r15 = E.stop_election_risk(b, a, a * 0.85)
check("a -10% stop is elected by a bid print with the mid UNMOVED",
      r10["electable_by_noise"],
      f"13.7% spread => a bid print is {drop_to_bid:.2%} below the ask you paid, so "
      f"anything shallower than -{drop_to_bid:.1%} is free money for the tape")
check("a -15% stop is past the noise band (deeper than -12.8%)",
      not r15["electable_by_noise"], r15["note"])
check("min_print is the bid — the lowest ordinary non-adverse print",
      abs(r10["min_print"] - b) < 1e-12, f"{r10['min_print']:.4f}")

# THE HEADLINE. exitrules.HARD_STOP_PCT = -0.60, so broker_stop_price rests a stop at
# 0.40 x entry. On a 13.7%-wide quote that stop is reachable by ordinary print noise
# well before the option is actually down 60%.
STOP = 0.40                                     # -60% of a $1.00 entry mid
mid_at_election = STOP / (1 - SPREAD / 2)       # the mid whose BID equals the stop
b2, a2 = quote(mid_at_election, SPREAD)
r60 = E.stop_election_risk(b2, a2, STOP)
donated = 0.60 - (1.0 - mid_at_election)
check("a -60% stop on a 13.7%-spread contract IS noise-electable",
      r60["electable_by_noise"],
      f"mid only has to reach {mid_at_election:.4f} (-{(1-mid_at_election):.1%}) for the "
      f"BID to touch the {STOP:.2f} stop: the 13.7% spread donates {donated*100:.1f} of "
      f"the 60 points, then election converts it to a MARKET order that books the rest")
check("...and one tick higher on the mid it is NOT yet electable (the boundary is sharp)",
      not E.stop_election_risk(*quote(mid_at_election + 0.001, SPREAD), STOP)["electable_by_noise"])

# The bot's own "phantom market" (see test_watcher.py §5): 0.01 x 0.05 is a 133%-wide
# quote. There, even a -60% stop is elected instantly with the mid unmoved.
rp = E.stop_election_risk(0.01, 0.05, 0.03 * 0.40)
check("phantom 0.01x0.05 market (133% wide) elects even a -60% stop instantly",
      rp["electable_by_noise"],
      f"a bid print is {(0.05-0.01)/0.05:.0%} below the ask; no stop survives this quote")
check("unusable quote fails SAFE (assumes electable)",
      E.stop_election_risk(0.0, 0.40, 0.20)["electable_by_noise"]
      and E.stop_election_risk(1.10, 0.90, 0.50)["electable_by_noise"])

print()
print("=" * 74)
print("8. safe_stop_price — monotone, never looser than requested")
print("=" * 74)
s1 = E.safe_stop_price(b, a, 0.90)
check("a -10% stop is pushed below the noise band", s1 < b,
      f"requested 0.90, returned {s1:.2f}, bid {b:.4f}")
check("the pushed stop is no longer noise-electable",
      not E.stop_election_risk(b, a, s1)["electable_by_noise"])
check("never returns ABOVE the requested stop",
      all(E.safe_stop_price(b, a, d) <= d + 1e-12
          for d in [x / 100 for x in range(2, 200)]))
seq = [E.safe_stop_price(b, a, x / 100) for x in range(2, 200)]
check("monotone non-decreasing in desired_stop (a ratchet cannot ratchet down)",
      all(seq[i] <= seq[i + 1] + 1e-12 for i in range(len(seq) - 1)))
check("an already-deep stop is left alone", E.safe_stop_price(b, a, 0.30) == 0.30)
check("floors at 0.01, never 0.00 or negative",
      E.safe_stop_price(0.02, 0.06, 0.01) == 0.01
      and E.safe_stop_price(0.02, 0.06, -5.0) == 0.01)
check("an unusable quote leaves the requested stop alone rather than moving a risk limit",
      E.safe_stop_price(None, None, 0.55) == 0.55
      and E.safe_stop_price(1.10, 0.90, 0.55) == 0.55)
check("result is always on a valid tick",
      all(abs((x := E.safe_stop_price(b, a, d)) / E.tick_size(x)
              - round(x / E.tick_size(x))) < 1e-6
          for d in (0.13, 0.87, 1.5, 3.33, 4.07)))

print()
print("=" * 74)
print("9. tick_size / round_to_tick / marketable_limit at the $3.00 boundary")
print("=" * 74)
# Penny Interval Program (note 07 §D.1): $0.01 below $3.00, $0.05 at/above.
check("tick_size(2.99) = 0.01 (approaching from below)", E.tick_size(2.99) == 0.01)
check("tick_size(2.9999) = 0.01", E.tick_size(2.9999) == 0.01)
check("tick_size(3.00) = 0.05 ($3.00 is on the COARSE side)", E.tick_size(3.00) == 0.05)
check("tick_size(3.01) = 0.05 (approaching from above)", E.tick_size(3.01) == 0.05)
check("tick_size(0.20) = 0.01 (the bot's cheap end)", E.tick_size(0.20) == 0.01)
check("SPY/QQQ/IWM quote in pennies on ALL series",
      E.tick_size(5.00, all_penny=True) == 0.01)
check("garbage price -> 0.05, valid in BOTH tick regimes",
      E.tick_size(None) == 0.05 and E.tick_size(float("nan")) == 0.05)

check("round_to_tick(3.03,'buy') = 3.00 (buy rounds DOWN)",
      E.round_to_tick(3.03, "buy") == 3.00)
check("round_to_tick(3.03,'sell') = 3.05 (sell rounds UP)",
      E.round_to_tick(3.03, "sell") == 3.05)
check("round_to_tick(2.995,'sell') = 3.00 (crosses the boundary upward, stays valid)",
      E.round_to_tick(2.995, "sell") == 3.00)
check("round_to_tick(2.995,'buy') = 2.99", E.round_to_tick(2.995, "buy") == 2.99)
check("an on-tick price is returned unchanged, both sides",
      E.round_to_tick(3.00, "buy") == 3.00 and E.round_to_tick(3.00, "sell") == 3.00
      and E.round_to_tick(1.23, "buy") == 1.23 and E.round_to_tick(1.23, "sell") == 1.23)
check("rounding never moves a price the wrong way",
      all(E.round_to_tick(x, "buy") <= x + 1e-9 <= x + 1e-9
          and E.round_to_tick(x, "sell") >= x - 1e-9
          for x in (0.117, 1.004, 2.998, 3.001, 3.049, 4.44)))
check("float dust does not drop a whole tick",
      E.round_to_tick(3.0000000001, "buy") == 3.00
      and E.round_to_tick(2.9999999999, "sell") == 3.00)

check("marketable_limit(buy) at slippage 0 is exactly the ask",
      E.marketable_limit(0.95, 1.05, "buy") == 1.05)
check("marketable_limit(sell) at slippage 0 is exactly the bid",
      E.marketable_limit(0.95, 1.05, "sell") == 0.95)
ok_cross, detail = True, ""
for (bb, aa) in ((0.95, 1.05), (2.90, 3.10), (0.20, 0.25), (2.95, 3.05)):
    for sl in (0.0, 0.25, 0.5, 1.0):
        lb = E.marketable_limit(bb, aa, "buy", sl)
        ls = E.marketable_limit(bb, aa, "sell", sl)
        want_b, want_s = aa + sl * (aa - bb), bb - sl * (aa - bb)
        if not (aa - 1e-9 <= lb <= want_b + 1e-9):
            ok_cross, detail = False, f"buy {bb}/{aa} sl={sl}: {lb} not in [{aa},{want_b}]"
        if not (want_s - 1e-9 <= ls <= bb + 1e-9):
            ok_cross, detail = False, f"sell {bb}/{aa} sl={sl}: {ls} not in [{want_s},{bb}]"
check("a rounded marketable limit is marketable and never crosses FURTHER than asked",
      ok_cross, detail)
check("marketable limits land on valid ticks",
      all(abs((x := E.marketable_limit(bb, aa, sd, 0.5)) / E.tick_size(x)
              - round(x / E.tick_size(x))) < 1e-6
          for bb, aa in ((0.95, 1.05), (2.90, 3.10), (0.20, 0.25))
          for sd in ("buy", "sell")))
check("all_penny quotes are not coarsened to a nickel",
      E.marketable_limit(2.98, 3.02, "buy", 0.0, all_penny=True) == 3.02)
check("unusable quote -> None, which cannot be mistaken for an order price",
      E.marketable_limit(0.0, 1.0, "buy") is None
      and E.marketable_limit(1.10, 0.90, "sell") is None
      and E.marketable_limit(0.95, 1.05, "wat") is None)

print()
print("=" * 74)
print("10. Garbage-input battery — nothing raises, nothing returns NaN")
print("=" * 74)
NAN, INF = float("nan"), float("inf")
GARBAGE = [None, NAN, INF, -INF, -1.0, 0.0, "abc", [], {}, True, -0.0001]

CASES = [
    # (fn, good args, which positions to poison)
    (E.norm_cdf, (0.5,), [0]),
    (E.bs_price, (100, 100, 0.25, 0.30, 0.02, True), [0, 1, 2, 3, 4, 5]),
    (E.bs_greeks, (100, 100, 0.25, 0.30, 0.02, True), [0, 1, 2, 3, 4, 5]),
    (E.implied_vol, (3.0, 100, 100, 0.25, 0.02, True), [0, 1, 2, 3, 4, 5]),
    (E.timing_edge, (100, 100, 0.25, 0.30, 2.9, 3.1, True, "buy"),
     [0, 1, 2, 3, 4, 5, 6, 7]),
    (E.should_cross, (100, 100, 0.25, 0.30, 2.9, 3.1, True, "buy", 0.0),
     [0, 1, 2, 3, 4, 5, 6, 7, 8]),
    (E.roundtrip_cost_frac, (0.95, 1.05, 0.5, 0.0, None), [0, 1, 2, 3, 4]),
    (E.required_gross_edge, (0.95, 1.05), [0, 1]),
    (E.cost_verdict, (0.95, 1.05, 1.00), [0, 1, 2]),
    (E.stop_election_risk, (0.95, 1.05, 0.60), [0, 1, 2]),
    (E.safe_stop_price, (0.95, 1.05, 0.60), [0, 1, 2]),
    (E.marketable_limit, (0.95, 1.05, "buy", 0.0), [0, 1, 2, 3]),
    (E.tick_size, (1.50,), [0]),
    (E.round_to_tick, (1.50, "buy"), [0, 1]),
]

raised, nanned, n_calls = [], [], 0
for fn, good, positions in CASES:
    for pos in positions:
        for junk in GARBAGE:
            args = list(good)
            args[pos] = junk
            n_calls += 1
            try:
                out = fn(*args)
            except Exception as exc:                       # noqa: BLE001 — the point
                raised.append(f"{fn.__name__}(pos={pos}, {junk!r}) raised {exc!r}")
                continue
            if not clean(out):
                nanned.append(f"{fn.__name__}(pos={pos}, {junk!r}) -> {out!r}")
    # every argument poisoned at once
    n_calls += 1
    try:
        out = fn(*[NAN] * len(good))
        if not clean(out):
            nanned.append(f"{fn.__name__}(all NaN) -> {out!r}")
    except Exception as exc:                               # noqa: BLE001
        raised.append(f"{fn.__name__}(all NaN) raised {exc!r}")

check(f"{n_calls} garbage calls across {len(CASES)} public functions: none raised",
      not raised, "; ".join(raised[:3]))
check("none returned NaN anywhere in their output", not nanned, "; ".join(nanned[:3]))

# Sentinels are the SAFE direction, not merely non-NaN.
check("an unusable quote costs COST_SENTINEL, which fails every gate",
      E.roundtrip_cost_frac(0.0, 1.0) == E.COST_SENTINEL
      and E.COST_SENTINEL > 1.0 and math.isfinite(E.COST_SENTINEL))
check("COST_SENTINEL is finite so it survives a JSON round trip into state.json",
      math.isfinite(E.COST_SENTINEL))
check("capture outside [0,1] is rejected, not extrapolated",
      E.roundtrip_cost_frac(0.95, 1.05, 1.5, 0.0) == E.COST_SENTINEL
      and E.roundtrip_cost_frac(0.95, 1.05, -0.5, 0.0) == E.COST_SENTINEL)
check("inverted (crossed) quotes are rejected by every quote-taking function",
      E.roundtrip_cost_frac(1.05, 0.95) == E.COST_SENTINEL
      and not E.timing_edge(100, 100, .25, .3, 1.05, 0.95, True, "buy")["favorable"]
      and E.marketable_limit(1.05, 0.95, "buy") is None)
check("zero bid ('phantom market') is rejected, not treated as a cheap contract",
      E.roundtrip_cost_frac(0.0, 0.40) == E.COST_SENTINEL
      and E.marketable_limit(0.0, 0.40, "sell") is None)
check("negative prices never produce a tradeable answer",
      E.bs_price(-100, 100, 0.25, 0.3) == 0.0
      and E.implied_vol(-1.0, 100, 100, 0.25) is None
      and E.round_to_tick(-1.5, "buy") is None)
check("should_cross returns a real bool, never a truthy sentinel",
      isinstance(E.should_cross(None, None, None, None, None, None), bool))

print()
# ---------------------------------------------------------------------------
print("=" * 74)
print("9. best_quoted — tightest strike wins, near misses resample, gates untouched")
print("=" * 74)
BQ = E.best_quoted

def mkq(sp):
    mid = 2.00
    return {"bid": round(mid*(1-sp/2), 4), "ask": round(mid*(1+sp/2), 4),
            "mid": mid, "spread_pct": sp}

# 1) picks the tightest of three
cands = ["A", "B", "C"]
quotes = {"A": mkq(0.061), "B": mkq(0.029), "C": mkq(0.048)}
c, q, notes = BQ(cands, lambda c: quotes[c], max_spread=0.04)
check("tightest strike selected", c == "B" and abs(q["spread_pct"] - 0.029) < 1e-12)
check("strike-scan note emitted", any("strike scan" in n for n in notes), str(notes))

# 2) unquoted candidates skipped; single survivor used
quotes = {"A": None, "B": mkq(0.031), "C": None}
c, q, _ = BQ(cands, lambda c: quotes[c], max_spread=0.04)
check("None quotes skipped, survivor selected", c == "B")

# 3) all None -> (None, None)
c, q, _ = BQ(cands, lambda c: None, max_spread=0.04)
check("all-None quotes -> None result", c is None and q is None)

# 4) empty candidates -> (None, None)
c, q, _ = BQ([], lambda c: mkq(0.01), max_spread=0.04)
check("empty candidate list -> None result", c is None and q is None)

# 5) quote_fn raising treated as unusable, not fatal
def boom(c):
    raise RuntimeError("feed hiccup")
c, q, _ = BQ(cands, boom, max_spread=0.04)
check("raising quote_fn -> None result, no raise", c is None and q is None)

# 6) near miss resamples and improves; sleeps counted
calls = {"n": 0}
seq = {"A": [mkq(0.047), mkq(0.047), mkq(0.036)]}   # improves on round 2
def qf(c): return seq[c].pop(0) if seq[c] else mkq(0.036)
c, q, notes = BQ(["A"], qf, max_spread=0.04, resample=2,
                 sleep_fn=lambda: calls.__setitem__("n", calls["n"] + 1))
check("near miss improved by resample", abs(q["spread_pct"] - 0.036) < 1e-12, str(q["spread_pct"]))
check("resample note emitted", any("resample" in n for n in notes), str(notes))
check("slept once per resample round used", calls["n"] == 2, f"slept {calls['n']}")

# 7) hopeless miss (outside band) does NOT resample
calls = {"n": 0}
c, q, _ = BQ(["A"], lambda c: mkq(0.12), max_spread=0.04, resample=2,
             sleep_fn=lambda: calls.__setitem__("n", calls["n"] + 1))
check("wide miss (12% vs 4% cap, band 1.75x) skips resampling", calls["n"] == 0)
check("wide miss still returned for the gate to reject", q is not None and q["spread_pct"] == 0.12)

# 8) per-candidate minimum is monotone — a later WIDER read never replaces a tighter one
seq2 = {"A": [mkq(0.05), mkq(0.09), mkq(0.09)]}
def qf2(c): return seq2[c].pop(0) if seq2[c] else mkq(0.09)
c, q, _ = BQ(["A"], qf2, max_spread=0.04, resample=2, sleep_fn=None)
check("later wider read never degrades the kept quote", abs(q["spread_pct"] - 0.05) < 1e-12, str(q["spread_pct"]))

# 9) stops early once a resample passes the cap
calls = {"n": 0}
seq3 = {"A": [mkq(0.047), mkq(0.038)]}
def qf3(c): return seq3[c].pop(0) if seq3[c] else mkq(0.038)
c, q, _ = BQ(["A"], qf3, max_spread=0.04, resample=5,
             sleep_fn=lambda: calls.__setitem__("n", calls["n"] + 1))
check("stops resampling once inside the cap", calls["n"] == 1 and q["spread_pct"] <= 0.04,
      f"slept {calls['n']}, spread {q['spread_pct']}")

# 10) garbage spread values in quotes are unusable, never selected
quotes = {"A": {"bid": 1, "ask": 1.1, "mid": 1.05, "spread_pct": float("nan")},
          "B": mkq(0.033), "C": {"bid": 1, "ask": 1.1, "mid": 1.05, "spread_pct": -0.2}}
c, q, _ = BQ(cands, lambda c: quotes[c], max_spread=0.04)
check("NaN/negative spread quotes excluded", c == "B")

# 11) the helper never invents a passing quote: if nothing tightens, verdict stays wide
c, q, _ = BQ(["A"], lambda c: mkq(0.05), max_spread=0.04, resample=2, sleep_fn=None)
check("un-tightened near miss returned wide (gate will reject)", q["spread_pct"] == 0.05)


print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
print("=" * 74)
sys.exit(1 if FAIL else 0)
