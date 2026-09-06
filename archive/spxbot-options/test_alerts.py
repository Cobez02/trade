"""Alerting layer checks. The one law: an alert failure NEVER breaks trading.
Run: python3 test_alerts.py"""
from __future__ import annotations
import os, sys, types

import alerts
import urllib.request

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")

# scrub env so tests never send anything real
for k in ("SPXBOT_WA_PHONE", "SPXBOT_WA_APIKEY", "SPXBOT_ALERT_WEBHOOK",
          "SPXBOT_HEALTHCHECK_URL"):
    os.environ.pop(k, None)

print("=" * 70)
print("1. unset secrets -> pure no-op, no network, no raise")
print("=" * 70)
calls = []
orig = urllib.request.urlopen
def spy(*a, **kw):
    calls.append(a)
    raise RuntimeError("network should not be touched")
urllib.request.urlopen = spy
try:
    ok = alerts.send_alert("test")
    check("send_alert with no secrets returns False", ok is False)
    check("send_alert with no secrets touches no network", calls == [])
    alerts.ping_deadman()
    check("ping_deadman with no secret touches no network", calls == [])
finally:
    urllib.request.urlopen = orig

print()
print("=" * 70)
print("2. network failure -> swallowed, never raised")
print("=" * 70)
os.environ["SPXBOT_WA_PHONE"] = "+000"
os.environ["SPXBOT_WA_APIKEY"] = "x"
os.environ["SPXBOT_HEALTHCHECK_URL"] = "https://example.invalid/ping"
def boom(*a, **kw):
    raise OSError("connection refused")
urllib.request.urlopen = boom
try:
    ok = alerts.send_alert("boom test")
    check("send_alert survives a dead network and returns False", ok is False)
    try:
        alerts.ping_deadman()
        check("ping_deadman survives a dead network", True)
    except Exception:
        check("ping_deadman survives a dead network", False)
finally:
    urllib.request.urlopen = orig
    for k in ("SPXBOT_WA_PHONE", "SPXBOT_WA_APIKEY", "SPXBOT_HEALTHCHECK_URL"):
        os.environ.pop(k, None)

print()
print("=" * 70)
print("3. message hygiene")
print("=" * 70)
os.environ["SPXBOT_WA_PHONE"] = "+000"
os.environ["SPXBOT_WA_APIKEY"] = "k"
seen = {}
class FakeResp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False
def capture(url, *a, **kw):
    seen["url"] = url if isinstance(url, str) else url.full_url
    return FakeResp()
urllib.request.urlopen = capture
try:
    alerts.send_alert("X" * 1000)
    from urllib.parse import parse_qs, urlparse
    text = parse_qs(urlparse(seen["url"]).query)["text"][0]
    check("message truncated to bound", len(text) <= alerts.MAX_LEN)
    check("message carries the SPXBOT prefix", text.startswith("SPXBOT: "))
finally:
    urllib.request.urlopen = orig
    os.environ.pop("SPXBOT_WA_PHONE", None)
    os.environ.pop("SPXBOT_WA_APIKEY", None)

print()
print("=" * 70)
print("4. wiring in main.py")
print("=" * 70)
src = open("main.py").read()
check("dead-man ping fires at run start",
      "alerts.ping_deadman()" in src.split('if __name__')[1])
check("crash alert wraps run() and re-raises",
      "RUN CRASHED" in src and src.count("raise") >= 2)
check("assignment repair alerts (success and failure paths)",
      "ASSIGNMENT detected" in src and "ASSIGNMENT REPAIR FAILED on" in src)
check("stop-arm failure alerts", "STOP-ARM FAILED" in src)

print()
print("=" * 70)
print("5. trade receipts + EOD summary logic")
print("=" * 70)
import types
import main as M

sent = []
_orig_send = alerts.send_alert
alerts.send_alert = lambda msg, prefix="SPXBOT: ": (sent.append((prefix, msg)), True)[1]
try:
    J = [{"symbol": "A260814C1", "closed_on": "2026-08-06", "pnl": 100.0,
          "pnl_pct": 0.25, "sleeve": "tech", "exit_reason": "take-profit"},
         {"symbol": "B260814C1", "closed_on": "2026-08-06", "pnl": -50.0,
          "pnl_pct": -0.30, "sleeve": "news", "exit_reason": "stop-loss"}]
    st = {}
    n = M.notify_settled_trades(st, J)
    check("first deploy seeds silently (no spam of history)",
          n == 0 and sent == [] and len(st["notified_trades"]) == 2)
    J2 = J + [{"symbol": "C260814P1", "closed_on": "2026-08-07", "pnl": 80.0,
               "pnl_pct": 0.2, "sleeve": "tech", "exit_reason": "trail"}]
    n = M.notify_settled_trades(st, J2)
    check("new settled trade sends exactly one receipt", n == 1 and len(sent) == 1)
    check("receipt has no alarm prefix and carries P/L",
          sent[0][0] == "" and "+$80" in sent[0][1] and "C260814P1" in sent[0][1])
    n = M.notify_settled_trades(st, J2)
    check("already-notified trades never re-send", n == 0 and len(sent) == 1)

    sent.clear()
    class ClosedClock:
        is_open = False
    class FakeBroker:
        def clock(self): return ClosedClock()
    tdy = M.today()
    st2 = {"equity_history": [{"date": tdy, "equity": 101704.03}],
           "closed": [{"closed_on": tdy, "pnl": 369.0},
                      {"closed_on": tdy, "pnl": -45.0},
                      {"closed_on": "2026-01-01", "pnl": 999.0}],
           "start_equity": 10000.0}
    M.send_eod_summary(FakeBroker(), st2)
    check("EOD summary sends once with today's trades only",
          len(sent) == 1 and "2 trades" in sent[0][1] and "$324" in sent[0][1])
    check("EOD summary marks the date", st2.get("eod_summary_sent") == tdy)
    M.send_eod_summary(FakeBroker(), st2)
    check("EOD summary never sends twice per day", len(sent) == 1)
    st3 = {"equity_history": [{"date": "2020-01-01", "equity": 1}], "closed": []}
    M.send_eod_summary(FakeBroker(), st3)
    check("no session today -> no summary (weekend/holiday)", len(sent) == 1)
finally:
    alerts.send_alert = _orig_send

print()
print("=" * 70)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 70)
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
