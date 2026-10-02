#!/usr/bin/env bash
# One-shot install of the paper lab on an always-on Ubuntu 22.04/24.04 server (a $5 VPS or an
# Oracle Always-Free VM). Run as a sudo-capable user:
#
#   curl -fsSL https://raw.githubusercontent.com/Cobez02/trade/main/deploy/install.sh | bash
#
# Then put the Alpaca keys in /etc/spxf.env and (optionally) add the printed deploy key to the
# GitHub repo so the server can push its records. The systemd timer starts the runner at
# 05:00 America/Chicago every weekday; the runner itself waits for the open, trades, sells the
# overnight sleeve at 08:31, flattens at 14:52, buys the overnight sleeve at 14:57 and exits.
set -euo pipefail
REPO=https://github.com/Cobez02/trade.git
DIR=/opt/spxf
sudo apt-get update -qq && sudo apt-get install -y -qq git python3 python3-venv python3-pip >/dev/null
sudo mkdir -p "$DIR" && sudo chown "$USER" "$DIR"
if [ ! -d "$DIR/.git" ]; then git clone -q "$REPO" "$DIR"; else git -C "$DIR" pull -q; fi
python3 -m venv "$DIR/venv" && "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
if [ ! -f /etc/spxf.env ]; then
  sudo tee /etc/spxf.env >/dev/null <<'ENV'
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
SPXF_ALPACA_FEED=sip
SPXF_INSTRUMENT=QQQ
SPXF_PROXY_SHARES=20
SPXF_OVERNIGHT_SYMBOL=QQQM
SPXF_OVERNIGHT_NOTIONAL=50000
ENV
  sudo chmod 600 /etc/spxf.env
fi
sudo tee /etc/systemd/system/spxf-lab.service >/dev/null <<UNIT
[Unit]
Description=SPX-Beater paper lab session (intraday QQQ + overnight QQQM)
After=network-online.target
[Service]
Type=oneshot
User=$USER
WorkingDirectory=$DIR
EnvironmentFile=/etc/spxf.env
Environment=TZ=America/Chicago
ExecStartPre=/usr/bin/git -C $DIR pull -q --ff-only
ExecStart=/bin/bash -c '$DIR/venv/bin/python -m live.runner --broker alpaca --symbol QQQ --instrument QQQ --source alpaca --start \$(date -u -d "-45 days" +%%F) --end \$(date -u +%%F) >> $DIR/logs/service.log 2>&1; bash $DIR/scripts/commit_records.sh "vps session" >> $DIR/logs/service.log 2>&1 || true'
TimeoutStartSec=12h
UNIT
sudo tee /etc/systemd/system/spxf-lab.timer >/dev/null <<'UNIT'
[Unit]
Description=Start the paper lab every weekday before the open
[Timer]
OnCalendar=Mon..Fri 05:00 America/Chicago
Persistent=true
[Install]
WantedBy=timers.target
UNIT
mkdir -p "$DIR/logs"
sudo systemctl daemon-reload && sudo systemctl enable --now spxf-lab.timer
# a deploy key so the server can push its records back to GitHub (optional but recommended)
if [ ! -f "$HOME/.ssh/spxf_deploy" ]; then
  ssh-keygen -q -t ed25519 -N "" -f "$HOME/.ssh/spxf_deploy" -C "spxf-lab@$(hostname)"
  cat >> "$HOME/.ssh/config" <<CFG
Host github.com-spxf
  HostName github.com
  IdentityFile $HOME/.ssh/spxf_deploy
  IdentitiesOnly yes
CFG
  git -C "$DIR" remote set-url origin git@github.com-spxf:Cobez02/trade.git
fi
echo
echo "Installed. Next:"
echo "  1. sudo nano /etc/spxf.env            <- paste the Alpaca keys"
echo "  2. Add this DEPLOY KEY (write access) at github.com/Cobez02/trade/settings/keys :"
cat "$HOME/.ssh/spxf_deploy.pub"
echo "  3. systemctl list-timers spxf-lab.timer ; journalctl -u spxf-lab.service -f"
echo "  4. Disable the GitHub 'lab' workflow in the Actions tab so the two never trade at once."
