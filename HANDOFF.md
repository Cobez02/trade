# HANDOFF — read this first (written 2026-10-12 for a Claude Code session on Connor's Mac)

## What this is
A Claude-built futures paper-trading bot aimed at passing a prop-firm evaluation. Two months of work
and ~35 backtests live in this repo; the conclusions are in `research/` and must not be re-derived
from scratch. The short version:
- Day-only intraday index strategies: one small Nasdaq effect (Sharpe ~0.5), not enough. Closed.
- Overnight drift on index futures, bought at the **17:00 CT Globex reopen** with a **1% stop**, sold at
  **08:30 CT**: Sharpe ~1.1 net on real NQ/ES futures (2024-26), worst night -$382 (MES) / -$638 (MNQ).
  **1 MES ~ 66% P(pass) on a Topstep $50K Combine** vs ~20% for a coin flip. This is the plan.
  Pre-registration, numbers, kill rules: `research/PREREG-2026-10-08-globex-overnight.md`.
- Holding from the 14:59 cash close is NOT allowed by any firm (crosses the 16:00 CT session boundary).
  The 17:00 -> 08:30 window is inside one Globex session and is allowed (Topstep: flat by 15:10 CT).

## What runs where (as of 2026-10-12)
- **Mac, `~/spxf/trade`** (this clone): launchd agent `com.spxf.lab` ticks every 30 min; on weekdays
  04:30-14:50 CT it runs `live/runner.py` (intraday QQQ paper sleeve on Alpaca + overnight QQQM paper
  sleeve). Keys in `~/.spxf.env` (never commit, never paste into chat). Logs: `logs/mac.log`, `logs/<date>.log`.
  First install never started a session (launchd killed the backgrounded runner) — fixed in f936266;
  **verify a session actually runs** (`tail -f logs/mac.log`, expect `session <date>` within a minute).
- **GitHub Actions `lab` workflow**: DISABLED on purpose (it ran 4-8 h late and must not trade alongside the Mac).
- **Alpaca paper account**: may still hold a QQQ short (-40) that the GitHub chain never flattened on 10/12;
  close it with `AlpacaProxyBroker().flatten("QQQ")` (cancels the resting stop first, confirms flat).

## Open items, in order
1. Confirm the Mac session runs end to end for 3 days (open, decisions every 30 min, 08:31 QQQM exit,
   14:52 flatten with confirmation, 14:57 QQQM buy, record committed). Logs + `days.jsonl` are the evidence.
2. Topstep $50K Combine + ProjectX API (Connor's card). Then put PROJECTX_USER / PROJECTX_API_KEY /
   PROJECTX_ACCOUNT (the PRACTICE account first) in `~/.spxf.env`.
3. Make `live/overnight_futures.py` + `broker/projectx.py` work against the practice account with 1 MES:
   the adapter was written from public docs and has never touched a real account. Verify: fill, the 1% stop
   RESTING on Globex overnight (query it back), 08:30 exit confirmed flat, record = broker. 10 clean nights.
   Needs a launchd schedule: enter at 17:02 CT Sun-Thu, exit at 08:30 CT Mon-Fri; the Mac must be awake
   (consider `pmset repeat wake`). Topstep bans VPS/VPN: this sleeve runs from the Mac only.
4. Then switch PROJECTX_ACCOUNT to the Combine account. 1 MES, this rule only. Kill rule: 30 nights with
   mean < -$10, or any night < -$600 (stop not working) -> stop.

## Invariants (learned the hard way; each one cost a real bug)
- Never trust local state for positions: ask the broker (`_raw_qty`, `resting_stop_qty`), then act.
- Every time rule is in America/Chicago via `data/sessions.py`; never UTC.
- A flatten is not done until the broker confirms flat (cancel -> wait -> close -> verify -> retry).
- Alpaca reports short qty as NEGATIVE; sign comes from `side` only (`signed_qty`).
- Records are append-only and union-merged (`scripts/commit_records.sh`); never rebase them.
- Pre-register before running a test; report failures as loudly as passes (`research/PREREG-*.md`).
- Secrets live in `~/.spxf.env` (Mac) or repo Actions secrets; never in files, commits, or chat.

## Tests
`./venv/bin/python -m tests.test_core` -> 77 passed. Run before every commit.

## Research program (added 2026-10-14; full state handoff in research/program/HANDOFF-PROGRAM.md — read that first for a research session)
`research/program/PROGRAM.md` is the 500-hour plan; `QUEUE.md` is the next work; `SCOREBOARD.md` is the ledger of every
test ever run. Phase 0 code exists: `data.py` (budgeted, holdout-sealed Databento loader), `stats.py` (deflated Sharpe,
PBO, purged folds), `run_test.py` (harness; anchor test 3A-1 registered). DATABENTO_API_KEY is in ~/.spxf.env.
CREDIT RULE: stop and report when the loader refuses a request for budget; never raise the budget yourself.

Kickoff prompt for the first session:
  Read HANDOFF.md, research/program/PROGRAM.md and QUEUE.md. Load ~/.spxf.env. Run `python -m research.program.data`
  and report the Phase 0 cost estimate. If it is under $125, write research/program/prereg/3A-1.md from the template
  (predictions before data), then `python -m research.program.run_test 3A-1`. Commit the prereg, the ledger and the
  scoreboard line. If the loader refuses for budget at any point, stop, commit the ledger, and report.
