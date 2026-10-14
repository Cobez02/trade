# The 500-hour research program — raising the portfolio Sharpe by disciplined search

Written 2026-10-13. Target: a portfolio of independent, mechanism-grounded sleeves whose COMBINED out-of-sample
Sharpe is as high as the data allows — honestly expected 1.2–1.6 after costs, from ~1.0 today. The program's
value is as much in what it rejects as in what it finds: a tireless researcher without a tireless referee
converges on the most convincing accident (see the 9/9 regime leak), so the referee is built into the process.

## 0. Operating model (how 500 hours get spent without a human in the loop)
- **Unit of work = one pre-registered test**, executed in one Claude Code session on the Mac (`cd ~/spxf/trade && claude`).
  Each session reads `research/program/QUEUE.md`, takes the top item, writes `research/program/prereg/<id>.md` BEFORE
  running anything, runs it, appends one line to `research/program/SCOREBOARD.md`, commits, pushes. Stateless: the repo is the memory.
- **Hypothesis budget**: 120 tests total across the program (a hard ceiling). Every test, pass or fail, counts. The
  scoreboard tracks N so the multiple-testing correction is always computable. Running out of budget ends the search.
- **Sealed holdout**: all data from 2024-01-01 is LOCKED. A test may only use ≤ 2023 data for development. A candidate
  earns ONE holdout evaluation at the end of its family (Phase 3), never during search. (The intraday work in
  September looked at the holdout repeatedly; those numbers are contaminated and are not reused.)
- **Mechanism-first**: a hypothesis must state WHO PAYS and WHY (risk transfer, forced flow, constraint, behaviour)
  and cite the paper or the structural reason. "The chart does X" is not a hypothesis. No pattern mining.
- **Fixed cost model**: futures ~1 tick slippage + commissions per side (MES $1.00, MNQ $1.00, M2K/MYM ~$1.00);
  equities 1c/share + any borrow. Costs are never tuned to make a result pass.
- **Red-team before verdict**: a separate session reviews the code for look-ahead, survivorship and label leakage
  before any candidate touches the holdout. The regime-switch leak would have been caught here.
- **Human cadence**: 20 minutes a week — read SCOREBOARD.md, approve the next week's QUEUE.md. That is the only
  human input the program needs. Credits: a session is ~2–4 hours of agent time; 500 hours ≈ 150 sessions ≈ 5 months
  at one session a day, or faster in parallel.

## 0a. The credit rule (Connor, 2026-10-14)
Phase 0 runs on the Databento signup credit ($125). `research/program/data.py` estimates every request with the free
metadata endpoint, keeps a spend ledger, and REFUSES any request that would pass the budget. When that happens the
session stops, commits the ledger, and reports: what was downloaded, what it cost, what remains, what the next
request would cost. It does not continue, raise the budget, or find a workaround until Connor says so.
Before the first download, run `python -m research.program.data` to print the full Phase 0 estimate.

## 1. Statistics that gate everything (implemented once, Phase 0)
- Walk-forward / combinatorial purged cross-validation (López de Prado) on ≤2023 data for every candidate.
- Deflated Sharpe ratio (Bailey & López de Prado) using the running trial count N from the scoreboard.
- Probability of backtest overfitting (PBO) reported for any test with a parameter grid.
- Minimum track length: a sleeve needs ≥ 400 trades or ≥ 5 years of daily P&L to be scored at all.
- Gate to Phase 3 (per sleeve, on ≤2023 data): deflated Sharpe > 0 at 95%, PF ≥ 1.15, costs < 40% of gross,
  worst-day inside the venue's floor, and correlation < 0.5 with every existing sleeve.
- Verdict (Phase 3, one shot on the holdout): holdout Sharpe ≥ 0.5 of in-sample and > 0 with t ≥ 1.0. Fail → retired forever.

## 2. Phases and hour budget
| Phase | Hours | Output |
|---|---|---|
| 0 Infrastructure | 40 | Data lake (Databento CME full-session 2010+, Alpaca all-stock minute 2016+, EDGAR fundamentals, event calendars); the stats module; the test harness + prereg template; holdout sealed by code (loader refuses 2024+ unless `--verdict`) |
| 1 Prop-compatible sleeves | 150 | Index/rates/metals/energy futures, intraday or single-Globex-session only. ~40 tests. Catalog §3A |
| 2 Own-capital sleeves | 150 | Multi-day futures (TSMOM, carry, calendar), cross-sectional equities on the Alpaca universe. ~50 tests. Catalog §3B |
| 3 Portfolio & verdicts | 60 | Red-team, one-shot holdouts, correlation matrix, vol-targeted combination, drawdown-aware sizing per venue |
| 4 Live validation | 100 | Paper (Alpaca) + practice/eval (Topstep) running the surviving sleeves; monthly re-score; fill reconciliation; kill rules |

## 3. Hypothesis catalog (each line = one or two tests; the mechanism is the entry ticket)
### 3A. Prop-compatible (flat by 15:10 CT; Globex single-session holds allowed)
1. Globex overnight drift, MES/MNQ/M2K/MYM, 1% stop — DONE on 2 years; redo on 2010-2026 minute data (the anchor sleeve).
2. Overnight drift conditioned on the prior day's intraday move (Lou-Polk-Skouras tug-of-war: overnight continuation after intraday reversal).
3. Overnight drift conditioned on VIX term structure (contango/backwardation) — risk premium is paid when hedging demand is high.
4. FOMC-cycle effect (Cieslak-Morse-Vissing-Jorgensen): returns concentrate in even weeks of the FOMC cycle — hold overnight only in those weeks.
5. Pre-FOMC drift post-2015 (Lucca-Moench, decayed per Kurov-Wolfe-Gilbert) — expect FAIL; test once to close it.
6. Macro-release nights: hold through / avoid CPI, NFP, FOMC nights — does the premium pay for the gap, or not?
7. Turn-of-month and month-end rebalancing flows in ES (pension/target-date rebalancing, documented) — intraday last-hour positioning on T-1..T+1.
8. Opex-week and quarterly-roll-week behaviour in ES/NQ (dealer gamma unwinds) — last-30-min continuation/reversal by week type.
9. Last-30-minute momentum conditioned on dealer gamma proxy (Baltussen mechanism): sign of the day's move × a vol-regime proxy. (Unconditional version FAILED in Sept.)
10. Overnight drift in Treasury futures (ZN/ZB) and gold (GC): is the night premium an equity-only phenomenon?
11. Crude (CL) inventory-report (Wed 09:30 CT) drift and the EIA-day intraday pattern — event-driven, mechanism = scheduled information release.
12. Opening-range breakout on MNQ, un-leveraged (Zarattini-Aziz) — expect thin; close it properly on 15 years.
13. Cross-market lead-lag: ES overnight move → RTY/YM catch-up at the open (small-cap lag) — single-session, both legs allowed.
14. Volatility-regime switch for the overnight sleeve (corrected, no look-ahead) — size 1 vs 0 by trailing realized vol.
### 3B. Own-capital (multi-day holds, broad universe)
15. Time-series momentum across 20+ CME futures, 1–12-month lookbacks, weekly rebalance (Moskowitz-Ooi-Pedersen 2012; AQR/Baltas-Kosowski replications). The deepest-documented futures premium; Sharpe ~0.8–1 diversified.
16. Carry across futures (roll yield / term-structure slope) — Koijen et al. "Carry".
17. Cross-sectional 12-1 momentum, monthly, top/bottom decile on the Alpaca universe (point-in-time universe needed — Norgate/Sharadar).
18. Short-term reversal (1-week/1-month) in small caps — liquidity provision; capacity-limited = retail-sized.
19. Overnight-vs-intraday cross-sectional (Lou-Polk-Skouras): long high-overnight-return stocks held overnight only.
20. Post-earnings-announcement drift (PEAD) using EDGAR 8-K timing + Alpaca prices.
21. Same-month seasonality (Heston-Sadka) and January/December tax-loss effects in small caps.
22. Low-volatility / betting-against-beta long-short (Frazzini-Pedersen) — needs shorting; check borrow.
23. Index-rebalancing and inclusion/deletion flows (Russell reconstitution in June) — forced flow, dates public.
24. Dividend-capture and ex-date behaviour in ETFs — small, mechanism-grounded, check costs.
### 3C. Explicitly excluded (and why)
- Price-only machine learning on bars (no mechanism; decades of out-of-sample failure).
- Anything requiring sub-second execution or order-book data (we pay that spread; we don't earn it).
- Options selling on a prop account (not offered); on own capital only after Phase 2 (OPRA data cost, tail risk).
- Any hypothesis generated by looking at the holdout, a recent live P&L, or a chart.

## 4. What ends the program early
- Budget exhausted (120 tests) with fewer than 3 survivors → the retail-accessible space is as thin as the literature says; stop.
- Any holdout verdict that contradicts a strong in-sample result → log it, do not re-test, move on. One shot means one shot.
- Live validation (Phase 4) showing fills > 2× the modelled cost on any sleeve → that sleeve is retired regardless of backtest.

## 5. Honest expectation, written before the first test
Survivors: 3–8 sleeves. Combined holdout Sharpe: 1.2–1.6 at 10–12% unlevered volatility. Probability of finding any
standalone sleeve with holdout Sharpe > 2: well under 10%. If the combined number lands below 1.0 after 120 tests,
the program failed and the September conclusion stands.
