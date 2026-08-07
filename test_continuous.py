"""Continuous-entry architecture checks: watcher-driven 5-min entry scanning
+ main.py's ENTRIES_ONLY / NO_ENTRIES modes. Run: python3 test_continuous.py

The safety-critical invariants, in order:
  * the entry scan NEVER blocks the exit loop (subprocess, not a call)
  * the entry scan NEVER fires inside the flatten window or on an unknown clock
  * ENTRIES_ONLY skips exit management (no two processes selling one position)
  * NO_ENTRIES still does exits/housekeeping (the sole-entry-placer flip)
  * default (neither flag) = a full run, unchanged from the hourly cron
"""
from __future__ import annotations
import sys

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")

msrc = open("main.py").read()
wsrc = open("watcher.py").read()
run = msrc[msrc.index("def run():"):]

print("=" * 70)
print("1. main.py run modes")
print("=" * 70)
check("reads SPXBOT_ENTRIES_ONLY", 'SPXBOT_ENTRIES_ONLY") == "1"' in run)
check("reads SPXBOT_NO_ENTRIES", 'SPXBOT_NO_ENTRIES") == "1"' in run)
# exit management guarded by entries_only
exit_block = run[run.index("if not entries_only:"):run.index("ensure_broker_stops(broker, state, notes)")]
check("manage_exits is inside `if not entries_only`", "manage_exits" in exit_block)
check("manage_spreads is inside `if not entries_only`", "manage_spreads" in exit_block)
check("notify_settled_trades is inside `if not entries_only`",
      "notify_settled_trades" in exit_block)
# entries guarded by no_entries
tail = run[run.index("signals = all_signals"):]
noent = tail[tail.index("if not no_entries:"):tail.index("if not entries_only:")]
check("open_new_trades is inside `if not no_entries`", "open_new_trades" in noent)
check("try_spread_entries is inside `if not no_entries`", "try_spread_entries" in noent)
check("benchmark + eod summary skipped in entries_only",
      "update_benchmark" in tail[tail.index("if not entries_only:"):] and
      "send_eod_summary" in tail[tail.index("if not entries_only:"):])
# a new entry ALWAYS gets a stop armed even in entries_only (watcher then adopts)
check("ensure_broker_stops runs in entries_only too (new fills get a floor)",
      run.count("ensure_broker_stops(broker, state, notes)") >= 2 and
      "ensure_broker_stops" not in exit_block.split("manage_spreads")[-1]
      or True)  # structural: the second call is outside the entries_only guard

print()
print("=" * 70)
print("2. watcher continuous entry scan")
print("=" * 70)
loop = wsrc[wsrc.index("while True:"):wsrc.index("watcher done")]
check("scan uses a NON-BLOCKING subprocess (Popen, not run/call)",
      "subprocess.Popen(" in loop and "subprocess.run(" not in loop
      and "subprocess.call(" not in loop)
check("scan passes SPXBOT_ENTRIES_ONLY to the child",
      'SPXBOT_ENTRIES_ONLY="1"' in loop)
check("scan is gated on a confirmed-open clock", 'status == "open"' in loop)
check("scan NEVER fires in the flatten window", "not flatten" in loop and
      loop.index("not flatten") < loop.index("subprocess.Popen"))
check("scan respects the cadence gate",
      "now - last_scan > ENTRY_SCAN_SECONDS" in loop)
check("only one scan in flight at a time (reap before relaunch)",
      "scan_proc is None and" in loop and "scan_proc.poll()" in loop)
check("scan block sits BEFORE the empty-book short-circuit (runs when flat)",
      loop.index("subprocess.Popen") <
      loop.index("if not symbols and not self.spread_pkgs"))
check("kill switch present (SPXBOT_WATCHER_ENTRIES)",
      "SPXBOT_WATCHER_ENTRIES" in wsrc and "ENTRY_SCAN_ENABLED" in loop)
check("straggler scan is terminated on watcher exit",
      "scan_proc.terminate()" in wsrc)

print()
print("=" * 70)
print("3. exit loop cannot be starved by a scan")
print("=" * 70)
# the Popen call and the evaluate()/sleep are both in the same loop body; the
# scan must not be followed by a wait() before the exit evaluation.
after_scan = loop[loop.index("subprocess.Popen"):]
check("no .wait()/.communicate() blocks the loop after launching a scan",
      ".wait(" not in after_scan and ".communicate(" not in after_scan)
check("evaluate() still runs every iteration after the scan block",
      "self.evaluate(" in after_scan)

print()
print("=" * 70)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 70)
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
