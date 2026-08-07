"""Phone alerting for live issues — CallMeBot WhatsApp + dead-man's switch.

Design rules, in order of importance:
  1. An alert failure must NEVER break trading. Every function here swallows
     every exception; the worst case is a silent alert, never a dead run.
  2. Secrets stay in GitHub repo secrets (SPXBOT_WA_PHONE, SPXBOT_WA_APIKEY,
     SPXBOT_HEALTHCHECK_URL), mapped to env by trade.yml. Unset = no-op, so
     paper runs and tests need no configuration.
  3. CallMeBot is an unofficial personal bridge (owner-chosen channel,
     reliability caveat disclosed 8/7): treat delivery as best-effort. The
     dead-man's switch (healthchecks.io) is the guaranteed layer — it alerts
     on SILENCE, which no sender-side code can do for itself.
  4. Messages are truncated hard: WhatsApp chops long texts and the URL has
     length limits; an alert's job is "look at the account now", not detail.

Channels, any or all (unset = skipped):
  SPXBOT_ALERT_WEBHOOK — any webhook URL taking POST JSON. The payload carries
      BOTH {"content": ...} (Discord's field) and {"text": ...} (generic/Slack
      -compatible); receivers ignore the key they don't use.
  SPXBOT_TG_TOKEN + SPXBOT_TG_CHAT — first-class Telegram (official bot API).
  SPXBOT_WA_PHONE + SPXBOT_WA_APIKEY — CallMeBot WhatsApp (unofficial bridge;
      kept for the day their capacity opens, owner's original channel pick).
"""
from __future__ import annotations
import json
import os
import urllib.parse
import urllib.request

MAX_LEN = 280
TIMEOUT = 10


def send_alert(msg: str) -> bool:
    """Best-effort phone alert. Returns True only if some channel accepted it."""
    ok = False
    text = ("SPXBOT: " + str(msg))[:MAX_LEN]
    phone = os.environ.get("SPXBOT_WA_PHONE", "").strip()
    key = os.environ.get("SPXBOT_WA_APIKEY", "").strip()
    if phone and key:
        try:
            url = ("https://api.callmebot.com/whatsapp.php?"
                   + urllib.parse.urlencode(
                       {"phone": phone, "apikey": key, "text": text}))
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                ok = 200 <= r.status < 300
        except Exception:
            pass
    tg_token = os.environ.get("SPXBOT_TG_TOKEN", "").strip()
    tg_chat = os.environ.get("SPXBOT_TG_CHAT", "").strip()
    if tg_token and tg_chat:
        try:
            url = (f"https://api.telegram.org/bot{tg_token}/sendMessage?"
                   + urllib.parse.urlencode({"chat_id": tg_chat, "text": text}))
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                ok = ok or 200 <= r.status < 300
        except Exception:
            pass
    hook = os.environ.get("SPXBOT_ALERT_WEBHOOK", "").strip()
    if hook:
        try:
            req = urllib.request.Request(
                hook, data=json.dumps({"content": text, "text": text}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                ok = ok or 200 <= r.status < 300
        except Exception:
            pass
    return ok


def ping_deadman() -> None:
    """Tell healthchecks.io this run happened. A missed ping IS the alert —
    it is how a silently dead scheduler (Thursday's failure) reaches a phone."""
    url = os.environ.get("SPXBOT_HEALTHCHECK_URL", "").strip()
    if not url:
        return
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT):
            pass
    except Exception:
        pass
