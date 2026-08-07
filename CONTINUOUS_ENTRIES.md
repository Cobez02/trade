# Continuous entry scanning — the scheduler-independence fix

Built 2026-08-07 at Connor's request ("scan continuously rather than at
specific times, at least every 5 minutes"). Ships to PAPER for a proof
week; nothing about it touches the go-live money decision except to
strengthen the case that the plumbing is ready.

## What was wrong

The bot has two halves. The **watcher** (`watcher.py`) is a persistent
process that runs a 0.25-second loop the entire session — but by design it
only manages EXITS. The decision of what to BUY lived on GitHub Actions'
**hourly cron**, which is best-effort and has been delayed or dropped
repeatedly this week. Result: on a bad day the bot looked for entries once
and then went dark for hours (Thursday: zero looks; Friday: one look at
10:44 ET, then silence). A flat book on those days wasn't discipline — it
was absence.

## What changed

Entry scanning now runs inside the watcher's existing continuous loop.
Every `ENTRY_SCAN_SECONDS` (default 300 = 5 min) the watcher fires the
**production entry pipeline** — the same `main.py`, same screens, same
caps, same vol-edge gate — as a **non-blocking subprocess** in
`SPXBOT_ENTRIES_ONLY` mode. Key properties, each tested:

- **Never blocks exits.** The scan is a `subprocess.Popen`, not a call;
  the 0.25s exit loop keeps evaluating stops while the scan runs in
  parallel. Only one scan is ever in flight.
- **Runs when the book is empty.** The scan sits before the loop's
  empty-book short-circuit — an empty book is exactly when we most need to
  be looking.
- **Never opens late or blind.** Skipped inside the flatten window and
  whenever the clock reads anything but confirmed-open.
- **Clean separation of duties.** `ENTRIES_ONLY` mode skips exit
  management so the watcher (which owns exits, continuously) and the scan
  (which owns entries) never reach for the same position. New fills still
  get a resting broker stop immediately, which the watcher then adopts.
- **Kill switch.** `SPXBOT_WATCHER_ENTRIES=0` disables it instantly.

A companion `SPXBOT_NO_ENTRIES` mode was added to `main.py` so the hourly
cron can later be flipped to exits/housekeeping-only, making the watcher
the *sole* entry-placer. That flip is deferred: during the proof week the
hourly cron KEEPS placing entries as a fallback, so if the watcher is down
we still trade. The cost is a small, bounded double-entry race if both
fire in the same second (worst case: two $600 positions instead of one —
a sizing blip, not an unbounded loss, and visible on paper). Flip to
`NO_ENTRIES` once the watcher has proven reliable.

## The honest limit

The watcher itself is still LAUNCHED by a GitHub cron (`watch.yml`, at
13:31 and 17:00 UTC). So this reduces the cron dependency from ~7 fires
per session to **1 launch fire** — a 7× cut in failure surface, not zero.
Closing the last point requires an external always-on trigger to launch
the watcher (the "redundant heartbeat" piece), which is the remaining
weekend item and the real reason go-live wants a full proof week: watch
the continuous loop run green, unattended, for five sessions before a
dollar rides on it.

## Tests

`test_continuous.py` (20 checks) pins the non-blocking property, the
flatten/clock guards, the empty-book ordering, the entries/exits
separation, and the kill switch. Full suite green: 473 checks across 9
files, including the watcher's existing 87 unbroken.
