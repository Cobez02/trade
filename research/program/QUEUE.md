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
- 3A-11 CL inventory-report (Wed 09:30 CT) drift — needs CL ($17.10, exhausts the credit; approved by Connor 2026-10-14 if 3A-11 is next)
