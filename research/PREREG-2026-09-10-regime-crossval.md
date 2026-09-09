# Pre-registration — corrected regime switch: re-run, cross-validation, robustness (written before the run)

Batch 4's regime result (S1 QQQ: PF 1.38, Sharpe 1.17, P(pass) 67%) used a switch whose 14-day window
included the session's OWN close-to-close move. That is look-ahead and it flatters a momentum strategy.
The switch is corrected (window ends at the previous close) and everything regime-related is re-run.

## Runs (Alpaca SIP minute bars 2016-2026, constant $200 risk, futures-equivalent costs, holdout 2024-)
1. S1 noise-area + corrected regime on QQQ  (the number that replaces batch 4's)
2. Cross-validation on indices the regime idea has NOT been tested on: IWM (~RTY, M2K) and DIA (~YM, MYM),
   plus SPY, each with and without the regime switch. Costs: 1 M2K ~ 25 IWM shares, round trip ~$3.60 ->
   7.2c/share -> 3.6c/side; 1 MYM ~ 12 DIA shares, ~$3.00 -> 12.5c/share -> 6.2c/side (approximate).
3. Robustness grid on QQQ: lookback {10, 14, 20} x percentile {0.4, 0.5, 0.6}. Reported as a table, NOT
   used to pick a parameter; the live config stays 14 / 0.5 whatever the grid says.

## Predictions
- Corrected S1 QQQ regime: PF drops from 1.38 toward 1.2-1.3; Sharpe from 1.17 to ~0.8-1.0. If it drops
  all the way back to the unfiltered 1.13 / 0.56, the regime effect WAS the leak.
- Cross-validation: if the effect is real it should show as improvement in Sharpe with the switch ON for
  IWM and DIA too (they share the mechanism); if only QQQ improves, treat it as QQQ-specific fragility.
- Grid: all nine neighbours positive and within ~30% of the 14/0.5 cell if the effect is robust.

## Decision rule
Proceed to the paper-lab confirmation (QQQ + regime, 20 sessions) only if, corrected:
  QQQ regime PF >= 1.25 AND holdout t >= 1.0 AND at least one of IWM/DIA also improves with the switch
  AND >= 7 of 9 grid cells have PF >= 1.15. Otherwise the regime switch is withdrawn.
