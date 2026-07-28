"""Replay real price paths through the exit rules. No network, no orders."""
import sys; sys.path.insert(0,'/home/claude/spxbot')
import exitrules as E

def replay(entry, path, spread=0.05, label=""):
    """path = list of bid prices. Returns (exit_pct, reason, ticks_held)."""
    peak = None
    for i, bid in enumerate(path):
        q = {"bid": bid, "ask": bid*(1+spread), "spread_pct": spread}
        d = E.decide(entry, q, peak)
        peak = d["peak_pct"]
        if d["action"] == "exit":
            return d["pnl_pct"], d["reason"], i
    return (path[-1]-entry)/entry, "held to end", len(path)

print("="*74)
print("A. NVDA260731P00200000 — the +92% winner (entry 3.15 -> 6.05)")
print("="*74)
path = [3.15,3.4,3.9,4.57,5.1,5.6,6.05,5.9]   # ran up, small fade at the end
pct, why, t = replay(3.15, path)
print(f"  trailing rule : {pct:+.1%}  ({why})")
print(f"  fixed +45% TP : +45.0%  (capped the moment it crossed 4.57)")
print(f"  -> trail keeps {(pct-0.45)*3.15*100:+.0f} dollars more on 1 contract")

print()
print("="*74)
print("B. SLB260731C00053000 — the -51% loser (entry 0.65 -> 0.32, 33% spread)")
print("="*74)
for sp in (0.05, 0.15, 0.33):
    path = [0.65,0.60,0.55,0.50,0.46,0.42,0.38,0.34,0.32]
    pct, why, t = replay(0.65, path, spread=sp)
    print(f"  spread {sp*100:>4.0f}% : exit {pct:+.1%} at tick {t:>2}  ({why})")
print(f"  actual hourly-checked outcome: -50.8% at the final tick")

print()
print("="*74)
print("C. Whipsaw — dips to -28% then recovers to +40%")
print("="*74)
path = [1.00,0.90,0.80,0.72,0.85,1.10,1.40]
pct, why, t = replay(1.00, path)
print(f"  exit {pct:+.1%} at tick {t} ({why})")
print("  -> a -30% stop should NOT fire on a -28% dip; it must ride to the trail")

print()
print("="*74)
print("D. Spike-and-collapse — +80% then straight back to entry")
print("="*74)
path = [1.00,1.30,1.60,1.80,1.55,1.20,1.00]
pct, why, t = replay(1.00, path)
print(f"  exit {pct:+.1%} at tick {t} ({why})")
print("  -> trail must bank the move, not ride it back to zero")

print()
print("="*74)
print("E. Bad-quote handling (must never trigger a sale)")
print("="*74)
cases = [
    ("zero bid",        {"bid":0.0,"ask":1.0,"spread_pct":0.1}, None),
    ("crossed market",  {"bid":1.2,"ask":1.0,"spread_pct":0.1}, None),
    ("stale quote",     {"bid":0.10,"ask":0.12,"spread_pct":0.2}, 120.0),
    ("empty",           None, None),
]
for name, q, age in cases:
    d = E.decide(1.00, q or {}, None, age_s=age)
    verdict = "OK" if d["action"]=="hold" else "FAIL — WOULD HAVE SOLD"
    print(f"  {name:<18} action={d['action']:<6} {d['reason'][:38]:<40}{verdict}")

print()
print("="*74)
print("F. Spread-aware stop thresholds")
print("="*74)
for sp in (0.0,0.05,0.10,0.20,0.33,0.60,2.0):
    print(f"  spread {sp*100:>5.0f}%  ->  stop fires at {E.effective_stop(sp):+.1%}")
print(f"  hard floor: {E.HARD_STOP_PCT:+.0%} — relief can never exceed it")

print()
print("="*74)
print("G. Liquidity deterioration — the exit that spread relief must not swallow")
print("="*74)
# Every position clears a 15% spread gate on entry, so a wide market later is
# news. But wide-and-winning is not the same trade as wide-and-losing.
liq = [
    ("wide + losing  (55% sprd, -20%)", 0.80, 1.40, "exit"),
    ("wide + winning (55% sprd, +30%)", 1.30, 2.28, "hold"),
    ("wide + flat    (55% sprd,  -5%)", 0.95, 1.66, "hold"),
    ("tight + losing (10% sprd, -20%)", 0.80, 0.88, "hold"),
]
for name, bid, ask, want in liq:
    mid = (bid+ask)/2
    q = {"bid":bid,"ask":ask,"mid":mid,"spread_pct":(ask-bid)/mid}
    d = E.decide(1.00, q, None)
    ok = "OK" if d["action"]==want else f"FAIL (wanted {want})"
    print(f"  {name:<34} -> {d['action']:<5} {d['reason'][:34]:<36}{ok}")
print("  -> a wide WINNER is left to the trail; only a wide LOSER is dumped")

print()
print("="*74)
print("H. No-market rejection — an absent bid is not a cheap contract")
print("="*74)
for name, bid, ask in [("0.01 x 0.40 (phantom)",0.01,0.40),
                       ("0.18 x 0.22 (real, tight)",0.18,0.22),
                       ("0.30 x 0.60 (real, wide)",0.30,0.60)]:
    mid=(bid+ask)/2
    ok,why = E.quote_ok({"bid":bid,"ask":ask})
    print(f"  {name:<26} usable={str(ok):<6}{why}")
print(f"  threshold: {E.NO_MARKET_SPREAD:.0%} wide — beyond it we hold, not dump")

print()
print("="*74)
print("I. Confirmation window — ticks AND wall-clock, unless it is a collapse")
print("="*74)
conf = [
    ("1 tick, 0.0s, at threshold",      1, 0.0, -0.31, -0.30, False),
    ("3 ticks, 0.1s (stream burst)",    3, 0.1, -0.31, -0.30, False),
    ("3 ticks, 1.5s (sustained)",       3, 1.5, -0.31, -0.30, True),
    ("2 ticks, 9.0s (slow feed)",       2, 9.0, -0.31, -0.30, False),
    ("1 tick, 0.0s, -45% vs -30%",      1, 0.0, -0.45, -0.30, True),
    ("1 tick, calendar exit (no trig)", 1, 0.0,  0.30,  None, True),
]
for name, t, el, cur, trig, want in conf:
    got = E.confirm_ok(t, el, cur, trig)
    print(f"  {name:<34} act={str(got):<6}{'OK' if got==want else 'FAIL'}")
print(f"  need {E.CONFIRM_TICKS} ticks AND {E.CONFIRM_MIN_SEC}s; "
      f"{E.PANIC_MARGIN:.0%} past the trigger overrides both")
