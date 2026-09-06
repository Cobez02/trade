# Pre-registration: the 2026-08-31 configuration change

Written before the change went live. This is a paper account, so shipping the
change IS the experiment. The point of writing it down first is that in six
weeks I will otherwise be able to explain whatever happened.

## What changed

21 DTE preferred (window 14-35), $450 premium cap, one entry per underlying per
session. Exits, entry signal, watchlist and sleeve structure all unchanged.

## What the backtest predicts, stated as numbers

Over 2024-03 to 2026-08 on the intraday live-parity rig, out of sample and
after 1% assumed round-trip cost:

* Old configuration: **-$8.12 per trade**
* New configuration: **+$1.81 per trade**

So the prediction is a swing of roughly **+$10 per trade**, and an absolute
result **near zero, slightly positive**. I am NOT predicting the sleeve becomes
clearly profitable.

Secondary predictions:

* **Trade count roughly halves.** The $450 cap plus 21 DTE rejects more
  signals, and the cooldown removes same-day re-entries. Expect something like
  40-60% of the old rate. If volume does not fall, the config did not take.
* **Median premium rises** from about $221 toward $370. If it does not, check
  `SPXBOT_DTE_PREFER` is actually being read.
* **Realised round-trip cost is the whole question.** Above roughly 1% none of
  this holds. The OPRA fill audit measures it.

## What would falsify it

* Per-trade P&L stays at or below the old configuration over 100+ trades.
* Trade count does not fall.
* Measured round-trip cost comes in above 1%.

Any of those and the change is reverted. The env vars are the whole change on
the config side, so reverting is editing five lines.

## What I will not accept as confirmation

A profitable fortnight. Week 5 of this project was -$183 and week 4 was
positive on the same rules; two weeks of paper P&L on ~20 trades cannot
distinguish +$1.81 per trade from -$8.12. **The minimum honest read is 100
settled trades**, and even then the standard error will be wide.

I will also not accept "the learner adapted" as an explanation of any result,
in either direction. The journal fix changes the learner's training data on the
same day as the config change, which means the two are confounded in whatever
comes next. That is a real weakness of shipping them together, and I am doing
it anyway because the journal fix is a correctness bug that should not wait for
a study. It is recorded here so the confound cannot be quietly forgotten.

## Sample size and honesty about power

At roughly 15 trades a week and an expected halving, 100 trades is about 13
weeks. The out-of-sample backtest evidence is 196 trades. Neither is a large
number for an instrument whose P&L is this fat-tailed, and the honest position
is that this change is **directionally supported and not established**.
