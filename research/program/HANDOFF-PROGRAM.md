# HANDOFF — the research program, written 2026-10-23 for the next Claude Code session (read this whole file first)

You are resuming a pre-registered, budgeted, holdout-sealed research program on futures data. The previous session (2026-10-14)
ran 14 hypotheses, 48 scoreboard lines, spent the whole Databento credit, and left the program at a decision point that only
Connor can take. This file is everything that session knew. The repo is the memory; nothing lives in chat.

## 0. Start here (do these in order, every session)
1. `cd ~/spxf/trade` (the repo is NOT at ~/; a session started at ~/ finds nothing). `set -a; . ~/.spxf.env; set +a` loads keys
   (DATABENTO_API_KEY, Alpaca keys). Never print, commit or paste a key.
2. Read: this file, `HANDOFF.md` (the trading bot side), `research/program/PROGRAM.md` (the rules, incl. §0a credit rule and
   §0b bp cost rule), `research/program/QUEUE.md` (what is next and what is done), the LAST 10 lines of
   `research/program/SCOREBOARD.md`, and `research/program/REDTEAM.md` (the checklist every test answers).
3. `git fetch; git status -sb`. If behind, `git merge --no-edit origin/main` (NEVER rebase: records are append-only, union-merged).
   The Mac's launchd agent `com.spxf.lab` commits "mac session ..." records on its own; expect local commits you did not make.
4. `./venv/bin/python -m tests.test_core` -> expect "83 passed, 0 failed". Run again before every commit.
5. Pushing: plain `git push` returns 403 (the shell exports a read-only fine-grained GITHUB_TOKEN; git's keychain lookup returns a
   read-only app token). This one-off command works and changes no config:
   `env -u GITHUB_TOKEN git -c credential.helper= -c credential.helper='!gh auth git-credential' push origin main`
   If it stops working, report to Connor; do not edit tokens or git config.
6. Scratch files go in the session scratchpad, never in the repo. Downloads of untrusted files go in their own directory.

## 1. State on 2026-10-23
- Scoreboard: 48 lines (N=48), 14 hypotheses (3A-1..3A-14), 0 passes under the $ cost rule and 0 under the §0b bp rule.
  The Phase 1 prop-compatible catalog (PROGRAM.md §3A) is COMPLETE. Every test has a prereg with a post-run "Outcome" section.
- Databento ledger `research/program/databento_spend.json`: $122.96 of the $125 credit spent, $2.04 left. The loader
  (`data.py`) REFUSES any request that would exceed the budget; every CME symbol costs > $2, so it now refuses everything.
  Do not pass `budget=` to raise it; do not work around it; that is Connor's decision (PROGRAM.md §0a).
- Purchased and cached (GLBX.MDP3 ohlcv-1m, .v.0 continuous, 2010-06-06..2023-12-31): ES NQ RTY YM ZN ZB GC CL, in
  `cache/research/*.parquet` (467 MB, 20 files incl. derived night/day tables). **cache/ is gitignored: this directory is the
  ONLY copy of $122.96 of data.** Do not delete it; if asked to move machines, copy it (a re-download would be refused).
- Holdout (2024-01-01 onward) has never been touched. The loader raises HoldoutViolation on development requests that reach it;
  the one-shot verdict path (`verdict=True`) has never been used. The 2024-2026 data is NOT purchased.
- The queue's last block (QUEUE.md) states the pending decision: (a) Phase 3 on the surviving facts, which needs 2024-26 data
  (new Databento spend, Connor's call), or (b) Phase 2 own-capital sleeves on Alpaca equity data (free with the account).
- The Mac also runs the paper-trading bot (HANDOFF.md). It is unrelated to the program's code; do not touch `live/`, `broker/`,
  `days.jsonl`, `state_*.json` during a research session.

## 2. The rules the program runs on (PROGRAM.md, condensed; the file wins if this summary disagrees)
- One pre-registered test per unit of work. Write `research/program/prereg/<id>.md` from `PREREG_TEMPLATE.md` BEFORE running
  anything, including numeric predictions (trades/yr, bp per night in the held subset, gross $/trade vs the round trip, PF, Sharpe
  range, worst day, what would make you distrust a pass). After the run append a dated "## Outcome" section; never edit the
  predictions. The harness refuses to run an id without a prereg file.
- Mechanism first: who pays and why, with a paper or structural reason. Pattern mining is excluded (§3C).
- Hypothesis budget 120 tests; every scoreboard line counts toward N and N feeds the deflated Sharpe. Never delete a line.
- Sealed holdout: ≤ 2023-12-31 for development. One holdout shot per candidate, in Phase 3, after red-team.
- Gate (§1): DSR p ≥ 0.95 at the running N, PF ≥ 1.15, cost share < 40%, worst day inside the venue floor, ≥ 400 trades or
  ≥ 5 years; correlation < 0.5 with existing sleeves. The harness applies the first three automatically.
- Costs (§0, §0b): fixed per side = commission + 1 tick (MES $2.25, MNQ/M2K/MYM $1.50, ZN $17.625, ZB $33.25, GC/CL $12.50),
  never tuned. Since 2026-10-14 the scoreboard also carries the §0b bp-rule cell: round trip in bp of notional at the Oct-2026
  level, held constant — MES 1.4, MNQ 0.6, M2K 2.1, MYM 1.2, ZN 3.4, ZB 6.4, GC 0.6, CL 2.6 (`nights.COST_BP`). Connor set the
  MES/MNQ figures; they are ~20% conservative vs the live mid-Oct-2026 levels and are used as stated.
- Credit rule (§0a): when the loader refuses, stop, commit the ledger, report what was bought, what it cost, what remains.
- Red-team (REDTEAM.md) before any candidate touches the holdout: look-ahead, survivorship, label leakage, cost tuning, the
  .v.0 roll-inside-the-window artefact, stop-fill modelling.
- Human cadence: Connor reads SCOREBOARD.md and approves QUEUE.md. Reorders are recorded in QUEUE.md with a reason. Do not
  reorder on your own; do not start Phase 3 or a purchase without his explicit approval in the prompt.

## 3. The code (all under research/program/; ~900 lines; read them, they are short)
- `data.py` — `fetch_cme_minutes(symbol, start, end, verdict=False)`: Databento loader. Caches parquet in `cache/research/`;
  estimates cost with the free metadata endpoint; refuses over budget (BudgetExceeded) or into the holdout (HoldoutViolation);
  appends to the ledger only after a SUCCESSFUL download. `python -m research.program.data` prints the Phase 0 estimate.
  Dataset starts 2010-06-06 (not 2010-01-01). Streams sometimes die mid-download ("Read timed out", "Response ended
  prematurely"): just re-run; the cache keeps finished symbols and the ledger is not charged for the failed stream.
- `stats.py` — `deflated_sharpe`, `pbo` (CSCV; biased toward calling things overfit, by design), `purged_folds`, `sharpe_ann`.
- `nights.py` — the shared OVERNIGHT table: one row per Sun-Thu night, buy at the 17:02 CT bar open, sell at the 08:30 CT bar
  open, stop P&L for stops {0, 0.75%, 1%, 1.5%, 2%}, `day_ret` (prior day session 08:30->14:59), `maxstep` (roll/crash flag).
  `nights(root)` builds/caches `cache/research/nights_<root>_...parquet`; `summarize(sub, root)` returns what `score()` needs,
  including `gross_bp`/`stopped_bp`/`cost_bp` for the bp rule. Contract specs (`MICRO`, `POINT`, `COST_SIDE`, `COST_BP`) live here.
- `days.py` — the shared DAY-SESSION table: opens at fixed CT times (`o_0800`, `o_0830`, `o_0835`, `o_0845`, `o_0900`, `o_0929`,
  `o_0930`, `o_1000`, `o_1030`, `o_1300`, `o_1400`, `o_1430`), closes (`c_0834`, `c_0844`, `c_0928`, `c_1329`, `c_1429`, `c_1459`),
  first-5/15-minute ranges, pre-computed ORB-5/ORB-15 outcomes. `trade_result(sub, root, entry_col, exit_col, side_col)` is the
  generic intraday scorer (no stop). Built for ES NQ RTY YM CL.
- `run_test.py` — the harness. `python -m research.program.run_test <id>`: refuses without a prereg; calls `TESTS[id]()`; one
  `score()` line per returned key (`<id>/<root>`), with ppy = the series' own trades per year, PBO over `params_grid`, the
  gross-vs-round-trip ratio in notes, and the bp-rule cell as the last column. Helpers: `_cal`, `_fomc_week`, `_vix_ratio`,
  `_vix_state` (expanding thresholds, `shift(1)`, verified no look-ahead), `_tom_flags`, `_opex_week`, `_last30`, `_orb`,
  `_pack` (nights-based subset + grid) and `_pack2` (any mix of result dicts + grid), `_complement_note`/`_note`.
  Register a new test by adding `test_<id>()` to `TESTS` and a hypothesis string to `HYP`. A test returns
  `{root: result}`; a result has `daily` (pd.Series indexed by date string, $ net), `trades`, `gross`, `costs`, `gross_per_night`,
  `rt_cost`, `bp_per_night`, `root`, `gross_bp`, `stopped_bp`, `cost_bp`, `params_grid` (T x K, variants' daily P&L aligned and
  zero-filled), `notes`, `sub`.
- `rescore.py` — adds the bp-rule cell to scoreboard rows that lack it, rebuilding subsets from cache (no new line, same N).
- `calendars/` — committed reference data with provenance in its README: `fomc.csv` (Fed pages, 111 scheduled + 9 unscheduled),
  `cpi.csv` and `nfp.csv` (BLS archive file-name dates; BLS blocks curl, these came via the fetch tool and were validated),
  `eia_wpsr.csv` (rule-based: Wednesday, Thursday in federal-holiday weeks; shifted releases are at 10:00-11:00 CT, not 09:30),
  `vix_daily.csv`/`vix3m_daily.csv` (CBOE, truncated to ≤ 2023).
- Smoke-test pattern before an official run: call `RT.TESTS[id]()` from a python snippet and print the primary summaries —
  this writes nothing to the scoreboard and catches code errors; the official `run_test` call is what logs the line.
- Session discipline that worked: write ALL preregs for a batch before running any of them; run tests strictly in queue
  order (scoreboard order = queue order); append each Outcome before the next run; commit once per batch with tests green.

## 4. What the 48 lines say (the knowledge; details and numbers in each prereg's Outcome)
Three facts survived every pre-registered check; everything else is retired and must not be re-tested:
1. The Globex overnight index premium (17:02->08:30 CT) is real, ~3 bp/night on ES 2010-2023, positive in 12 of 14 years,
   and it is a VOL-STATE premium: monotonic in VIX/VIX3M terciles on every symbol, ~0 in deep contango, 13.5 bp in backwardation
   (3A-3). A no-look-ahead expanding-median switch (3A-14) holds 59% of nights at 5.5 bp vs -0.3 bp stood aside. Under the bp rule
   3A-14/NQ nets 4.3 bp, PF 1.17, cost 9%, DSR 0.76. The unconditional 3A-1/NQ is the closest line to the gate: DSR 0.99, PF 1.149.
2. The night INTO a scheduled FOMC decision (3A-5, 3A-6): 2016-2023 ES 21.8 bp/night, PF 3.7, t 3.3, all four symbols, robust
   ex-2020; the night AFTER reverses (-15 bp). Only 63 nights; 2010-2015 was weak (a pre-registered distrust flag). Candidate
   OVERLAY for the holdout, not a sleeve.
3. Last-30-minute momentum in the high vol state (3A-9): +2.8 bp/trade ES vs -0.1 in the low state, scaling with the day's move,
   PBO ~0. NQ nets +2.0 bp under the bp rule, PF 1.16, fails DSR. Tiny; a possible add-on, not a sleeve.
Retired with the sign wrong or no mechanism: FOMC cycle weeks (3A-4), prior-day reversal (3A-2), turn-of-month (3A-7; the pre-turn
window was the strongest), opex-week fade (3A-8), ORB (3A-12), ES->RTY lead-lag (3A-13), bonds/gold overnight (3A-10: ~0 and
negative; the premium is equity-only), CL into the EIA report (3A-11). Macro nights (3A-6) are 2-3x ordinary nights but the
whole effect is the FOMC night. Under the bp rule the index results are all cheaper but none passes DSR; no line passes.
The honest summary: the prop-compatible intraday/overnight space holds one thin stress-state premium plus one calendar
night. At 2024+ index levels (roughly double 2010-23) the same bp is ~2x the dollars per micro — that is a holdout question.

## 5. What the next session does, by scenario
A. Connor approves Phase 3 (and the data spend): (1) red-team 3A-14, 3A-5 (and 3A-9) against REDTEAM.md in writing — in
   particular fix the .v.0 roll-inside-the-window artefact (per-contract stitching with a roll rule outside 17:02-08:30) and
   re-check the result on ≤ 2023 data as a NEW scoreboard line only if the code changes; (2) correlation matrix of the three
   daily series; (3) define the ONE holdout rule in a prereg before buying 2024-01-01..2026-10-01 for ES and NQ only (estimate
   first with `metadata.get_cost`; needs Connor's budget), run it ONCE with `verdict=True`, log the verdict line, stop.
B. Connor chooses Phase 2 (own-capital, §3B): no futures data needed. Build an Alpaca daily-bar loader with the same budget/
   holdout wrapper discipline (free data, but the holdout seal and the ledger pattern stay), a point-in-time universe is the
   known hard problem (§3B.17), then take §3B items in order with the same prereg/score/commit loop. Register tests in `TESTS`.
C. Anything else: say what the queue says, ask for the decision, do not spend, do not reorder.
In every scenario: pre-register before running, one line per result, outcomes appended, tests green, commit, push with the
command in §0.5, and end the session with a report that names what was spent, what remains, and what the next decision is.

## 6. Things that cost time last session (so you do not repeat them)
- The repo is at ~/spxf/trade; the session started at ~/ and spent its first minutes finding it.
- Pushing needs the §0.5 command. `gh auth status` showing a PAT is the read-only one.
- BLS and Wayback block scripted fetches (403 / 429); the fetch tool reads BLS fine; EIA's schedule page lists only 2025-26.
- Databento: start at 2010-06-06; streams die occasionally — re-run; the ledger charges only on success; RTY exists from 2017-07,
  ZN/ZB 17:02 bars are missing in 2010.
- pandas 3 is installed: `DataFrame.where` with a numpy mask errors (use `.loc[mask] = nan`); `groupby(index.date)` is fine.
- `run_test` lines are long; when reading the scoreboard in a terminal, `cut -c1-400` or read the prereg Outcomes instead.
