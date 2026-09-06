# Watchlist validation — the production rules replayed on every name we trade

Run: 2026-08-01. The six-year backtest that green-lit the singles sleeve
tested SPY+QQQ only; the TSLA cap study added TSLA. This study closes the
gap: the production sim (`sim_singles` — same RSI/MACD entries, same
vol-edge gate, same $600 cap, same exits, pessimistic fills) replayed on
the 11 remaining watchlist names, 2020-10 → 2026-07. One cell per name,
production config only — a validation, not a parameter search.
Pre-registered in `dd0e68e` before results were seen.

## Results (six years per name, sorted)

| name | n | total | PF | win rate | skew | maxDD |
|---|---|---|---|---|---|---|
| GOOGL | 51 | **+$1,818** | 1.71 | 47% | +0.9 | −$533 |
| AAPL | 65 | **+$1,779** | 1.62 | 37% | +2.5 | −$427 |
| AMZN | 59 | +$997 | 1.28 | 42% | +0.7 | −$907 |
| TSLA* | 42 | +$942 | 1.14 | 36% | +1.0 | −$2,569 |
| NVDA | 55 | +$857 | 1.14 | 36% | +0.8 | −$1,426 |
| PLTR | 113 | +$809 | 1.13 | 32% | +1.9 | −$1,070 |
| COIN | 14 | +$183 | 1.07 | 29% | +1.5 | −$2,045 |
| META | 43 | +$146 | 1.03 | 42% | +0.5 | −$1,683 |
| AMD | 77 | −$285 | 0.96 | 34% | +1.8 | −$1,953 |
| **IWM** | 64 | **−$1,359** | **0.58** | 25% | +1.6 | −$1,628 |
| **MSFT** | 34 | **−$2,370** | **0.35** | 21% | +1.1 | −$2,436 |
| **NFLX** | 31 | **−$3,346** | **0.13** | 13% | +0.5 | −$3,532 |

*TSLA from Friday's cap study, same harness and config.

**Aggregate across the 11 previously-untested names: −$771.** The validated
core (SPY, QQQ, TSLA) earned; the unaudited tail gave a chunk of it back.
That is exactly what this study existed to find out.

## Reading it honestly

With 11 looks, one or two nominally-negative names would be expected by
chance even if all were fine. But PF 0.13 over 31 trades (NFLX), 0.35 over
34 (MSFT), and 0.58 over 64 (IWM) are not chance-shaped: those are
systematic failures of our entry logic on those names. AMD (PF 0.96, n=77)
IS chance-shaped — a coin flip, not a defect. Everything above it is
somewhere between "fine" and "strong," with GOOGL/AAPL looking like the
sleeve's true home turf.

Why would the same rules earn on GOOGL and bleed on NFLX? A stated
hypothesis, not a proven mechanism: NFLX (and to a degree MSFT) move in
event-driven cliffs — earnings gaps — and an RSI-oversold entry
systematically catches those knives mid-fall, while the vol-edge gate
underprices gap risk on names whose variance arrives in lumps. IWM fails
differently: small-cap index mean-reversion has been structurally weak in
this window, and its premium runs thin against our fee/spread floor.

**The NFLX paradox deserves its own paragraph**, because live-NFLX has been
one of our best real tickers (+$189 and +$70 last week — 2 for 2). Both
things are true: 2 live wins, and 31 backtest trades losing 87% of the time
over six years. A 2-trade sample contains no information against a
31-trade one. This is precisely the trap the backtests exist to catch —
the live sample seduces, the long sample instructs.

## Selection caveat, stated plainly

Dropping the three worst names *after* looking at 11 results is post-hoc
selection — the honest forward claim is NOT "+$7,075 recovered" (their
combined losses). Some of that would be noise we're pruning by luck. The
defensible claim is narrower: three names show six-year systematic losses
under our exact rules, and continuing to trade them means taking daily
positions backed by *negative* evidence. Removal eliminates unsupported
exposure; it does not bank the backtest's losses as future profit.

## Recommendation (Connor's decision — nothing changed yet)

Drop **NFLX, MSFT, IWM** from the watchlist. Keep **AMD** (statistical
coin flip, positive skew, biggest sample among the marginal names — the
learner's live evidence can adjudicate it). Keep everything else. The
change is one line in `strategies.py`; on your word I'll make it, test it,
and push it before Monday's open.

## Addendum (same day): the trailing-exit challenge, tested

Connor's objection — "don't drop our strongest earner" (NFLX, 3-for-3
live) — had one strong technical version: this study graded names under
the sim's simplified fixed exits, while the live watcher TRAILS (arms at
+25% peak, exits on a 20-point giveback), and NFLX's live wins were
exactly the let-it-run kind. So the identical entries were replayed under
the live exit rules (`liveexit_replay.py`, post-hoc diagnostic, labeled as
such; EOD-conservative — closing bids only, which *understates* trailing).

Result: **trailing does not rescue any flagged name.** NFLX under live
exits: −$3,422, PF 0.04, 2 winners in 30. MSFT: PF 0.13. IWM: PF 0.30.
The drop recommendation survives its strongest challenge under both exit
models. (Two side-findings for the deprioritized exit study, hypothesis
only: PLTR and AMZN graded materially BETTER under trailing — +$5,540/PF
1.99 and +$1,602/PF 1.43 — despite the EOD handicap; and the live NFLX
wins are consistent with drawing twice from the thin winning tail of a
distribution that loses 27-of-31 times.)

Owner decision as of this addendum: **no change made** — NFLX, MSFT, and
IWM remain on the watchlist pending Connor's call, per the standing rule
that watchlist membership is his.

## Limitations

Same harness limitations as the parent study: EOD granularity (flagged
counts are high on volatile names), IEX bar feed, one position per symbol,
window excludes 2018/Mar-2020, pessimistic fills. COIN has only 14 trades
(IPO 2021) — its cell is directional at best. Per-name stress variants not
run (the parent study's stress margin carried the aggregate verdict; a
name-level stress pass is cheap if any keep/drop call feels close).
