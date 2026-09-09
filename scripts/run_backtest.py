"""
Run the backtest and score it against prop-firm rules.

Examples
  # smoke test, free data, no keys (60 days of SPY 5-minute bars — NOT research)
  python -m scripts.run_backtest --source yfinance --symbol SPY --instrument SPY

  # the real thing: MES continuous front month from Databento
  DATABENTO_API_KEY=... python -m scripts.run_backtest --source databento \
      --symbol MES.v.0 --instrument MES --start 2019-06-01 --end 2026-09-01

  # SPY/QQQ proxy history from your Alpaca SIP subscription
  ALPACA_API_KEY=... ALPACA_SECRET_KEY=... python -m scripts.run_backtest \
      --source alpaca --symbol SPY --instrument SPY --start 2020-01-01 --end 2026-09-01

  # out-of-sample discipline: fit nothing, but REPORT the holdout separately
  ... --holdout-from 2024-01-01

Outputs (in ./reports): trades CSV, daily CSV, summary JSON, and a markdown
report with the prop-rule Monte Carlo for every preset.
"""
from __future__ import annotations
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config as C
from data import bars as D
from strategy.noise_area import build_sessions, NoiseAreaStrategy
from strategy.orb import ORBStrategy
from strategy.lasthalf import LastHalfHourStrategy, vol_regime
from risk.engine import RiskEngine
from risk import rules as R
from backtest.simulate import Backtester, Report


def run(args) -> dict:
    inst = C.INSTRUMENTS[args.instrument.upper()]
    if args.source == "yfinance":
        bars = D.load_yfinance(args.symbol, period=args.period, interval=args.interval)
    else:
        bars = D.load(args.source, args.symbol, args.start, args.end)
    if bars.empty:
        raise SystemExit("no bars loaded")
    print(f"loaded {len(bars):,} bars {bars.index[0]} .. {bars.index[-1]}")
    ctxs = build_sessions(bars, args.bar_minutes, args.lookback, args.mult)
    print(f"{len(ctxs)} sessions; {sum(c.ready() for c in ctxs)} with bands")
    if args.strategy == "orb":
        strat = ORBStrategy(instrument=inst)
    elif args.strategy == "lasthalf":
        strat = LastHalfHourStrategy(min_move=args.min_move, confirm=not args.no_confirm,
                                     long_only=args.long_only, instrument=inst)
    else:
        strat = NoiseAreaStrategy(trail=args.trail, allow_reversal=not args.no_reversal,
                                  long_only=args.long_only, max_entries=args.max_entries,
                                  stop_range_mult=args.stop_mult, instrument=inst)
    risk = RiskEngine(inst, args.risk, args.max_contracts, args.kill, args.cap, args.max_dd,
                      max_entries=args.max_entries)
    flt = vol_regime(ctxs, lookback=args.regime_lookback, pct=args.regime_pct) if args.regime else None
    if flt is not None:
        on = sum(1 for c in ctxs if flt.get(c.day, True)); print(f"regime switch: {on}/{len(ctxs)} sessions ON")
    rep = Backtester(strat, risk, inst, args.bar_minutes, verbose=args.verbose).run(ctxs, args.max_dd, session_filter=flt)

    os.makedirs(args.out, exist_ok=True)
    tag = f"{strat.name}{'_regime' if args.regime else ''}_{inst.symbol}_{args.source}"
    rep.trades_frame().to_csv(os.path.join(args.out, f"{tag}_trades.csv"), index=False)
    pd.DataFrame([d.__dict__ for d in rep.days]).to_csv(os.path.join(args.out, f"{tag}_daily.csv"), index=False)

    summ = rep.summary()
    lines = [f"# Backtest: {strat.name} on {inst.symbol} ({args.source})", "",
             f"`{C.describe()}`", "", "## Summary (all sessions)", "```", json.dumps(summ, indent=2), "```"]
    by = rep.by_year()
    if not by.empty:
        lines += ["", "## By year", "```", by.to_string(), "```"]

    # holdout split
    if args.holdout_from:
        is_days = [d for d in rep.days if d.day < args.holdout_from]
        oos_days = [d for d in rep.days if d.day >= args.holdout_from]
        for label, days in (("IN-SAMPLE", is_days), ("HOLDOUT (out-of-sample)", oos_days)):
            sub = Report([t for t in rep.trades if (t.day < args.holdout_from) == (label == "IN-SAMPLE")], days, inst)
            lines += ["", f"## {label} — {len(days)} sessions", "```", json.dumps(sub.summary(), indent=2), "```"]

    lines += ["", "## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)",
              "", "Scale = multiple of the sizing above. P(pass) is the share of bootstrapped",
              "paths that hit the target before touching the trailing floor (intraday-touch aware).", ""]
    scores = {}
    for key in args.presets.split(","):
        rules = R.PRESETS[key]
        lines.append(f"### {R.describe(rules)}")
        lines.append("")
        lines.append("| scale | P(pass) | P(fail) | median days | top fail reason |")
        lines.append("|---|---|---|---|---|")
        for scale in (1.0, 2.0, 3.0):
            sc = rep.prop_score(rules, n=args.mc, scale=scale)
            reasons = {k: v for k, v in sc["reasons"].items() if k not in ("target", "still running")}
            top = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else "-"
            lines.append(f"| {scale:.0f}x | {sc['pass']:.1%} | {sc['fail']:.1%} | {sc['median_days']} | {top} |")
            scores[f"{key}@{scale:.0f}x"] = {"pass": sc["pass"], "fail": sc["fail"], "median_days": sc["median_days"]}
        sc1 = rep.prop_score(rules, n=args.mc, scale=1.0)
        lines.append("")
        lines.append(f"This exact history replayed once at 1x: **{sc1['this_history']}**  "
                     f"(intraday-low fraction used: {sc1['intraday_low_frac_used']})")
        lines.append("")
    # zero-edge control
    rules = R.PRESETS[args.presets.split(",")[0]]
    import statistics as st
    d = rep.daily
    if len(d) > 5:
        m = st.mean(d); demeaned = [x - m for x in d]
        mc0 = R.monte_carlo(rules, demeaned, n=args.mc)
        lines += [f"Zero-edge control (same daily P&L, mean removed) on {rules.name}: "
                  f"P(pass) = {mc0['pass']:.1%} — anything close to this number is luck, not edge.", ""]
    lines += ["## Falsification gates (from the research brief)", "",
              f"- days with intraday low <= -$700: **{summ.get('days_below_-700')}** of {summ.get('sessions')}",
              f"- days with intraday low <= -$1,000: **{summ.get('days_below_-1000')}**",
              f"- daily t-stat: **{summ.get('t_stat_daily')}** (want > 2 on the holdout, not just in-sample)",
              f"- costs as share of gross: **{(summ.get('costs_total') or 0) / max(abs(summ.get('net_pnl') or 1) + (summ.get('costs_total') or 0), 1):.0%}**",
              ""]
    path = os.path.join(args.out, f"{tag}_report.md")
    open(path, "w").write("\n".join(lines))
    json.dump({"summary": summ, "prop": scores}, open(os.path.join(args.out, f"{tag}_summary.json"), "w"), indent=2)
    print("\n".join(lines))
    print(f"\nwrote {path}")
    return {"summary": summ, "prop": scores}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="yfinance", choices=["yfinance", "alpaca", "databento"])
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--instrument", default=C.INSTRUMENT.symbol)
    ap.add_argument("--start", default="2019-06-01"); ap.add_argument("--end", default="2026-09-01")
    ap.add_argument("--period", default="60d"); ap.add_argument("--interval", default="5m")
    ap.add_argument("--strategy", default="noise_area", choices=["noise_area", "orb", "lasthalf"])
    ap.add_argument("--min-move", type=float, default=0.0005); ap.add_argument("--no-confirm", action="store_true")
    ap.add_argument("--regime", action="store_true", help="volatility-regime switch (trade only when trailing vol >= expanding median)")
    ap.add_argument("--regime-pct", type=float, default=0.5)
    ap.add_argument("--regime-lookback", type=int, default=14)
    ap.add_argument("--bar-minutes", type=int, default=C.BAR_MINUTES)
    ap.add_argument("--lookback", type=int, default=C.NOISE_LOOKBACK_DAYS)
    ap.add_argument("--mult", type=float, default=C.NOISE_MULT)
    ap.add_argument("--trail", default=C.TRAIL_MODE, choices=["band", "vwap", "both", "none"])
    ap.add_argument("--no-reversal", action="store_true"); ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--max-entries", type=int, default=C.MAX_ENTRIES_PER_DAY)
    ap.add_argument("--stop-mult", type=float, default=C.STOP_RANGE_MULT)
    ap.add_argument("--risk", type=float, default=C.RISK_PER_TRADE)
    ap.add_argument("--max-contracts", type=int, default=C.MAX_CONTRACTS)
    ap.add_argument("--kill", type=float, default=C.DAILY_KILL_LOSS)
    ap.add_argument("--cap", type=float, default=C.DAILY_PROFIT_CAP)
    ap.add_argument("--max-dd", type=float, default=2000.0)
    ap.add_argument("--presets", default="mffu_core_50k,topstep_50k,tradeify_growth_50k")
    ap.add_argument("--mc", type=int, default=5000)
    ap.add_argument("--holdout-from", default=None)
    ap.add_argument("--out", default=os.path.join(C.ROOT, "reports"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    main()
