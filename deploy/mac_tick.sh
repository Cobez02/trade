#!/usr/bin/env bash
# Runs every 30 min from launchd. Starts a session if one should be running and none is.
DIR="$HOME/spxf/trade"; ENVF="$HOME/.spxf.env"; LOCK="$DIR/logs/session.pid"
cd "$DIR" || exit 0
set -a; . "$ENVF"; set +a
export TZ=America/Chicago
dow=$(date +%u); hm=$(date +%H%M)
[ "$dow" -le 5 ] || exit 0                                   # weekdays only
[ "$hm" -ge 0430 ] && [ "$hm" -le 1450 ] || exit 0            # start window (CT)
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then exit 0; fi   # already running
git pull -q --ff-only 2>/dev/null || true
# Run the session in the FOREGROUND. launchd kills a job's whole process group when the job's main
# process exits, so a backgrounded session died the instant the tick script returned (the first
# install left a stale session.pid and no mac.log). launchd will not start a second tick while this
# one is still running, so a foreground session is also the lock.
echo $$ > "$LOCK"
echo "[$(date '+%F %T')] tick: starting a session" >> "$DIR/logs/mac.log"
caffeinate -i -s "$DIR/venv/bin/python" -m live.runner --broker alpaca --symbol QQQ --instrument QQQ \
   --source alpaca --start "$(date -u -v-45d +%F)" --end "$(date -u +%F)" >> "$DIR/logs/mac.log" 2>&1
bash "$DIR/scripts/commit_records.sh" "mac session" >> "$DIR/logs/mac.log" 2>&1 || true
rm -f "$LOCK"
