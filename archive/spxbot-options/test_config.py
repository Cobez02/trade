"""Config and change-set tests. Run: python3 test_config.py

WHY. The 2026-08-31 change set lives mostly in workflow environment variables,
which no test has ever covered. That is how the repo ended up with root copies
of trade.yml and watch.yml that still said MAX_OPEN=2 while the live workflows
under .github/workflows had run 3 for weeks. A stale file that looks
authoritative is worse than no file, and editing one changes nothing.

These checks pin the settings that were actually backtested, so a later edit
that drifts from them fails loudly instead of silently trading a different
strategy.
"""
from __future__ import annotations
import os
import re

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  - ' + detail if detail else ''}")


def env_of(path, key):
    m = re.search(r'SPXBOT_' + key + r':\s*"([^"]*)"', open(path).read())
    return m.group(1) if m else None


LIVE = ".github/workflows"
EXPECTED = {"MAX_PREM": "450", "DTE_MIN": "14", "DTE_MAX": "35",
            "DTE_PREFER": "21", "UNDERLYING_COOLDOWN": "1"}

print("=" * 74)
print("1. The workflows GitHub actually runs carry the backtested settings")
print("=" * 74)
for wf in ("trade.yml", "watch.yml"):
    p = os.path.join(LIVE, wf)
    for k, v in EXPECTED.items():
        got = env_of(p, k)
        check(f"{wf}: SPXBOT_{k} == {v}", got == v, f"got {got}")

print()
print("=" * 74)
print("2. The inert root copies (WARNING ONLY, never fatal)")
print("=" * 74)
# Deliberately non-fatal. trade.yml runs this file as a pre-flight before
# placing orders, and a stale INERT duplicate must never be a reason the bot
# stops trading. GitHub reads only .github/workflows; the root copies do
# nothing. They are worth a warning because a stale one misleads whoever reads
# it next (it said MAX_OPEN=2 for weeks while live ran 3), and worth exactly
# nothing more than that.
for wf in ("trade.yml", "watch.yml"):
    if not os.path.exists(wf):
        print(f"  ok    {wf}: no root duplicate (preferred: delete them)")
        continue
    same = open(wf).read() == open(os.path.join(LIVE, wf)).read()
    print(f"  {'ok   ' if same else 'WARN '} {wf}: root duplicate "
          f"{'matches' if same else 'is STALE - inert, but delete or sync it'}")

print()
print("=" * 74)
print("3. DTE preference actually reorders contract selection")
print("=" * 74)
import datetime as dt
import types
import importlib
import engine as E


def C(strike, days):
    return types.SimpleNamespace(
        strike_price=str(strike), open_interest="5000",
        expiration_date=(dt.date.today() + dt.timedelta(days=days)).isoformat())


class T:
    def get_option_contracts(self, req):
        return types.SimpleNamespace(option_contracts=[
            C(101, 15),     # nearest strike, wrong expiry
            C(105, 21),     # worse strike, preferred expiry
        ])


class B:
    trading = T()


os.environ["SPXBOT_DTE_PREFER"] = "0"
importlib.reload(E)
got = E.Broker.find_contracts(B(), "AAPL", "bull", 100.0, n=1)
check("PREFER=0 keeps the old behaviour (nearest strike wins)",
      bool(got) and float(got[0].strike_price) == 101.0,
      str(float(got[0].strike_price)) if got else "none")

os.environ["SPXBOT_DTE_PREFER"] = "21"
importlib.reload(E)
got = E.Broker.find_contracts(B(), "AAPL", "bull", 100.0, n=1)
check("PREFER=21 picks the preferred expiry over the nearer strike",
      bool(got) and float(got[0].strike_price) == 105.0,
      str(float(got[0].strike_price)) if got else "none")
os.environ.pop("SPXBOT_DTE_PREFER", None)
importlib.reload(E)

print()
print("=" * 74)
print("4. The same-underlying cooldown is cross-sleeve and same-day only")
print("=" * 74)
import main as M

today = dt.date.today().isoformat()
yday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
st = {"journal": [
    {"underlying": "NVDA", "closed_on": today, "sleeve": "tech"},
    {"underlying": "AMD", "closed_on": yday, "sleeve": "news"},
]}
check("blocks a name already traded today", M.traded_today(st, "NVDA"))
check("does NOT block a name traded yesterday", not M.traded_today(st, "AMD"))
check("does NOT block an untraded name", not M.traded_today(st, "SPY"))
check("is cross-sleeve (the journal row's sleeve is irrelevant)",
      M.traded_today({"journal": [{"underlying": "NVDA", "closed_on": today,
                                   "sleeve": "news"}]}, "NVDA"))
check("survives a malformed journal row", not M.traded_today(
    {"journal": ["garbage", None, {"underlying": None}]}, "NVDA"))
check("default is OFF in code, so the workflow is what turns it on",
      M.UNDERLYING_COOLDOWN is False or os.environ.get("SPXBOT_UNDERLYING_COOLDOWN") == "1")

print()
print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 74)
for f in FAIL:
    print("  FAILED:", f)
raise SystemExit(1 if FAIL else 0)
