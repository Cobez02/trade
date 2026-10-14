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
- 3A-10 overnight drift in ZN/ZB/GC
