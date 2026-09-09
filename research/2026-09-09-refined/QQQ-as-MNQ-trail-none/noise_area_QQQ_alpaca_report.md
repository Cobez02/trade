# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 11:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 788,
  "trades_per_day": 0.29,
  "net_pnl": 7250.94,
  "mean_trade": 9.2,
  "win_rate": 0.506,
  "avg_win": 161.06,
  "avg_loss": -146.56,
  "payoff": 1.1,
  "profit_factor": 1.13,
  "mean_day": 2.71,
  "sd_day": 100.97,
  "sharpe_daily_ann": 0.43,
  "best_day": 660.8,
  "worst_day": -389.3,
  "worst_intraday_low": -389.3,
  "max_drawdown": -3967.04,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 261,
  "flattens": 527,
  "costs_total": 4028.76,
  "t_stat_daily": 1.39
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst   best
year                                                      
2016   249      74  -530.0      -2.1   108.3 -195.0  631.0
2017   251      68 -1315.0      -5.2   103.9 -196.0  661.0
2018   249      72  3152.0      12.7   117.0 -195.0  580.0
2019   252      67 -1712.0      -6.8    81.5 -195.0  325.0
2020   253      55    88.0       0.3    99.0 -389.0  592.0
2021   252      79  3561.0      14.1    96.3 -197.0  447.0
2022   251      86  1620.0       6.5   106.6 -198.0  656.0
2023   250      81   604.0       2.4   108.9 -379.0  605.0
2024   252      83   918.0       3.6   105.6 -198.0  496.0
2025   250      72  1086.0       4.3    87.4 -338.0  467.0
2026   170      51  -222.0      -1.3    85.8 -235.0  432.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 582,
  "trades_per_day": 0.29,
  "net_pnl": 5468.66,
  "mean_trade": 9.4,
  "win_rate": 0.5,
  "avg_win": 168.49,
  "avg_loss": -149.7,
  "payoff": 1.13,
  "profit_factor": 1.13,
  "mean_day": 2.72,
  "sd_day": 103.18,
  "sharpe_daily_ann": 0.42,
  "best_day": 660.8,
  "worst_day": -389.3,
  "worst_intraday_low": -389.3,
  "max_drawdown": -3967.04,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 196,
  "flattens": 386,
  "costs_total": 3592.52,
  "t_stat_daily": 1.18
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 206,
  "trades_per_day": 0.31,
  "net_pnl": 1782.28,
  "mean_trade": 8.65,
  "win_rate": 0.524,
  "avg_win": 141.05,
  "avg_loss": -137.26,
  "payoff": 1.03,
  "profit_factor": 1.13,
  "mean_day": 2.65,
  "sd_day": 94.09,
  "sharpe_daily_ann": 0.45,
  "best_day": 496.44,
  "worst_day": -337.96,
  "worst_intraday_low": -337.96,
  "max_drawdown": -1407.27,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 65,
  "flattens": 141,
  "costs_total": 436.24,
  "t_stat_daily": 0.73
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 24.5% | 32.6% | 236 | drawdown (intraday touch) |
| 2x | 37.1% | 62.5% | 94 | drawdown (intraday touch) |
| 3x | 33.3% | 66.7% | 47.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 314, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1686.0)}**  (intraday-low fraction used: 0.229)

### Topstep Combine $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of target, flat by 15:10 CT, bots on funded: False

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 24.5% | 32.6% | 236 | drawdown (intraday touch) |
| 2x | 37.1% | 62.5% | 94 | drawdown (intraday touch) |
| 3x | 33.2% | 66.8% | 47.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 314, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1686.0)}**  (intraday-low fraction used: 0.229)

### Tradeify Growth $50K: target $3,000, DD $2,000 (eod, locks), DLL $0, consistency 35% of profit, flat by 15:59 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 24.5% | 32.6% | 236 | drawdown (intraday touch) |
| 2x | 35.9% | 63.5% | 96.5 | drawdown (intraday touch) |
| 3x | 28.7% | 71.2% | 53.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 314, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1686.0)}**  (intraday-low fraction used: 0.229)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 11.7% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **1.39** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **36%**
