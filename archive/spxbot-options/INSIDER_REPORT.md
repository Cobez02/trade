# Insider Cluster-Buy Study — SEC Form 4 clusters expressed as call debit spreads

Run: 2026-07-31, ThetaData NBBO EOD history, entries 2020-10-01 → 2026-04-15.
Signal: SEC structured Form 4 data (23 quarters, 2020q3–2026q1), open-market
purchases (code P) only; a **cluster** = ≥2 distinct insiders buying ≥$200k
combined inside 10 days, deduplicated per ticker per 30 days → **5,258 events,
2,533 tickers**. Expression: next trading day after the cluster, buy the
nearest-ATM call / sell the call nearest +8% above spot, 60–90 DTE (prefer 75);
exits at 1.75× debit (take), 0.50× (stop), or 21 DTE (time). Pessimistic fills
(open at net ask side, close at net bid side), $0.10/contract/side fees.

**Pre-registration:** every rule above was committed to this repo (`a74d7e4`,
with the frozen event file) *before* the result file existed. Nothing was
tuned after seeing results; the post-hoc cells below are labeled as such.

## Executive verdict

**Failed — and not narrowly.** The pre-registered configuration produced
**4 trades in 5½ years, all losers, −$446.60**. The disclosed sensitivity
grid then separated the two possible explanations, and both came back
negative: real execution costs are catastrophic (paying the spread on every
quoted candidate loses 95 cents of each gross dollar), **and the signal
itself has no pre-cost edge in this expression** — at frictionless
mid-market fills with zero fees, 738 trades still net −$557 (profit factor
0.98, a coin flip). There is no cost model under which this sleeve makes
money, because there is nothing for a cost model to preserve.

**No insider sleeve will be built.** No kill switch is needed for a thing
that was never wired in.

## The funnel — where 5,258 events went

| stage | events | note |
|---|---|---|
| clusters detected | 5,258 | 2020-07 → 2026-06 |
| in test window | 5,023 | entries 2020-10-01 → 2026-04-15 |
| ticker has listed options | 4,007 | 80% — the rest are too small |
| has usable price bars | 3,985 | IEX daily feed |
| two-sided quotes at our strikes | 778 | **only 20%** of the eligible — thin books at 60–90 DTE |
| passed cost + size gates | **4** | median round-trip cost is 10× our gate |

`probe_errors: 0, data_errors: 0` — the funnel is data-complete; every
exclusion above is a market fact, not a fetch failure.

## Pre-registered result (primary): n=4, −$446.60

| ticker | cluster | entry | exit | reason | P/L |
|---|---|---|---|---|---|
| INTC | 2022-11-08 | 11-09 | 12-20 | stop −52% | −$58.40 |
| SPG | 2023-04-03 | 04-04 | 05-09 | stop −59% | −$290.40 |
| BMY | 2023-11-30 | 12-01 | 01-26 | time (21 DTE) | −$48.40 |
| CLF | 2024-05-01 | 05-02 | 06-05 | stop −55% | −$49.40 |

Four trades is not a sample; it is a symptom. The reason the funnel pinched
to 4 is the finding:

**Round-trip cost as a fraction of the package debit, all 771 measurable
quoted candidates:** p10 = 23%, p25 = 40%, **median = 86%**, p75 = 180%,
p90 = 393%. Our gate — carried over from the penny-wide SPY/QQQ world the
production bot lives in — was **8%**. On single names at 60–90 DTE, even the
*tightest decile* of markets is ~3× that gate; the median market charges
most of your debit just to get in and out. The four names that squeaked
through (INTC, SPG, BMY, CLF) are exactly the mega-liquid exceptions.

## Disclosed post-hoc sensitivity — is it the costs, or the signal?

| cell | fills | cost gate | n | total | PF | win rate |
|---|---|---|---|---|---|---|
| pre-registered | pessimistic | 8% | 4 | −$447 | 0.00 | 0% |
| A | pessimistic | 15% | 23 | −$1,842 | 0.28 | 22% |
| B | pessimistic | 25% | 78 | −$5,194 | 0.29 | 22% |
| C | pessimistic | none | 705 | **−$124,602** | 0.05 | 12% |
| D | **mid, no fees** | none | 738 | **−$558** | **0.98** | 42% |

Reading, in order: loosening the cost gate (A, B) admits more trades that
lose more. Removing it entirely (C) is a wood-chipper — crossing an 86%-
median spread twice per trade converts $124.6k of premium into market-maker
revenue. And cell D is the one that closes the case: it books every trade at
**mid-market with zero fees** — an execution no retail account achieves —
and the strategy *still* loses (738 trades, PF 0.98, exits: 368 stops / 262
takes / 58 time / 50 series-end). The signal's gross expectancy in this
expression is zero. Costs didn't kill an edge; there was no edge to kill.

Multiple-testing note: four post-hoc cells were examined and the *best* —
with impossible fills — is negative. No search-breadth correction can turn
that positive. No cell was, or will be, promoted to production.

## Why this diverges from "insider buying works" (the literature)

The academic result is real but it is a different animal: stock-level
returns, months-to-years horizons, portfolio-weighted across hundreds of
names, measured pre-cost — and concentrated in small, illiquid companies.
This study asked the only question that matters for us: **does that alpha
survive translation into a defined-risk, retail-executable options position?**
The answer has three parts, all visible in the funnel: a fifth of cluster
names have no options at all; of those that do, only a fifth have two-sided
markets at the strikes the thesis wants; and where markets exist, the spread
consumes the alpha several times over — while the +8%-OTM/75-DTE spread
structure itself (capped upside, theta bleed, hard stop) clips the exact
long-tail outcomes the insider signal is supposed to deliver. What's proven
in the literature is not what's buyable at the NBBO.

## Limitations

EOD granularity (entries and exits at end-of-day marks; entry uses the next
day's close, not open). **Survivorship:** tickers delisted before today are
missing bars/quotes in both data feeds — this *excludes* acquisition
outcomes, which are a win case for insider signals, so the pure-signal cell
(D) could be somewhat understated; it would need to overcome ~zero PF *and*
86%-median friction to matter, but the caveat is real. IEX bars are a thin
feed (spot marks may differ from consolidated tape). One expression tested:
this verdict binds the 60–90-DTE +8%-OTM debit-spread expression, not
insider-following as such (e.g., stock positions with month-scale holds were
not tested and cannot be, in an options-only program). Window excludes
2018 and March 2020.

## Production decision

Nothing changes in the live bot. No insider sleeve, no new env flag, no
watchlist changes. The pipeline (`clusters.py`, `insider_events.json`,
`insider_bt.py`, `sens_insider.py`) stays in the repo as the reusable
pattern for "test someone else's proven idea before trading it" — this is
the second strategy this week (after the 5–10-DTE credit spreads) that a
$0-marginal-cost backtest refuted before it could touch the account.
