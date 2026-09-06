"""DISCLOSED POST-HOC sensitivity for the insider study. The pre-registered
run (insider_result.json) stands as the primary result; these cells exist to
separate 'the signal is dead' from 'the market's friction makes it untradeable'.
Cell D books both sides at MID with no fees — a diagnostic ceiling, NOT a
tradeable configuration, and it is labeled that way in the report."""
import json
from collections import Counter
import insider_bt as I
import backtest as B

CELLS = [
    ("C_cost_off_pess", dict(cost_frac=None)),                # max sample, real fills
    ("A_cost15_pess",  dict(cost_frac=0.15)),                 # mild loosening
    ("B_cost25_pess",  dict(cost_frac=0.25)),                 # 3x the shipped gate
    ("D_mid_no_fees",  dict(cost_frac=None, mid_fills=True)), # pure-signal ceiling
]

out = {}
for name, kw in CELLS:
    tr, fn, costs = I.run(quiet=True, **kw)
    s = B.score(tr, name)
    s["exit_mix"] = dict(Counter(t["exit_reason"].split()[0].rstrip("+-0123456789%(")
                                 for t in tr))
    s["flagged_series_end"] = sum(1 for t in tr if t["flagged"])
    out[name] = {"params": {k: v for k, v in kw.items()}, "score": s}
    print(name, "n=%(n)s total=%(total)s pf=%(profit_factor)s wr=%(win_rate)s" % s,
          flush=True)

json.dump(out, open("insider_sensitivity.json", "w"), indent=1)
print("sensitivity done", flush=True)
