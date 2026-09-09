"""Sleeve 3 backtest: intraday relative value SPY vs QQQ (proxy for ES vs NQ).

  python -m scripts.run_pair_backtest --source alpaca --start 2016-01-04 --end 2026-09-05 --holdout-from 2024-01-01
"""
from __future__ import annotations
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import config as C
from data import bars as D
from backtest.pair_simulate import PairBacktester
from backtest.simulate import Report
from strategy.noise_area import build_sessions
from strategy.lasthalf import vol_regime
from risk import rules as R


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="yfinance"); ap.add_argument("--a", default="SPY"); ap.add_argument("--b", default="QQQ")
    ap.add_argument("--start", default="2016-01-04"); ap.add_argument("--end", default="2026-09-05")
    ap.add_argument("--period", default="60d"); ap.add_argument("--interval", default="5m")
    ap.add_argument("--z-entry", type=float, default=1.5); ap.add_argument("--z-exit", type=float, default=0.5)
    ap.add_argument("--stop-sigma", type=float, default=1.0); ap.add_argument("--risk", type=float, default=C.RISK_PER_TRADE)
    ap.add_argument("--slip-a", type=float, default=0.045); ap.add_argument("--slip-b", type=float, default=0.019)
    ap.add_argument("--regime", action="store_true"); ap.add_argument("--holdout-from", default=None)
    ap.add_argument("--mc", type=int, default=5000); ap.add_argument("--out", default=os.path.join(C.ROOT, "reports"))
    args = ap.parse_args(argv)
    if args.source == "yfinance":
        ba = D.load_yfinance(args.a, period=args.period, interval=args.interval); bb = D.load_yfinance(args.b, period=args.period, interval=args.interval)
    else:
        ba = D.load(args.source, args.a, args.start, args.end); bb = D.load(args.source, args.b, args.start, args.end)
    print(f"loaded {len(ba):,} {args.a} bars, {len(bb):,} {args.b} bars")
    flt = vol_regime(build_sessions(bb, C.BAR_MINUTES, C.NOISE_LOOKBACK_DAYS, 1.0)) if args.regime else None
    bt = PairBacktester(args.z_entry, args.z_exit, args.stop_sigma, args.risk, slip_a=args.slip_a, slip_b=args.slip_b)
    rep = bt.run(ba, bb, session_filter=flt)
    os.makedirs(args.out, exist_ok=True); tag = f"pair_spread{'_regime' if args.regime else ''}_{args.a}{args.b}_{args.source}"
    pd.DataFrame([t.__dict__ for t in rep.trades]).to_csv(os.path.join(args.out, f"{tag}_trades.csv"), index=False)
    pd.DataFrame([d.__dict__ for d in rep.days]).to_csv(os.path.join(args.out, f"{tag}_daily.csv"), index=False)
    summ = rep.summary()
    lines = [f"# Pair backtest: {args.a} vs {args.b} ({args.source})", "", f"z_entry {args.z_entry}, z_exit {args.z_exit}, stop {args.stop_sigma} sigma, risk ${args.risk:.0f}, slippage {args.slip_a}/{args.slip_b} $/share/side, regime={args.regime}",
             "", "## Summary", "```", json.dumps(summ, indent=2), "```"]
    by = rep.by_year()
    if not by.empty:
        lines += ["", "## By year", "```", by.to_string(), "```"]
    if args.holdout_from:
        for label, keep in (("IN-SAMPLE", lambda d: d.day < args.holdout_from), ("HOLDOUT", lambda d: d.day >= args.holdout_from)):
            sub = type(rep)([t for t in rep.trades if keep(t)], [d for d in rep.days if keep(d)], rep.inst)
            lines += ["", f"## {label} — {len(sub.days)} sessions", "```", json.dumps(sub.summary(), indent=2), "```"]
    scores = {}
    for key in ("mffu_core_50k",):
        rules = R.PRESETS[key]; sc = rep.prop_score(rules, n=args.mc, scale=1.0)
        lines += ["", f"## Prop-rule Monte Carlo — {R.describe(rules)}", f"P(pass) 1x = **{sc['pass']:.1%}**, median days {sc['median_days']}"]
        scores[key] = sc["pass"]
        import statistics as st
        d = rep.daily
        if len(d) > 5:
            mc0 = R.monte_carlo(rules, [x - st.mean(d) for x in d], n=args.mc)
            lines += [f"Zero-edge control: P(pass) = {mc0['pass']:.1%}"]
    open(os.path.join(args.out, f"{tag}_report.md"), "w").write("\n".join(lines))
    json.dump({"summary": summ, "prop": scores}, open(os.path.join(args.out, f"{tag}_summary.json"), "w"), indent=2)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
