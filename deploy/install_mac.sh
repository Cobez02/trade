#!/usr/bin/env bash
# Paper lab on a Mac: one paste, no server.
#
#   curl -fsSL https://raw.githubusercontent.com/Cobez02/trade/main/deploy/install_mac.sh | bash
#
# What it does: clones the repo to ~/spxf/trade, builds a Python venv, installs a launchd agent
# that wakes every 30 minutes, converts the clock to America/Chicago (so it keeps working wherever
# the Mac is), and - on a weekday between 04:30 and 14:50 CT, if no session is already running -
# starts the runner under `caffeinate` so the Mac does not idle-sleep during the session. The
# runner itself waits for the open, trades, sells the overnight sleeve at 08:31, flattens at
# 14:52, buys the overnight sleeve at 14:57 and exits. Records are committed and pushed with
# the Mac's own GitHub credentials if it has any; otherwise they stay in ~/spxf/trade.
#
# Requirements: the Mac must be awake (plugged in, lid open or external display) during the
# session. caffeinate prevents IDLE sleep; it cannot prevent lid-closed sleep on battery.
set -euo pipefail
DIR="$HOME/spxf/trade"; ENVF="$HOME/.spxf.env"; AGENT="$HOME/Library/LaunchAgents/com.spxf.lab.plist"
command -v git >/dev/null || { echo "git is missing: install Xcode Command Line Tools (xcode-select --install) and re-run"; exit 1; }
PY=$(command -v python3.12 || command -v python3 || true)
[ -n "$PY" ] || { echo "python3 is missing: brew install python@3.12, then re-run"; exit 1; }
mkdir -p "$HOME/spxf"
if [ ! -d "$DIR/.git" ]; then git clone -q https://github.com/Cobez02/trade.git "$DIR"; else git -C "$DIR" pull -q --ff-only || true; fi
"$PY" -m venv "$DIR/venv" && "$DIR/venv/bin/pip" install -q --upgrade pip && "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
if [ ! -f "$ENVF" ]; then
  cat > "$ENVF" <<'ENV'
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
SPXF_ALPACA_FEED=sip
SPXF_INSTRUMENT=QQQ
SPXF_PROXY_SHARES=20
SPXF_OVERNIGHT_SYMBOL=QQQM
SPXF_OVERNIGHT_NOTIONAL=50000
ENV
  chmod 600 "$ENVF"
fi
mkdir -p "$DIR/logs" "$HOME/Library/LaunchAgents"
cat > "$DIR/deploy/mac_tick.sh" <<'TICK'
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
(
  caffeinate -i -s "$DIR/venv/bin/python" -m live.runner --broker alpaca --symbol QQQ --instrument QQQ \
     --source alpaca --start "$(date -u -v-45d +%F)" --end "$(date -u +%F)" >> "$DIR/logs/mac.log" 2>&1
  bash "$DIR/scripts/commit_records.sh" "mac session" >> "$DIR/logs/mac.log" 2>&1 || true
  rm -f "$LOCK"
) &
echo $! > "$LOCK"
TICK
chmod +x "$DIR/deploy/mac_tick.sh"
cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.spxf.lab</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/deploy/mac_tick.sh</string></array>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/launchd.log</string>
</dict></plist>
PLIST
launchctl unload "$AGENT" 2>/dev/null || true
launchctl load "$AGENT"
echo
echo "Installed. Next:"
echo "  1. open -e $ENVF          <- paste the Alpaca keys, save"
echo "  2. Disable the 'lab' workflow at github.com/Cobez02/trade/actions so GitHub and the Mac never trade at once"
echo "  3. Keep the Mac plugged in and awake on weekdays 04:30-15:00 Chicago time"
echo "  4. Watch it: tail -f $DIR/logs/mac.log    (sessions also land in $DIR/logs/<date>.log)"
echo "  5. Optional, so the records reach GitHub: gh auth login   (or any git credential helper)"
