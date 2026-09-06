# Backtest Report — the shipped rules vs six years of real option quotes

Run: 2026-07-31, ThetaData NBBO EOD history, SPY + QQQ, 2020-10-01 → 2026-07-24
(~1,459 sim days per symbol, 2,918 chain snapshots, 6 data errors total).
Fills booked pessimistically throughout: spreads open at net bid / close at net
ask; singles buy the ask / sell the bid; $0.10/contract/side fees. The harness
imports the production functions themselves (HAR forecast, implied vol, exit
rules, signal indicators) and `test_backtest.py` pins every threshold to the
live engine, so what was tested is what trades.

## Executive verdict

**The credit-spreads sleeve failed its backtest and its entries are now
disabled** (kill switch, `SPXBOT_SPREADS`, default off; management of any open
package continues). At shipped settings it never trades — not once in six
years. In every parameter neighborhood tested it trades a little and loses:
profit factors 0.81–0.98 before stress, −$1,146 to −$1,378 after — in a window
that *excludes* 2018 and March 2020, the two worst short-premium regimes in
modern history, so reality would have been worse.

**The tech singles sleeve survived.** 203 trades, +$2,653 total, profit factor
1.24, the correct positive-skew shape (+1.31: small losses, larger wins), and
it stays positive under its stress re-booking (+$1,694, PF 1.15). It is
supportive evidence, not proof: PSR says 88% probability the true edge is
positive (our bar for "proven" is 95%), the MinTRL math wants ~397 trades and
we have 203, and the sleeve's worst regime is the current one.

## Spreads sleeve — why it never trades, and why looser versions lose

Production gates: short strike ≈ 1.1 expected moves down at 5–10 DTE, implied
−forecast ≥ 2.0 vol points, uptrend only, credit ≥ 20% of width, package round
trip ≤ 8% of credit, max loss ≤ $400.

Gate autopsy (per-day rejection counts, both symbols): after the trend veto
(1,412 days) and the richness bar (883), **the credit floor rejected every
single surviving day — 1,468 of them.** The diagnosis is structural, not bad
luck: a 20%-of-width credit is native to the 30–45-DTE spreads the management
literature studies; at our 5–10 DTE the same-delta spread collects roughly
half that. Two of our rules were imported from different regimes and their
intersection is the empty set. The richness bar has the same flavor milder:
implied−forecast at this strike/tenor ran +1.3 to +2.9 points on ordinary
days — a 2.0 bar admits only the tail, and those days are mostly downtrends
where the (correct) trend veto stands.

Disclosed sensitivity grid (6 cells, trend veto always on; multiple-testing
note below):

| cell | n | total | win rate | PF | skew | maxDD | stress total |
|---|---|---|---|---|---|---|---|
| rich 2pt, floor 20% (production) | 0 | — | — | — | — | — | — |
| rich 2pt, floor 12% | 22 | −$59 | 68% | 0.87 | −1.42 | −$232 | −$1,146 |
| rich 1pt, floor 12% | 22 | −$59 | 68% | 0.87 | −1.42 | −$232 | −$1,146 |
| rich 1pt, floor 10% | 46 | −$15 | 72% | 0.98 | −1.40 | −$362 | −$1,315 |
| rich 2pt, floor 10% | 40 | −$148 | 68% | 0.81 | −1.19 | −$370 | −$1,378 |
| rich 1pt, floor 20% | 0 | — | — | — | — | — | — |

The shape is exactly the textbook warning: 68–72% win rates, negative skew,
net losses. The 50%-credit take-profit forfeits winners' second halves while
the 2×-credit stop eats full-size losses, and at 5–10 DTE the credits are too
small for the arithmetic to overcome pessimistic fills. **No tested variant
is worth trading.** A redesign targeting 30–45 DTE (where the 20% floor and
the managed-early evidence are native) is a legitimate future research
question — it is not a parameter tweak of this sleeve, and nothing re-enables
until that work is done and separately backtested.

Stress definition (spreads): every trade whose leg-OHLC bounds imply the 2×
stop was breached intraday is re-booked at 2.5× credit — the EOD-granularity
lower bound.

## Tech singles sleeve — supportive, unproven, regime-dependent

Headline (both symbols, 203 trades, ~38.6/year): total **+$2,653**, win rate
36%, profit factor **1.24**, avg +$13.07/trade, skew **+1.31**, per-trade
Sharpe 0.079 (annualized ≈ **0.49**), PSR **0.88**, MinTRL **397 trades**,
max drawdown **−$1,762**, 120 of 203 trades flagged for possible intraday
stop breach.

Stress variant (flagged trades re-booked at −35% of premium — the live
watcher stops at −30% intraday, so flagged EOD prints may flatter): total
**+$1,694**, PF **1.15**, annualized Sharpe ≈ 0.32, maxDD −$2,055. Positive
either way — the conclusion does not rest on the optimistic reading.

Per regime:

| regime | n | total | win rate | PF |
|---|---|---|---|---|
| 2020H2 rebound | 40 | −$457 | 35% | 0.79 |
| 2022 bear | 29 | **+$2,210** | 48% | **2.17** |
| 2023–24 recovery | 95 | +$1,602 | 36% | 1.45 |
| 2025–26 modern | 39 | **−$702** | 28% | 0.80 |

The pattern is coherent: RSI/MACD mean-reversion entries with a vol-forecast
gate earn in volatile two-way markets and bleed in steady grinds — and the
current regime is the bleeding kind. This is precisely what the live learner
exists to detect with its own growing sample; nothing here justifies turning
the sleeve off, and nothing justifies scaling it up.

## Methodology honesty — bugs caught during the study

Two of our own errors were caught and fixed mid-study, both disclosed because
a backtest whose mistakes are hidden is worthless: (1) a first sensitivity
grid passed richness thresholds in the wrong units (vol points where the code
wants decimals), demanding 50–200 points of richness — its all-zero cells
were garbage and were discarded; the production run was unaffected. (2) an
early smoke test truncated the shared stock-bars cache (END-scoped pull);
fixed by pinning the cache to the full range. The units bug is also why the
per-cell gate counters exist: identical counts across thresholds is what
exposed it.

Multiple-testing note: six sensitivity cells were examined. With that search
breadth, the expected maximum Sharpe under the null is materially positive —
and the best cell still came out *negative*, which makes the spreads verdict
stronger, not weaker. No cell was promoted to production.

## Limitations — read before believing anything above

EOD granularity (intraday exits approximated; 120/203 singles and all spread
stress flags exist for this reason). The window begins 2020-10: **2018
Volmageddon and the March-2020 crash are not in sample, and both are worst
cases for short premium** — the spreads verdict would only degrade with them.
News sleeve untestable (no historical news); singles tested on SPY/QQQ only,
so the live watchlist's single-name results may differ. One position per
sleeve per symbol. Pessimistic fills may modestly understate real execution
(live fills at mid-ish have been achieved on penny-wide chains). Six years,
one market: regime dependence is demonstrated *inside* the sample, so treat
every aggregate number as regime-weighted, not universal.

## Production changes made on this evidence

1. `SPXBOT_SPREADS` kill switch added, **default off**, set off in trade.yml.
   `manage_spreads()` still runs — any open package is managed to its exit.
   Re-enabling is one env flip in two places, and should wait for a 30–45-DTE
   redesign with its own backtest.
2. Nothing else. Singles continue unchanged; no cap or gate was loosened on
   the strength of a six-cell grid, per the finalize-rules discipline.

## Recommendations (owner decisions, stated plainly)

Keep the singles sleeve running as-is and let live evidence accumulate toward
the ~400-trade bar; expect it to underperform while the current low-vol grind
lasts. Treat the spreads sleeve as retired pending a 30–45-DTE redesign
study (cheap now — the data subscription and harness exist). Spend nothing
further on data this month; the $80 answered the question it was bought to
answer, twice over.
