"""
Overnight sleeve, paper lab — buy before the close, sell at the next open.

    python -m live.overnight --action buy    # waits until 14:57 CT, buys $NOTIONAL of SYMBOL (skips Fridays/holidays)
    python -m live.overnight --action sell   # waits until 08:31 CT, sells whatever the sleeve holds

Trades QQQM (same index as QQQ, ~1/4 the price) so it never collides with the
intraday sleeve's QQQ position in the same paper account. Notional defaults
to $50,000 — one MNQ-equivalent, the size the backtest used — because the
point of the lab is to observe the real fills and gaps of the exact book we
would trade, not a scaled-down one. Paper account, so the -6.7% night is
information, not money.

Every action appends one JSON line to days_overnight.jsonl; the sell line
carries the round-trip P&L computed from the two fills.
"""
from __future__ import annotations
import argparse, json, os, sys, time
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as C
from data import sessions as S

STATE = os.path.join(C.ROOT, "state_overnight.json")
LOGF = os.path.join(C.ROOT, "days_overnight.jsonl")


def log(msg):
    print(f"[{S.now_ct().strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_until(target: dt.datetime, max_wait_min: int):
    deadline = S.now_ct() + dt.timedelta(minutes=max_wait_min)
    while S.now_ct() < target:
        if S.now_ct() > deadline:
            return False
        time.sleep(min(30, max(1, (target - S.now_ct()).total_seconds())))
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", choices=["buy", "sell"], required=True)
    ap.add_argument("--symbol", default="QQQM"); ap.add_argument("--notional", type=float, default=50_000.0)
    ap.add_argument("--buy-at", default="14:57"); ap.add_argument("--sell-at", default="08:31")
    ap.add_argument("--max-wait-min", type=int, default=340)
    args = ap.parse_args(argv)
    from broker.alpaca_proxy import AlpacaProxyBroker
    b = AlpacaProxyBroker(shares_per_contract=1)
    now = S.now_ct(); day = now.date()
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}

    if args.action == "buy":
        if not S.is_trading_day(day):
            log("not a trading day"); return
        if day.weekday() == 4:
            log("Friday: no weekend hold"); return
        nxt = day + dt.timedelta(days=1)
        while not S.is_trading_day(nxt):
            nxt += dt.timedelta(days=1)
        if (nxt - day).days > 1:
            log(f"next session is {nxt}: multi-day hold, skipping"); return
        if st.get("held_from") == str(day):
            log("already bought today"); return
        _, close_ts, _, _ = S.session_bounds(day)
        target = dt.datetime.combine(day, dt.time(*map(int, args.buy_at.split(":"))), S.TZ)
        if now > close_ts:
            log("started after the close; skipping"); return
        if not wait_until(target, args.max_wait_min):
            log("gave up waiting"); return
        px = b.last_price(args.symbol); qty = int(args.notional // px)
        r = b.place_market(args.symbol, +1, qty, "overnight-buy")
        log(f"BUY {qty} {args.symbol} @ ~{px:.2f} (${qty * px:,.0f}) -> {r.ok} {r.detail}")
        time.sleep(5)
        p = b.position(args.symbol)
        rec = {"day": str(day), "action": "buy", "symbol": args.symbol, "qty": p.qty, "avg_px": p.avg_px, "ok": r.ok}
        st.update({"held_from": str(day), "qty": p.qty, "avg_px": p.avg_px})
        json.dump(st, open(STATE, "w"), indent=2)
    else:
        p = b.position(args.symbol)
        if not p.qty:
            log("nothing held; nothing to sell")
            return
        _, _, _, _ = S.session_bounds(day)
        open_ts, _, _, _ = S.session_bounds(day)
        target = dt.datetime.combine(day, dt.time(*map(int, args.sell_at.split(":"))), S.TZ)
        if not wait_until(target, args.max_wait_min):
            log("gave up waiting"); return
        px_before = b.last_price(args.symbol)
        r = b.flatten(args.symbol)
        log(f"SELL {p.qty} {args.symbol} @ ~{px_before:.2f} -> {r.ok} {r.detail}")
        time.sleep(5)
        entry = float(st.get("avg_px") or p.avg_px or px_before)
        pnl = (px_before - entry) * p.qty
        rec = {"day": str(day), "action": "sell", "symbol": args.symbol, "qty": p.qty, "entry_px": entry,
               "exit_px_est": px_before, "pnl_est": round(pnl, 2), "held_from": st.get("held_from"), "ok": r.ok}
        st = {}
        json.dump(st, open(STATE, "w"), indent=2)
    with open(LOGF, "a") as f:
        f.write(json.dumps(rec) + "\n")
    log(f"recorded: {rec}")


if __name__ == "__main__":
    main()
