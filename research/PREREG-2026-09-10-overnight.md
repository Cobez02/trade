# Pre-registration — overnight sleeve + portfolio with the intraday Nasdaq sleeve (2026-09-10, before the run)

## Why this and not more intraday work
28 backtests say the day-only index space has one small Nasdaq effect (Sharpe ~0.5) and nothing else.
The overnight drift is the largest documented index effect (Cooper-Cliff-Gulen 2008; Lou-Polk-Skouras 2019).
A daily-bar sanity check (free yfinance, 2016-2026, weekday nights, GROSS of costs) shows it in all four
indices: QQQ +5.6 bp/night Sharpe 1.09; IWM +5.4 bp Sharpe 1.02; SPY +3.7 bp Sharpe 0.86; DIA +3.2 bp
Sharpe 0.78; positive in every sub-period. That check is exploratory; this run is the test with minute-bar
fills, futures-equivalent costs, contract-sized notionals and the prop simulator.

## Sizing rule (decided before the run): the worst night, not the average, sets the size
1 M2K (micro Russell) ~ $12k notional; 1 MYM (micro Dow) ~ $23k; 1 MES ~ $38k; 1 MNQ ~ $50k.
Worst weekday nights 2016-2026 are -6.7% to -7.2%. Against a $2,000 floor only M2K survives a worst night
at 1 contract (-$860); MYM (-$1,550) survives a $3,000 floor; MES/MNQ do not at 1 contract. So:
  overnight-A: 1 M2K (IWM proxy, notional 12,000, slip 3.5c/share/side)
  overnight-B: 1 MYM (DIA proxy, notional 23,000, slip 3.0c/share/side)
  overnight-C: 1 MNQ (QQQ proxy, notional 50,000, slip 1.9c) — reported to show WHY it is too big.
Weekday nights only (no Friday->Monday: firms forbid weekend holds). No overnight stop modelled.

## Predictions
- Net Sharpe per sleeve 0.7-1.0 (gross ~1.0 minus ~1.2 bp/night costs). Worst night within the numbers above.
- Correlation between overnight sleeves ~0.7-0.8 (same macro news); between overnight and the intraday
  Nasdaq sleeve (S1) < 0.2.
- Portfolio {S1 QQQ intraday, overnight-A, overnight-B}: combined Sharpe 1.0-1.3.

## Gates (scripts/portfolio.py, unchanged): holdout Sharpe >= 1.2, holdout t >= 1.5, P(pass) >= 65% all-years
and >= 55% holdout, zero combined days <= -$1,000, max corr < 0.5. Scored on the TradeDay $50K static preset
(the firm whose written rules allow both overnight holds and a personal bot) and on the $100K static preset.
A pass on the $100K preset counts, because the $100K account's floor is what makes a 2-contract overnight
book survivable; the fee difference is small.

## What a pass means
Proceed to: (1) written confirmation from TradeDay on overnight + EA + news rules, (2) the paper lab switched
to IWM/DIA overnight + QQQ intraday for 20 sessions (Alpaca can hold shares overnight), (3) then, and only
then, an evaluation. Multiplicity after this batch: 32 backtests on this data.
