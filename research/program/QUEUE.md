# QUEUE — next tests, in order. A session takes the top item, writes its prereg, runs it, logs it, removes it.
# (Phase 0 items first. Do not reorder without a reason written here.)
- [done 2026-10-14] P0-1 data: Databento GLBX.MDP3 ohlcv-1m, full session, 2010-01-01..2023-12-31 for ES NQ RTY YM ZN ZB GC CL (needs DATABENTO_API_KEY); loader refuses >= 2024-01-01 unless --verdict
- [done 2026-10-14] P0-2 stats: deflated Sharpe, PBO, purged CV (research/program/stats.py) + tests
- [done 2026-10-14] P0-3 harness: research/program/run_test.py — takes a prereg file, enforces cost model, writes SCOREBOARD line
- [done 2026-10-14] P0-4 red-team checklist: research/program/REDTEAM.md (look-ahead, survivorship, label leakage, cost tuning)
- [done 2026-10-14, FAIL x4: drift +3 bp/night is real but < micro costs at 2010-23 levels; see prereg/3A-1.md] 3A-1 Globex overnight (anchor)
- [done 2026-10-14, FAIL x4 by gate; premium 2-3x on macro nights, pre-FOMC night 14-25 bp; see prereg/3A-6.md] 3A-6 macro-release nights
- [done 2026-10-14, FAIL x4, sign wrong: odd weeks > even; retired] 3A-4 FOMC-cycle weeks
- [done 2026-10-14, FAIL x4, not monotonic; retired] 3A-2 overnight conditioned on prior intraday move
- [done 2026-10-14, FAIL x4 by gate but monotonic and robust: premium lives in backwardation (13 bp vs ~0 in deep contango); see prereg/3A-3.md] 3A-3 overnight conditioned on VIX term structure
# Reordered 2026-10-14 (Connor): 3A-14 then 3A-5 then 3A-10. Reason: 3A-3 showed the overnight premium is a vol-state premium
# (monotonic in VIX/VIX3M), so the state-sized anchor (3A-14) and the pre-FOMC night (3A-5, the one calendar subset with gross >> cost)
# are tested before buying the rates/metals data.
- [done 2026-10-14, FAIL x4: held nights 5.5 bp ($6.27 vs $4.50) vs -0.3 bp stood aside; filter works, dollars too thin at 2010-23 levels; see prereg/3A-14.md] 3A-14 vol-state switch
- [done 2026-10-14, FAIL by gate (63 nights) but 2016-23 ES 21.8 bp, $39.78 vs $4.50, t 3.3, all 4 symbols; 2010-15 weak (distrust flag); holdout overlay candidate; see prereg/3A-5.md] 3A-5 pre-FOMC night
- [done 2026-10-14, FAIL x3: ZN/ZB -0.1 bp, GC -1.0 bp; equity-only confirmed; $47.70 spent, ledger $105.86] 3A-10 overnight drift in ZN/ZB/GC
- [done 2026-10-14, FAIL: -0.3 bp into the release, reaction symmetric; CL bought $17.10, ledger $122.96 of $125 — credit exhausted] 3A-11 CL EIA-day drift
- [done 2026-10-14, FAIL x4: TOM 4.1 vs 3.0 bp; pre-turn window is the strongest (distrust flag)] 3A-7 turn-of-month nights
- [done 2026-10-14, FAIL x4, sign wrong: opex week continues, no pinning] 3A-8 opex-week last-30-min fade
- [done 2026-10-14, FAIL x4 by gate; mechanism confirmed: high-state follow +2.8 bp vs low -0.1 (ES), NQ net +2.0 bp PF 1.16] 3A-9 last-30-min momentum in high vol state
- [done 2026-10-14, FAIL x2: NQ ORB-5 +2.5 bp gross, 77% stop-outs, PF 1.11; ES flat] 3A-12 opening-range breakout
- [done 2026-10-14, FAIL x2: no lead-lag at the futures level] 3A-13 ES overnight -> RTY/YM open
# Phase 1 catalog (3A-1..3A-14) is COMPLETE as of 2026-10-14: 48 scoreboard lines, 0 passes under either cost rule.
# Databento credit exhausted ($122.96 of $125); the loader refuses all further requests. Next is Connor's call:
#   (a) Phase 3 on the three surviving facts (vol-state overnight 3A-14, pre-FOMC night 3A-5, high-state last-30-min 3A-9):
#       red-team per REDTEAM.md, correlation matrix, then the ONE-SHOT holdout (needs 2024-2026 data = new Databento spend), or
#   (b) Phase 2 own-capital sleeves (3B) on Alpaca equity data (free with the account) — no futures data needed.
