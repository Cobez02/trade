"""
Session runner — runs ONE trading session then exits. A scheduler (launchd,
cron, systemd timer, or the GitHub Actions research workflow) starts it each
morning; the exchange clock, not the scheduler, decides when things happen.

    python -m live.runner --broker sim --dry-run --day 2026-08-20 --source yfinance ...
    python -m live.runner --broker alpaca --symbol SPY --instrument SPY
    python -m live.runner --broker projectx --symbol MESZ6 --instrument MES --feed projectx
    python -m live.runner --broker tradovate --symbol MESZ6 --instrument MES --feed databento

Each minute:
  1. pull today's 1-minute bars, rebuild today's SessionContext on top of the
     cached lookback (so the bands are exactly what the backtester would have
     computed)
  2. watchdog: if we hold a position and no protective stop is resting at the
     broker, rest one NOW
  3. kill-switch: day P&L <= -DAILY_KILL -> flatten, halt
  4. flatten time -> flatten, halt, write the day's summary, exit
  5. on a decision-bar boundary -> strategy -> risk engine -> orders

The runner never trusts its own memory of a position: it asks the broker
every minute (the options bot's "rebuild from the broker" principle). State
on disk is only entries-today / halted, for restart safety.
"""
from __future__ import annotations
import argparse, json, os, sys, time
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config as C
from data import sessions as S
from data import bars as D
from strategy.noise_area import build_sessions, NoiseAreaStrategy, SessionContext
from strategy.orb import ORBStrategy
from risk.engine import RiskEngine, DayState, AccountState
from broker.base import Broker, SimBroker


def log(msg: str):
    print(f"[{S.now_ct().strftime('%H:%M:%S')}] {msg}", flush=True)


class Runner:
    def __init__(self, broker: Broker, strategy, risk: RiskEngine, symbol: str, instrument,
                 history_1m: pd.DataFrame, day: dt.date, state_path: str = C.STATE_PATH,
                 bar_minutes: int = C.BAR_MINUTES, acct: AccountState | None = None,
                 feed_bars=None, dry_run: bool = False, until: str | None = None):
        self.b = broker; self.strat = strategy; self.risk = risk
        self.until = (dt.datetime.combine(day, dt.time(*map(int, until.split(":"))), S.TZ) if until else None)
        self.symbol = symbol; self.inst = instrument
        self.history = history_1m           # prior sessions' 1-min bars (UTC), for the lookback
        self.day = day; self.bar_minutes = bar_minutes
        self.state_path = state_path
        self.acct = acct or RiskEngine.fresh_account(risk.max_drawdown)
        self.feed_bars = feed_bars           # optional callable -> today's 1m bars (UTC)
        self.dry = dry_run
        self.o, self.c, self.flat, self.noent = S.session_bounds(day)
        self.state = self._load_state()
        self.dayst = DayState(entries=self.state.get("entries", 0), halted=self.state.get("halted", False),
                              halt_reason=self.state.get("halt_reason", ""))
        self.last_slot_done = self.state.get("last_slot", -1)
        self.entry_stop_pts = self.state.get("stop_pts", 0.0)
        self.start_day_pnl = self.state.get("start_day_pnl")   # set once per day, survives job handover

    # ---- state -----------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            s = json.load(open(self.state_path))
            if s.get("day") == str(self.day) and s.get("symbol") == self.symbol:
                return s
        except Exception:
            pass
        return {"day": str(self.day), "symbol": self.symbol}

    def _save_state(self):
        self.state.update({"day": str(self.day), "symbol": self.symbol, "entries": self.dayst.entries,
                           "halted": self.dayst.halted, "halt_reason": self.dayst.halt_reason,
                           "last_slot": self.last_slot_done, "stop_pts": self.entry_stop_pts,
                           "start_day_pnl": self.start_day_pnl, "updated": S.now_ct().isoformat()})
        if not self.dry:
            json.dump(self.state, open(self.state_path, "w"), indent=2)

    # ---- context ----------------------------------------------------------
    def _today_bars(self) -> pd.DataFrame:
        b = self.feed_bars() if self.feed_bars else self.b.bars_today(self.symbol)
        return b

    def _context(self, today: pd.DataFrame) -> SessionContext | None:
        if today is None or len(today) < 5:
            return None
        allb = pd.concat([self.history, today]).sort_index()
        allb = allb[~allb.index.duplicated(keep="last")]
        ctxs = build_sessions(allb, self.bar_minutes, C.NOISE_LOOKBACK_DAYS, C.NOISE_MULT)
        return ctxs[-1] if ctxs and ctxs[-1].day == self.day else None

    # ---- orders -------------------------------------------------------------
    def _day_pnl(self) -> float:
        a = self.b.account()
        if self.start_day_pnl is None:
            self.start_day_pnl = a["day_pnl"] if self.b.name != "sim" else 0.0
            self.state["start_day_pnl"] = self.start_day_pnl; self._save_state()
        return a["day_pnl"] - (self.start_day_pnl or 0.0)

    def _flatten(self, why: str):
        p = self.b.position(self.symbol); q = p.qty
        if q:
            r = self.b.flatten(self.symbol)
            log(f"FLATTEN {q:+d} {self.symbol}: {why} -> {r.ok} {r.detail}")
        else:
            self.b.cancel_all(self.symbol)

    def _enter(self, side: int, stop_pts: float, why: str):
        self.dayst.realized = self._day_pnl(); self.dayst.unrealized = 0.0
        sz = self.risk.size(stop_pts, self.dayst, self.acct)
        if not sz.ok:
            log(f"skip entry ({why}): {sz.reason}"); self._save_state(); return
        r = self.b.place_market(self.symbol, side, sz.qty, f"{self.strat.name}-{side:+d}")
        log(f"ENTER {side:+d}x{sz.qty} {self.symbol} ({why}) -> {r.ok} {r.detail}")
        if not r.ok:
            return
        self.dayst.entries += 1; self.entry_stop_pts = sz.stop_points
        self._save_state()
        self._ensure_stop(force=True)

    def _ensure_stop(self, force: bool = False):
        """Watchdog: a position must always have a resting stop behind it."""
        p = self.b.position(self.symbol)
        if not p.qty:
            return
        if not force and self.b.resting_stop_qty(self.symbol) == abs(p.qty):
            return                                   # floor is in place; do nothing
        self.b.cancel_all(self.symbol)               # stale / partial / missing -> rebuild
        side = -1 if p.qty > 0 else 1
        pts = self.entry_stop_pts or self.inst.min_stop_pts
        ref = p.avg_px or self.b.last_price(self.symbol)
        stop_px = round((ref - (1 if p.qty > 0 else -1) * pts) / self.inst.tick_size) * self.inst.tick_size
        r = self.b.place_stop(self.symbol, side, abs(p.qty), stop_px, self.strat.name)
        log(f"stop {abs(p.qty)}x @ {stop_px:.2f} ({pts:.2f} pts) -> {r.ok} {r.detail}")

    # ---- one minute -----------------------------------------------------------
    def step(self, now: dt.datetime) -> bool:
        """Returns False when the session is over."""
        if now < self.o:
            return True
        today = self._today_bars()
        p = self.b.position(self.symbol)
        day_pnl = self._day_pnl()
        self.dayst.realized = day_pnl; self.dayst.unrealized = 0.0
        # kill-switch
        if p.qty and day_pnl <= -self.risk.daily_kill:
            self._flatten(f"daily kill: {day_pnl:+.0f}")
            self.dayst.halted = True; self.dayst.halt_reason = "daily kill"; self._save_state()
        # flatten time
        if now >= self.flat:
            self._flatten("session flatten")
            self._end_of_day(day_pnl)
            return False
        # watchdog
        if p.qty:
            self._ensure_stop()
        # decision boundary?
        elapsed = (now - self.o).total_seconds() / 60.0
        slot_done = int(elapsed // self.bar_minutes) - 1     # the bar that just completed
        if slot_done >= 0 and slot_done > self.last_slot_done and elapsed % self.bar_minutes < 1.5:
            ctx = self._context(today)
            self.last_slot_done = slot_done
            if ctx is None or not ctx.ready():
                log(f"slot {slot_done}: no context/bands yet"); self._save_state(); return True
            bar_end = self.o + dt.timedelta(minutes=self.bar_minutes * (slot_done + 1))
            pos = 1 if p.qty > 0 else (-1 if p.qty < 0 else 0)
            d = self.strat.on_bar_close(ctx, slot_done, pos, self.dayst.entries, pd.Timestamp(bar_end))
            log(f"slot {slot_done} close {float(ctx.bars_n['close'].iloc[slot_done]):.2f} "
                f"band [{ctx.lower[slot_done]:.2f}, {ctx.upper[slot_done]:.2f}] pos {pos:+d} -> {d.target:+d} ({d.reason})")
            if d.target != pos:
                if pos != 0:
                    self._flatten(d.reason)
                if d.target != 0 and now < self.noent and not self.dayst.halted:
                    self._enter(d.target, d.stop_points, d.reason)
            self._save_state()
        return True

    def _end_of_day(self, day_pnl: float):
        self.acct = self.risk.end_of_day(self.acct, day_pnl)
        summ = {"day": str(self.day), "symbol": self.symbol, "day_pnl": round(day_pnl, 2),
                "entries": self.dayst.entries, "halted": self.dayst.halt_reason,
                "acct_balance": round(self.acct.balance, 2), "floor": round(self.acct.floor, 2),
                "buffer": round(self.acct.buffer, 2)}
        log(f"END OF DAY {summ}")
        self.state["eod"] = summ; self._save_state()
        if not self.dry:
            with open(os.path.join(C.ROOT, "days.jsonl"), "a") as f:
                f.write(json.dumps(summ) + "\n")

    # ---- loops --------------------------------------------------------------------
    def run_live(self):
        log(f"session {self.day}: open {self.o.strftime('%H:%M')} flatten {self.flat.strftime('%H:%M')} CT | {C.describe()}")
        while True:
            now = S.now_ct()
            if now.date() != self.day:
                log("date rolled; exiting"); return
            if self.until and now >= self.until:
                p = self.b.position(self.symbol)
                self._save_state()
                log(f"handover at {self.until.strftime('%H:%M')} CT: exiting with position {p.qty:+d} "
                    f"(resting stop {self.b.resting_stop_qty(self.symbol)}) for the next job")
                return
            if not self.step(now):
                return
            # sleep to 2 s past the next minute boundary
            nxt = (now + dt.timedelta(minutes=1)).replace(second=2, microsecond=0)
            time.sleep(max(1.0, (nxt - S.now_ct()).total_seconds()))

    def run_dry(self, sim: SimBroker, day_bars_1m: pd.DataFrame):
        """Replay one historical day minute by minute through the SimBroker."""
        sim.feed(self.symbol, day_bars_1m)
        self.feed_bars = lambda: sim.bars_today(self.symbol)
        while sim.advance(self.symbol):
            ts = sim.bars[self.symbol].index[-1]
            now = ts.tz_convert(C.SESSION_TZ).to_pydatetime() + dt.timedelta(minutes=1, seconds=2)
            if not self.step(now):
                break
        else:
            self._flatten("data ended"); self._end_of_day(self._day_pnl())
        return self.b.account()


# ---------------------------------------------------------------------------
def build(args):
    inst = C.INSTRUMENTS[args.instrument.upper()]
    strat = ORBStrategy(instrument=inst) if args.strategy == "orb" else NoiseAreaStrategy(instrument=inst)
    risk = RiskEngine(inst, args.risk, args.max_contracts, args.kill, args.cap, args.max_dd)
    if args.broker == "sim":
        broker = SimBroker(inst)
    elif args.broker == "alpaca":
        from broker.alpaca_proxy import AlpacaProxyBroker
        broker = AlpacaProxyBroker()
    elif args.broker == "tradovate":
        from broker.tradovate import TradovateBroker
        broker = TradovateBroker()
    elif args.broker == "projectx":
        from broker.projectx import ProjectXBroker
        broker = ProjectXBroker()
    else:
        raise SystemExit("unknown broker")
    return inst, strat, risk, broker


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default="sim", choices=["sim", "alpaca", "tradovate", "projectx"])
    ap.add_argument("--symbol", default="SPY"); ap.add_argument("--instrument", default="SPY")
    ap.add_argument("--strategy", default="noise_area", choices=["noise_area", "orb"])
    ap.add_argument("--source", default="yfinance", choices=["yfinance", "alpaca", "databento"])
    ap.add_argument("--hist-symbol", default=None, help="research symbol for the lookback (default: --symbol)")
    ap.add_argument("--start", default=None); ap.add_argument("--end", default=None)
    ap.add_argument("--day", default=None, help="session date (default today); required for --dry-run")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--risk", type=float, default=C.RISK_PER_TRADE)
    ap.add_argument("--max-contracts", type=int, default=C.MAX_CONTRACTS)
    ap.add_argument("--kill", type=float, default=C.DAILY_KILL_LOSS)
    ap.add_argument("--cap", type=float, default=C.DAILY_PROFIT_CAP)
    ap.add_argument("--max-dd", type=float, default=2000.0)
    ap.add_argument("--until", default=None, help="HH:MM CT: exit cleanly (no flatten) for a job handover")
    args = ap.parse_args(argv)

    inst, strat, risk, broker = build(args)
    day = dt.date.fromisoformat(args.day) if args.day else S.now_ct().date()
    if not S.is_trading_day(day):
        log(f"{day} is not a trading day; nothing to do"); return
    hist_sym = args.hist_symbol or args.symbol
    if args.source == "yfinance":
        hist = D.load_yfinance(hist_sym, period="60d", interval="5m")
    else:
        end = args.end or str(day)
        start = args.start or str(day - dt.timedelta(days=45))
        hist = D.load(args.source, hist_sym, start, end)
    day_idx = hist.index.tz_convert(C.SESSION_TZ).date
    today_bars = hist[day_idx == day]
    lookback = hist[day_idx < day]
    r = Runner(broker, strat, risk, args.symbol, inst, lookback, day, dry_run=args.dry_run, until=args.until)
    if args.dry_run:
        if today_bars.empty:
            raise SystemExit(f"no bars for {day} in the history source")
        acct = r.run_dry(broker, S.rth_only(today_bars).tz_convert("UTC"))
        log(f"dry run done: {acct}")
        for line in broker.log:
            print("   ", line)
    else:
        r.run_live()


if __name__ == "__main__":
    main()
