"""
Globex overnight sleeve on real micro futures (ProjectX / TopstepX, or Tradovate).

    python -m live.overnight_futures --broker projectx --root MES --action enter    # waits for 17:02 CT, buys 1, rests a 1% stop
    python -m live.overnight_futures --broker projectx --root MES --action exit     # waits for 08:30 CT, flattens
    python -m live.overnight_futures --broker sim --root MES --action enter --now   # exercise the logic without a broker

Rule (research/PREREG-2026-10-08-globex-overnight.md): buy CONTRACTS at the 17:00 CT reopen (Sun-Thu),
rest a stop at entry * (1 - STOP_FRAC), sell at 08:30 CT. Never hold past the cash open; never enter
when the next day is a holiday; never enter if already holding. Everything is confirmed against the
broker, never against local state. Records go to days_overnight_futures.jsonl.

UNTESTED against a real account: the ProjectX/Tradovate adapters were written from public docs. Run
on the practice account first, with CONTRACTS=1, and compare every line of the record to the broker.
"""
from __future__ import annotations
import argparse, json, os, sys, time
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as C
from data import sessions as S

REC = os.path.join(C.ROOT, "days_overnight_futures.jsonl")
STATE = os.path.join(C.ROOT, "state_overnight_futures.json")


def log(msg):
    line = f"[{S.now_ct().strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.join(C.ROOT, "logs"), exist_ok=True)
        with open(os.path.join(C.ROOT, "logs", f"overnight_futures-{S.now_ct().date()}.log"), "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def record(rec: dict):
    with open(REC, "a") as f:
        f.write(json.dumps(rec) + "\n")


def wait_for(target: dt.datetime, max_minutes: int) -> bool:
    now = S.now_ct()
    if now >= target:
        return (now - target).total_seconds() <= 20 * 60      # a late start still acts inside 20 minutes
    deadline = now + dt.timedelta(minutes=max_minutes)
    while S.now_ct() < target:
        if S.now_ct() > deadline:
            return False
        time.sleep(min(30, max(1, (target - S.now_ct()).total_seconds())))
    return True


def next_trading_day(d: dt.date) -> dt.date:
    n = d + dt.timedelta(days=1)
    while not S.is_trading_day(n):
        n += dt.timedelta(days=1)
    return n


def make_broker(name: str, inst):
    if name == "projectx":
        from broker.projectx import ProjectXBroker
        return ProjectXBroker()
    if name == "tradovate":
        from broker.tradovate import TradovateBroker
        return TradovateBroker()
    from broker.base import SimBroker
    return SimBroker(inst)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default="sim", choices=["sim", "projectx", "tradovate"])
    ap.add_argument("--root", default="MES"); ap.add_argument("--symbol", default=None, help="exact contract, e.g. MESZ6 (default: broker front month)")
    ap.add_argument("--action", choices=["enter", "exit"], required=True)
    ap.add_argument("--contracts", type=int, default=1); ap.add_argument("--stop-frac", type=float, default=0.01)
    ap.add_argument("--enter-at", default="17:02"); ap.add_argument("--exit-at", default="08:30")
    ap.add_argument("--max-wait-min", type=int, default=600); ap.add_argument("--now", action="store_true", help="act immediately (testing)")
    args = ap.parse_args(argv)
    inst = C.INSTRUMENTS[args.root.upper()]
    b = make_broker(args.broker, inst)
    sym = args.symbol or b.front_month(args.root)
    now = S.now_ct(); today = now.date()

    if args.action == "enter":
        # the night belongs to the NEXT session's date; Fri/Sat never enter, and not before a holiday
        session_day = today if now.hour >= 17 else today
        nxt = next_trading_day(session_day)
        if today.weekday() in (4, 5):
            log("Friday/Saturday: no weekend hold"); return
        if (nxt - session_day).days > 1:
            log(f"next session is {nxt}: multi-day hold, not entering"); return
        held = b.position(sym).qty
        if held != 0:
            log(f"already holding {held:+d} {sym}; not entering"); return
        target = dt.datetime.combine(today, dt.time(*map(int, args.enter_at.split(":"))), S.TZ)
        if not args.now and not wait_for(target, args.max_wait_min):
            log("outside the entry window; not entering"); return
        px = b.last_price(sym)
        r = b.place_market(sym, +1, args.contracts, "globex-overnight")
        log(f"ENTER +{args.contracts} {sym} @ ~{px:.2f} -> {r.ok} {r.detail}")
        if not r.ok:
            record({"night_of": str(today), "action": "enter", "symbol": sym, "ok": False, "detail": r.detail}); return
        time.sleep(3 if args.broker != "sim" else 0)
        p = b.position(sym); entry = p.avg_px or px
        stop_px = round((entry * (1 - args.stop_frac)) / inst.tick_size) * inst.tick_size
        rs = b.place_stop(sym, -1, abs(p.qty) or args.contracts, stop_px, "globex-overnight")
        log(f"STOP {abs(p.qty) or args.contracts}x @ {stop_px:.2f} ({args.stop_frac:.1%} below {entry:.2f}) -> {rs.ok} {rs.detail}")
        # verify the floor is really resting; this is the line that protects the account overnight
        for _ in range(6):
            if b.resting_stop_qty(sym) >= (abs(p.qty) or args.contracts):
                break
            time.sleep(2)
        resting = b.resting_stop_qty(sym)
        if resting < (abs(p.qty) or args.contracts):
            log("!!! STOP NOT CONFIRMED -- flattening rather than holding unprotected")
            b.flatten(sym)
            record({"night_of": str(today), "action": "enter", "symbol": sym, "ok": False, "detail": "stop not confirmed; flattened"}); return
        rec = {"night_of": str(today), "action": "enter", "symbol": sym, "qty": p.qty, "entry_px": entry,
               "stop_px": stop_px, "ok": True}
        record(rec); json.dump(rec, open(STATE, "w"), indent=2); log(f"recorded {rec}")
    else:
        p = b.position(sym)
        if p.qty == 0:
            log("nothing held (flat or stopped out overnight)")
            st = json.load(open(STATE)) if os.path.exists(STATE) else {}
            if st.get("qty"):
                record({"night_of": st.get("night_of"), "action": "exit", "symbol": sym, "qty": 0, "detail": "stopped out overnight (verify fill at the broker)"})
                json.dump({}, open(STATE, "w"))
            return
        target = dt.datetime.combine(today, dt.time(*map(int, args.exit_at.split(":"))), S.TZ)
        if not args.now and not wait_for(target, args.max_wait_min):
            log("outside the exit window; still holding -- the firm's cutoff is the backstop"); return
        px = b.last_price(sym)
        r = b.flatten(sym)
        log(f"EXIT {p.qty:+d} {sym} @ ~{px:.2f} -> {r.ok} {r.detail}")
        st = json.load(open(STATE)) if os.path.exists(STATE) else {}
        entry = float(st.get("entry_px") or p.avg_px or px)
        record({"night_of": st.get("night_of", str(today)), "action": "exit", "symbol": sym, "qty": p.qty, "entry_px": entry,
                "exit_px_est": px, "pnl_est": round((px - entry) * inst.point_value * p.qty, 2), "ok": r.ok})
        json.dump({}, open(STATE, "w"))


if __name__ == "__main__":
    main()
