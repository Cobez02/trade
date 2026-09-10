"""
Sleeve 4 — the overnight drift ("night effect").

Literature: Cooper, Cliff & Gulen (2008) "Return differences between trading and
non-trading hours"; Lou, Polk & Skouras (2019) "A tug of war: overnight versus
intraday expected returns"; Kelly & Clark (2011). Most of the equity index
premium accrues between the close and the next open. A day-only prop rulebook
forbids it; a swing-capable one (TradeDay weekday overnight; Elite Trader
Funding Diamond Hands/DTF) allows it.

THE RULE: buy at the last RTH minute (14:59 CT close), sell at the next
session's first RTH minute (08:30 CT open). Weekday nights only — no Friday ->
Monday (firms forbid weekend holds). Fixed contract count per instrument; the
worst historical night, not the average, sets the size. No stop is modelled
overnight (the proxy has no overnight path; in live MNQ/M2K a resting Globex
stop can and should exist — stated as a limitation). Costs per share per side
mapped to the micro contract's round trip.
"""
from __future__ import annotations
import argparse, json, os, sys, math, statistics as st
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import config as C
from data import bars as D
from data import sessions as S
from backtest.simulate import DayResult, Report
from risk import rules as R


def overnight_daily(bars_1m_utc: pd.DataFrame, notional: float, slip_side: float, comm_side: float,
                    skip_friday: bool = True) -> tuple[list[DayResult], list[dict]]:
    rth = S.rth_only(bars_1m_utc)
    days = sorted({d for d in rth.index.date})
    per = {d: g for d, g in rth.groupby(rth.index.date)}
    out = []; trades = []
    for i in range(1, len(days)):
        d0, d1 = days[i - 1], days[i]
        if skip_friday and d0.weekday() == 4:
            out.append(DayResult(str(d1), 0.0, 0.0, 0.0, 0, "weekend skip")); continue
        b0, b1 = per[d0], per[d1]
        if len(b0) < 60 or len(b1) < 60:
            out.append(DayResult(str(d1), 0.0, 0.0, 0.0, 0, "short session")); continue
        px_in = float(b0["close"].iloc[-1]) + slip_side          # buy the close (pay the ask)
        px_out = float(b1["open"].iloc[0]) - slip_side           # sell the open (hit the bid)
        shares = notional / px_in
        pnl = shares * (px_out - px_in) - 2 * shares * comm_side
        out.append(DayResult(str(d1), round(pnl, 2), round(min(pnl, 0.0), 2), round(max(pnl, 0.0), 2), 1, ""))
        trades.append({"day": str(d1), "entry_px": round(px_in, 2), "exit_px": round(px_out, 2), "shares": round(shares, 1),
                       "ret_bp": round((px_out / px_in - 1) * 1e4, 1), "pnl": round(pnl, 2), "qty": 1, "reason_out": "open"})
    return out, trades


class OvernightReport(Report):
    def summary(self):
        d = [x.pnl for x in self.days if x.halted == ""]
        if not d:
            return {"trades": 0}
        wins = [x for x in d if x > 0]; losses = [x for x in d if x <= 0]
        m = st.mean(d); sd = st.pstdev(d)
        alld = self.daily
        import numpy as np
        eq = np.cumsum(alld); dd = eq - np.maximum.accumulate(eq)
        return {"nights": len(d), "net_pnl": round(sum(d), 2), "mean_night": round(m, 2), "sd_night": round(sd, 2),
                "win_rate": round(len(wins) / len(d), 3), "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else None,
                "sharpe_ann": round(m / sd * math.sqrt(252), 2) if sd else None,
                "t_stat": round(m / (sd / math.sqrt(len(d))), 2) if sd else None,
                "worst_night": round(min(d), 2), "best_night": round(max(d), 2), "max_drawdown": round(float(dd.min()), 2),
                "nights_below_-700": sum(1 for x in d if x <= -700), "nights_below_-1000": sum(1 for x in d if x <= -1000),
                }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="alpaca"); ap.add_argument("--symbol", default="IWM")
    ap.add_argument("--start", default="2016-01-04"); ap.add_argument("--end", default="2026-09-05")
    ap.add_argument("--notional", type=float, required=True, help="$ held overnight (e.g. 1 M2K ~ 12000, 1 MYM ~ 23000, 1 MNQ ~ 50000)")
    ap.add_argument("--slip-side", type=float, required=True, help="$ per share per side"); ap.add_argument("--comm-side", type=float, default=0.0)
    ap.add_argument("--allow-weekend", action="store_true"); ap.add_argument("--holdout-from", default="2024-01-01")
    ap.add_argument("--presets", default="tradeday_50k_static,tradeday_100k_static,mffu_core_50k"); ap.add_argument("--mc", type=int, default=4000)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    bars = D.load_yfinance(args.symbol, period="60d", interval="5m") if args.source == "yfinance" else D.load(args.source, args.symbol, args.start, args.end)
    days, trades = overnight_daily(bars, args.notional, args.slip_side, args.comm_side, skip_friday=not args.allow_weekend)
    rep = OvernightReport(trades, days, C.SPY)
    os.makedirs(args.out, exist_ok=True); tag = f"overnight_{args.symbol}_{args.source}"
    pd.DataFrame(trades).to_csv(os.path.join(args.out, f"{tag}_trades.csv"), index=False)
    pd.DataFrame([d.__dict__ for d in days]).to_csv(os.path.join(args.out, f"{tag}_daily.csv"), index=False)
    summ = rep.summary()
    lines = [f"# Overnight sleeve: {args.symbol} ({args.source}), notional ${args.notional:,.0f}, slip {args.slip_side}/share/side, weekend={'yes' if args.allow_weekend else 'no'}", "",
             "## Summary", "```", json.dumps(summ, indent=2), "```"]
    df = pd.DataFrame([d.__dict__ for d in days]); df["year"] = df["day"].str[:4]
    by = df[df.halted == ""].groupby("year")["pnl"].agg(["count", "sum", "mean", "std", "min"]).round(1)
    lines += ["", "## By year", "```", by.to_string(), "```"]
    for label, keep in (("IN-SAMPLE", lambda x: x.day < args.holdout_from), ("HOLDOUT", lambda x: x.day >= args.holdout_from)):
        sub = OvernightReport([t for t in trades if keep(pd.Series(t))], [d for d in days if keep(d)], C.SPY)
        lines += ["", f"## {label}", "```", json.dumps(sub.summary(), indent=2), "```"]
    scores = {}
    for key in args.presets.split(","):
        rules = R.PRESETS[key]; sc = rep.prop_score(rules, n=args.mc, scale=1.0)
        d = rep.daily; mc0 = R.monte_carlo(rules, [x - st.mean(d) for x in d], n=args.mc)
        lines += ["", f"## {R.describe(rules)}", f"P(pass) 1x = **{sc['pass']:.1%}** (median {sc['median_days']} days); zero-edge control {mc0['pass']:.1%}; this history: {sc['this_history']}"]
        scores[key] = sc["pass"]
    open(os.path.join(args.out, f"{tag}_report.md"), "w").write("\n".join(lines))
    json.dump({"summary": summ, "prop": scores}, open(os.path.join(args.out, f"{tag}_summary.json"), "w"), indent=2)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
