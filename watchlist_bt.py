"""Watchlist validation study — 2026-08-01, Connor-approved.

The 6-year backtest validated the singles rules on SPY+QQQ only (a disclosed
limitation), and the TSLA cap study validated TSLA. The live bot trades 11
more names daily. This study replays the PRODUCTION sim (sim_singles,
unmodified, production cap/gates) on each remaining watchlist name.

VALIDATION, not a search: one cell per name, production config only.
Multiple-looks note for the report: with 11 names, 1-2 nominally negative
results are expected by chance even if every name is fine (and vice versa) —
flag names for Connor's decision only on strongly negative totals AND PF
well under 1 AND enough trades to mean something. No automatic config change.

Progressive output: watchlist_validation.json is rewritten after every name,
so a crash loses nothing (data layer is disk-cached besides).
"""
import json, os
import backtest as B

NAMES = ["AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN",
         "GOOGL", "NFLX", "IWM", "COIN", "PLTR"]
OUT = "watchlist_validation.json"

out = {}
if os.path.exists(OUT):
    out = json.load(open(OUT))          # resume after crash/restart

for name in NAMES:
    if name in out:
        print(f"{name}: cached result, skipping", flush=True)
        continue
    try:
        bars = B.stock_bars(name)
        exps = B.expirations(name)
        days = [d for d in bars.index if B.START <= d.date() <= B.END]
        print(f"{name}: {len(bars)} bars, {len(exps)} exps, {len(days)} days",
              flush=True)
        if len(days) < 120:
            out[name] = {"skipped": f"only {len(days)} sim days"}
            json.dump(out, open(OUT, "w"), indent=1)
            continue
        B.prefetch_days(name, exps, days, workers=4,
                        log=lambda m: print(m, flush=True))
        tr = B.sim_singles(name, bars, exps, log=lambda *a: None)
        s = B.score(tr, name)
        s["mean_pnl_pct"] = (round(sum(t["pnl_pct"] for t in tr) / len(tr), 4)
                             if tr else None)
        out[name] = {"score": s, "trades": tr}
        print(f"  -> n={s['n']} total={s['total']} pf={s['profit_factor']} "
              f"wr={s['win_rate']}", flush=True)
    except Exception as e:
        out[name] = {"error": str(e)[:120]}
        print(f"{name}: ERROR {e}", flush=True)
    json.dump(out, open(OUT, "w"), indent=1)

print("watchlist study done", flush=True)
