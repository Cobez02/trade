# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 1974,
  "trades_per_day": 0.74,
  "net_pnl": 14267.67,
  "mean_trade": 7.23,
  "win_rate": 0.366,
  "avg_win": 170.48,
  "avg_loss": -86.91,
  "payoff": 1.96,
  "profit_factor": 1.13,
  "mean_day": 5.33,
  "sd_day": 150.36,
  "sharpe_daily_ann": 0.56,
  "best_day": 1587.92,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -5736.46,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 260,
  "flattens": 618,
  "costs_total": 12456.02,
  "t_stat_daily": 1.83
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249     202 -2441.0      -9.8   154.2 -384.0   805.0
2017   251     182  -515.0      -2.0   167.8 -375.0  1588.0
2018   249     186  7106.0      28.5   178.5 -384.0   966.0
2019   252     177 -3083.0     -12.2   120.4 -382.0   520.0
2020   253     177  1130.0       4.5   157.5 -387.0   851.0
2021   252     187  4098.0      16.3   142.9 -386.0   634.0
2022   251     213  2862.0      11.4   158.6 -384.0   984.0
2023   250     175  2502.0      10.0   143.0 -384.0   864.0
2024   252     182  2586.0      10.3   151.3 -375.0   747.0
2025   250     171   469.0       1.9   141.9 -373.0  1340.0
2026   170     122  -448.0      -2.6   114.7 -292.0   432.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 1499,
  "trades_per_day": 0.75,
  "net_pnl": 11660.13,
  "mean_trade": 7.78,
  "win_rate": 0.36,
  "avg_win": 175.81,
  "avg_loss": -86.84,
  "payoff": 2.02,
  "profit_factor": 1.14,
  "mean_day": 5.81,
  "sd_day": 153.94,
  "sharpe_daily_ann": 0.6,
  "best_day": 1587.92,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -5736.46,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 194,
  "flattens": 458,
  "costs_total": 11292.84,
  "t_stat_daily": 1.69
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 475,
  "trades_per_day": 0.71,
  "net_pnl": 2607.54,
  "mean_trade": 5.49,
  "win_rate": 0.383,
  "avg_win": 154.65,
  "avg_loss": -87.17,
  "payoff": 1.77,
  "profit_factor": 1.1,
  "mean_day": 3.88,
  "sd_day": 139.12,
  "sharpe_daily_ann": 0.44,
  "best_day": 1340.16,
  "worst_day": -375.42,
  "worst_intraday_low": -375.42,
  "max_drawdown": -2096.53,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 66,
  "flattens": 160,
  "costs_total": 1163.18,
  "t_stat_daily": 0.72
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 44.1% | 47.4% | 165.0 | drawdown (intraday touch) |
| 2x | 36.0% | 63.8% | 51 | drawdown (intraday touch) |
| 3x | 30.3% | 69.7% | 28 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 112, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1041.0)}**  (intraday-low fraction used: 0.21)

### Topstep Combine $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of target, flat by 15:10 CT, bots on funded: False

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 44.1% | 47.4% | 165.0 | drawdown (intraday touch) |
| 2x | 35.9% | 63.9% | 51 | drawdown (intraday touch) |
| 3x | 30.0% | 69.9% | 29 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 112, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1041.0)}**  (intraday-low fraction used: 0.21)

### Tradeify Growth $50K: target $3,000, DD $2,000 (eod, locks), DLL $0, consistency 35% of profit, flat by 15:59 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 41.1% | 47.7% | 169 | drawdown (intraday touch) |
| 2x | 31.8% | 67.0% | 57.0 | drawdown (intraday touch) |
| 3x | 24.2% | 75.2% | 31 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 112, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1041.0)}**  (intraday-low fraction used: 0.21)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 22.7% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **1.83** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **47%**
