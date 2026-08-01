"""30-45 DTE credit-spreads redesign study — PRE-REGISTERED 2026-08-01.

Every rule below was fixed before any 30-45 DTE option data was examined
(this commit predates the result file). The 5-10 DTE sleeve failed its
backtest for a structural reason: a 20%-of-width credit floor is native to
the 30-45 DTE tenor of the managed-early literature, not to weeklies. This
study asks whether the sleeve works AT that native tenor. It reuses the
production functions (HAR forecast, implied vol, trend filter) and the
tested backtest.py data layer unmodified.

RULES (primary cell = production-analog):
  underlyings SPY, QQQ; window 2020-10-01..2026-07-24; one package per
  symbol at a time. Entry daily when flat:
    expiration nearest 38 DTE within [30, 45]
    short put: strike nearest spot*(1 - 1.1*EM), EM = forecast*sqrt(DTE/365)
    long put: nearest lower strike with max loss = (width-credit)*100 <= $400
    gates: implied(short) - HAR forecast >= 2.0 vol pts; uptrend only
           (trend_up); credit >= 20% of width; package RT cost <= 8% of
           credit; both legs two-sided
  Exits (managed-early triple): take at 50% credit / stop at 2.0x credit /
  time-exit at 21 DTE. Pessimistic fills: open at net bid, close at net ask;
  $0.10/contract/side x 4. Stress: flagged intraday 2x-breach re-booked at
  2.5x credit.

PRE-REGISTERED sensitivity grid (declared before running; 4 cells total,
trend veto always on): richness {1.0, 2.0} x credit floor {15%, 20%}.
The (2.0, 20%) cell is primary.

PRE-REGISTERED success bar: primary cell PF > 1 AND stress total > 0 AND
n >= 40 -> "supportive"; anything less -> not shipped. Ship/no-ship is
Connor's decision either way; nothing auto-enables.
"""
import json, math, os, sys, datetime as dt
import pandas as pd
import backtest as B
import vol as volmod
import execution
from strategies import trend_up

RICH_PRIMARY, FLOOR_PRIMARY = 0.02, 0.20      # decimals (vol pts /100, frac)
GRID = [(0.02, 0.20), (0.01, 0.20), (0.02, 0.15), (0.01, 0.15)]
DTE_LO, DTE_HI, DTE_PREF = 30, 45, 38
EM_MULT, MAX_LOSS, TAKE, STOP, TIME_DTE = 1.1, 400.0, 0.50, 2.00, 21
COST_FRAC = 0.08
FEE = B.FEE_PER_CONTRACT_SIDE


def pick_short_long(snap, spot, em_frac, iso):
    ks = B.snap_strikes(snap, "PUT")
    k_target = spot * (1 - em_frac)
    ks_short = B.nearest(ks, k_target)
    if ks_short is None:
        return None
    rs = snap.get((ks_short, "PUT"), {}).get(iso)
    if not rs:
        return None
    for kl in sorted((k for k in ks if k < ks_short), reverse=True):
        rl = snap.get((kl, "PUT"), {}).get(iso)
        if not rl:
            continue
        width = ks_short - kl
        credit_bid = rs["bid"] - rl["ask"]            # pessimistic open
        if credit_bid <= 0:
            continue
        if (width - credit_bid) * 100 <= MAX_LOSS:
            return ks_short, kl, rs, rl, width, credit_bid
    return None


def sim_spreads45(symbol, bars, exps, rich, floor, log=print):
    trades, open_pos = [], None
    days = [d for d in bars.index if B.START <= d.date() <= B.END]
    gates = {"days": 0, "no_ctx": 0, "trend": 0, "no_exp": 0, "no_snap": 0,
             "no_strikes": 0, "rich": 0, "floor": 0, "cost": 0, "entered": 0}
    for day in days:
        iso = day.date().isoformat()
        gates["days"] += 1
        if open_pos is not None:
            qs = open_pos["qs"].get(iso)
            ql = open_pos["ql"].get(iso)
            dte_left = (dt.date.fromisoformat(open_pos["exp"]) - day.date()).days
            if qs and ql:
                v_mid = qs["mid"] - ql["mid"]
                reason = None
                if v_mid <= TAKE * open_pos["credit"]:
                    reason = "take"
                elif v_mid >= STOP * open_pos["credit"]:
                    reason = "stop"
                elif dte_left <= TIME_DTE:
                    reason = "time"
                if reason:
                    buyback = qs["ask"] - ql["bid"]   # pessimistic close
                    pnl = (open_pos["credit"] - buyback) * 100 - 4 * FEE
                    hi = (qs.get("high") or qs["mid"]) - (ql.get("low") or ql["mid"])
                    flagged = hi >= STOP * open_pos["credit"] and reason != "stop"
                    trades.append({"symbol": symbol, "entry_date": open_pos["entry"],
                                   "exit_date": iso, "exit_reason": reason,
                                   "credit_fill": open_pos["credit"],
                                   "pnl": round(pnl, 2), "flagged": flagged or reason == "stop"})
                    open_pos = None
            elif dte_left <= 0:
                pnl = open_pos["credit"] * 100 - 4 * FEE   # expired: keep credit
                trades.append({"symbol": symbol, "entry_date": open_pos["entry"],
                               "exit_date": iso, "exit_reason": "expired-no-quote",
                               "credit_fill": open_pos["credit"],
                               "pnl": round(pnl, 2), "flagged": True})
                open_pos = None
        if open_pos is not None:
            continue
        ctx = B.day_context(bars, day)
        if ctx is None:
            gates["no_ctx"] += 1
            continue
        spot, fc, closes = ctx
        if not trend_up(pd.Series(closes)):
            gates["trend"] += 1
            continue
        pe = B.pick_expiration(exps, day.date(), DTE_LO, DTE_HI, prefer=DTE_PREF)
        if pe is None:
            gates["no_exp"] += 1
            continue
        exp, dte_e = pe
        snap = B.day_snapshot(symbol, exp, iso)
        if not snap:
            gates["no_snap"] += 1
            continue
        em_frac = EM_MULT * fc * math.sqrt(dte_e / 365.0)
        pick = pick_short_long(snap, spot, em_frac, iso)
        if pick is None:
            gates["no_strikes"] += 1
            continue
        ks, kl, rs, rl, width, credit = pick
        iv = execution.implied_vol(rs["mid"], spot, ks, dte_e / 365.0, is_call=False)
        if iv is None or (iv - fc) < rich:
            gates["rich"] += 1
            continue
        if credit < floor * width:
            gates["floor"] += 1
            continue
        credit_mid = rs["mid"] - rl["mid"]
        rt = ((credit_mid - credit) + ((rs["ask"] - rl["bid"]) - credit_mid))
        if credit > 0 and rt / credit > COST_FRAC:
            gates["cost"] += 1
            continue
        gates["entered"] += 1
        open_pos = {"exp": exp, "entry": iso, "credit": credit,
                    "qs": B.contract_series(symbol, exp, ks, "PUT", iso),
                    "ql": B.contract_series(symbol, exp, kl, "PUT", iso)}
    return trades, gates


if __name__ == "__main__":
    out = {"grid": []}
    for sym in ("SPY", "QQQ"):
        bars = B.stock_bars(sym)
        exps = B.expirations(sym)
        days = [d for d in bars.index if B.START <= d.date() <= B.END]
        # warm the cache for the 30-45 DTE picks (distinct from the 3-12 set)
        keys = []
        for day in days:
            pe = B.pick_expiration(exps, day.date(), DTE_LO, DTE_HI, prefer=DTE_PREF)
            if pe:
                keys.append((pe[0], day.date().isoformat()))
        print(f"{sym}: {len(keys)} snapshot days to warm", flush=True)
        done = [0]
        from concurrent.futures import ThreadPoolExecutor
        def one(k):
            B.day_snapshot(sym, k[0], k[1])
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"  prefetch {sym}45: {done[0]}/{len(keys)}", flush=True)
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(one, keys))
    for rich, floor in GRID:
        cell = {"rich": rich, "floor": floor, "cells": {}}
        allt = []
        for sym in ("SPY", "QQQ"):
            tr, gates = sim_spreads45(sym, B.stock_bars(sym), B.expirations(sym),
                                      rich, floor, log=lambda *a: None)
            allt += tr
            cell["cells"][sym] = {"gates": gates}
        s = B.score(allt, f"45dte r{rich} f{floor}")
        st = B.stress_spreads(allt)
        s["stress_total"] = round(sum(t["pnl"] for t in st), 2)
        cell["score"] = s
        cell["trades"] = allt if (rich, floor) == (RICH_PRIMARY, FLOOR_PRIMARY) else len(allt)
        out["grid"].append(cell)
        print(f"cell rich={rich} floor={floor}: n={s['n']} total={s['total']} "
              f"pf={s['profit_factor']} stress={s['stress_total']}", flush=True)
    json.dump(out, open("spreads45_result.json", "w"), indent=1)
    print("spreads45 study done", flush=True)
