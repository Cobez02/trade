# Backtest: noise_area on SPY (alpaca)

`instrument=SPY tick=0.01/$0.5 point=$50.0 rt_cost=$4.50 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2681,
  "trades": 87,
  "trades_per_day": 0.03,
  "net_pnl": -1482.74,
  "mean_trade": -17.04,
  "win_rate": 0.23,
  "avg_win": 156.95,
  "avg_loss": -68.98,
  "payoff": 2.28,
  "profit_factor": 0.68,
  "mean_day": -0.55,
  "sd_day": 23.21,
  "sharpe_daily_ann": -0.38,
  "best_day": 498.0,
  "worst_day": -315.5,
  "worst_intraday_low": -315.5,
  "max_drawdown": -1787.24,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 18,
  "flattens": 21,
  "costs_total": 1363.5,
  "t_stat_daily": -1.23
}
```

## By year
```
      days     pnl  mean_day  sd_day  worst   best
year                                              
2016   249 -1483.0      -6.0    76.1 -316.0  498.0
2017   251     0.0       0.0     0.0    0.0    0.0
2018   251     0.0       0.0     0.0    0.0    0.0
2019   252     0.0       0.0     0.0    0.0    0.0
2020   253     0.0       0.0     0.0    0.0    0.0
2021   252     0.0       0.0     0.0    0.0    0.0
2022   251     0.0       0.0     0.0    0.0    0.0
2023   250     0.0       0.0     0.0    0.0    0.0
2024   252     0.0       0.0     0.0    0.0    0.0
2025   250     0.0       0.0     0.0    0.0    0.0
2026   170     0.0       0.0     0.0    0.0    0.0
```

## IN-SAMPLE — 2009 sessions
```
{
  "sessions": 2009,
  "trades": 87,
  "trades_per_day": 0.04,
  "net_pnl": -1482.74,
  "mean_trade": -17.04,
  "win_rate": 0.23,
  "avg_win": 156.95,
  "avg_loss": -68.98,
  "payoff": 2.28,
  "profit_factor": 0.68,
  "mean_day": -0.74,
  "sd_day": 26.81,
  "sharpe_daily_ann": -0.44,
  "best_day": 498.0,
  "worst_day": -315.5,
  "worst_intraday_low": -315.5,
  "max_drawdown": -1787.24,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 18,
  "flattens": 21,
  "costs_total": 1363.5,
  "t_stat_daily": -1.23
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "trades": 0
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 0.0% | 0.0% | 250.0 | drawdown (intraday touch) |
| 2x | 0.0% | 8.0% | 306 | drawdown (intraday touch) |
| 3x | 0.4% | 26.7% | 258.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'open', 'days': 2681, 'reason': 'still running', 'balance': np.float64(-1483.0)}**  (intraday-low fraction used: 0.175)

### Topstep Combine $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of target, flat by 15:10 CT, bots on funded: False

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 0.0% | 0.0% | 250.0 | drawdown (intraday touch) |
| 2x | 0.0% | 8.0% | 306 | drawdown (intraday touch) |
| 3x | 0.3% | 26.7% | 258.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'open', 'days': 2681, 'reason': 'still running', 'balance': np.float64(-1483.0)}**  (intraday-low fraction used: 0.175)

### Tradeify Growth $50K: target $3,000, DD $2,000 (eod, locks), DLL $0, consistency 35% of profit, flat by 15:59 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 0.0% | 0.0% | 250.0 | drawdown (intraday touch) |
| 2x | 0.0% | 8.0% | 306 | drawdown (intraday touch) |
| 3x | 0.1% | 26.7% | 258 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'open', 'days': 2681, 'reason': 'still running', 'balance': np.float64(-1483.0)}**  (intraday-low fraction used: 0.175)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 0.0% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2681
- days with intraday low <= -$1,000: **0**
- daily t-stat: **-1.23** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **48%**
