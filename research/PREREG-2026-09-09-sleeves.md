# Pre-registration — sleeves 2 & 3, regime switch, portfolio (2026-09-09, written before the run)

Same data (SPY/QQQ SIP minute bars 2016-01-04..2026-09-05), same costs (MES/MNQ-equivalent), same
constant $200 risk per trade, same holdout split (2024-01-01). The holdout is already contaminated by
batches 1-3 and is treated as such: a pass here is a reason to run real MNQ/ES data, not to buy anything.

## Sleeves
- S1  noise-area intraday momentum, QQQ, band exit — ALREADY RUN (batch 2: PF 1.13, holdout PF 1.10).
      Used as-is from research/2026-09-07-lots10/QQQ-as-MNQ. SPY excluded from the portfolio: it failed
      in all three prior batches (decided now, before this run).
- S2  last-half-hour momentum (Gao-Han-Li-Zhou): one trade at 14:30 CT, sign of the first-half-hour
      return with second-to-last-bar confirmation, hold to 14:58 CT. Run on QQQ and SPY.
- S3  intraday relative value SPY vs QQQ: fade a >= 1.5-sigma divergence since the open, exit <= 0.5 sigma,
      stop 1 sigma further, one entry/day, dollar-neutral legs sized so a stop-out costs $200.
- Regime switch: session ON only if trailing 14-day mean |daily return| >= expanding median (no look-ahead).
      Applied as variants of S1, S2 and S3.

## Predictions
- S2 QQQ: PF between 1.05 and 1.25; more trades than S1 (one per day) and lower cost share.
- S3: PF >= 1.1, daily-P&L correlation with S1 and S2 below 0.3 (market-neutral).
- Regime: fewer sessions, higher Sharpe on the holdout for S1; unclear for S2/S3.
- Combined equal-weight portfolio {S1, S2 QQQ, S3}: Sharpe roughly sqrt(3) x the average sleeve Sharpe
  if correlations are low — i.e. ~0.9-1.1, which would still be BELOW the gate.

## Gates (scripts/portfolio.py evaluates exactly these; no subset selection)
  combined holdout Sharpe >= 1.2;  combined holdout t >= 1.5;  P(pass) all-years >= 65%;
  P(pass) holdout >= 55%;  zero sessions with combined intraday low <= -$1,000;  max pairwise corr < 0.5.
Two portfolios are scored: {S1, S2-QQQ, S3} and the regime variants {S1r, S2r-QQQ, S3r}. If either passes
all six gates: proceed to the Databento MNQ/ES run. If neither: the funded-account goal is closed for this
strategy family; the engine and paper lab remain as a platform.

Multiplicity after this batch: 6 sleeve runs + 2 portfolios here; 19 backtests in total on this data.
