# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 11:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 839,
  "trades_per_day": 0.31,
  "net_pnl": 4431.77,
  "mean_trade": 5.28,
  "win_rate": 0.378,
  "avg_win": 137.2,
  "avg_loss": -74.83,
  "payoff": 1.83,
  "profit_factor": 1.11,
  "mean_day": 1.65,
  "sd_day": 80.01,
  "sharpe_daily_ann": 0.33,
  "best_day": 660.8,
  "worst_day": -389.3,
  "worst_intraday_low": -389.3,
  "max_drawdown": -2320.43,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 78,
  "flattens": 170,
  "costs_total": 4193.68,
  "t_stat_daily": 1.07
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst   best
year                                                      
2016   249      77   312.0       1.3    92.6 -195.0  631.0
2017   251      70  -933.0      -3.7    82.8 -196.0  661.0
2018   249      78  2781.0      11.2    92.7 -195.0  546.0
2019   252      69  -795.0      -3.2    61.7 -195.0  325.0
2020   253      58  -424.0      -1.7    69.1 -389.0  455.0
2021   252      80  2182.0       8.7    79.4 -219.0  432.0
2022   251      91   931.0       3.7    84.1 -196.0  656.0
2023   250      89  -109.0      -0.4    88.1 -379.0  605.0
2024   252      93  1373.0       5.4    88.5 -198.0  496.0
2025   250      79  -102.0      -0.4    64.2 -232.0  467.0
2026   170      55  -785.0      -4.6    63.4 -232.0  432.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 612,
  "trades_per_day": 0.3,
  "net_pnl": 3945.06,
  "mean_trade": 6.45,
  "win_rate": 0.376,
  "avg_win": 144.99,
  "avg_loss": -76.97,
  "payoff": 1.88,
  "profit_factor": 1.13,
  "mean_day": 1.97,
  "sd_day": 81.92,
  "sharpe_daily_ann": 0.38,
  "best_day": 660.8,
  "worst_day": -389.3,
  "worst_intraday_low": -389.3,
  "max_drawdown": -2320.43,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 62,
  "flattens": 123,
  "costs_total": 3720.96,
  "t_stat_daily": 1.08
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 227,
  "trades_per_day": 0.34,
  "net_pnl": 486.71,
  "mean_trade": 2.14,
  "win_rate": 0.383,
  "avg_win": 116.62,
  "avg_loss": -68.99,
  "payoff": 1.69,
  "profit_factor": 1.05,
  "mean_day": 0.72,
  "sd_day": 74.02,
  "sharpe_daily_ann": 0.16,
  "best_day": 496.44,
  "worst_day": -232.18,
  "worst_intraday_low": -257.59,
  "max_drawdown": -1612.13,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 16,
  "flattens": 47,
  "costs_total": 472.72,
  "t_stat_daily": 0.25
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 10.7% | 18.2% | 281.0 | drawdown (intraday touch) |
| 2x | 34.8% | 59.1% | 146.0 | drawdown (intraday touch) |
| 3x | 32.0% | 67.8% | 76 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'pass', 'days': 1496, 'reason': 'target', 'balance': np.float64(3093.0)}**  (intraday-low fraction used: 0.216)

### Topstep Combine $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of target, flat by 15:10 CT, bots on funded: False

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 10.7% | 18.2% | 281.0 | drawdown (intraday touch) |
| 2x | 34.8% | 59.1% | 146.0 | drawdown (intraday touch) |
| 3x | 31.8% | 68.0% | 77 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'pass', 'days': 1496, 'reason': 'target', 'balance': np.float64(3093.0)}**  (intraday-low fraction used: 0.216)

### Tradeify Growth $50K: target $3,000, DD $2,000 (eod, locks), DLL $0, consistency 35% of profit, flat by 15:59 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 10.7% | 18.2% | 281.0 | drawdown (intraday touch) |
| 2x | 32.3% | 59.6% | 149 | drawdown (intraday touch) |
| 3x | 25.8% | 72.2% | 86 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'pass', 'days': 1496, 'reason': 'target', 'balance': np.float64(3093.0)}**  (intraday-low fraction used: 0.216)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 5.4% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **1.07** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **49%**
