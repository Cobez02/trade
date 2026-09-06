# Change set, 2026-09-02

Suite green: **16 files, ~1,500 assertions, 0 failures.** Secret sweep clean.
Everything below defaults to CURRENT behaviour except the two bug fixes.

## Files

| file | status |
|---|---|
| `main.py` | race guard + incomplete-history note |
| `engine.py` | `closed_orders` now paginates |
| `exitrules.py` | overnight exemption, **default OFF** |
| `test_journal.py` | +16 checks (race guard, pagination) |
| `test_exitrules.py` | +8 checks, and a stale-path fix |
| `test_learn.py` | stale-path fix |

---

## 1. The duplicate-entry race, fixed

9/1: NVDA entered twice nine seconds apart, two order ids, identical
fingerprint, same sleeve. **$840 into one underlying against a $450 cap whose
ceiling including every multiplier is $703.** The watcher's 300-second entry
scan and the hourly run each read `state` before the other's fill landed.

`already_positioned()` reads run-start state. `pending` only knows this
process's orders. `traded_today()` keys on SETTLED rows and nothing settles in
nine seconds. None of them can see a concurrent process.

**Fix:** `broker_clear_to_open()` asks the BROKER, in the last instant before
submitting, whether a position or working order already exists in that
contract. **It fails closed** - a query error blocks the entry. Skipping one
signal costs a signal; doubling a position silently costs the cap its meaning.

I own this one. I validated the cooldown on a single-process backtest rig,
which cannot exhibit a race by construction.

## 2. The learner was forgetting, fixed

The settled count went **88 to 85** mid-session on 9/1. Measured:
`closed_orders(limit=500)` returned exactly 500 while the account held **520**.
Alpaca caps a request at 500, so the "limit" was a single-page ceiling.

**16 filled orders were already invisible, the OLDEST first** - which is to say
weeks 1 and 2, the profitable ones. It would have got worse with every trade
and nothing would have said so.

**Fix:** paginate, dedupe by order id, `limit` is now a total across pages
(default 2000). Verified live: **520 orders, 191 filled, oldest back to
2026-07-24**, the actual start date. And a failed page now marks the history
incomplete and the run notes say so, instead of looking like the end of
history.

## 3. Overnight holding: the switch exists, defaults off, and I do not recommend it

You asked for the sleeve to hold overnight when it expects to profit. I
measured it on the live-parity rig rather than just wiring it up: **486 trades,
21-35 DTE, $450 cap, each priced both ways.**

| arm | fit half | test half |
|---|---|---|
| flat at 15:45 (current) | **+$5.53/trade** | **+$4.09/trade** |
| overnight to next 15:45 | +$1.52 | +$1.56 |
| overnight to next open | +$0.89 | +$2.87 |

Paired: **-$3.56/trade, t = -0.89**, same direction in both halves.

Then the gates, judged only on the trades each selects:

| gate | fit delta | test delta |
|---|---|---|
| **winner at 15:45** | **-$17.27** | **-$15.46** |
| **loser at 15:45** | **+$8.12** | **+$10.93** |
| DTE >= 25 | -$38.00 | -$41.50 (n=2, meaningless) |
| premium < $300 | -$4.52 | -$0.95 |
| premium >= $300 | -$3.64 | -$3.71 |

**"Let winners run" is the worst rule available.** The only gate with a
consistent sign is holding LOSERS, and it merely turns -$35.74 into -$27.62: a
smaller loss on trades you would rather not have had. Not significant
(t about 0.7), and it means carrying gap risk on positions already going
against you.

So `SPXBOT_OVERNIGHT=1` exists and implements the gate the data supports rather
than the one intuition suggests: carry only a LOSER, only above -50%, only with
more than 3 DTE, only with a usable quote. Everything else still flattens.
**Default off. Turning it on is choosing a measured expected loss.**

## 4. Two test files were grading a stale copy

`test_exitrules.py` and `test_learn.py` began with
`sys.path.insert(0, '/home/claude/spxbot')` and read `main.py` from there.

In CI that path does not exist, Python skips it, and the repo is imported - so
**this was never a production problem.** But on any machine where the directory
exists the tests graded a snapshot. Mine did: three days stale, predating the
whole 8/31 change set. My "suite green" claims covered the wrong files for
those two.

Both now resolve relative to the test file. `test_learn.py` reports **282
assertions** that were previously being counted as zero, which is why the suite
total moved from ~1,194 to ~1,500.

---

## What I did NOT change

`MAX_OPEN`, the cap, DTE, the cooldown, the exits, the entry signal. The 8/31
study is still running and this change set does not touch its parameters. The
race fix removes duplicate entries, which will slightly reduce trade count -
worth remembering when reading the trade-count prediction.
