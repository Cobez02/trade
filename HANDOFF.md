# HANDOFF — the paper-trading lab and the prop-firm plan (written 2026-10-23 11:40 CT; read this whole file first)

Two handoffs live in this repo. This one is the TRADING side: the Alpaca paper lab on Connor's Mac, the plan to run
1 MES overnight on a Topstep Combine, and the operating knowledge that cost real bugs. The RESEARCH side (the
500-hour pre-registered program, N=48, Databento credit spent) is `research/program/HANDOFF-PROGRAM.md`; a research
session reads that one first. The repo is the memory: nothing lives in chat. Everything below was verified on
2026-10-23 against the broker, launchd, the logs and git, not copied from an older file.

## 0. Start here, every session, in this order
1. `cd ~/spxf/trade` (the clone is here, not at `~`). Load keys: `set -a; . ~/.spxf.env; set +a`. The file holds
   ALPACA_API_KEY, ALPACA_SECRET_KEY, SPXF_ALPACA_FEED, SPXF_INSTRUMENT, SPXF_PROXY_SHARES, SPXF_OVERNIGHT_SYMBOL,
   SPXF_OVERNIGHT_NOTIONAL, DATABENTO_API_KEY. Never print, commit, or paste a value. To check a key exists, print
   names only (`awk -F= '/^[A-Z_]+=/{print $1}' ~/.spxf.env`).
2. `git fetch; git status -sb`. If behind: `git merge --no-edit origin/main`. NEVER rebase anything that touches
   `days*.jsonl`, `state_*.json`, `account_futures.json` or `logs/` (append-only, union-merged by
   `scripts/commit_records.sh`). A commit of your own that only touches code may be rebased.
3. Expect company. The launchd agent commits "mac session ..." on its own, and a second Claude Code session (the
   research one, started 2026-10-14, still alive on 2026-10-23) shares THIS working tree and commits as `spxf-bot`.
   Before editing a file, `git status` it; never write `research/program/*` from a lab session, and the research
   session never writes `live/`, `broker/`, `deploy/`, the records or this file.
4. `./venv/bin/python -m tests.test_core` -> expect `83 passed, 0 failed`. Run again before every commit.
5. Push: a plain `git push` from an interactive shell returns 403, because the shell exports a read-only
   GITHUB_TOKEN that the gh credential helper prefers. Use `env -u GITHUB_TOKEN git push origin main`. If that
   fails, the research handoff's variant also works:
   `env -u GITHUB_TOKEN git -c credential.helper= -c credential.helper='!gh auth git-credential' push origin main`.
   launchd sessions have no such variable and push fine. Do not edit tokens or git config. Push immediately after
   every commit: see §6 F3 for why an unpushed commit does not survive the next session.
6. The repo's local git identity is `spxf-bot` (the records script sets it). For a hand-made commit that should be
   attributed to Connor, add `-c user.name=Cobez02 -c user.email=Cobez02@users.noreply.github.com`.
7. Scratch files go in the session scratchpad, never in the repo. `venv/`, `cache/`, `logs/mac.log`,
   `logs/launchd.log`, `logs/session.pid`, `.DS_Store` are gitignored; keep it that way.

## 1. The plan in one paragraph (why any of this exists)
Two months of backtests (`research/`, conclusions in `research/PREREG-*.md`) say: day-only intraday index
strategies are too thin, and the one thing worth a prop-firm attempt is the Globex OVERNIGHT drift on micro index
futures, bought at the 17:00 CT reopen with a 1% resting stop and sold at 08:30 CT (Sharpe ~1.1 net on real NQ/ES
2024-26, worst night -$382 on MES; 1 MES ~66% P(pass) on a Topstep $50K Combine vs ~20% for a coin flip).
Pre-registration, numbers and kill rules: `research/PREREG-2026-10-08-globex-overnight.md`. The 17:00 -> 08:30
window sits inside one Globex session, which every firm allows (Topstep: flat by 15:10 CT). Holding from the 14:59
cash close is NOT allowed anywhere. The research program (other handoff) later found, on 2010-2023 data, that the
same premium is real but thin at 2010-23 index levels and is a vol-state premium; that is a holdout question for
Phase 3 and does not change the live plan until Connor says so.

The Alpaca paper lab is the free rehearsal: the same runner, risk engine and record discipline on QQQ (20 shares
per "contract") plus a QQQM overnight sleeve ($50K notional), to prove the plumbing before money touches ProjectX.

## 2. The machine and the schedule
- Hardware: Connor's MacBook Air, system zone now `America/Boise` (it moved from Oslo on 2026-10-15). Every rule in
  the code is in America/Chicago via `data/sessions.py`; the tick script exports TZ=America/Chicago itself, so the
  Mac's own zone never matters. Until 2026-11-01 Mountain time = Chicago time - 1 h.
- launchd agent `com.spxf.lab` (`~/Library/LaunchAgents/com.spxf.lab.plist`): runs `/bin/bash
  ~/spxf/trade/deploy/mac_tick.sh` every 1800 s, RunAtLoad, AbandonProcessGroup, stdout/stderr -> `logs/launchd.log`.
  The interval is anchored to the last load, so after the 2026-10-12 reload ticks landed at :10 and :40 past each
  hour; launchd only fires while the Mac is AWAKE, a missed tick fires once on wake (10-23: woke ~11:30, tick 11:33),
  and the phase drifts after such wakes. Do not rely on the minute; rely on `tick: starting a session` in mac.log. `launchctl print gui/$(id -u)/com.spxf.lab
  | grep -E "state =|runs =|last exit"` shows the state and a run counter (44 runs on 2026-10-23; a continuously
  awake Mac would show ~520 since the reload, which is how you can tell it slept through the last week).
- `deploy/mac_tick.sh` is GENERATED by `deploy/install_mac.sh` (the heredoc there is the source of truth; the file is
  also tracked since 57c1863 so a fresh clone has it). It: loads `~/.spxf.env`; exits unless weekday and
  04:30 <= HHMM <= 14:50 CT; exits if `logs/session.pid` names a live process; `git pull --ff-only`; writes its own pid
  to the lock; appends `tick: starting a session` to `logs/mac.log`; runs the session in the FOREGROUND under
  `caffeinate -i -s` (idle-sleep prevention only); then `scripts/commit_records.sh "mac session"`; then removes the lock.
  Foreground matters: launchd kills a job's process group when the job's main process exits (the first install
  backgrounded the runner and never produced a session). AbandonProcessGroup is the belt.
- Sleep is the whole game. `caffeinate` cannot stop lid-closed sleep on a laptop. The Mac must be plugged in with the
  lid open (or an external display) from 03:30 to 14:00 Mountain on weekdays, or nothing runs. Options Connor has
  not chosen yet: `sudo pmset repeat wakeorpoweron MTWRF 03:25:00` (needs sudo, lid must still be open), or move the
  lab to an always-on Ubuntu box with `deploy/install.sh` (systemd timer, same runner). Only the future MES sleeve is
  tied to the Mac (Topstep bans VPS/VPN); the Alpaca lab can run anywhere.

### The daily sequence (Chicago time; Mountain = 1 h earlier)
| CT | What | Who |
|---|---|---|
| 04:40 | first tick inside the window starts `live/runner.py --broker alpaca --symbol QQQ --instrument QQQ --source alpaca` with 45 days of history; it logs `session <date>` and heartbeats `waiting for the open` every 30 min | tick + runner |
| 08:30 | session open; from here `step()` runs every minute: today's bars, position, P&L | runner |
| 08:31+ | `_overnight_exit`: if the broker holds QQQM, `flatten()` it (cancel -> wait -> close -> verify -> retry), record a sell line, reset `state_overnight.json` to `{}`; retried every minute until confirmed | runner |
| 08:30-14:00 | 30-min slots (slot 0 = 08:30 bar): noise-area bands, ENTER +/-1x(2 contracts = 40 shares), a broker-side DAY stop placed one minute later, trailing band exits, max 2 entries, $200 risk/trade, kill at -$700, cap +$1200 | runner |
| 14:00 | no new entries after this | runner |
| 14:52 | `_flatten_until_flat("session flatten")`, then END OF DAY -> one line in `days.jsonl`, `account_futures.json` updated | runner |
| 14:57 | `_overnight_entry`: Mon-Thu only (never into Fri/holiday/early close), skipped if already holding; market buy `int(50000 // last_price)` QQQM, WAIT for the order to settle (`wait_filled`, 90 s cap), re-read the broker, record buy line + `state_overnight.json` | runner |
| ~14:58 | runner exits; `commit_records.sh` unions the records into origin/main and pushes; lock removed | tick |

A runner started late in the day (any tick up to 14:50) joins at the current minute; a restart on the same day
resumes from `state_futures.json` (entries, halted, last_slot) and a position it finds is only treated as "carried"
(closed at once) when the state shows zero entries. The broker-side stop survives the process dying.

## 3. Evidence files (what each one means)
- `logs/mac.log` (untracked, grows forever): every tick start, the runner's stdout/stderr including tracebacks, and
  the commit script's output (`PUSHED <sha>` or `PUSH FAILED after retries`). First place to look.
- `logs/<date>.log` (tracked): the runner's own session log, one per Chicago date. `logs/overnight-*.log` are the old
  GitHub overnight workflow's logs (dead since 10-01). `logs/links/<name>-HHMM-<run>.log`: per-job copies the commit
  script files so a union mistake can never lose a morning again.
- `days.jsonl`: one END OF DAY line per completed session: `day, symbol, day_pnl, entries, halted, acct_balance
  (cumulative lab P&L), floor, buffer`. A missing date means the session never reached 14:52 (see §5).
- `days_overnight.jsonl`: `buy` lines (`qty, avg_px, by, ok`) and `sell` lines (`entry_px, exit_px_est, pnl_est`).
- `state_futures.json` (today's intraday state), `state_overnight.json` (`{}` when flat, else `held_from, qty,
  avg_px`), `account_futures.json` (balance/peak/floor for the buffer gate). Snapshots: the newest job's version wins.
- The broker is the truth; every file above is a claim about it. When they disagree, the broker wins and the
  disagreement is a bug to log (see §5, the 125-share line).

## 4. State on 2026-10-23 11:40 CT (verified)
- Alpaca paper: equity $103,324.18, NO positions, NO open orders (`_raw_qty` 0 for QQQ and QQQM). Nothing to flatten.
- Records: last `days.jsonl` line is 2026-10-12 (day_pnl +2.32, 1 entry). Last overnight pair: buy 2026-10-12 (file
  says 125 shares; the broker held 162) and sell 2026-10-13 162 QQQM @ ~304.74 (pnl_est -384.31). `state_overnight.json`
  is `{}`. Cumulative lab balance in `account_futures.json`: +68.15.
- Full unattended days achieved: ZERO. 2026-10-12 started at 14:41 CT (flatten + QQQM buy only). 2026-10-13 ran
  the morning (QQQM exit confirmed at 08:34:48 after two `FLATTEN NOT CONFIRMED` attempts, i.e. slow paper fills at
  the open, handled by the minute-by-minute retry), then the Mac lost DNS at ~09:15 CT and every restart that day
  (10:32, 12:54, 13:32, 14:45) died within a second on `NameResolutionError` from `data.alpaca.markets`.
  2026-10-14 did the same: waited for the open, died at 08:30 on the first bar fetch, restarted and died at 09:09,
  09:57, 10:34, 11:12, 11:51, 12:30, 13:08; its last commit stayed local (`PUSH FAILED`, no network) until the
  research session pushed it on 10-23. No trades on either day, nothing was left open. 2026-10-15 to 10-23: no tick
  ever fired inside the window because the Mac was asleep (travel, then closed). Open item 1 in §7 is therefore
  still open, with the same wording.
- Today, Fri 2026-10-23: the Mac woke inside the window and the on-wake tick started a session at 11:33 CT (joined
  at slot 6, flatten 14:52, no QQQM buy on a Friday). Its END OF DAY line and `logs/2026-10-23.log` are the first
  thing to check next session; if it is missing, read `logs/mac.log` for the traceback.
- GitHub Actions: `lab` is `disabled_manually` on purpose (it ran hours late and must never trade alongside the Mac;
  its last run was a manual dispatch on 10-12 that flattened a QQQ short). `backtest` is active but is
  `workflow_dispatch` only (it runs the tests and backtests; it does not trade).
- Tests: 83 passed, 0 failed (73 original + 4 for `wait_for_fill` + 6 from the research program's loader/stats).
- Memory of the previous Claude sessions, if useful: 2026-10-12 verified the first session, fixed the overnight
  fill record, cleaned the gitignore; 2026-10-14 ran the research program; 2026-10-23 wrote both handoffs.

## 5. Known fragilities and bugs, in priority order (fix the first two before trusting any "3 clean days")
F1. **A network error kills the runner.** `run_live` -> `step` -> `_today_bars` -> `AlpacaProxyBroker.bars_today`
    has no retry; any `ConnectionError` propagates and the process exits. The tick restarts it only at the next
    :10/:40 tick and never after 14:50, so an outage from 14:20 on would leave a QQQ position with only its DAY stop
    (expires at the close; the position would then carry overnight and be closed as "carried" at the next session's
    open, as happened 9/23 -> 9/24). Fix: catch exceptions around `self.step(now)` in `run_live`, log them, sleep
    ~30 s and continue; keep `_flatten_until_flat` trying through the close. Add a test with a feed that raises
    twice then succeeds. Do NOT add retries that could place a second order: entries go through the strategy's slot
    logic, which is idempotent per slot via `last_slot` in the state.
F2. **Sleep.** See §2. Until Connor picks an option, every "missing day" is most likely the lid.
F3. **`commit_records.sh` resets local `main` to `origin/main`** (`git checkout -B main origin/main`) before it
    re-applies the records as a union. Any local commit not yet on origin is orphaned (still in reflog, not on the
    branch). Push every commit immediately. With no network it logs `PUSH FAILED after retries` and leaves the commit
    local; the next session with network just pushes. Its fallback path runs `git stash -u` (venv is ignored now).
F4. **The 2026-10-12 QQQM buy line says 125, the broker held 162.** Cause: the record was read 4 s after the order
    while 125 of 162 had filled. Fixed in b5d9caa (`wait_for_fill` polls until the order status is terminal; the
    record is the broker's position afterwards). The wrong line stays: records are append-only, and a rewritten line
    would be re-added by the union. The 10-13 sell line carries the true 162.
F5. **Slow paper fills at the open.** `flatten()` gives each attempt ~6 s to show flat and reports
    `FLATTEN NOT CONFIRMED` otherwise; `_overnight_exit` calls it again every minute until the broker is flat. On
    10-13 that took three calls (08:32:29 -> 08:34:48). Do not "fix" this by trusting the first attempt.
F6. **Stale lock.** If sessions stop starting while the Mac is awake inside the window, check `logs/session.pid`:
    a pid recycled by an unrelated process makes the tick exit as "already running". Delete the file.
F7. **The `lab` GitHub workflow must stay disabled** while the Mac trades the same paper account.
F8. **Early closes.** The runner skips the QQQM buy when the session close is 14:00 or earlier and flattens at the
    configured 14:52 regardless; check `data/sessions.py`'s holiday/early-close tables before a half day.

## 6. Cookbook (copy these; each one was used in a session)
Load keys first: `set -a; . ~/.spxf.env; set +a` (then `PYTHONPATH=$PWD ./venv/bin/python ...`).
- Broker truth (read-only):
  ```
  PYTHONPATH=$PWD ./venv/bin/python -c "
  from broker.alpaca_proxy import AlpacaProxyBroker; b=AlpacaProxyBroker()
  print(b.account()); print([(p.symbol,p.qty,str(p.side)) for p in b.trading.get_all_positions()])
  from alpaca.trading.requests import GetOrdersRequest; from alpaca.trading.enums import QueryOrderStatus
  print([(o.symbol,str(o.side),str(o.type),o.qty,o.stop_price) for o in b.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))])
  print('QQQ',b._raw_qty('QQQ'),'stop',b.resting_stop_qty('QQQ'),'QQQM',b._raw_qty('QQQM'))"
  ```
- Flatten with confirmation: same prelude, then `print(b.flatten("QQQ"))` -> `OrderResult(ok=True, detail='flat'|'closed (attempt n)')`.
- Is a session running: `cat logs/session.pid; ps aux | grep -E "live\.runner|mac_tick" | grep -v grep`.
- Watch it: `tail -f logs/mac.log`; today's session log: `cat logs/$(TZ=America/Chicago date +%F).log`.
- launchd: state as in §2; reload after editing the plist or the tick:
  `launchctl unload ~/Library/LaunchAgents/com.spxf.lab.plist; launchctl load ~/Library/LaunchAgents/com.spxf.lab.plist`
  (RunAtLoad fires a tick at once: inside the window that starts a session within ~10 s; the :10/:40 phase moves to
  the reload minute).
- Regenerate the tick from the installer if they ever differ (`diff` them first):
  `sed -n "/^cat > \"\$DIR\/deploy\/mac_tick.sh\" <<'TICK'$/,/^TICK$/p" deploy/install_mac.sh | sed '1d;$d' > deploy/mac_tick.sh`
- Run a session by hand inside the window: `bash deploy/mac_tick.sh` (foreground until ~14:58 CT; respects the window
  and the lock). Never run two runners on the same account.
- Dry run with no keys: `./venv/bin/python -m live.runner --broker sim --dry-run --day 2026-07-28 --source yfinance --symbol SPY --instrument SPY`.
- Commit records by hand (needs network; see F3): `bash scripts/commit_records.sh "mac session"`.
- Tests: `./venv/bin/python -m tests.test_core` -> 83 passed. Push: `env -u GITHUB_TOKEN git push origin main`.

## 7. Open items, in order (the gate logic: do not spend on the next before the current one is done)
1. **Three consecutive full unattended lab days.** Evidence = three dated END OF DAY lines in `days.jsonl` with
   matching `logs/<date>.log` (open, slots, 14:52 flatten, 14:57 buy Mon-Thu), buy/sell pairs in
   `days_overnight.jsonl` whose qty equals what the broker held, and `PUSHED` in `mac.log`. Prerequisites: F1 fixed
   and tested, and the Mac awake (F2). Before declaring a day clean, run the broker-truth snippet and compare.
2. **Topstep $50K Combine + ProjectX API** (Connor's card, ~$49/mo + ~$14.50/mo). Then put PROJECTX_USER /
   PROJECTX_API_KEY / PROJECTX_ACCOUNT (the PRACTICE account first) in `~/.spxf.env`. Re-read Topstep's account
   parameters page that day; the presets in `risk/rules.py` are dated Sept 2026.
3. **`live/overnight_futures.py` + `broker/projectx.py` on the practice account, 1 MES.** Both were written from
   public docs and have never touched a real account. Verify, in writing, per night: the fill, the 1% stop RESTING
   on Globex overnight (query it back), the 08:30 exit confirmed flat, record (`days_overnight_futures.jsonl`) =
   broker. Ten clean nights. Needs its own launchd schedule: enter 17:02 CT Sun-Thu (16:02 MT), exit 08:30 CT Mon-Fri
   (07:30 MT); the Mac must be awake for both (F2 again); Topstep bans VPS/VPN so this sleeve runs on the Mac only.
4. **The Combine.** Switch PROJECTX_ACCOUNT, 1 MES, this rule only, nothing else. Kill rules from the prereg: stop
   after 30 nights with mean < -$10, or any single night < -$600 (the stop is not working), or fills > 2 ticks worse
   than the hourly model on the practice account.
5. **Research program decision** (not a lab task): Connor must choose Phase 3 + a new Databento spend, or Phase 2 on
   free Alpaca equity data. See `research/program/HANDOFF-PROGRAM.md` §5. Do not start either without his approval.

## 8. Invariants (each one cost a real bug; keep them)
- Never trust local state for positions: ask the broker (`_raw_qty`, `resting_stop_qty`, `position`), then act.
- Every time rule is in America/Chicago via `data/sessions.py`; never UTC, never the Mac's zone.
- A flatten is not done until the broker confirms flat (cancel -> wait -> close -> verify -> retry).
- An order is not recorded until it has SETTLED; the record is the broker's position afterwards (`wait_for_fill`).
- Alpaca reports short qty as NEGATIVE; sign comes from `side` only (`signed_qty`).
- Records are append-only and union-merged; never rebase them, never rewrite a line, never delete a scoreboard line.
- Pre-register before running a test; report failures as loudly as passes (`research/PREREG-*.md`, `research/program/prereg/`).
- Secrets live in `~/.spxf.env` (Mac) or repo Actions secrets; never in files, commits, or chat.
- One runner per account at a time: the Mac OR a server, never both; the `lab` workflow stays disabled.
- Report what was verified, what was skipped, and what is still open, every session, in the final message.

## 9. Research program (added 2026-10-14; full state handoff in research/program/HANDOFF-PROGRAM.md — read that first for a research session)
`research/program/PROGRAM.md` is the 500-hour plan; `QUEUE.md` is the next work; `SCOREBOARD.md` is the ledger of every
test ever run (48 lines, 0 passes, Phase 1 catalog complete, Databento credit $122.96 of $125 spent, the loader now
refuses every request). `cache/research/` (gitignored, 467 MB) is the ONLY copy of that data. DATABENTO_API_KEY is in
~/.spxf.env. CREDIT RULE: stop and report when the loader refuses a request for budget; never raise the budget yourself.
