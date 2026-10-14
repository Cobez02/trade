"""
Re-score existing SCOREBOARD lines under PROGRAM.md §0b (costs in bp at the Oct-2026 level). Adds the last column "bp-rule"
to every row that lacks it, rebuilding each line's subset from the CACHED tables through the same test functions (no download:
the loader serves from cache; no new scoreboard line; N per row is the row's own N). Run once: python -m research.program.rescore
"""
import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import pandas as pd, numpy as np
from research.program import run_test as RT, nights as NI

rows = open(RT.SCORE).read().splitlines()
hdr = rows.index(next(r for r in rows if r.startswith("| N ")))
if "bp-rule" not in rows[hdr]:
    rows[hdr] = rows[hdr].rstrip() + " bp-rule (Oct-2026 costs, PROGRAM.md §0b) |"
    rows[hdr + 1] = rows[hdr + 1].rstrip() + "---|"
cache = {}
def subset(tid, root):
    if tid in ("3A-1", "3A-10"):
        n = NI.nights(root); return NI.summarize(n, root)
    if tid not in cache:
        cache[tid] = RT.TESTS[tid]()
    return cache[tid][root]
out = []
for r in rows:
    if r.startswith("| ") and not r.startswith("| N ") and r.count("|") >= 12 and not r.rstrip().endswith("(net bp) |") and "bp-rule" not in r:
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        if len(cells) == 11:                        # original 11 columns: add the cell
            n = int(cells[0]); tid, root = cells[1].split("/")
            res = subset(tid, root)
            d = res["daily"]; years = max((pd.to_datetime(d.index[-1]) - pd.to_datetime(d.index[0])).days / 365.25, 0.5)
            ppy = len(d) / years
            cell = RT.bp_rule_cell(res, n, ppy)
            r = r.rstrip() + f" {cell} |"
            print(cells[0], cells[1], cell)
    out.append(r)
open(RT.SCORE, "w").write("\n".join(out) + "\n")
