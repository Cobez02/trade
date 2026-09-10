"""
Prop-firm rulebooks as data, and a simulator that replays a daily P&L path
against them.

A funded-account evaluation is a first-passage problem: hit +TARGET before the
equity touches a barrier that (usually) trails your own high-water mark, while
no single day exceeds a share of the target and every night you are flat.
Nothing about the signal enters that description; only the shape of your
daily P&L distribution does. So the simulator takes daily P&L numbers and
nothing else.

Two uses:
  simulate_path(rules, daily_pnl)        -> outcome of one specific path
  monte_carlo(rules, daily_pnl_sample)   -> P(pass), P(fail), days-to-result,
                                            by block-bootstrapping the sample

PRESETS are transcribed from the firms' own pages as of the research brief
(Sept 2026). They change monthly. Re-verify on the firm's account-parameters
page before you buy, then override with RuleSet(**changes).
"""
from __future__ import annotations
import random
import statistics as st
from dataclasses import dataclass, replace
from typing import Iterable, Optional


@dataclass(frozen=True)
class RuleSet:
    name: str
    account_size: float
    profit_target: float
    max_drawdown: float                 # $ from the (trailing) high-water mark
    trailing: str = "eod"               # "eod" | "intraday" | "static"
    trail_locks_at_start: bool = True   # floor stops trailing once it reaches the start balance
    daily_loss_limit: float = 0.0       # 0 = none. Treated as a session stop, not a fail,
    daily_loss_is_fail: bool = False    #   unless daily_loss_is_fail is True
    consistency_pct: float = 0.0        # best day <= pct * (target if of_target else total profit)
    consistency_of_target: bool = True
    min_trading_days: int = 0
    min_winning_days: int = 0
    winning_day_threshold: float = 0.0
    max_days: int = 0                   # 0 = no time limit
    max_contracts: int = 5              # micros
    flat_by_ct: str = "15:10"
    automation_ok_funded: bool = False
    notes: str = ""


PRESETS = {
    # Topstep Trading Combine $50K (topstep.com help center, verified Sep 2026):
    # $3,000 target, $2,000 EOD-trailing MLL (locks at start), $1,000 DLL,
    # best day <= 50% of target, 5 winning days >= $200. Bots OK via the
    # TopstepX/ProjectX API; VPN banned; bots NOT allowed on Live Funded.
    "topstep_50k": RuleSet("Topstep Combine $50K", 50_000, 3_000, 2_000, "eod", True,
                           1_000, False, 0.50, True, 0, 5, 200, 0, 5, "15:10", False,
                           "API bots OK in Combine/XFA only; VPN banned; own device"),
    # MyFundedFutures Core $50K (help center 'Fair Play' policy, Jul 2025 rev.):
    # EOD-trailing $2,000; consistency applies at payout; bots explicitly OK on
    # eval AND funded; no VPS restriction. VERIFY target/DLL on purchase day.
    "mffu_core_50k": RuleSet("MyFundedFutures Core $50K", 50_000, 3_000, 2_000, "eod", True,
                             1_000, False, 0.50, False, 0, 0, 0, 0, 5, "15:10", True,
                             "bots OK eval+funded (no HFT >200 trades/day); VPS OK; VERIFY numbers"),
    # Tradeify Growth $50K: EOD trailing that locks at start+100 once funded;
    # bots OK all stages (sole owner, no HFT); login without VPN/VPS.
    "tradeify_growth_50k": RuleSet("Tradeify Growth $50K", 50_000, 3_000, 2_000, "eod", True,
                                   0, False, 0.35, False, 0, 0, 0, 0, 5, "15:59", True,
                                   "bots OK all stages; 35% consistency on sim-funded; VERIFY"),
    # TradeDay static-drawdown accounts (VERIFY on purchase: target, floor, consistency 30% eval-only,
    # no daily loss limit, weekday overnight allowed, no weekend, own EAs allowed, news restrictions).
    "tradeday_50k_static": RuleSet("TradeDay $50K static", 50_000, 3_000, 2_000, "static", True,
                                   0, False, 0.30, False, 5, 0, 0, 0, 5, "overnight ok", True,
                                   "static floor; 30% consistency eval only; weekday overnight OK; VERIFY"),
    "tradeday_100k_static": RuleSet("TradeDay $100K static", 100_000, 6_000, 3_000, "static", True,
                                    0, False, 0.30, False, 5, 0, 0, 0, 10, "overnight ok", True,
                                    "static floor; VERIFY"),
    # Apex intraday-trailing variant — here only to show why intraday trailing
    # is brutal; Apex bans bots on funded accounts anyway.
    "apex_intraday_50k": RuleSet("Apex 4.0 $50K intraday-trail", 50_000, 3_000, 2_500, "intraday",
                                 True, 0, False, 0.50, False, 0, 0, 0, 0, 10, "15:59", False,
                                 "bots banned on funded PA"),
}


@dataclass
class Outcome:
    result: str          # "pass" | "fail" | "open"
    days: int
    balance: float
    peak: float
    best_day: float
    winning_days: int
    reason: str = ""


def simulate_path(rules: RuleSet, daily: Iterable[float], intraday_lows: Optional[Iterable[float]] = None
                  ) -> Outcome:
    """Replay one daily P&L path.

    `daily` are end-of-day P&L numbers. `intraday_lows` (optional, same length)
    are the worst intraday cumulative-P&L points of each day, which matters
    because every firm checks the drawdown in real time even when the floor
    only *moves* at end of day. Without them the simulation is optimistic.
    """
    bal = 0.0; peak = 0.0; floor = -rules.max_drawdown
    best = 0.0; wins = 0; i = 0
    lows = list(intraday_lows) if intraday_lows is not None else None
    for i, x in enumerate(daily, start=1):
        low_bal = bal + (lows[i - 1] if lows is not None else min(x, 0.0))
        # daily loss limit as a session stop: the day cannot lose more than DLL
        if rules.daily_loss_limit and x < -rules.daily_loss_limit:
            if rules.daily_loss_is_fail:
                return Outcome("fail", i, bal + x, peak, best, wins, "daily loss limit")
            x = -rules.daily_loss_limit
            low_bal = min(low_bal, bal + x)
        # intraday check against the current floor
        if low_bal <= floor:
            return Outcome("fail", i, floor, peak, best, wins, "drawdown (intraday touch)")
        bal += x
        if bal <= floor:
            return Outcome("fail", i, bal, peak, best, wins, "drawdown (close)")
        best = max(best, x)
        if x >= max(rules.winning_day_threshold, 0.0) and x > 0:
            wins += 1
        # consistency: best day must not exceed pct of target (or of total profit)
        target = rules.profit_target
        if rules.consistency_pct:
            if rules.consistency_of_target:
                target = max(target, best / rules.consistency_pct)
            elif bal > 0 and best > rules.consistency_pct * bal:
                target = max(target, best / rules.consistency_pct)
        passed = (bal >= target and i >= rules.min_trading_days and wins >= rules.min_winning_days)
        if passed:
            return Outcome("pass", i, bal, peak, best, wins, "target")
        # move the floor
        if rules.trailing == "intraday":
            peak = max(peak, bal + max(lows[i - 1], 0.0) if lows is not None else bal + max(x, 0.0))
        elif rules.trailing == "eod":
            peak = max(peak, bal)
        # static: peak stays 0
        new_floor = peak - rules.max_drawdown
        if rules.trail_locks_at_start:
            new_floor = min(new_floor, 0.0)
        floor = max(floor, new_floor)
        if rules.max_days and i >= rules.max_days:
            return Outcome("fail", i, bal, peak, best, wins, "time limit")
    return Outcome("open", i, bal, peak, best, wins, "still running")


def monte_carlo(rules: RuleSet, daily_sample: list[float], n: int = 20_000, block: int = 3,
                max_days: int = 400, seed: int = 7, scale: float = 1.0,
                intraday_low_frac: float = 0.0) -> dict:
    """Block-bootstrap the daily sample and replay each synthetic path.

    intraday_low_frac approximates the intraday low of a day as
    min(x, 0) - frac * |x| — i.e. a day that closed +$100 may have been -$40
    at some point (frac 0.4). 0 means "trust the close", which is optimistic.
    """
    rng = random.Random(seed)
    src = [x * scale for x in daily_sample]
    m = len(src)
    if m == 0:
        return {"pass": 0.0, "fail": 0.0, "open": 1.0}
    res = {"pass": 0, "fail": 0, "open": 0}; days_to = []; reasons = {}
    for _ in range(n):
        path = []; lows = []
        while len(path) < max_days:
            j = rng.randrange(m)
            for k in range(block):
                x = src[(j + k) % m]
                path.append(x)
                lows.append(min(x, 0.0) - intraday_low_frac * abs(x))
        o = simulate_path(rules, path[:max_days], lows[:max_days])
        res[o.result] += 1
        if o.result != "open":
            days_to.append(o.days)
        reasons[o.reason] = reasons.get(o.reason, 0) + 1
    out = {k: v / n for k, v in res.items()}
    out["median_days"] = st.median(days_to) if days_to else None
    out["reasons"] = {k: v / n for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])}
    out["n_days_in_sample"] = m
    out["mean_day"] = st.mean(src); out["sd_day"] = st.pstdev(src) if m > 1 else 0.0
    return out


def describe(rules: RuleSet) -> str:
    return (f"{rules.name}: target ${rules.profit_target:,.0f}, DD ${rules.max_drawdown:,.0f} "
            f"({rules.trailing}{', locks' if rules.trail_locks_at_start else ''}), "
            f"DLL ${rules.daily_loss_limit:,.0f}, consistency {rules.consistency_pct:.0%} "
            f"of {'target' if rules.consistency_of_target else 'profit'}, "
            f"flat by {rules.flat_by_ct} CT, bots on funded: {rules.automation_ok_funded}")
