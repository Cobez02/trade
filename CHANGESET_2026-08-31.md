# Change set, 2026-08-31

Suite green: **16 files, 1,196 assertions, 0 failures.** Secret sweep clean.

## Files to upload

| file | status |
|---|---|
| `.github/workflows/trade.yml` | modified (this is the one GitHub runs) |
| `.github/workflows/watch.yml` | modified |
| `trade.yml`, `watch.yml` | modified (inert duplicates, see note 6) |
| `main.py` | modified |
| `engine.py` | modified |
| `backtest.py` | modified (warning only) |
| `test_journal.py` | **NEW** |
| `test_config.py` | **NEW** |

---

## 1. The journal dedupe bug, fixed, plus a second one I had not diagnosed

`rebuild_from_alpaca` kept only the newest buy and newest sell per option
symbol. Any contract traded twice lost every round trip but its last. Since
winners are what get re-entered, the losses were mostly winners.

Replaced with **FIFO lot matching** over all fills.

The second bug, found only by writing the test: P&L used the **buy's**
quantity. A 2-lot buy closed by a 1-lot sell booked **$400 instead of $200**.
Partial closes have been booking double.

A third, found by the existing suite catching my own fix: I sorted by
`filled_at`, and Python's stable sort keeps input order on ties. Alpaca returns
newest-first, so same-second buy/sell pairs came out reversed and journaled
nothing. Fixed by breaking ties on input index descending.

`test_journal.py` (17 checks) covers all of it. **9 of the 17 fail against the
old implementation**, which I verified rather than assumed.

## 2. Notifications will not replay

The recovered round trips were notified when they happened, but a recovered
row's pnl can round differently and miss the dedup key. A one-time silent seed
guarded by `state['journal_rebuild_v'] == 2` stops months of settled trades
arriving as phone messages on the first run. Tested both ways: nothing on the
first run, a genuinely new trade still notifies on the next.

## 3. Config changes, all from the intraday live-parity backtest

| setting | was | now | evidence |
|---|---|---|---|
| `SPXBOT_MAX_PREM` | 600 | **450** | best cap at BOTH 21 and 35 DTE out of sample after 1% cost; raising it is monotonically destructive across 15 cells |
| `SPXBOT_DTE_MIN` | 3 | **14** | weeklies lose at zero assumed cost |
| `SPXBOT_DTE_MAX` | 12 | **35** | |
| `SPXBOT_DTE_PREFER` | (new) | **21** | without it `find_contracts` ranks on strike alone and would mix 14- and 35-day contracts arbitrarily |
| `SPXBOT_UNDERLYING_COOLDOWN` | (new) | **1** | one entry per underlying per session |

Combined, out of sample after 1% cost: **+$1.81/trade against the live
configuration's -$8.12/trade.**

## 4. The cooldown, finally with evidence

I raised this four times on the strength of one bad NVDA session and never
tested it. Now measured on the rig:

| | live 7DTE/$600 | proposed 21DTE/$450 |
|---|---|---|
| re-entry allowed | -$770 | +$2,450 |
| **cooldown** | **+$1,162** | **+$2,698** |
| out of sample @1% | -$4,245 -> **-$2,684** | +$106 -> **+$146** |

Better on every measure at both configurations, and most where the strategy is
worst, which is what a rule that stops compounding a bad entry should do.
Cross-sleeve by design: two sleeves firing on one name the same morning are
one bet.

## 5. trade.yml ran no tests before placing orders

`watch.yml` self-tested; **the workflow that actually submits entries did not.**
Added a pre-flight step running `test_config`, `test_journal`, `test_screens`,
`test_strategies`.

## 6. The root workflow copies were stale and inert

`trade.yml` and `watch.yml` at the repo root are **not read by GitHub**, which
only looks in `.github/workflows/`. They still said `MAX_OPEN=2` while live has
run 3 for weeks. I edited them first and it would have changed nothing.

They are now synced, and `test_config.py` fails if they ever drift again. The
cleaner fix is to delete them; I have not, because that needs a deletion rather
than an upload. **Recommend deleting both root copies when convenient.**

## 7. backtest.py carries a warning it cannot be missed

Its singles sim holds for a median of 3 days; the bot is flat at 15:45. It
produced the 1.91% breakeven quoted as fact for weeks. The module docstring and
`sim_singles` now say so in terms. The spreads sim is unaffected.

---

## What I did NOT change, and why

* **Exits (+45%/-30%).** 49 configurations, all negative, live ranked 4th.
* **Moneyness.** ATM was worse at both expiries and the ordering is not
  monotone. My own bar calls that noise.
* **`MAX_OPEN`.** No evidence either way: the rig allows one position per
  symbol with no global cap, so it is more permissive than live's 3 per sleeve.
  A disclosed deviation, not a recommendation.
* **The entry signal.** Weak (p = 0.12 on direction) but 126 alternatives were
  worse.

## Honest limits on all of the above

The out-of-sample evidence is about **196 trades**. Every deviation in the rig
is optimistic: stops fill at the level with no slippage, bid equals ask so zero
spread is charged, one strike candidate instead of three, and no global
concurrent-position cap.

This is a **paper account**, so shipping this IS the study rather than a bet.
`PAPER_STUDY_PREREG.md` states what it should produce before it runs. What I
would still not do is fund real money on it.
