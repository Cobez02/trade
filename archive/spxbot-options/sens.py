import json
import backtest as B
# CORRECT UNITS: rich in DECIMAL vol (0.02 = 2 points). Veto stays ON (safety).
grid = [(0.02, 0.20), (0.02, 0.12), (0.01, 0.20), (0.01, 0.12), (0.01, 0.10), (0.02, 0.10)]
out = {}
for sym in ("SPY", "QQQ"):
    bars = B.stock_bars(sym)
    exps = [e for e in B.expirations(sym) if "2020-06-01" <= e <= "2026-08-31"]
    for rich, floor in grid:
        key = f"rich={rich*100:.0f}pt|floor={floor*100:.0f}%"
        B.GATE_COUNTS.clear()
        tr = B.sim_spreads(sym, bars, exps, rich_pts=rich, credit_floor=floor)
        cell = out.setdefault(key, {"trades": [], "gates": {}})
        cell["trades"] += tr
        for k, v in B.GATE_COUNTS.items():
            cell["gates"][k] = cell["gates"].get(k, 0) + v
        print(f"{sym} {key}: {len(tr)} trades", flush=True)
res = {}
for key, cell in out.items():
    sc = B.score(cell["trades"], key)
    st = B.score(B.stress_spreads(cell["trades"]), key + "|stress")
    res[key] = {"score": sc, "stress_total": st.get("total"),
                "stress_pf": st.get("profit_factor"), "gates": cell["gates"]}
json.dump(res, open("sensitivity_result.json", "w"), indent=1)
print("SENSITIVITY DONE", flush=True)
