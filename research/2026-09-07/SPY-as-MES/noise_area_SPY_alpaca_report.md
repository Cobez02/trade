# Backtest: noise_area on SPY (alpaca)

`instrument=SPY tick=0.01/$0.5 point=$50.0 rt_cost=$4.50 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2681,
  "trades": 1836,
  "trades_per_day": 0.68,
  "net_pnl": -3124.63,
  "mean_trade": -1.7,
  "win_rate": 0.327,
  "avg_win": 150.48,
  "avg_loss": -75.58,
  "payoff": 1.99,
  "profit_factor": 0.97,
  "mean_day": -1.17,
  "sd_day": 131.37,
  "sharpe_daily_ann": -0.14,
  "best_day": 1323.75,
  "worst_day": -362.75,
  "worst_intraday_low": -362.75,
  "max_drawdown": -9734.51,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 321,
  "flattens": 509,
  "costs_total": 21123.0,
  "t_stat_daily": -0.46
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249     206 -4686.0     -18.8   115.2 -361.0   498.0
2017   251     202 -4665.0     -18.6   105.0 -265.0   591.0
2018   251     201  2896.0      11.5   177.9 -363.0  1259.0
2019   252     167 -2363.0      -9.4   103.8 -302.0   570.0
2020   253     136   257.0       1.0   131.6 -344.0   872.0
2021   252     165  2935.0      11.6   110.6 -310.0   596.0
2022   251     158  3518.0      14.0   158.2 -338.0  1324.0
2023   250     191  -337.0      -1.3   118.7 -350.0   745.0
2024   252     181  1669.0       6.6   165.6 -335.0  1208.0
2025   250     135 -1039.0      -4.2   119.4 -300.0  1204.0
2026   170      94 -1309.0      -7.7    98.1 -318.0   456.0
```

## IN-SAMPLE — 2009 sessions
```
{
  "sessions": 2009,
  "trades": 1426,
  "trades_per_day": 0.71,
  "net_pnl": -2444.86,
  "mean_trade": -1.71,
  "win_rate": 0.322,
  "avg_win": 151.3,
  "avg_loss": -74.34,
  "payoff": 2.04,
  "profit_factor": 0.97,
  "mean_day": -1.22,
  "sd_day": 130.45,
  "sharpe_daily_ann": -0.15,
  "best_day": 1323.75,
  "worst_day": -362.75,
  "worst_intraday_low": -362.75,
  "max_drawdown": -9734.51,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 245,
  "flattens": 394,
  "costs_total": 18238.5,
  "t_stat_daily": -0.42
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 410,
  "trades_per_day": 0.61,
  "net_pnl": -679.77,
  "mean_trade": -1.66,
  "win_rate": 0.344,
  "avg_win": 147.84,
  "avg_loss": -80.02,
  "payoff": 1.85,
  "profit_factor": 0.97,
  "mean_day": -1.01,
  "sd_day": 134.07,
  "sharpe_daily_ann": -0.12,
  "best_day": 1207.5,
  "worst_day": -335.0,
  "worst_intraday_low": -335.0,
  "max_drawdown": -3690.58,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 76,
  "flattens": 115,
  "costs_total": 2884.5,
  "t_stat_daily": -0.2
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 15.7% | 72.4% | 167.0 | drawdown (intraday touch) |
| 2x | 18.6% | 81.2% | 60.0 | drawdown (intraday touch) |
| 3x | 16.3% | 83.6% | 34 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 112, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1696.0)}**  (intraday-low fraction used: 0.189)

### Topstep Combine $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of target, flat by 15:10 CT, bots on funded: False

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 15.7% | 72.5% | 167.5 | drawdown (intraday touch) |
| 2x | 18.4% | 81.4% | 60.0 | drawdown (intraday touch) |
| 3x | 16.0% | 83.9% | 34 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 112, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1696.0)}**  (intraday-low fraction used: 0.189)

### Tradeify Growth $50K: target $3,000, DD $2,000 (eod, locks), DLL $0, consistency 35% of profit, flat by 15:59 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 12.5% | 72.8% | 168.0 | drawdown (intraday touch) |
| 2x | 12.8% | 85.2% | 63 | drawdown (intraday touch) |
| 3x | 10.2% | 88.7% | 36 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 112, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1696.0)}**  (intraday-low fraction used: 0.189)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 19.8% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2681
- days with intraday low <= -$1,000: **0**
- daily t-stat: **-0.46** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **87%**
