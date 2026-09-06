# spxbot (options) — retired 2026-09-06

This folder is the complete final state of the Claude-built S&P 500 options
paper-trading bot (July 24 – Sept 5, 2026): 100 settled trades, +$1,305 on the
$10k logical bankroll vs +$476 for SPY, driven by 3 trades; statistically
indistinguishable from zero edge (bootstrap 95% CI on $/trade: −$12 to +$43);
−$230 over the last 75 trades.

Its two workflows (`.github/workflows/trade.yml`, `watch.yml`) were moved here
ON PURPOSE so they no longer run: the replacement bot forward-tests on SPY
shares in the same Alpaca paper account, and `reconcile_assignments()` in this
bot sells any stock position at market on every hourly run.

Nothing here is wired to anything. It is kept for its journal (`state.json`),
its reports, and its tests. See the repository root for the futures rebuild.
