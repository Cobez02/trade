# SPX-Beater / futures

> The retired options bot (July–Sept 2026) lives in `archive/spxbot-options/`; its
> workflows were moved there deliberately so they no longer run.

The options bot rebuilt for the only market a funded account will let a bot
trade: CME micro index futures (MES / MNQ), day session only, flat by the
close, sized by a prop-firm rule engine.

What carried over from `spxbot`: the "rebuild from the broker, trust nothing
in memory" principle, broker-side resting stops, one pre-registered
strategy with written rules, a test suite that guards the config, and the
habit of reporting failures as loudly as passes.

What is new: **the instrument** (futures, not options — no theta, no 4%
spreads, no assignment), **the clock** (every time rule in the exchange's own
zone; UTC never appears), **the risk engine** (daily kill, daily profit cap,
trailing-drawdown buffer, floor-not-ceiling sizing), **the prop-rule
simulator** (P(pass) from your own daily P&L), and **the strategy**
(intraday momentum with a time-of-day noise area — the one family with a
peer-reviewed mechanism *and* an independent ES/NQ replication).

```
config.py                 every knob, env-overridable (SPXF_*)
data/sessions.py          DST-safe session clock, RTH filter, bar resampling
data/bars.py              Databento (MES/MNQ) · Alpaca (SPY/QQQ proxy) · yfinance (smoke)
strategy/noise_area.py    primary strategy — rules written out at the top of the file
strategy/orb.py           secondary — 5-minute opening range breakout
risk/rules.py             prop-firm rulebooks as data + path simulator + Monte Carlo
risk/engine.py            sizing, kill-switch, profit cap, buffer gate
backtest/simulate.py      minute-level backtester with costs, stops, flatten
broker/base.py            Broker interface + SimBroker
broker/alpaca_proxy.py    Alpaca PAPER, SPY/QQQ shares as the MES/MNQ stand-in
broker/tradovate.py       MyFundedFutures / Tradeify           [UNTESTED skeleton]
broker/projectx.py        TopstepX                             [UNTESTED skeleton]
live/runner.py            one-session runner (live or dry-run)
scripts/run_backtest.py   backtest -> metrics -> P(pass) per firm -> reports/
scripts/fetch_data.py     cache the history
tests/test_core.py        47 checks, no network
.github/workflows/paper_session.yml   Alpaca proxy forward-test (DST-safe crons)
```

## Verify it works (5 minutes, no keys)

```bash
pip install -r requirements.txt
python -m tests.test_core                                   # expect: 47 passed, 0 failed
python -m scripts.run_backtest --source yfinance --symbol SPY --instrument SPY   # smoke test
python -m live.runner --broker sim --dry-run --day 2026-07-28 --source yfinance --symbol SPY --instrument SPY
```

The yfinance smoke test uses 60 days of 5-minute bars. It proves the plumbing,
not the strategy. **Do not draw conclusions from it** — in the quiet summer of
2026 it loses money, which is exactly what the literature says a
cost-sensitive momentum edge does in a low-volatility window.

## The steps, in order

Do them in this order. Each one is a gate; if the gate fails, stop and do
not spend money on the next step.

### 1. Data (day 1, ~$0–10)
Sign up at databento.com, create an API key, accept the CME data agreement.
New accounts get $125 of credit; a 7-year 1-minute history for one product
costs a few dollars of it.
```bash
export DATABENTO_API_KEY=...
python -m scripts.fetch_data databento MES.v.0 2019-05-06 2026-09-05
python -m scripts.fetch_data databento MNQ.v.0 2019-05-06 2026-09-05
```
(MES/MNQ launched May 2019. For a longer sample use ES.v.0 / NQ.v.0 and
`--instrument MES`; the strategy is in price terms, only the $/point changes.)

### 2. The backtest that decides everything (day 1–2)
```bash
python -m scripts.run_backtest --source databento --symbol MES.v.0 --instrument MES \
    --start 2019-05-06 --end 2026-09-05 --holdout-from 2024-01-01
python -m scripts.run_backtest --source databento --symbol MNQ.v.0 --instrument MNQ \
    --start 2019-05-06 --end 2026-09-05 --holdout-from 2024-01-01
```
Read `reports/*_report.md`. The gates, written down before you look:
- holdout (2024→) daily t-stat **> 2** and profit factor **> 1.3** after costs
- win rate and payoff in the neighbourhood of the ES/NQ replication (≈38% / ≈2.2)
- days with an intraday low ≤ −$700: **< 1 per 100 sessions**; ≤ −$1,000: **zero**
- P(pass) on `mffu_core_50k` at 1× **> 40%**, and clearly above the zero-edge control
- costs < 35% of gross

Do NOT tune parameters to make the holdout pass. If you must change
`--mult`, `--trail`, `--lookback` or `--stop-mult`, re-split the sample and
treat the new holdout as untouched. If the gates fail for both MES and MNQ,
the plan is falsified at zero cost — that is the point of the order.

### 3. Forward-test on Alpaca paper (4 weeks, $0)
Alpaca has no futures, so the proxy is SPY (for MES) / QQQ (for MNQ) shares,
20 shares per "contract". Same code, same bands, same risk engine.
```bash
# once: add ALPACA_API_KEY / ALPACA_SECRET_KEY as repo secrets, enable Actions
# the workflow runs two half-sessions a day and commits days.jsonl
```
Gate: 20 sessions with the same win-rate/payoff shape as the backtest and no
day below −$700 (scaled). Compare `days.jsonl` to what the backtester says
those same days should have done — that difference is your live-vs-backtest
slippage, and it is the number the options bot never measured.

### 4. Pick the firm to fit the bot, not the marketing (day 30)
- **MyFundedFutures Core $50K** — the only firm whose written policy allows
  bots on the evaluation *and* the funded account with no VPS restriction.
  Execution = Tradovate API (`broker/tradovate.py`). Check the API add-on fee
  and whether MFFU has to enable API access on your account.
- **Tradeify Growth $50K** — bots allowed at every stage; log in once
  without a VPN/VPS, then run from wherever. Tradovate too.
- **Topstep $50K** — cheapest API ($14.50/mo), bots fine in the Combine and
  XFA, **not** on Live Funded, VPN banned, must run from your own device.
  Execution = `broker/projectx.py`.
- Not for a bot: Apex, Take Profit Trader, Elite Trader Funding, Alpha
  Futures (bots banned on funded or at all stages, per their own terms).

Re-read the firm's account-parameters page the day you buy; the presets in
`risk/rules.py` are dated Sept 2026 and these change monthly. Then set
`--max-dd`, `--kill`, `--cap` from that page: kill ≈ 35% of the drawdown,
cap ≈ 40% of the target.

### 5. Wire the broker (day 30–35, qty = 1, human watching)
The two live adapters were written from public API docs without an account.
Run them on the firm's demo/eval with `SPXF_MAX_CONTRACTS=1`, watch every
fill for a week, and fix whatever field names the vendor has since renamed.
Then let the runner trade the evaluation unattended:
```bash
# macOS launchd / cron on your own machine (Topstep) or a VPS (MFFU/Tradeify)
25 8 * * 1-5  cd ~/spxbeater-futures && set -a && . ./.env && set +a && \
   python -m live.runner --broker projectx --symbol MESZ6 --instrument MES --source databento >> logs/$(date +\%F).log 2>&1
```
The runner runs one session and exits; it flattens at 14:52 CT regardless
of DST; the resting stop survives the process dying.

### 6. Buy the evaluation only when step 2 said > 40% and step 3 confirmed it
Budget three attempts (~$150–$500 total). Expect, honestly, a 40–70%
per-attempt pass probability with a genuine edge and a base rate of ~14%
without one. Log every session to `days.jsonl`; after 20 funded sessions
re-run the prop Monte Carlo on the *live* P&L and compare with step 2.

## Do you still need Alpaca?
- **Keep the account** (free): it is the forward-test lab in step 3 and the
  only free place to run this code against a live tape.
- **Keep Algo Trader Plus ($99/mo) only while** you still want the options
  bot's fill-reconciliation study or SIP minute history for SPY/QQQ; once the
  Databento history is cached and the options bot is retired, downgrade — the
  proxy lab works on IEX (`SPXF_ALPACA_FEED=iex`).
- **Alpaca can never be the live path**: it has no futures, and no prop firm
  clears through it. The funded account runs on Tradovate/Rithmic (MFFU,
  Tradeify) or ProjectX (Topstep).

## What is deliberately NOT here
- No learner. 100 trades is not enough to learn from and a prop account has
  no room for a size that grows on noise. Sizing is fixed-dollar-risk.
- No news sleeve, no sentiment, no options. The direction signal is one
  documented intraday effect with a mechanism.
- No leverage beyond the risk engine's contract cap. The published 1,484%
  and 1,985% returns need leverage and zero-slippage fills a prop rulebook
  forbids; the honest expectation for a single-micro bot is a low double-digit
  annual return on a $50K notional with a Sharpe near 1, if the edge is real.
