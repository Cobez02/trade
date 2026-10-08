# Pre-registration — the Globex overnight sleeve (2026-10-08, written before any live test)

## What was found today (real NQ/ES/RTY/YM futures, Yahoo hourly bars, 2024-05-17 .. 2026-10-08, 582 nights)
Buying the micro index future at the 17:00 CT Globex reopen, resting a 1% stop, and selling at the
08:00-08:30 CT cash open captures MORE of the overnight drift than the cash-close-to-open window
(Sharpe 1.11 vs 0.92 on MNQ), and it is the window every futures prop firm allows: a position opened
after 17:00 CT and closed before the next day's cutoff stays inside one Globex session (Topstep: flat by
15:10 CT; TradeDay: by 15:50 CT). The cash-close hold (14:59 -> next morning) crosses the 16:00 CT
session boundary and is NOT allowed anywhere — so the paper lab's QQQM hold was testing the wrong window.

| book, 1% stop, net of ~$3-4.50 round trip | $/night | sd  | Sharpe | worst | P(pass) Topstep $50K | zero-edge |
|---|---|---|---|---|---|---|
| 1 MES  | 11.7 | 163 | 1.14 | -382 | **66%** (~1 year) | ~20% |
| 1 MNQ  | 23.2 | 332 | 1.11 | -638 | 49% (~37 days)     | ~25% |
| 1 MNQ + 1 MES | 34.9 | 484 | 1.15 | -998 | 42% | |
| 2 MNQ  | 46.4 | 665 | 1.11 | -1276 | 38% | |
Smaller is better for passing: the floor is fixed, so P(pass) falls as size rises. MES is the pass
configuration; MNQ is the faster, riskier one. The 1% stop costs ~15% of the drift and removes every
night worse than -$640 (MNQ) / -$390 (MES) in two years.

## Why this is "hopeful" and not "proven"
- Two years of futures data (the ETF proxy says the same effect held for ten; the literature for decades).
- Stops modelled on hourly lows; real fills a few ticks worse. Sunday-night entries included (Sunday 17:00
  -> Monday 08:00 is one session); holiday nights excluded.
- The intraday sleeve ADDS NOISE in this window (Sharpe 0.27 in 2024-26) and is left out of the funded plan.

## The test (the evaluation IS the test; no money is at risk beyond the fee)
1. Topstep $50K Combine ($49/month, no time limit) + ProjectX API ($14.50/month). Bots are allowed in the
   Combine and the XFA (sim-funded, pays out); not on Live Funded. Alternative to verify: MyFundedFutures
   with Tradovate API access enabled (bots allowed on funded accounts too).
2. First 10 nights on the TopstepX PRACTICE account with 1 MES: confirm fills, the stop rests on Globex,
   the 08:30 exit confirms flat, the record matches the broker. Fix the adapter until it does.
3. Then the Combine itself, 1 MES, exactly the rule above, nothing else. Expected: ~66% per attempt,
   median ~1 year at $49/month; a fail costs a reset. Stop the test if 30 nights show a mean below -$10
   or any night below -$600 (the stop is not working).
4. TradeDay is OUT: static accounts retired May 2026 and no API is exposed (EAs only via NinjaTrader/
   TradingView). MFFU in if its Tradovate API can be enabled; same test.

## Falsifiers
- The practice account shows the stop not resting overnight or fills > 2 ticks worse than the hourly model.
- 60 live nights with mean/night below $0 (MES) — the drift is absent in this regime.
