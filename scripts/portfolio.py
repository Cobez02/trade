"""
Portfolio test — combine the sleeves' daily P&L and score the combination.

  python -m scripts.portfolio --holdout-from 2024-01-01 --out research/<date>-portfolio \
      name1=path/to/daily.csv name2=path/to/daily.csv ...

For each sleeve: sessions, net, mean/day, Sharpe. Then the correlation matrix,
the equal-weight (1x each) combination, an in-sample/holdout split, and the
prop-rule Monte Carlo with a zero-edge control. Gates are evaluated at the
bottom exactly as pre-registered; the script does not pick the best subset.
"""
from __future__ import annotations
import argparse, json, math, os, statistics as st, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from risk import rules as R


def stats(x: pd.Series) -> dict:
    x = x.dropna()
    m = float(x.mean()); sd = float(x.std(ddof=0)) if len(x) > 1 else 0.0
    return {"sessions": int(len(x)), "net": round(float(x.sum())), "mean_day": round(m, 2), "sd_day": round(sd, 1),
            "sharpe": round(m / sd * math.sqrt(252), 2) if sd else None,
            "t": round(m / (sd / math.sqrt(len(x))), 2) if sd and len(x) > 1 else None,
            "worst_day": round(float(x.min())), "best_day": round(float(x.max()))}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sleeves", nargs="+", help="name=daily.csv")
    ap.add_argument("--holdout-from", default="2024-01-01"); ap.add_argument("--mc", type=int, default=6000)
    ap.add_argument("--preset", default="mffu_core_50k"); ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    frames = {}
    for spec in args.sleeves:
        name, path = spec.split("=", 1)
        d = pd.read_csv(path)[["day", "pnl", "low"]].set_index("day")
        frames[name] = d
    days = sorted(set().union(*[set(f.index) for f in frames.values()]))
    P = pd.DataFrame(index=days)
    L = pd.DataFrame(index=days)
    for n, f in frames.items():
        P[n] = f["pnl"].reindex(days).fillna(0.0); L[n] = f["low"].reindex(days).fillna(0.0)
    P["combined"] = P[list(frames)].sum(axis=1); L["combined"] = L[list(frames)].sum(axis=1)
    rules = R.PRESETS[args.preset]
    lines = [f"# Portfolio — {', '.join(frames)}", ""]
    lines += ["## Per sleeve and combined (all sessions)", "", "| sleeve | sessions | net | $/day | sd | Sharpe | t |", "|---|---|---|---|---|---|---|"]
    for c in list(frames) + ["combined"]:
        s = stats(P[c]); lines.append(f"| {c} | {s['sessions']} | {s['net']} | {s['mean_day']} | {s['sd_day']} | {s['sharpe']} | {s['t']} |")
    corr = P[list(frames)].corr().round(2)
    lines += ["", "## Daily P&L correlation", "```", corr.to_string(), "```"]
    ho = P.index >= args.holdout_from
    res = {}
    for label, mask in (("IN-SAMPLE", ~ho), ("HOLDOUT", ho)):
        lines += ["", f"## {label} ({int(mask.sum())} sessions)", "", "| sleeve | net | $/day | Sharpe | t |", "|---|---|---|---|---|"]
        for c in list(frames) + ["combined"]:
            s = stats(P.loc[mask, c]); res[f"{label}:{c}"] = s
            lines.append(f"| {c} | {s['net']} | {s['mean_day']} | {s['sharpe']} | {s['t']} |")
    comb = P["combined"].tolist(); lows = L["combined"].tolist()
    extra = [abs(l - min(x, 0.0)) for x, l in zip(comb, lows)]
    frac = (st.mean(extra) / st.mean([abs(x) for x in comb])) if comb and st.mean([abs(x) for x in comb]) else 0.25
    mc = R.monte_carlo(rules, comb, n=args.mc, intraday_low_frac=min(frac, 1.0))
    mc0 = R.monte_carlo(rules, [x - st.mean(comb) for x in comb], n=args.mc, intraday_low_frac=min(frac, 1.0))
    mch = R.monte_carlo(rules, P.loc[ho, "combined"].tolist(), n=args.mc, intraday_low_frac=min(frac, 1.0)) if ho.sum() > 30 else {"pass": None}
    days_700 = int((L["combined"] <= -700).sum()); days_1000 = int((L["combined"] <= -1000).sum())
    lines += ["", f"## Prop-rule Monte Carlo — {R.describe(rules)}", "",
              f"- combined, all years: **P(pass) {mc['pass']:.1%}** (median {mc['median_days']} days); zero-edge control {mc0['pass']:.1%}",
              f"- combined, holdout only: **P(pass) {mch['pass']:.1%}**" if mch["pass"] is not None else "- holdout too short for MC",
              f"- sessions with combined intraday low <= -$700: **{days_700}**; <= -$1,000: **{days_1000}**", ""]
    hs = res["HOLDOUT:combined"]
    import numpy as np
    off = corr.mask(pd.DataFrame(np.eye(len(corr), dtype=bool), index=corr.index, columns=corr.columns))
    max_corr = float(off.abs().max().max()) if len(corr) > 1 else 0.0
    gates = {
        "combined holdout Sharpe >= 1.2": (hs["sharpe"] or -9) >= 1.2,
        "combined holdout t >= 1.5": (hs["t"] or -9) >= 1.5,
        "P(pass) all-years >= 65%": mc["pass"] >= 0.65,
        "P(pass) holdout >= 55%": (mch["pass"] or 0) >= 0.55,
        "zero sessions below -$1,000": days_1000 == 0,
        "max pairwise correlation < 0.5": max_corr < 0.5,
    }
    lines += ["## Pre-registered gates", ""] + [f"- {'PASS' if v else 'FAIL'} — {k}" for k, v in gates.items()]
    lines += ["", f"**Verdict: {'PASS' if all(gates.values()) else 'FAIL'}** ({sum(gates.values())}/{len(gates)} gates)"]
    os.makedirs(args.out, exist_ok=True)
    open(os.path.join(args.out, "portfolio_report.md"), "w").write("\n".join(lines))
    P.to_csv(os.path.join(args.out, "portfolio_daily.csv"))
    json.dump({"per": res, "corr": corr.to_dict(), "mc": {"all": mc["pass"], "holdout": mch["pass"], "zero_edge": mc0["pass"]},
               "gates": gates}, open(os.path.join(args.out, "portfolio_summary.json"), "w"), indent=2)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
