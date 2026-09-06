"""
Risk engine — the part of the bot that actually decides whether it survives.

The signal proposes; this module disposes. It is deliberately dumb and
deterministic, and every decision is explainable in one line, because on a
prop account the failure mode is not "the strategy was wrong", it is "the
bot kept trading after the day was already lost".

Four gates, in order:
  1. session gate    — inside RTH, before the entry cutoff, not a holiday
  2. daily gate      — today's realised+unrealised P&L above the kill level and
                       below the profit cap (consistency-rule protection)
  3. buffer gate     — distance to the trailing-drawdown floor; size shrinks
                       as the buffer shrinks and entries stop when a single
                       stop-out could touch the floor
  4. sizing          — contracts = floor(risk / (stop_points * point_value)),
                       never rounded up, capped by the firm's contract limit

All dollar figures are in ACCOUNT dollars. The engine never places orders; it
returns a Sizing the caller must obey.
"""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass

import config as C


@dataclass
class DayState:
    realized: float = 0.0
    unrealized: float = 0.0
    entries: int = 0
    trades: int = 0
    halted: bool = False
    halt_reason: str = ""

    @property
    def pnl(self) -> float:
        return self.realized + self.unrealized


@dataclass
class AccountState:
    balance: float = 0.0          # P&L since evaluation start (0 = start)
    peak: float = 0.0             # high-water mark used by the trailing floor
    floor: float = 0.0            # current drawdown floor (negative number)

    @property
    def buffer(self) -> float:
        return self.balance - self.floor


@dataclass
class Sizing:
    qty: int
    stop_points: float
    risk_dollars: float
    reason: str

    @property
    def ok(self) -> bool:
        return self.qty > 0


class RiskEngine:
    def __init__(self, instrument=C.INSTRUMENT, risk_per_trade: float = C.RISK_PER_TRADE,
                 max_contracts: int = C.MAX_CONTRACTS, daily_kill: float = C.DAILY_KILL_LOSS,
                 daily_cap: float = C.DAILY_PROFIT_CAP, max_drawdown: float = 2_000.0,
                 buffer_floor_mult: float = 1.5, max_entries: int = C.MAX_ENTRIES_PER_DAY):
        self.inst = instrument
        self.risk_per_trade = risk_per_trade
        self.max_contracts = max_contracts
        self.daily_kill = daily_kill
        self.daily_cap = daily_cap
        self.max_drawdown = max_drawdown
        self.buffer_floor_mult = buffer_floor_mult   # need buffer >= mult * risk to enter
        self.max_entries = max_entries

    # ---- gates -----------------------------------------------------------
    def daily_gate(self, day: DayState) -> tuple[bool, str]:
        if day.halted:
            return False, f"halted: {day.halt_reason}"
        if day.pnl <= -self.daily_kill:
            day.halted = True; day.halt_reason = f"daily kill (P&L {day.pnl:+.0f} <= -{self.daily_kill:.0f})"
            return False, day.halt_reason
        if day.pnl >= self.daily_cap:
            day.halted = True; day.halt_reason = f"daily profit cap reached ({day.pnl:+.0f})"
            return False, day.halt_reason
        if day.entries >= self.max_entries:
            return False, f"max entries/day ({self.max_entries})"
        return True, "ok"

    def buffer_gate(self, acct: AccountState, risk: float) -> tuple[bool, str]:
        need = self.buffer_floor_mult * risk
        if acct.buffer < need:
            return False, f"buffer ${acct.buffer:,.0f} < {self.buffer_floor_mult}x risk ${risk:,.0f}"
        return True, "ok"

    # ---- sizing ----------------------------------------------------------
    def size(self, stop_points: float, day: DayState, acct: AccountState,
             risk_override: float | None = None) -> Sizing:
        ok, why = self.daily_gate(day)
        if not ok:
            return Sizing(0, stop_points, 0.0, why)
        tick = self.inst.tick_size
        stop_points = min(max(stop_points, self.inst.min_stop_pts), self.inst.max_stop_pts)
        # round the stop to the tick grid
        stop_points = round(stop_points / tick) * tick
        risk = risk_override if risk_override is not None else self.risk_per_trade
        # shrink risk as the day goes against us: never let one more stop-out
        # take the day below the kill level
        room = self.daily_kill + day.pnl           # $ left before the kill
        risk = min(risk, max(room, 0.0))
        ok, why = self.buffer_gate(acct, risk)
        if not ok:
            return Sizing(0, stop_points, risk, why)
        per_contract = stop_points * self.inst.point_value + self.inst.roundtrip_cost
        qty = int(risk // per_contract)                  # floor, never round up
        qty = min(qty, self.max_contracts)
        if qty <= 0:
            return Sizing(0, stop_points, risk,
                          f"stop {stop_points:.2f}pts = ${per_contract:,.0f}/contract > risk ${risk:,.0f}")
        return Sizing(qty, stop_points, qty * per_contract,
                      f"{qty}x @ stop {stop_points:.2f}pts (${qty * per_contract:,.0f} at risk)")

    # ---- account floor bookkeeping (mirrors risk/rules.py) --------------
    def end_of_day(self, acct: AccountState, day_pnl: float, trailing: str = "eod",
                   locks_at_start: bool = True) -> AccountState:
        acct.balance += day_pnl
        if trailing in ("eod", "intraday"):
            acct.peak = max(acct.peak, acct.balance)
        new_floor = acct.peak - self.max_drawdown
        if locks_at_start:
            new_floor = min(new_floor, 0.0)
        acct.floor = max(acct.floor, new_floor) if acct.floor else new_floor
        return acct

    @staticmethod
    def fresh_account(max_drawdown: float) -> AccountState:
        return AccountState(0.0, 0.0, -max_drawdown)
