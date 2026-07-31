"""TSLA cap study — Connor's question, 2026-07-31: does raising or removing
MAX_PREMIUM_PER_TRADE improve the singles sleeve ON TSLA specifically?

Method: the production sim (sim_singles, unmodified) replayed on TSLA over the
full window at three cap levels. The cap is monkeypatched per cell —
backtest.py on disk keeps the production mirror, and cell 1 IS production.
Note honestly: the sim rejects a day when the single target strike (~1.5% OTM)
exceeds the cap, while the live engine's budget-fit scan can substitute a
cheaper strike — so the $600 cell here UNDERSTATES what live cap-600 trades.
The comparison still answers the direction question: do the trades that a
higher cap ADMITS make money or lose it?
"""
import json
import backtest as B

CELLS = [("cap600_production", 600.0), ("cap900", 900.0), ("uncapped", 1e9)]

bars = B.stock_bars("TSLA")
exps = B.expirations("TSLA")
days = [d for d in bars.index if B.START <= d.date() <= B.END]
print(f"TSLA: {len(bars)} bars, {len(exps)} expirations, {len(days)} sim days",
      flush=True)
n = B.prefetch_days("TSLA", exps, days, workers=4)
print(f"prefetch complete: {n} day snapshots", flush=True)

out = {}
for name, cap in CELLS:
    B.MAX_PREMIUM_PER_TRADE = cap
    tr = B.sim_singles("TSLA", bars, exps, log=lambda *a: None)
    s = B.score(tr, name)
    s["mean_pnl_pct"] = (round(sum(t["pnl_pct"] for t in tr) / len(tr), 4)
                         if tr else None)
    out[name] = {"cap": cap, "score": s,
                 "trades": tr if name == "cap600_production" else
                 [t for t in tr]}
    print(f"{name}: n={s['n']} total={s['total']} pf={s['profit_factor']} "
          f"wr={s['win_rate']} mean_ret={s['mean_pnl_pct']}", flush=True)

json.dump(out, open("tsla_cap_result.json", "w"), indent=1)
print("done", flush=True)
