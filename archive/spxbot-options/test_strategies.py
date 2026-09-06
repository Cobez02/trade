"""
Sleeve-retirement and crowd-veto tests. No network: the WSB feed and the news
endpoint are monkeypatched. Run: python3 test_strategies.py

What this file pins:

  * Retired sleeves (engine.RETIRED_SLEEVES) never have their entry functions
    CALLED — not "called and discarded," never called. The wsb feed's only
    remaining job is the veto, and flow's OI scan is 2x500-contract pagination
    per name per run that must not silently keep burning rate limit.
  * The retirement is a partition: SLEEVES == ACTIVE + RETIRED exactly, and
    every consumer keyed on sleeve name (journal, dashboard, learner) still
    sees all four keys in all_signals' output.
  * crowd_veto is DEFENSIVE and FAIL-OPEN: it can only remove candidate buys
    (it returns tickers, no directions, so it cannot be inverted into a short
    even by a bug downstream), and every failure mode of a free third-party
    API maps to "no veto," never to a crash or a full-book veto.
  * main.py's entry loop iterates ACTIVE_SLEEVES and applies the veto; the
    dashboard labels retired sleeves instead of dropping them.
"""
from __future__ import annotations
import sys, types

import engine
import strategies as S
import reporting

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


class FakeRequests:
    """Stands in for the `requests` module inside strategies."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get(self, *a, **k):
        self.calls += 1
        if isinstance(self.payload, Exception) and not isinstance(
                self.payload, (list, dict)):
            raise self.payload
        return FakeResp(self.payload)


print("=" * 74)
print("1. The retirement is a clean partition of the sleeve set")
print("=" * 74)
check("RETIRED ⊂ SLEEVES",
      set(engine.RETIRED_SLEEVES) <= set(engine.SLEEVES),
      str(engine.RETIRED_SLEEVES))
check("ACTIVE ∩ RETIRED = ∅",
      not set(engine.ACTIVE_SLEEVES) & set(engine.RETIRED_SLEEVES))
check("ACTIVE ∪ RETIRED = SLEEVES",
      set(engine.ACTIVE_SLEEVES) | set(engine.RETIRED_SLEEVES)
      == set(engine.SLEEVES))
check("ACTIVE preserves SLEEVES order",
      engine.ACTIVE_SLEEVES
      == [s for s in engine.SLEEVES if s not in engine.RETIRED_SLEEVES])
check("wsb and flow are the retired pair",
      set(engine.RETIRED_SLEEVES) == {"wsb", "flow"})
check("allocation still divides by the FULL sleeve count (capital retired, "
      "not redistributed)",
      engine.SLEEVE_ALLOCATION == engine.START_EQUITY / len(engine.SLEEVES),
      f"${engine.SLEEVE_ALLOCATION:.0f}")

print()
print("=" * 74)
print("2. all_signals never calls a retired sleeve's entry function")
print("=" * 74)
calls = {"wsb": 0, "news": 0, "tech": 0, "flow": 0}
orig = (S.sleeve_wsb, S.sleeve_news, S.sleeve_tech, S.sleeve_flow)


def _spy(name, ret):
    def f(*a, **k):
        calls[name] += 1
        return ret
    return f


S.sleeve_wsb = _spy("wsb", [{"underlying": "GME", "direction": "bull",
                             "thesis": "x", "score": 9.9}])
S.sleeve_news = _spy("news", [{"underlying": "AAPL", "direction": "bull",
                               "thesis": "n", "score": 3}])
S.sleeve_tech = _spy("tech", [])
S.sleeve_flow = _spy("flow", [{"underlying": "TSLA", "direction": "bear",
                               "thesis": "f", "score": 2.0}])
try:
    out = S.all_signals(None, "k", "s")
finally:
    S.sleeve_wsb, S.sleeve_news, S.sleeve_tech, S.sleeve_flow = orig

check("all four sleeve keys present (absent key would read as feed failure)",
      set(out.keys()) == set(engine.SLEEVES), str(sorted(out.keys())))
check("wsb entry fn NEVER called", calls["wsb"] == 0, f"calls={calls['wsb']}")
check("flow entry fn NEVER called", calls["flow"] == 0, f"calls={calls['flow']}")
check("news still called", calls["news"] == 1)
check("tech still called", calls["tech"] == 1)
check("retired sleeves yield [] even when their fn would signal",
      out["wsb"] == [] and out["flow"] == [])
check("active sleeve signals pass through",
      out["news"] and out["news"][0]["underlying"] == "AAPL")

print()
print("=" * 74)
print("3. all_signals survives an active sleeve raising")
print("=" * 74)
S.sleeve_news = _spy("news", None)


def _boom(*a, **k):
    raise RuntimeError("feed exploded")


S.sleeve_news = _boom
try:
    out = S.all_signals(None, "k", "s")
finally:
    S.sleeve_wsb, S.sleeve_news, S.sleeve_tech, S.sleeve_flow = orig
check("raising sleeve becomes [], run continues", out["news"] == [])

print()
print("=" * 74)
print("4. crowd_veto — fail-open on every feed failure mode")
print("=" * 74)
real_requests = S.requests
for label, payload in [
    ("network error", ConnectionError("down")),
    ("non-JSON body", ValueError("no json")),
    ("JSON but not a list", {"error": "rate limited"}),
    ("empty list", []),
    ("list of garbage rows", [None, 42, "x", {"ticker": None}]),
]:
    if isinstance(payload, Exception) and not isinstance(payload, (list, dict)):
        S.requests = FakeRequests(payload)
    elif isinstance(payload, ValueError):
        S.requests = FakeRequests(payload)
    else:
        S.requests = FakeRequests(payload)
    try:
        v = S.crowd_veto()
    except Exception as e:
        v = f"RAISED {e!r}"
    finally:
        S.requests = real_requests
    check(f"{label} -> empty veto, no raise", v == {}, str(v)[:60])

print()
print("=" * 74)
print("5. crowd_veto — filtering, capping, and shape")
print("=" * 74)
feed = ([{"ticker": f"T{i}", "no_of_comments": 200 - i, "sentiment": "Bullish"}
         for i in range(9)]                       # T0..T8: crowded, alpha? no —
        )
# tickers must be alphabetic: T0..T8 contain digits and must be dropped
feed += [
    {"ticker": "GME", "no_of_comments": 500, "sentiment": "Bullish"},
    {"ticker": "AMC", "no_of_comments": 120, "sentiment": "Bearish"},
    {"ticker": "NVDA", "no_of_comments": 49, "sentiment": "Bullish"},   # below min
    {"ticker": "TOOLONGG", "no_of_comments": 300, "sentiment": "Bullish"},
    {"ticker": "BRK.B", "no_of_comments": 300, "sentiment": "Bullish"}, # non-alpha
    {"ticker": "spy", "no_of_comments": 75, "sentiment": "Bearish"},    # lowercase ok
    {"ticker": "AAPL", "no_of_comments": "not a number", "sentiment": "?"},
]
S.requests = FakeRequests(feed)
try:
    v = S.crowd_veto()
finally:
    S.requests = real_requests
check("digit tickers dropped", not any(t[0] == "T" and t[1:].isdigit() for t in v))
check("crowded names present", "GME" in v and "AMC" in v, str(sorted(v)))
check("below-threshold name absent", "NVDA" not in v)
check(">5-char ticker dropped", "TOOLONGG" not in v)
check("non-alpha ticker dropped", "BRK.B" not in v)
check("lowercase ticker uppercased", "SPY" in v)
check("unreadable comment count dropped, not crashed", "AAPL" not in v)
check("reasons are strings mentioning the comment count",
      all(isinstance(r, str) and "comments" in r for r in v.values()))
check("veto carries NO direction field anywhere (cannot be inverted)",
      all("bull" not in r.lower().split("comments")[0] for r in v.values())
      and all(isinstance(k, str) for k in v),
      "keys are bare tickers, values are prose reasons")

S.requests = FakeRequests(
    [{"ticker": chr(65 + i // 26) + chr(65 + i % 26), "no_of_comments": 60 + i,
      "sentiment": "Bullish"} for i in range(200)])
try:
    v = S.crowd_veto()
finally:
    S.requests = real_requests
check(f"veto capped at {S.CROWD_VETO_MAX_NAMES} names (a broken feed cannot "
      f"veto the whole book)", len(v) == S.CROWD_VETO_MAX_NAMES, f"{len(v)}")
check("cap keeps the MOST crowded names",
      all(int(r.split()[0]) >= 60 + 200 - S.CROWD_VETO_MAX_NAMES
          for r in v.values()),
      "kept the top of the attention ranking")

print()
print("=" * 74)
print("6. main.py wiring — entry iterates ACTIVE_SLEEVES and applies the veto")
print("=" * 74)
src = open("main.py").read()
code = "\n".join(ln for ln in src.splitlines()
                 if not ln.lstrip().startswith("#"))
check("entry ordering built from ACTIVE_SLEEVES",
      "ordered = sorted(ACTIVE_SLEEVES" in code)
check("no entry ordering over the full SLEEVES list remains",
      "ordered = sorted(SLEEVES" not in code)
check("crowd_veto imported and fetched once per run",
      "crowd_veto" in code and "crowd = crowd_veto()" in code)
check("veto applied before any API spend on the name",
      "if und in crowd:" in code)
check("last_signals still recorded for ALL sleeves (dashboard sees retirees)",
      "for sleeve in SLEEVES:" in code)

print()
print("=" * 74)
print("7. Dashboard — retired sleeves labelled, never dropped")
print("=" * 74)
state = {"positions": {}, "closed": [], "history": [], "learning": {},
         "last_signals": {}}
rows = reporting.sleeve_rows(state)
check("all four sleeves still have a dashboard row", len(rows) == 4)
by = {r["sleeve"]: r for r in rows}
check("wsb/flow rows flagged retired",
      by["wsb"]["retired"] and by["flow"]["retired"])
check("news/tech rows not flagged",
      not by["news"]["retired"] and not by["tech"]["retired"])
h = reporting.build_dashboard(state)
check("dashboard HTML renders the retired badge", "retired" in h)
check("learning panel shows 'retired' status for retired sleeves",
      h.count("retired") >= 3, f"{h.count('retired')} mentions")
check("dashboard still renders every sleeve label",
      all(reporting.SLEEVE_LABEL[s].split(" ")[0] in h for s in engine.SLEEVES))

print()
print("=" * 74)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
print("=" * 74)
sys.exit(1 if FAIL else 0)
