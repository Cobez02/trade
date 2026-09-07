# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.8 point=$80.0 rt_cost=$3.04 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 1304,
  "trades_per_day": 0.49,
  "net_pnl": 8058.86,
  "mean_trade": 6.18,
  "win_rate": 0.348,
  "avg_win": 165.17,
  "avg_loss": -78.74,
  "payoff": 2.1,
  "profit_factor": 1.12,
  "mean_day": 3.01,
  "sd_day": 112.17,
  "sharpe_daily_ann": 0.43,
  "best_day": 1380.8,
  "worst_day": -382.08,
  "worst_intraday_low": -382.08,
  "max_drawdown": -4309.36,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 222,
  "flattens": 401,
  "costs_total": 9095.68,
  "t_stat_daily": 1.39
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249     202 -1816.0      -7.3   125.6 -360.0   629.0
2017   251     182   -31.0      -0.1   140.3 -322.0  1381.0
2018   249     180  4993.0      20.1   142.7 -343.0   736.0
2019   252     171 -2807.0     -11.1   103.3 -382.0   500.0
2020   253     115   557.0       2.2   112.2 -336.0   742.0
2021   252     125  3727.0      14.8   110.5 -294.0   546.0
2022   251      67  1576.0       6.3   110.4 -311.0   656.0
2023   250     123  1233.0       4.9   104.8 -245.0   692.0
2024   252      86  -196.0      -0.8    94.0 -268.0   523.0
2025   250      44  1413.0       5.7    99.1 -189.0  1340.0
2026   170       9  -589.0      -3.5    39.8 -193.0   355.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 1165,
  "trades_per_day": 0.58,
  "net_pnl": 7431.53,
  "mean_trade": 6.38,
  "win_rate": 0.35,
  "avg_win": 161.14,
  "avg_loss": -77.03,
  "payoff": 2.09,
  "profit_factor": 1.13,
  "mean_day": 3.7,
  "sd_day": 119.71,
  "sharpe_daily_ann": 0.49,
  "best_day": 1380.8,
  "worst_day": -382.08,
  "worst_intraday_low": -382.08,
  "max_drawdown": -4309.36,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 185,
  "flattens": 358,
  "costs_total": 8636.64,
  "t_stat_daily": 1.39
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 139,
  "trades_per_day": 0.21,
  "net_pnl": 627.33,
  "mean_trade": 4.51,
  "win_rate": 0.331,
  "avg_win": 200.9,
  "avg_loss": -92.63,
  "payoff": 2.17,
  "profit_factor": 1.07,
  "mean_day": 0.93,
  "sd_day": 85.73,
  "sharpe_daily_ann": 0.17,
  "best_day": 1340.16,
  "worst_day": -267.52,
  "worst_intraday_low": -289.2,
  "max_drawdown": -1476.67,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 37,
  "flattens": 43,
  "costs_total": 459.04,
  "t_stat_daily": 0.28
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 28.8% | 36.7% | 225.5 | drawdown (intraday touch) |
| 2x | 36.1% | 63.1% | 89 | drawdown (intraday touch) |
| 3x | 31.2% | 68.5% | 46 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 150, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1165.0)}**  (intraday-low fraction used: 0.193)

### Topstep Combine $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of target, flat by 15:10 CT, bots on funded: False

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 28.8% | 36.7% | 225.5 | drawdown (intraday touch) |
| 2x | 36.1% | 63.1% | 89 | drawdown (intraday touch) |
| 3x | 30.8% | 68.8% | 46 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 150, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1165.0)}**  (intraday-low fraction used: 0.193)

### Tradeify Growth $50K: target $3,000, DD $2,000 (eod, locks), DLL $0, consistency 35% of profit, flat by 15:59 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 24.6% | 36.8% | 230.0 | drawdown (intraday touch) |
| 2x | 31.9% | 64.9% | 94 | drawdown (intraday touch) |
| 3x | 24.9% | 73.3% | 52.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 150, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1165.0)}**  (intraday-low fraction used: 0.193)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 14.5% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **1.39** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **53%**
