# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 1670,
  "trades_per_day": 0.62,
  "net_pnl": 16041.69,
  "mean_trade": 9.61,
  "win_rate": 0.474,
  "avg_win": 186.72,
  "avg_loss": -149.77,
  "payoff": 1.25,
  "profit_factor": 1.12,
  "mean_day": 5.99,
  "sd_day": 168.61,
  "sharpe_daily_ann": 0.56,
  "best_day": 1587.92,
  "worst_day": -395.68,
  "worst_intraday_low": -395.68,
  "max_drawdown": -6441.8,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 622,
  "flattens": 1047,
  "costs_total": 10904.1,
  "t_stat_daily": 1.84
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249     170 -3038.0     -12.2   175.8 -389.0   805.0
2017   251     155  -497.0      -2.0   181.4 -388.0  1588.0
2018   249     156  5212.0      20.9   197.3 -384.0   966.0
2019   252     149 -2597.0     -10.3   137.0 -383.0   520.0
2020   253     152   -76.0      -0.3   177.8 -387.0   851.0
2021   252     159  4105.0      16.3   158.2 -396.0   634.0
2022   251     172  5944.0      23.7   182.7 -385.0   984.0
2023   250     148  3876.0      15.5   171.0 -384.0   864.0
2024   252     154  3249.0      12.9   164.1 -385.0   747.0
2025   250     147   527.0       2.1   155.9 -379.0  1340.0
2026   170     108  -664.0      -3.9   129.2 -379.0   432.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 1261,
  "trades_per_day": 0.63,
  "net_pnl": 12929.86,
  "mean_trade": 10.25,
  "win_rate": 0.47,
  "avg_win": 193.79,
  "avg_loss": -152.68,
  "payoff": 1.27,
  "profit_factor": 1.13,
  "mean_day": 6.44,
  "sd_day": 173.61,
  "sharpe_daily_ann": 0.59,
  "best_day": 1587.92,
  "worst_day": -395.68,
  "worst_intraday_low": -395.68,
  "max_drawdown": -6441.8,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 485,
  "flattens": 775,
  "costs_total": 9863.28,
  "t_stat_daily": 1.66
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 409,
  "trades_per_day": 0.61,
  "net_pnl": 3111.83,
  "mean_trade": 7.61,
  "win_rate": 0.484,
  "avg_win": 165.53,
  "avg_loss": -140.59,
  "payoff": 1.18,
  "profit_factor": 1.1,
  "mean_day": 4.63,
  "sd_day": 152.67,
  "sharpe_daily_ann": 0.48,
  "best_day": 1340.16,
  "worst_day": -384.68,
  "worst_intraday_low": -384.68,
  "max_drawdown": -2639.12,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 137,
  "flattens": 272,
  "costs_total": 1040.82,
  "t_stat_daily": 0.79
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 43.7% | 52.9% | 134 | drawdown (intraday touch) |
| 2x | 34.3% | 65.7% | 40 | drawdown (intraday touch) |
| 3x | 29.9% | 70.1% | 22.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 90, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1667.0)}**  (intraday-low fraction used: 0.229)

### Topstep Combine $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of target, flat by 15:10 CT, bots on funded: False

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 43.7% | 52.9% | 134 | drawdown (intraday touch) |
| 2x | 34.3% | 65.7% | 40 | drawdown (intraday touch) |
| 3x | 29.5% | 70.5% | 22.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 90, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1667.0)}**  (intraday-low fraction used: 0.229)

### Tradeify Growth $50K: target $3,000, DD $2,000 (eod, locks), DLL $0, consistency 35% of profit, flat by 15:59 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 41.9% | 53.3% | 137.0 | drawdown (intraday touch) |
| 2x | 30.4% | 69.2% | 43 | drawdown (intraday touch) |
| 3x | 23.3% | 76.5% | 24.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 90, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1667.0)}**  (intraday-low fraction used: 0.229)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 24.5% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **1.84** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **40%**
