"""Unit checks for the 30-45 DTE redesign study harness. Run: python3 test_spreads45.py"""
from __future__ import annotations
import sys

import spreads45_bt as S

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")

ISO = "2026-01-02"

def snap(rows):
    # rows: {(strike, 'PUT'): (bid, ask)}
    return {(k, "PUT"): {ISO: {"bid": b, "ask": a, "mid": (a + b) / 2}}
            for k, (b, a) in rows.items()}

print("=" * 70)
print("pick_short_long")
print("=" * 70)
s = snap({690.0: (2.00, 2.10), 687.0: (1.20, 1.30), 680.0: (0.60, 0.70)})
r = S.pick_short_long(s, 700.0, 0.015, ISO)   # target 700*(1-.015)=689.5 -> 690
check("short is nearest to EM target", r is not None and r[0] == 690.0)
check("long is nearest lower affordable strike", r[1] == 687.0)
check("credit is pessimistic (short bid - long ask)",
      abs(r[5] - (2.00 - 1.30)) < 1e-9, str(r[5]))
check("max loss bound respected",
      (r[4] - r[5]) * 100 <= S.MAX_LOSS)

s2 = snap({690.0: (2.00, 2.10)})
check("no lower strike -> None", S.pick_short_long(s2, 700.0, 0.015, ISO) is None)

s3 = snap({690.0: (2.00, 2.10), 600.0: (0.05, 0.10)})
r3 = S.pick_short_long(s3, 700.0, 0.015, ISO)
check("width busting the loss cap -> None (90-wide > $400 max loss)", r3 is None)

check("empty snap -> None", S.pick_short_long({}, 700.0, 0.015, ISO) is None)

print()
print("=" * 70)
print("pre-registration invariants")
print("=" * 70)
check("primary cell is first in grid",
      S.GRID[0] == (S.RICH_PRIMARY, S.FLOOR_PRIMARY))
check("grid is the declared 4 cells", len(S.GRID) == 4)
check("thresholds are decimals (units-bug guard)",
      S.RICH_PRIMARY < 0.1 and S.FLOOR_PRIMARY < 1.0)
check("managed-early triple", (S.TAKE, S.STOP, S.TIME_DTE) == (0.50, 2.00, 21))
check("tenor window", (S.DTE_LO, S.DTE_HI, S.DTE_PREF) == (30, 45, 38))

print()
print("=" * 70)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
print("=" * 70)
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
