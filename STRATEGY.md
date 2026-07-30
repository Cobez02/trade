# STRATEGY — what this program trades, and why every rule is the way it is

*Specification as of 2026-07-28. Every number in this document is either a live
constant in the code (named where it appears) or a computed result from the
research phase, reproduced by the test suites (`test_stats.py`, `test_screens.py`,
`test_vol.py`, `test_execution.py`, `test_exitrules.py`, `test_learn.py`,
`test_watcher.py`, `test_strategies.py` — 1,279 named checks, all passing).
The evidence base is the 117-paper library in `research/papers/` and the nine
synthesis notes in `research/notes/`. The honest evaluation of the whole
enterprise — including the probability math on the 10x goal — is in
`ASSESSMENT.md`, which is part of this specification, not an appendix to it.*

---

## 1. What this program is

An autonomous, options-only paper-trading system on Alpaca. It buys single-leg
equity and ETF options (long calls and long puts, never short), holds them for
minutes to hours, never overnight, and manages every open position with a
three-layer exit stack evaluated tick-by-tick. Entries come from two signal
sleeves (news catalysts and technical indicators), pass seven negative screens
built from the retail-options literature, pay the spread only when the
contract is cheap against the program's own volatility forecast, and are sized
by a learning loop that is allowed to shade position size between 0.25× and
1.25× — and is structurally incapable of switching anything off.

The design stance, stated plainly: the research phase found no reliable
evidence that this class of trade — buying short-dated options as a retail
account — has positive expectancy, and substantial evidence that it does not
(§2, and ASSESSMENT.md). What the research does support is a program that
loses as slowly as the evidence allows, measures itself honestly, and
accumulates a journal capable of *proving* an edge if one exists. Every rule
below is one of those two things: a cost the literature says we must not pay,
or an instrument for collecting evidence without fooling ourselves.

## 2. The cost floor that governs everything

The single most consistent finding across the library is that execution costs,
not signal quality, decide retail options outcomes. The round-trip cost model
(implemented in `screens.py`, verified in `test_screens.py`):

    RT = S(2 − c_e − c_x) / (2 + S(1 − c_e)) + fees/premium

where S is the quoted relative spread and c_e, c_x are entry/exit effective-
to-quoted ratios. At the 15% spreads this bot originally tolerated, the round
trip costs **10.93% of premium**. At the current 4% gate it costs **1.58%**.
For calibration, the best-documented cross-sectional option-return anomaly is
worth roughly 0.5% per month — so a single 15%-spread round trip burned ~22
months of the best edge in the literature. This is why `MAX_SPREAD_PCT = 0.04`
appears twice (engine and screens) and why there is no fallback anywhere that
relaxes it: an illiquid chain is a correct reason not to trade
(`engine.find_contract` returns nothing rather than re-scanning without the
open-interest floor, `MIN_OPEN_INTEREST = 250`).

The quote feed is Alpaca's INDICATIVE options feed, not OPRA, so the spread
gate is a proxy measured on indicative quotes, not a measurement of NBBO. The
stock feed is IEX (~4.5% of consolidated volume). Both facts are limitations,
not features, and they are stated here because pretending otherwise would
corrupt every downstream number.

## 3. Signal sleeves

Four sleeves exist in the code (`engine.SLEEVES = ["wsb", "news", "tech",
"flow"]`) because journal attribution, exit management, learning and the
dashboard key on sleeve names, and history must survive design changes. Two
still open positions (`engine.ACTIVE_SLEEVES = ["news", "tech"]`); two are
retired (`engine.RETIRED_SLEEVES = {"wsb", "flow"}`).

**news (active).** Alpaca market-news headlines over the trailing 48h, scored
by a keyword lexicon, aggregated per symbol; needs at least two confirming
headlines (|score| ≥ 2). The honest basis: news-momentum effects exist in the
literature but decay within hours and are largely spent by the time a retail
feed carries them. This sleeve is retained as a hypothesis under test, not a
proven edge, and the learning loop's job is to size it accordingly.

**tech (active).** RSI(14), MACD histogram and 50-day trend on a 14-name
liquid watchlist: oversold-plus-turning (RSI < 35, MACD rising), MACD bull
cross in an uptrend, or overbought rollover (RSI > 70, MACD falling).
Technical signals on daily bars are the weakest empirical claim in the
program; they are cheap to compute, produce interpretable journal features
(entry_rsi, macd_rising, trend_align), and are the second hypothesis under
test.

**flow (retired).** The sleeve read call/put *open-interest* skew as smart-
money positioning. Open interest has no trade direction in it — every
contract has a buyer and a seller. The literature that does extract signal
from options activity (Pan & Poteshman 2006) requires *signed, open/close*
volume data that this bot's feed does not carry. What the sleeve actually
measured was mostly hedging structure. No study supports the deployed signal;
retired outright. A signed-volume feed would be grounds to revive it (§9).

**wsb (retired as entry; survives as a veto).** The sleeve bought whatever
r/wallstreetbets was loudest about. The retail-options literature identifies
attention-driven lottery demand as the *most reliably overpriced* corner of
the market (Han & Kumar's retail concentration; Boyer & Vorkink's ex-ante
skewness premium; the WSB event studies in the library). Buying with the
crowd means paying that premium. The feed's one usable property — it knows
where the crowd is — became `strategies.crowd_veto()`: names above 50
comments (capped at 15 names) **block new buys in either direction**. The
veto is defensive only: it removes candidates, never creates a position, and
is never inverted into writing options against the crowd — that is a
margin-intensive short-vol strategy this account is not equipped or
authorized to run. It is fail-open: a dead feed vetoes nothing, because a
free third-party API must not gate the bot and the Tier-1 screens catch the
same lottery profile from the contract's own measurable shape.

Retiring a sleeve retires its capital. `SLEEVE_ALLOCATION` still divides
$10,000 by four ($2,500 per sleeve), so retirement halves maximum deployment
rather than doubling the survivors' budgets. The verdict on wsb/flow was
"this class of trade is negative-EV," which is not an argument that news/tech
deserve double stakes; at any Kelly fraction, a doubtful edge argues for less
deployment, not redistribution. This is flagged as an owner decision in §10.

## 4. The seven negative screens (Tier 1)

Every candidate entry passes `screens.screen_entry()` **before** the learner
sees it, because these thresholds come from samples of up to 889,967 real
retail round-trips while the learner reasons from this bot's own handful of
trades. All screens run even after the first failure so the journal records
every reason a trade was rejected. Thresholds are config-overridable but the
defaults are the researched values.

**spread** — reject if relative spread > 4% (`MAX_SPREAD_PCT`). Basis: the
cost model above.

**premium** — reject contracts under $0.50 (`MIN_CONTRACT_PRICE`). On a $0.20
contract, per-contract fees and payment-for-order-flow economics alone are
~6.0% of premium round-trip; cheap contracts are cost machines.

**dte** — reject under 2 days to expiry (`MIN_DTE`). The final days
concentrate theta decay and the worst measured buyer returns: the library's
event studies put 7-day OTM calls near −35%/week on average, with the
terminal days the worst of it. (0DTE is therefore excluded by construction.)

**moneyness** — reject strikes more than 15% OTM (`MAX_MONEYNESS_OTM`). Deep
OTM is where the lottery-demand overpricing concentrates.

**expiration blackout** — no entries that straddle the weekly expiration
rollover, where measured returns carry a mechanical artifact unrelated to any
signal.

**skew** — reject when the contract's *ex-ante* skewness (Boyer–Vorkink,
computed in the log domain in `screens.py` — the naive formula suffers
catastrophic cancellation and returned the wrong *sign* on exactly the deep-
OTM contracts it exists to catch; found and fixed, pinned in
`test_screens.py`) exceeds 4.0 (`MAX_EX_ANTE_SKEW`). High ex-ante skew is
the single strongest cross-sectional predictor of negative option buyer
returns in the library: the top skew quintile of puts measured near
−60%/week.

**lottery (MAX)** — reject underlyings whose recent maximum daily return
exceeds 15% (`MAX_LOTTERY_RETURN`). The MAX effect: recent extreme upside
marks a name the lottery crowd is chasing, and its options with it.

## 5. Execution discipline

**Only pay the spread for cheap volatility.** Before any order, the program
computes its own 21-day Yang–Zhang realized-vol forecast (gap-aware; the
gap-blind estimators — Parkinson, Garman–Klass, Rogers–Satchell — measured
2.2–2.5 vol points low on this watchlist, and a low vol anchor makes every
option look expensive) and backs implied vol out of the quote. The ex-ante
edge identity `E[edge] ≈ vega × (σ_forecast − σ_implied)` (Muravyev–Pearson
execution-timing logic, `execution.timing_edge`) must be favorable; buying a
contract whose implied vol already exceeds the forecast is paying the spread
for something the program itself believes is overpriced. A vol-forecasting
edge of the size this machinery can plausibly produce is worth well under one
vol point, which is smaller than the cost floor at wide spreads — one more
reason the 4% gate is load-bearing.

**Never a market order, never a blind premium.** Entries go as marketable
limits at the touch (`execution.marketable_limit`, slippage 0.0), not `ask ×
1.03` — the old 3% multiplier gave away up to 75% of a spread that did not
need to be crossed at all. When the two-sided quote is missing, the program
synthesizes a conservative ask from the mid at the spread ceiling
(expensive-but-fillable beats free-but-imaginary), and `engine.option_mid` is
named for what it returns — its predecessor was named `option_ask` while
returning the mid, which silently priced every fallback order half a spread
too tight.

## 6. The exit stack — three layers, ordered by what survives failure

Positions are day-trades in the strict sense: entries stop 90 minutes before
the close (`NO_NEW_ENTRY_MIN`), everything is flattened 15 minutes before the
bell (`EOD_FLATTEN_MIN`), and nothing is ever carried overnight.

**Layer 1 — a resting stop order on Alpaca's side** (survives everything on
our side dying). Placed at −60% from entry (`exitrules.HARD_STOP_PCT`),
deliberately far below the working stop so it fires only when nothing is
watching. Its price is *not* the naive level: Alpaca's sell-stop election
rule ("elects if there is a trade on the consolidated tape at or lower than
your stop price," and election converts it to a **market** order — the
"not outside the NBBO" qualifier exists only for buy stops) means any stop at
or above the bid can be elected by ordinary bid prints with the mid unmoved.
So the trigger is pushed at least one full quoted spread below the bid
(`execution.safe_stop_price` — never moves a stop up toward the noise band,
monotone so the ratchet can't reverse), and the order is a **stop-limit**
with the limit one further spread below the trigger (note 07 §E.5): in the
normal case the fill is identical, and in the pathological case — a 1-lot
inside quote, an empty book — the limit refuses to print into the hole. A
stop-limit can go unfilled in a true gap; that is why this is the floor and
not the primary exit, and why a rejected stop-limit is retried once as a
plain stop (an unprotected fill beats no floor). With no usable quote the
program falls back to a stop-market for the same reason. The stop only ever
ratchets upward as the position's peak rises (trail arms at +25%, gives back
20% of peak, `exitrules.broker_stop_price`).

**Layer 2 — the watcher** (`watcher.py`): a per-second process streaming live
option quotes over the websocket with REST backfill for any symbol quiet for
four seconds, running the decision loop every 0.25s. P&L is measured on the
**bid** — the price someone will actually pay — not the mid; the mid was
flattering positions by half a spread exactly when it mattered. A stop
decision requires three fresh quote sequence numbers over at least one second
(a single bad print must not sell), except a panic breach 8 points past the
trigger, which skips the debounce. Every sell is triple-guarded (in-flight
set, open-order check, live position re-read) because the watcher, the
resting stop and the hourly job can all reach for the same position, and
selling a long option twice opens a short.

**Layer 3 — the rules themselves** (`exitrules.py`): pure functions, no
sockets or clocks, replayable offline against historical paths. Working stop
at −30%, trailing take-profit (arms +25%, 20% give-back — the fixed +45%
target was removed after replay showed it turned +$63 of winners into +$7),
time-stop at 1 DTE even while winning.

## 7. Sizing and the learning loop

The loop's mandate: **size, never select.** The original design gated —
switched off — any feature bucket with a poor record at 5 samples. The
research phase measured that rule (full derivation in ASSESSMENT.md and
`research/data/out_02.txt`): at the strategy's own break-even win rate of
0.40, the gate fires falsely 33.7% of the time per bucket; across 17 buckets
the family-wise false-gate probability is 0.9991; the sample size the
decision actually needs is n≈61 uncorrected (n≈131 Bonferroni-corrected),
not 5 — an underpowering of 12–26×; and a fired gate was an absorbing state
(a blocked bucket can never earn the samples that would clear it), which had
already made the bot structurally short-only from five same-day observations.
Monte-Carlo of the old loop against a never-adapting bot: 56–59% probability
the learning made things *worse*.

The current loop (`learn.py` + `stats.py`, redesign validated in
`research/data/out_03.txt`):

* Every learned adjustment is a multiplier in **[0.25, 1.25]**
  (`stats.GATE_FLOOR/GATE_CEIL`), never zero — a discouraged setup keeps
  producing the evidence that could clear it.
* Below **30 effective samples** (`GATE_ACTIVATION_N`) the multiplier is
  exactly 1.0; confidence ramps in continuously. Effective samples means
  clustering-corrected: same-day trades are nearly one observation
  (Moulton design effect, `deff = 1 + (m̄−1)ρ`; the bot's first seven trades,
  all settled 2026-07-27, are n_eff ≈ 1.0–1.7, not 7).
* Sleeve weights use DerSimonian–Laird empirical-Bayes shrinkage toward the
  pooled mean — at n=4 per sleeve, honest shrinkage keeps ~8% of observed
  differences, not 100% clamped to the rails.
* Collinear feature dimensions are deduplicated by Cramér's V before they
  count as evidence (on the live journal, direction and trend_align were
  V=1.0 — the same fact wearing two names, which the old loop counted as two
  confirming lessons).
* When several dimensions all discourage a trade, the multipliers do **not**
  compound (six floors would be 0.25⁶ ≈ 0.0002 — a gate with extra steps);
  the worst single dimension governs (minimum rule, floor 0.25).
* The loop is total: a corrupt journal row (JSON round-trips, crash
  mid-write) maps to "unknown" and is excluded — it can never raise, because
  an exception in `learn()` kills the loop that closes positions, and it can
  never vote, because coerced garbage would size real trades.

## 8. Risk rails

$10,000 logical bankroll (`START_EQUITY`, mapped onto the $100k paper account
by `SPXBOT_BASELINE`), $2,500 per sleeve, $350 max premium per trade
(`MAX_PREMIUM_PER_TRADE`), 2 open positions per sleeve
(`MAX_OPEN_PER_SLEEVE`), target contracts 3–12 DTE about 1.5% OTM, one
position per (sleeve, underlying, direction). A trade the learner discourages
below one contract is dropped, never rounded back up to 1 — rounding up
would silently discard the learner's only lever. No overnight risk, ever.

## 9. What evidence would change this design

The retirements and screens are verdicts on evidence, not permanent taste.
Specifically: **flow** returns if the bot gains access to signed open/close
options volume (the Pan–Poteshman condition); **wsb** returns as an entry
source only with peer-reviewed evidence that a *free, public, lagged*
sentiment feed predicts option returns net of the 1.6–11% round-trip cost;
the **short-vol side** (harvesting the variance risk premium rather than
paying it — the one robustly documented options premium, though its index
form ran Sharpe +0.74 pre-2012 and −0.12 after) becomes discussable only
with margin approval, defined-risk spreads, and owner sign-off, because its
loss distribution is the mirror of the current program's. The learning loop's
thresholds loosen as the journal grows: at ~100 settled trades across ≥40
distinct days with n_eff ≥ 60, the activation gate is fully open and the
loop's shrinkage does the regularizing.

## 10. Decisions flagged for the owner (not applied unilaterally)

1. **Capital after retirement** (§3): deployment cap is now $5,000 of
   $10,000. Alternative: re-divide the bankroll across the two active
   sleeves. The code keeps the conservative reading; changing
   `SLEEVE_ALLOCATION` to divide by `len(ACTIVE_SLEEVES)` is a one-line
   owner decision.
2. **Legacy learner constants**: `MIN_SAMPLES_SLEEVE = 4` and
   `MIN_SAMPLES_GATE = 5` remain in the code as *floors for reporting*, but
   the operative thresholds are the activation rule (30 effective samples)
   and shrinkage. Recommendation on file since the research phase: treat 30+
   n_eff as the earliest any learned adjustment should move size, and ~100
   settled trades as the earliest a sleeve comparison means anything
   (MinTRL at the bot's own measured per-trade Sharpe of 0.044 is ~1,400
   trades — the honest number for "statistically demonstrated").
3. **The 10x goal itself** — see ASSESSMENT.md before committing real money.
   That document is the required reading; this one is only the machinery.

## Addendum 2026-07-29: the drought fix — measure better, never pay more

Two consecutive zero-trade days produced a post-mortem with a surprise in it:
the candidate universe was already liquid (the tech sleeve scans SPY, QQQ and
the megacaps) and strikes already target near-the-money. What was actually
failing was the *measurement*. Each signal was judged on exactly one strike's
quote at one instant, on the free indicative feed — which is not the NBBO and
whose quoted spreads flutter (one contract read 11% → 33% → 10% → 21% across
consecutive hourly runs on day 1). A single wide read is often staleness, not
the market's true price.

The change, live from 2026-07-30: each signal is judged on the 2–3 nearest
liquid strikes (`find_contracts`), each strike keeps the tightest of its
sampled quotes, and a near miss (within 1.75x the spread cap) is re-sampled
twice ~18s apart before rejection (`execution.best_quoted`). Every candidate
still clears the identical OI floor, spread cap, premium band, skew and
lottery screens — the gates did not move. TSLA missed day 2 by 0.7 points on
one strike's one reading; under this rule it would have been examined on
three strikes and up to nine readings.

What was deliberately NOT done, in order of how tempting it was: the 4%
spread cap was not raised (the modelled round trip at the observed 10–20%
spreads is 7–15% of premium per trade — no documented signal earns that
back); the OI floor was not lowered (the deleted fallback that ignored it
produced the single worst fill on the books); and no minimum-trades-per-day
rule was added (a forced trade is a guaranteed toll with no compensating
edge; the literature's most robust finding is that turnover for its own sake
is how retail loses). Days with no passing trades remain a correct output.
