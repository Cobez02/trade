# Override study — "go over what the bot wants when the run is confident"

Run: 2026-08-04, pre-registered in `532e58f` before results existed.
Question (Connor's, after Monday's zero-trade rally day): when the market is
visibly running, should the bot override its timing-edge gate and buy
anyway? Method: the exact production entry pipeline replayed over 14 names
and six years, taking ONLY the entries the edge gate blocked — then sliced
by how strongly the underlying was running in the signal's direction on
entry day. Production exits, pessimistic fills.

## Result: the more confident the day, the worse the override

| cell | n | total | PF | mean ret/premium |
|---|---|---|---|---|
| A — every blocked trade | 626 | +$459 | 1.01 | −1.2% |
| B — blocked, on ≥1.0% run days (the proposal) | 186 | **−$877** | 0.96 | −1.2% |
| C — blocked, on ≥1.5% run days | 139 | **−$1,685** | **0.89** | **−4.1%** |

Three readings. First, the gate is calibrated: everything it rejected,
pooled, earns PF 1.01 — statistical zero — versus PF 1.24 for what it
accepted. It is separating paid edges from noise, which is its entire job.
Second, the proposal's specific cell (B) fails the pre-registered bar:
negative total on 186 trades. Third — the finding that settles the
argument — **the loss grows with the "confidence."** Restricting to the
most obvious run days (C) makes returns three times worse per dollar. This
is adverse selection, measured: on the days a move is most visible,
option sellers have already repriced hardest, so the overpayment the gate
detects grows faster than any trend edge. The market charges retail for
obviousness, and the strongest-run days are when it charges most.

Monday, priced by this study: the stand-down cost $0 while SPY-hold gained
~$150 on the bench. Six years of overriding on Mondays like that (cell C)
cost −$1,685. The gate bought cheap regret and declined expensive regret.

## Verdict

**Not shipped — the pre-registered bar (run-day cell with PF > 1, total >
0, n ≥ 30) is failed in both run cells, with the dose-response gradient
pointing the wrong way.** No threshold exists in this data at which
"confidence in the run" pays for overriding the price gate. The
discretionary version of the same instinct still has its designated home:
the second-paper-account moonshot offer, where conviction trades can be
taken and honestly scored without touching the validated experiment.

Limitations: EOD granularity; run measured close-to-close (an intraday run
definition could differ — but would need to beat a −4.1%/trade headwind);
fixed production exits (the live-exit replay showed trailing does not
systematically rescue blocked-quality entries); 3 declared cells, none
promoted.
