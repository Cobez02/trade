"""Insider cluster-buy study: 60-90 DTE call debit spreads on cluster events.

Pre-registered rules (set before results were seen; no tuning pass):
  entry: next trading day after the cluster completes; long call nearest ATM,
         short call nearest +8% above; net debit at the ASK side (pessimistic);
         gates: both legs two-sided, package RT cost <= 8% of debit,
         debit <= $600 cap.
  exits: value >= 1.75x debit (take), value <= 0.50x debit (stop), or 21 DTE.
         Pessimistic close at net bid. Fees $0.10/contract/side.
"""
import json, math, os, sys, datetime as dt, urllib.request
import pandas as pd
import backtest as B


def _load_env():
    """stock_bars needs Alpaca keys; don't depend on the launching shell."""
    p = "/home/claude/spxbot/config.env"
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


def terminal_alive() -> bool:
    """Direct, cache-BYPASSING probe of the local theta terminal. theta_csv's
    disk cache would happily answer this from disk while the terminal is dead."""
    try:
        with urllib.request.urlopen(
                f"{B.THETA}/option/list/expirations?symbol=SPY", timeout=10) as r:
            return bool(r.read(64))
    except Exception:
        return False


EVENTS = json.load(open("/home/claude/sec/insider_events.json"))
TP_MULT, SL_MULT, TIME_DTE = 1.75, 0.50, 21
DTE_LO, DTE_HI, DTE_PREF = 60, 90, 75
SHORT_OTM = 1.08
COST_FRAC, DEBIT_CAP = 0.08, 600.0

funnel = {"events": len(EVENTS), "in_window": 0, "has_option_chain": 0,
          "has_bars": 0, "quoted": 0, "gates_passed": 0, "probe_errors": 0}

def bars_for(sym):
    try:
        return B.stock_bars(sym)
    except Exception:
        return None

def run(cost_frac=COST_FRAC, debit_cap=DEBIT_CAP, mid_fills=False, quiet=False):
    """Defaults reproduce the pre-registered run exactly. Non-default args are
    the DISCLOSED post-hoc sensitivity (see INSIDER_REPORT.md): cost_frac=None
    disables the cost gate; mid_fills=True books both sides at mid with no
    fees — a pure-signal diagnostic, not a tradeable configuration."""
    fn = {"events": len(EVENTS), "in_window": 0, "has_option_chain": 0,
          "has_bars": 0, "quoted": 0, "gates_passed": 0, "probe_errors": 0}
    costs = []          # RT cost fraction of every QUOTED candidate
    # 1) unique-ticker option-chain probe (cached; one request per ticker)
    tickers = sorted({e["ticker"] for e in EVENTS})
    has_chain = {}
    for i, t in enumerate(tickers):
        try:
            ex = B.expirations(t)
            has_chain[t] = bool(ex)     # "No data found" -> [] -> False: REAL no-chain
        except Exception:
            # An exception is NOT evidence about the ticker. If the terminal is
            # down, abort loudly — completed probes are disk-cached, relaunch
            # resumes here. If the terminal is up, count an isolated error.
            if not terminal_alive():
                print(f"ABORT: theta terminal unreachable at probe {i+1}/"
                      f"{len(tickers)} ({t}). Restart terminal and relaunch; "
                      f"cache resumes from here.", flush=True)
                sys.exit(3)
            fn["probe_errors"] += 1
        if (i + 1) % 250 == 0:
            if not quiet: print(f"  chain probe {i+1}/{len(tickers)}", flush=True)
    n_chain = sum(1 for v in has_chain.values() if v)
    if not quiet: print(f"  probe done: {n_chain}/{len(tickers)} tickers have option chains "
          f"({fn['probe_errors']} probe errors)", flush=True)
    trades = []
    empty_snaps = 0
    for n_ev, e in enumerate(EVENTS):
        if (n_ev + 1) % 250 == 0:
            if not quiet: print(f"  events {n_ev+1}/{len(EVENTS)} funnel={fn} "
                  f"trades={len(trades)}", flush=True)
            if not terminal_alive():
                print("ABORT: theta terminal died mid-run; relaunch resumes "
                      "from cache.", flush=True)
                sys.exit(3)
        d = dt.date.fromisoformat(e["date"])
        if not (dt.date(2020, 10, 1) <= d <= dt.date(2026, 4, 15)):
            continue
        fn["in_window"] += 1
        t = e["ticker"]
        if not has_chain.get(t):
            continue
        fn["has_option_chain"] += 1
        bars = bars_for(t)
        if bars is None or len(bars) < 30:
            continue
        idx = bars.index[bars.index.date > d]
        if not len(idx):
            continue
        day = idx[0]; iso = day.date().isoformat()
        fn["has_bars"] += 1
        spot = float(bars.loc[day, "close"])
        if spot < 5:
            continue
        try:
            exps = B.expirations(t)
        except Exception:
            continue
        pe = B.pick_expiration(exps, day.date(), DTE_LO, DTE_HI, prefer=DTE_PREF)
        if pe is None:
            continue
        exp, dte_e = pe
        snap = B.day_snapshot(t, exp, iso)
        if not snap:
            empty_snaps += 1
            if empty_snaps >= 25 and not terminal_alive():
                print("ABORT: 25 consecutive empty snapshots and terminal dead; "
                      "relaunch resumes from cache.", flush=True)
                sys.exit(3)
            continue
        empty_snaps = 0
        ks = B.snap_strikes(snap, "CALL")
        kl = B.nearest(ks, spot)
        ksh = B.nearest([k for k in ks if k > (kl or 0)], spot * SHORT_OTM)
        if kl is None or ksh is None or ksh <= kl:
            continue
        rl = snap.get((kl, "CALL"), {}).get(iso)
        rs = snap.get((ksh, "CALL"), {}).get(iso)
        if not rl or not rs:
            continue
        fn["quoted"] += 1
        debit_mid = rl["mid"] - rs["mid"]
        net_bid = rl["bid"] - rs["ask"]
        pess_fill = rl["ask"] - rs["bid"]               # pessimistic open
        debit_fill = debit_mid if mid_fills else pess_fill
        if debit_mid <= 0 or debit_fill <= 0 or pess_fill <= 0:
            continue
        costs.append((pess_fill - net_bid) / debit_mid)
        if cost_frac is not None and (pess_fill - net_bid) / debit_mid > cost_frac:
            continue
        if debit_fill * 100 > debit_cap:
            continue
        fn["gates_passed"] += 1
        ql = B.contract_series(t, exp, kl, "CALL", iso)
        qs = B.contract_series(t, exp, ksh, "CALL", iso)
        exit_pnl, exit_date, exit_reason, flagged = None, None, None, False
        for dd in sorted(ql):
            if dd <= iso or dd not in qs:
                continue
            v_mid = ql[dd]["mid"] - qs[dd]["mid"]
            dte_left = (dt.date.fromisoformat(exp) - dt.date.fromisoformat(dd)).days
            reason = None
            if v_mid >= TP_MULT * debit_fill:
                reason = f"take +{(v_mid/debit_fill-1)*100:.0f}%"
            elif v_mid <= SL_MULT * debit_fill:
                reason = f"stop {(v_mid/debit_fill-1)*100:.0f}%"
            elif dte_left <= TIME_DTE:
                reason = f"time ({dte_left} DTE)"
            if reason:
                close_fill = (ql[dd]["mid"] - qs[dd]["mid"]) if mid_fills \
                    else (ql[dd]["bid"] - qs[dd]["ask"])         # pessimistic close
                fees = 0.0 if mid_fills else 4 * B.FEE_PER_CONTRACT_SIDE
                exit_pnl = (close_fill - debit_fill) * 100 - fees
                exit_date, exit_reason = dd, reason
                break
        if exit_pnl is None:
            last = max((x for x in ql if x in qs), default=None)
            if last is None:
                continue
            close_fill = (ql[last]["mid"] - qs[last]["mid"]) if mid_fills \
                else (ql[last]["bid"] - qs[last]["ask"])
            fees = 0.0 if mid_fills else 4 * B.FEE_PER_CONTRACT_SIDE
            exit_pnl = (close_fill - debit_fill) * 100 - fees
            exit_date, exit_reason, flagged = last, "series-end", True
        trades.append({"ticker": t, "event": e["date"], "entry_date": iso,
                       "exit_date": exit_date, "exit_reason": exit_reason,
                       "kl": kl, "ks": ksh, "debit": round(debit_fill, 3),
                       "pnl": round(exit_pnl, 2), "flagged": flagged,
                       "n_insiders": e["n_insiders"], "value": e["value"]})
    return trades, fn, costs

if __name__ == "__main__":
    if "ALPACA_API_KEY" not in os.environ:
        print("ABORT: ALPACA_API_KEY missing (config.env not readable?)", flush=True)
        sys.exit(2)
    if not terminal_alive():
        print("ABORT: theta terminal not responding at startup", flush=True)
        sys.exit(2)
    print(f"insider study start: {len(EVENTS)} events, "
          f"{len({e['ticker'] for e in EVENTS})} unique tickers", flush=True)
    tr, funnel, costs = run()
    out = {"funnel": funnel, "headline": B.score(tr, "insider debit spreads"),
           "trades": tr, "data_errors": len(B.DATA_ERRORS),
           "data_error_sample": B.DATA_ERRORS[:10],
           "cost_frac_quartiles": ([round(q, 3) for q in
            __import__("numpy").percentile(costs, [25, 50, 75])] if costs else None)}
    json.dump(out, open("insider_result.json", "w"), indent=1)
    print("funnel:", funnel)
    print("data_errors:", len(B.DATA_ERRORS), flush=True)
    print(json.dumps(out["headline"], indent=1))
