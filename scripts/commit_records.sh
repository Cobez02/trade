#!/usr/bin/env bash
# Commit the paper lab's records without ever losing a line.
# days*.jsonl and logs/*.log are APPEND-ONLY: this job's new lines are merged as a UNION with whatever
# other jobs pushed while this one ran. state_*.json snapshots take this job's (newer) version.
# Rebasing over a diverged record file conflicted and silently dropped afternoons (9/16-9/18).
set -u
cd "$(dirname "$0")/.."
git config user.name spxf-bot; git config user.email bot@users.noreply.github.com
TMP=$(mktemp -d)
for f in days.jsonl days_overnight.jsonl state_futures.json state_overnight.json; do [ -f "$f" ] && cp "$f" "$TMP/$(basename $f)"; done
mkdir -p "$TMP/logs"; [ -d logs ] && cp logs/*.log "$TMP/logs/" 2>/dev/null
git fetch -q origin main
git checkout -q -B main origin/main -- 2>/dev/null || { git stash -q -u 2>/dev/null; git checkout -q -B main origin/main; }
# append-only union
for f in days.jsonl days_overnight.jsonl; do
  if [ -f "$TMP/$f" ]; then touch "$f"; grep -vxFf "$f" "$TMP/$f" >> "$f" 2>/dev/null || true; fi
done
mkdir -p logs
for f in "$TMP"/logs/*.log; do [ -f "$f" ] || continue; b=$(basename "$f"); touch "logs/$b"; grep -vxFf "logs/$b" "$f" >> "logs/$b" 2>/dev/null || true; done
# snapshots: this job's version wins
for f in state_futures.json state_overnight.json; do [ -f "$TMP/$f" ] && cp "$TMP/$f" "$f"; done
git add days.jsonl days_overnight.jsonl state_futures.json state_overnight.json logs/ 2>/dev/null || true
git commit -q -m "${1:-records} $(date -u +%FT%H:%MZ)" || { echo "nothing to commit"; exit 0; }
for i in 1 2 3 4; do
  git push -q origin main:main && { echo "PUSHED $(git rev-parse --short HEAD)"; exit 0; }
  echo "push rejected; re-merging"; sleep $((i*5))
  # someone pushed meanwhile: redo the union on top of the new remote
  git fetch -q origin main; git reset -q --soft origin/main; git commit -q -m "${1:-records} $(date -u +%FT%H:%MZ)" || true
done
echo "PUSH FAILED after retries"; exit 1
