# Pre-registration — refinement batch, 2026-09-09

Written BEFORE the batch runs. One refinement, two parameters, then no more tuning on this data.

## What the first two batches showed
- Gross edge positive on SPY (+$20.7k) and QQQ (+$26.7k) over 2016–2026 at constant $200 risk/trade;
  MES/MNQ-equivalent costs (~1.2 bp round trip) consume 131% of SPY's gross and 47% of QQQ's.
- Entries in the first two hours (09:00–11:00 CT) carry essentially all the gross profit.
- Band exits lose on average; trades held to the close win. Stops cost ~$186 each.

## Hypothesis (H1)
Cutting turnover keeps most of the gross and removes most of the cost:
  NOISE_MULT 1.0 -> 1.5   (breakout must clear 1.5x the mean absolute move: fewer, stronger signals)
  NO_NEW_ENTRY_AFTER 14:00 -> 11:00 CT   (only first-two-hour entries)
Everything else unchanged (30-min bars, 14-day lookback, band exit, 0.5x-width stop, $200 risk,
daily kill $700, cap $1,200, flatten 14:52 CT, costs 4.5c / 1.9c per share-side).

## Predictions
- Trades per day fall by >= 40% (from ~0.75 to <= 0.45).
- QQQ net PF rises from 1.13 to >= 1.20; holdout (2024-) PF >= 1.15.
- SPY stays at or below breakeven (PF <= 1.05). A SPY PF > 1.10 would be a surprise, not a confirmation.

## Decision rule (what "pass" means for this batch)
Proceed to the paper-lab stage with the refined config ONLY IF, on QQQ-as-MNQ:
  holdout PF >= 1.20  AND  holdout t-stat >= 1.0  AND  P(pass) MFFU 1x >= 45%  AND  0 days below -$700.
Otherwise: stop; the strategy family is falsified for a prop rulebook at these costs.

## Caveat, stated plainly
The 2024+ holdout has already been LOOKED AT in aggregate (batches 1 and 2), so it is no longer a
clean out-of-sample test. Any pass here must be confirmed by (a) the live paper lab and (b) the
Databento MNQ run before money is involved. Multiplicity: 3 backtests in this batch, 11 total.
