# SPX-Beater / futures

An autonomous trading research program: a risk-managed futures trading system,
a minute-level backtester, a prop-firm rule simulator, and a pre-registered
research pipeline, built and operated end to end by Claude Code sessions with
a human setting the rules and the budget.

The target market is CME micro index futures (MES / MNQ), the only market a
funded prop account will let a bot trade. The system is paper-only today: it
forward-tests on an Alpaca paper account (SPY/QQQ shares standing in for the
futures) while the research program decides whether any strategy has earned
real capital.

## Why it looks the way it does

This is the third iteration of the project, and each rebuild kept the rules
that earlier bugs paid for:

- **Trust nothing in memory.** Positions are rebuilt from the broker on every
  decision; a flatten is not done until the broker confirms flat.
- **Broker-side resting stops.** The stop order lives at the exchange, so it
  survives the process dying.
- **Every time rule in the exchange's own zone.** All session logic runs in
  America/Chicago via `data/sessions.py`; UTC never appears, so DST cannot
  silently shift an entry time.
- **Pre-register before testing.** Every experiment gets a written
  `research/PREREG-*.md` or `research/program/prereg/*.md` with predictions
  and kill rules recorded before the data is touched. Failures are reported
  as loudly as passes.
- **Append-only records.** Session results are committed to `days.jsonl` and
  union-merged, never rebased, so the live track record cannot be rewritten.

## Repo map

```
config.py                 every knob, env-overridable (SPXF_*)
data/sessions.py          DST-safe session clock, RTH filter, bar resampling
data/bars.py              Databento (MES/MNQ) · Alpaca (SPY/QQQ proxy) · yfinance (smoke)
strategy/                 noise_area (intraday momentum), orb, lasthalf
risk/rules.py             prop-firm rulebooks as data + path simulator + Monte Carlo
risk/engine.py            sizing, kill-switch, profit cap, trailing-drawdown gate
backtest/                 minute-level backtester with costs, stops, flatten;
                          overnight and pair-spread variants
broker/base.py            Broker interface + SimBroker
broker/alpaca_proxy.py    Alpaca PAPER, SPY/QQQ shares as the MES/MNQ stand-in
broker/tradovate.py       MyFundedFutures / Tradeify           [UNTESTED skeleton]
broker/projectx.py        TopstepX                             [UNTESTED skeleton]
live/runner.py            one-session runner (live or dry-run)
live/overnight*.py        the Globex overnight sleeve (ETF paper + futures)
scripts/                  backtest runners, data fetcher, record committer
research/                 pre-registrations and the reports they produced
research/program/         the 500-hour research program (see below)
tests/test_core.py        83 checks, no network
```

## Verify it works (5 minutes, no keys)

```bash
pip install -r requirements.txt
python -m tests.test_core                                   # expect: 83 passed, 0 failed
python -m scripts.run_backtest --source yfinance --symbol SPY --instrument SPY   # smoke test
python -m live.runner --broker sim --dry-run --day 2026-07-28 --source yfinance --symbol SPY --instrument SPY
```

The yfinance smoke test uses 60 days of 5-minute bars. It proves the plumbing,
not the strategy; do not draw conclusions from it.

## The research program

`research/program/PROGRAM.md` is a 500-hour plan for raising the portfolio's
out-of-sample Sharpe by disciplined search, designed so an agent can run it
without fooling itself:

- **A hard hypothesis budget** (120 tests total) tracked on a scoreboard, so
  the multiple-testing correction is always computable.
- **A sealed holdout**: data from 2024 on is locked by the loader itself;
  a candidate earns exactly one holdout evaluation, at the end.
- **Mechanism-first**: a hypothesis must state who pays and why, with a cited
  paper or structural reason. No pattern mining.
- **Statistics that gate everything**: purged walk-forward cross-validation,
  deflated Sharpe ratios, and probability-of-backtest-overfitting, implemented
  once in `research/program/stats.py` and applied to every test.
- **A budgeted data loader** (`research/program/data.py`) that estimates every
  Databento request before buying it, keeps a spend ledger, and refuses to
  pass the budget.
- **Red-team review** of every candidate for look-ahead and leakage before it
  touches the holdout.

The current pre-registered plan is the Globex overnight sleeve
(`research/PREREG-2026-10-08-globex-overnight.md`): long the micro future at
the 17:00 CT reopen with a 1% resting stop, flat by the 08:30 CT cash open,
inside a single Globex session so every prop firm's rules allow it.

## Current status

- The retired options bot (July to Sept 2026) lives in `archive/spxbot-options/`
  with its own README and post-mortems; its workflows were moved there
  deliberately so they no longer run.
- The intraday sleeves were researched, pre-registered, and closed: the
  measured edge (Sharpe ~0.5 on one Nasdaq effect) was not enough after costs.
  The research that closed them is in `research/`.
- The paper lab runs daily from a scheduled local runner and commits its
  records here; the GitHub Actions lab workflow is retained but disabled so
  two runners never trade the same account.
- The live futures adapters are skeletons written from public API docs and are
  labeled as such; they run against a practice account before any evaluation.

## What is deliberately NOT here

- No learner. 100 trades is not enough to learn from, and a prop account has
  no room for a size that grows on noise. Sizing is fixed-dollar-risk.
- No news sleeve, no sentiment. Every direction signal is a documented effect
  with a mechanism.
- No leverage beyond the risk engine's contract cap. The honest expectation
  for a single-micro bot is a low double-digit annual return on a $50K
  notional with a Sharpe near 1, if the edge is real.
- No secrets. Keys live in the environment or in repo Actions secrets; see
  `.env.example`.

This repository is a research project, not investment advice. Nothing here
has traded real money.
