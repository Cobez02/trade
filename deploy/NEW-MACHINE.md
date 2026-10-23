# Moving the lab to a new Mac (written 2026-10-23; pairs with HANDOFF.md §10)

The package folder `spxf-handoff-<date>` (made from this repo on the old Mac) holds everything a new machine needs
EXCEPT the keys. Its README lists the files. Do the steps in this order; each one is a gate.

## 0. On the OLD Mac first: stop its agent (one runner per account, never two)
```
launchctl unload ~/Library/LaunchAgents/com.spxf.lab.plist
cat ~/spxf/trade/logs/session.pid 2>/dev/null && echo "a session is still running; wait for it to end (~14:58 CT) or do this outside 04:30-15:00 CT"
```
Unloading does not kill a session already running in the foreground; it only stops new ticks. Both Macs ticking
the same Alpaca paper account would double-trade it.

## 1. Keys: copy `~/.spxf.env` by hand (it is NOT in the package)
From the old Mac: `scp ~/.spxf.env <new-mac>:~/.spxf.env` or AirDrop it, then on the new Mac `chmod 600 ~/.spxf.env`.
Or recreate it from `spxf.env.template` with the values from the Alpaca (paper) and Databento dashboards.
Do this BEFORE step 2: the installer writes an empty template only when the file is missing, and never overwrites it.

## 2. Prerequisites, then the installer
```
xcode-select --install                 # git
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # if no brew
brew install python@3.12 gh            # the old venv was Python 3.12.13; 3.14 is untested with pandas 3
curl -fsSL https://raw.githubusercontent.com/Cobez02/trade/main/deploy/install_mac.sh | bash
```
The repo is public, so the clone needs no auth. The installer clones to `~/spxf/trade`, builds `venv/`, regenerates
`deploy/mac_tick.sh`, installs and loads the launchd agent `com.spxf.lab` (RunAtLoad: inside a weekday 04:30-14:50 CT
window it starts a session immediately — fine once the keys are in place and the OLD agent is unloaded).
Offline alternative (no GitHub): `git clone <package>/trade.bundle ~/spxf/trade && git -C ~/spxf/trade remote set-url
origin https://github.com/Cobez02/trade.git`, then `bash ~/spxf/trade/deploy/install_mac.sh`.
Exact package versions, if `pip install -r requirements.txt` drifts: `~/spxf/trade/venv/bin/pip install -r <package>/requirements-frozen.txt`.

## 3. Restore the research data (the ONLY copy of $122.96 of Databento data; a re-download is refused)
```
cp -R <package>/cache ~/spxf/trade/           # -> ~/spxf/trade/cache/research/, 20 parquet files, 467 MB
cd <package> && shasum -a 256 -c MANIFEST.sha256 | grep -v ': OK$' || echo "all files verified"
```
`cache/` is gitignored on purpose; it never travels with the repo.

## 4. Push credentials (so the records reach GitHub)
`gh auth login` (GitHub.com, HTTPS, browser) stores a keyring token that git uses. Verify without pushing:
`cd ~/spxf/trade && env -u GITHUB_TOKEN git push --dry-run origin main`. Do not export GITHUB_TOKEN in your shell
profile; a read-only one is what broke pushes on the old Mac (HANDOFF.md §0.5).

## 5. Claude Code memory (optional but saves the first ten minutes)
Claude Code keys its memory to the project path. For `/Users/<user>/spxf/trade` the directory is
`~/.claude/projects/-Users-<user>-spxf-trade/memory/`. Copy `<package>/claude-memory/*` there (create it), keeping
`MEMORY.md` as the index. If the username differs from `connor`, nothing else changes.

## 6. Verify before trusting it
```
cd ~/spxf/trade
./venv/bin/python -m tests.test_core                              # expect 83 passed, 0 failed
./venv/bin/python -m live.runner --broker sim --dry-run --day 2026-07-28 --source yfinance --symbol SPY --instrument SPY
launchctl print gui/$(id -u)/com.spxf.lab | grep -E "state =|runs =|last exit"
set -a; . ~/.spxf.env; set +a; PYTHONPATH=$PWD ./venv/bin/python -c "from broker.alpaca_proxy import AlpacaProxyBroker as B; b=B(); print(b.account(), b._raw_qty('QQQ'), b._raw_qty('QQQM'))"
ls cache/research | wc -l                                         # 20
```
Then, on the next weekday inside 04:30-14:50 CT with the lid open: `tail -f logs/mac.log` and expect
`tick: starting a session` followed by `session <date>` within ~10 s. That is the moment the move is done.

## 7. Keep it awake, or it is not a lab
Plugged in, lid open or external display, 03:30-14:00 Mountain (04:30-15:00 Chicago) on weekdays. caffeinate in the
tick only prevents idle sleep. `sudo pmset repeat wakeorpoweron MTWRF 03:25:00` wakes a closed-but-plugged-in Mac,
but the lid still has to be open for the runner to keep running. No timezone setting is needed anywhere: the tick
converts to America/Chicago itself.

## 8. First Claude Code session on the new machine
`cd ~/spxf/trade && claude`, then: "Read HANDOFF.md and do its §0 checklist; report before changing anything."
