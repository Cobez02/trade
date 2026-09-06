# 30–45 DTE credit-spreads redesign — verdict: not shipped

Run: 2026-08-01, ThetaData NBBO EOD, SPY+QQQ, 2020-10 → 2026-07. Rules
pre-registered in `dd0e68e` before any 30-45 DTE data was examined; the
extension grid was declared in a later commit at a point when zero
trade-level results existed (all original cells were empty — there was
nothing to tune toward). Success bar, fixed in advance: primary cell PF > 1
AND stress total > 0 AND n ≥ 40.

## Verdict

**The redesign fails its own bar, and the failure is informative.** The
production-analog design (short put 1.1 expected-moves down) produced **zero
trades in six years at any tested credit floor** — with clean data, 477–592
days per symbol die at the credit floor alone. Moving the short strike to
where the floor is native (0.5 EM, ~30-delta — the strike zone the
managed-early literature actually trades) produced **at most 7 trades in six
years**: +$99 total, PF 1.6, 86% win rate, **negative skew (−2.1), and a
NEGATIVE stress total (−$270)**. Seven trades is ~1.2/year — no sample, no
sleeve, and the stress reading means even that trickle doesn't survive the
honest intraday lower bound.

## The two findings worth keeping

**1. The floor is delta-mismatched, not tenor-mismatched.** The original
backtest report hypothesized the 20%-of-width floor belonged at 30–45 DTE.
It doesn't — it belongs at ~30-delta strikes. A 1.1-EM short (~10 delta)
cannot collect 15–20% of width at any tenor or width; the two rules'
intersection is empty everywhere, not just at weeklies. That closes the
"future research question" left open by BACKTEST_REPORT.md.

**2. Our gate stack and short premium are philosophically incompatible.**
Gate autopsy at 0.5 EM: the uptrend veto removes ~half of days; the
richness gate (implied ≥ forecast + 1–2 pts) removes 130–377 more; the
floor 264–595; the 8% cost gate 84–97. Each gate is individually sensible —
together they demand fat credit, cheap execution, calm trend, AND rich vol
simultaneously, a conjunction that occurred 2–3 times per symbol per six
years. Selling premium at frequency requires accepting conditions our
discipline stack exists to refuse (selling into downtrends, selling
un-rich vol, paying wider spreads). A sleeve built by relaxing those
refusals would be a different and more dangerous strategy, and the n≤7
cells give it no empirical encouragement.

## Multiple-testing note

Eight cells were examined across both grids (plus a data-layer fix). The
best cell shows +$99 on n=7 — under any search-breadth correction that is
indistinguishable from zero. No cell approaches the pre-registered bar.

## Data-layer fix disclosed

The first run's expiry pick selected Mon/Wed weeklies from the
retrospective expiry list that were not yet listed on the pick date — a
look-ahead that surfaced as 689 empty snapshot days (it wasted days rather
than fabricating trades; direction of bias: none on P/L, coverage only).
Picks now restrict to Friday expirations, which list months ahead
(no_snap: 360 → 6 per symbol). The corrected data made the original
design's zero-trade verdict *cleaner*, not different.

## Production decision

Nothing ships. `SPXBOT_SPREADS` stays off; no 30–45 DTE sleeve is built.
The spreads research program under the current gate philosophy is
**retired** — three configurations tested end-to-end (5–10 DTE weeklies,
30–45 DTE at 1.1 EM, 30–45 DTE at 0.5–0.75 EM), none tradeable. Total live
dollars lost across all three: $0. The singles sleeve continues unchanged.
