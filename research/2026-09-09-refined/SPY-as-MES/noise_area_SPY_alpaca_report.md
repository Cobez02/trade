# Backtest: noise_area on SPY (alpaca)

`instrument=SPY tick=0.01/$0.1 point=$10.0 rt_cost=$0.90 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 11:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2681,
  "trades": 815,
  "trades_per_day": 0.3,
  "net_pnl": 1168.03,
  "mean_trade": 1.43,
  "win_rate": 0.351,
  "avg_win": 152.59,
  "avg_loss": -80.29,
  "payoff": 1.9,
  "profit_factor": 1.03,
  "mean_day": 0.44,
  "sd_day": 92.61,
  "sharpe_daily_ann": 0.07,
  "best_day": 1208.4,
  "worst_day": -380.8,
  "worst_intraday_low": -380.8,
  "max_drawdown": -3092.46,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 96,
  "flattens": 166,
  "costs_total": 9013.5,
  "t_stat_daily": 0.24
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249      73  -954.0      -3.8    72.0 -190.0   416.0
2017   251      66 -1003.0      -4.0    78.4 -247.0   497.0
2018   251      87   955.0       3.8   129.7 -374.0  1208.0
2019   252      75     6.0       0.0    68.5 -232.0   376.0
2020   253      68   202.0       0.8   102.1 -381.0   872.0
2021   252      74  2246.0       8.9    83.5 -376.0   536.0
2022   251      82  1718.0       6.8   110.0 -197.0   971.0
2023   250      77  -777.0      -3.1    79.7 -380.0   629.0
2024   252      82  1389.0       5.5   127.2 -273.0  1056.0
2025   250      74 -1343.0      -5.4    64.6 -214.0   440.0
2026   170      57 -1271.0      -7.5    61.3 -178.0   456.0
```

## IN-SAMPLE — 2009 sessions
```
{
  "sessions": 2009,
  "trades": 602,
  "trades_per_day": 0.3,
  "net_pnl": 2393.17,
  "mean_trade": 3.98,
  "win_rate": 0.352,
  "avg_win": 157.07,
  "avg_loss": -79.24,
  "payoff": 1.98,
  "profit_factor": 1.08,
  "mean_day": 1.19,
  "sd_day": 92.62,
  "sharpe_daily_ann": 0.2,
  "best_day": 1208.4,
  "worst_day": -380.8,
  "worst_intraday_low": -380.8,
  "max_drawdown": -2041.0,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 73,
  "flattens": 133,
  "costs_total": 7605.0,
  "t_stat_daily": 0.58
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 213,
  "trades_per_day": 0.32,
  "net_pnl": -1225.14,
  "mean_trade": -5.75,
  "win_rate": 0.347,
  "avg_win": 139.76,
  "avg_loss": -83.22,
  "payoff": 1.68,
  "profit_factor": 0.89,
  "mean_day": -1.82,
  "sd_day": 92.56,
  "sharpe_daily_ann": -0.31,
  "best_day": 1055.6,
  "worst_day": -273.0,
  "worst_intraday_low": -291.6,
  "max_drawdown": -3092.46,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 23,
  "flattens": 33,
  "costs_total": 1408.5,
  "t_stat_daily": -0.51
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 9.9% | 36.4% | 256 | drawdown (intraday touch) |
| 2x | 22.7% | 72.6% | 127.0 | drawdown (intraday touch) |
| 3x | 20.6% | 77.8% | 72 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 370, 'reason': 'drawdown (intraday touch)', 'balance': -2000}**  (intraday-low fraction used: 0.185)

### Topstep Combine $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of target, flat by 15:10 CT, bots on funded: False

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 9.9% | 36.4% | 256 | drawdown (intraday touch) |
| 2x | 22.5% | 72.7% | 127.0 | drawdown (intraday touch) |
| 3x | 20.4% | 78.0% | 73 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 370, 'reason': 'drawdown (intraday touch)', 'balance': -2000}**  (intraday-low fraction used: 0.185)

### Tradeify Growth $50K: target $3,000, DD $2,000 (eod, locks), DLL $0, consistency 35% of profit, flat by 15:59 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 8.7% | 36.5% | 256 | drawdown (intraday touch) |
| 2x | 13.7% | 74.8% | 132 | drawdown (intraday touch) |
| 3x | 12.2% | 82.1% | 74 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 370, 'reason': 'drawdown (intraday touch)', 'balance': -2000}**  (intraday-low fraction used: 0.185)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 8.8% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2681
- days with intraday low <= -$1,000: **0**
- daily t-stat: **0.24** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **89%**
