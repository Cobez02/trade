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
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 70)
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
