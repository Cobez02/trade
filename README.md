# SPX-Beater

An autonomous options bot that trades an Alpaca paper account on its own
schedule and measures itself against buy-and-hold SPY. It runs on GitHub
Actions, so there is no server to keep alive and nothing to leave running on
your laptop.

**Status: paper only.** No live-money flag is set. The bot has placed 85 round
trips over five weeks. Read the "What we actually know" section before
concluding anything from that.

## What it trades

Single-leg long calls and puts. Nothing else. No spreads, no short premium, no
overnight holds.

* 3 to 12 days to expiry, targeting about 1.5% out of the money
* Max $600 of premium per position, max 3 concurrent per sleeve
* Take profit at +45%, stop loss at -30%, time stop at 1 DTE
* Flat before every close, every day, without exception

Four "sleeves" generate candidates independently. A learning loop sizes each
one up or down between 0.25x and 1.25x based on its settled results. It never
blocks a sleeve outright, because a setup that is never taken can never earn
the evidence that would clear it.

| Sleeve | Signal source | Status |
|---|---|---|
| `news` | Alpaca market-news headline sentiment | active |
| `tech` | RSI, MACD, trend | active |
| `wsb` | r/wallstreetbets sentiment | retired as an entry source, survives as a crowd veto on hyped names |
| `flow` | Options open-interest skew | retired, OI is unsigned and carries no signal without open/close data |

## The screens

Every candidate has to clear all of these before an order is placed. Most
candidates do not.

| Screen | Rule | Why |
|---|---|---|
| Liquidity | open interest >= 250 | No fallback. When the chain is thin the filter fires, and a fallback would guarantee a fill in the least liquid contract available. That is adverse selection by construction. |
| Spread | bid/ask <= 4% of mid | At a 15% spread the modelled round trip costs 10.9% of premium against a best-documented edge under 1% per trade |
| Vol edge | implied vol must not exceed the internal forecast | Paying above model for the same exposure is a losing trade before it opens |
| Lottery | MAX over the last month <= 15% | Top-MAX-decile stocks underperform (Bali, Cakici and Whitelaw) |
| Expiration | no entries on monthly opex Friday or the Monday after | Garcia-Ares and Muravyev, -0.43% delta-hedged across the pair. See the open questions below, this one is contested by our own data |
| Crowd veto | skip names with heavy WSB attention | Attention inflates premium |

## Requirements

**Alpaca Algo Trader Plus.** The bot defaults to the OPRA options feed and the
SIP consolidated equity tape. On the free tier it would fall back to indicative
option quotes (synthetic derivatives of OPRA, not real quotes) and IEX-only
equity bars, which is a single venue carrying low single-digit volume share.
The technical indicators read those equity bars, so the free tier degrades the
signals themselves, not just the pricing.

Set `SPXBOT_OPT_FEED=indicative` and `SPXBOT_STOCK_FEED=iex` to run without the
subscription. Expect worse everything.

**A public repository.** Actions minutes are free on public repos and metered
on private ones. The continuous watcher runs about 6.5 hours a weekday, roughly
8,000 minutes a month, far past the 2,000-minute free allowance on a private
repo.

## Setup

1. Create a **public** repository and upload everything, keeping
   `.github/workflows/` intact. If drag-and-drop flattens the folders, create
   the workflow files by hand with **Add file, Create new file** and paste the
   contents in.
2. Add your Alpaca paper keys under **Settings, Secrets and variables,
   Actions**: `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`. GitHub encrypts these
   and they never appear in logs.
3. Open the **Actions** tab and enable workflows if prompted.
4. Run **SPX-Beater options bot** manually once. Check the run Summary for the
   digest. Outside market hours it records signals and places no orders, which
   is correct.
5. Optional: **Settings, Pages, Build from branch, main / root** puts the
   dashboard at `https://<user>.github.io/<repo>/dashboard.html`.

Optional alerts: add `SPXBOT_ALERT_WEBHOOK` (Discord) to get a receipt per
settled round trip.

## The two workflows

`trade.yml` decides what to buy. It runs hourly at :07 past the hour from 14:07
to 20:07 UTC, plus three close-the-book attempts at 19:45, 19:50 and 19:55.
Three attempts rather than one because a single delayed job would strand the
whole book overnight, and this bot does not hold overnight.

`watch.yml` decides what to sell. It watches every open position roughly four
times a second rather than once an hour. That gap is not academic: a position
that read -29% at one hourly check was liquidated at -51% at the next, and the
entire loss happened in the 41 minutes when nothing was watching. Two jobs
hand over at 17:07 UTC, because a GitHub job is capped at 6 hours and the US
session is 6.5.

Both use `:07` rather than `:00`. The top of the hour is GitHub's most
congested cron slot, and the `:00` schedule spent a week landing 40 to 45
minutes late or not at all.

## Statelessness

Every run rebuilds open positions and the full learning journal from Alpaca's
own order history. Each trade's signal features are encoded in its
`client_order_id`, so a missed run costs nothing. `state.json` is committed back
each run only to keep an equity-curve history.

## What we actually know

This section exists because the numbers above are easy to over-read.

Equity is up about 13% in five weeks. Annualised that would be roughly 271%,
which is well beyond any sustained track record in existence. That should be
read as a warning rather than a result.

The distribution matters more than the total:

| Week | Trades | P/L |
|---|---|---|
| 1 | 16 | +$1,019 |
| 2 | 9 | +$541 |
| 3 | 21 | -$227 |
| 4 | 23 | +$298 |
| 5 | 16 | -$481 |

Weeks 1 and 2 made $1,560. Weeks 3 through 5 lost $410. Across all 85 trades
the mean is +$13.53 with a standard deviation of $148.66, which puts the 95%
confidence interval at [-$18.07, +$45.13]. It contains zero. Separating that
mean from zero at 95% confidence would take roughly 463 trades.

There is also a measurement problem sitting underneath the sample-size problem.
Alpaca's paper engine models no market impact, no latency slippage and no queue
position, and its own documentation notes that an order can fill for far more
size than the market actually had. A 4% round trip on a $500 position costs
about $20 against a measured edge of $13.53. The entire apparent edge is
smaller than one crossing of the bid/ask that has never been observed.

So: the bot demonstrably places trades, manages exits and runs unattended. It
has not demonstrated an edge.

## Backtesting

`backtest.py` replays the same screens and exit rules over history.
`btdata.py` is the data layer, backed by Alpaca historical bars.

Two limits worth knowing before reading any backtest output:

* Option history starts February 2024. Equity history reaches 2016, so
  underlying-only studies have a much longer window than option studies.
* There are no historical option quotes at any endpoint. Mid is approximated
  by VWAP, which means bid equals ask, which means **the spread screen cannot
  run**. Every backtested P/L is therefore an upper bound. Set
  `SPXBOT_BT_RT_COST` to an assumed round-trip cost and re-run. A conclusion
  that only survives at 0% is an artifact of the missing spread.

## Tuning

Everything below is env-overridable in `trade.yml`. Change the same value in
`watch.yml` or the two entry paths will silently diverge.

| Variable | Default | Notes |
|---|---|---|
| `SPXBOT_MAX_PREM` | 600 | Max $ per position |
| `SPXBOT_MAX_OPEN` | 3 | Concurrent per sleeve. Counts open positions only, so a name that closes and reopens is unconstrained |
| `SPXBOT_MAX_SPREAD` | 0.04 | Read STRATEGY.md before raising |
| `SPXBOT_MIN_OI` | 250 | Liquidity floor |
| `SPXBOT_TP` / `SPXBOT_SL` | 0.45 / -0.30 | Take profit and stop |
| `SPXBOT_DTE_MIN` / `MAX` | 3 / 12 | Expiry window |
| `SPXBOT_OTM` | 0.015 | Strike distance. Never tested against an alternative |
| `SPXBOT_OPT_FEED` | opra | Set to `indicative` on the free tier |
| `SPXBOT_STOCK_FEED` | sip | Set to `iex` on the free tier |

## Tests

Fourteen suites, 1,441 named checks, no network and no orders:

```bash
for t in test_*.py; do python3 $t; done
```

They pin the statistics (`test_stats.py` reproduces published Deflated-Sharpe
worked examples to the digit), the screens, the execution math, the exit rules,
the learner's size-never-block property, the watcher's stop machinery and the
sleeve retirements.

## Open questions

Things that are known to be unresolved rather than quietly assumed.

* **Ledger dedupe bug.** `state.json['closed']` and `['journal']` are keyed by
  option symbol, so re-trading the same contract overwrites the earlier round
  trip. Six or more trips have been lost, nearly all of them winners. Equity is
  unaffected because it is broker-tracked, but the learner trains on the
  corrupted set. Compute P/L from `notified_trades` and cross-check against the
  `equity_history` delta.
* **Same-underlying concentration.** The per-sleeve cap counts concurrent
  positions only. On 2026-08-28 the bot took five NVDA call entries in one
  session for -$445 while every other name that day made +$102 combined.
* **The expiration screen may be wrong.** A pre-registered study returned
  INSUFFICIENT EVIDENCE, but the evidence leaned against the screen: blocked
  days averaged +$109 versus -$4.60 on normal days. Only six independent days
  were available. See OPEX_REPORT.md.
* **Volatility dependence.** Average win fell from $132 to $88 as SPY's average
  daily move halved from 0.70% to 0.31%. Win rate barely moved. The strategy is
  long volatility and the relationship is untested at any useful power.

## Documentation

* `STRATEGY.md` for what it trades and the evidence behind every rule
* `ASSESSMENT.md` for the honest math on the 10x goal
* Both are part of the deliverable, not decoration

## Caveats

* Scheduled GitHub runs are routinely delayed under load. That is fine hourly
  and is why the close-the-book crons fire three times.
* GitHub pauses schedules after 60 days without repo activity. The per-run
  commits keep it awake.
* Paper money. Not investment advice. Paper fills do not reflect real slippage
  or liquidity, which is the single largest open question about everything
  above.
