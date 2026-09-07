# Backtest: noise_area on SPY (alpaca)

`instrument=SPY tick=0.01/$0.1 point=$10.0 rt_cost=$0.90 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2681,
  "trades": 2055,
  "trades_per_day": 0.77,
  "net_pnl": -6423.21,
  "mean_trade": -3.13,
  "win_rate": 0.324,
  "avg_win": 175.41,
  "avg_loss": -88.73,
  "payoff": 1.98,
  "profit_factor": 0.95,
  "mean_day": -2.4,
  "sd_day": 160.56,
  "sharpe_daily_ann": -0.24,
  "best_day": 1460.15,
  "worst_day": -384.0,
  "worst_intraday_low": -384.0,
  "max_drawdown": -14474.63,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 332,
  "flattens": 565,
  "costs_total": 27168.3,
  "t_stat_daily": -0.77
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249     206 -6259.0     -25.1   141.2 -370.0   592.0
2017   251     202 -7781.0     -31.0   132.7 -364.0   591.0
2018   251     202  3952.0      15.7   213.8 -372.0  1460.0
2019   252     167 -2339.0      -9.3   129.5 -372.0   798.0
2020   253     174  -241.0      -1.0   160.8 -380.0  1046.0
2021   252     175  3480.0      13.8   132.4 -365.0   715.0
2022   251     209  4451.0      17.7   193.3 -377.0  1324.0
2023   250     196   -53.0      -0.2   142.8 -384.0   943.0
2024   252     195  2287.0       9.1   193.5 -379.0  1433.0
2025   250     189 -1087.0      -4.3   158.9 -371.0  1365.0
2026   170     140 -2835.0     -16.7   120.6 -373.0   456.0
```

## IN-SAMPLE — 2009 sessions
```
{
  "sessions": 2009,
  "trades": 1531,
  "trades_per_day": 0.76,
  "net_pnl": -4788.13,
  "mean_trade": -3.13,
  "win_rate": 0.319,
  "avg_win": 180.16,
  "avg_loss": -89.14,
  "payoff": 2.02,
  "profit_factor": 0.95,
  "mean_day": -2.38,
  "sd_day": 159.18,
  "sharpe_daily_ann": -0.24,
  "best_day": 1460.15,
  "worst_day": -384.0,
  "worst_intraday_low": -384.0,
  "max_drawdown": -14474.63,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 250,
  "flattens": 419,
  "costs_total": 23303.7,
  "t_stat_daily": -0.67
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 524,
  "trades_per_day": 0.78,
  "net_pnl": -1635.08,
  "mean_trade": -3.12,
  "win_rate": 0.338,
  "avg_win": 162.29,
  "avg_loss": -87.49,
  "payoff": 1.85,
  "profit_factor": 0.95,
  "mean_day": -2.43,
  "sd_day": 164.62,
  "sharpe_daily_ann": -0.23,
  "best_day": 1432.6,
  "worst_day": -378.8,
  "worst_intraday_low": -378.8,
  "max_drawdown": -5289.21,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 82,
  "flattens": 146,
  "costs_total": 3864.6,
  "t_stat_daily": -0.38
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 17.1% | 80.2% | 124.0 | drawdown (intraday touch) |
| 2x | 17.1% | 82.9% | 43 | drawdown (intraday touch) |
| 3x | 15.8% | 84.2% | 24.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 106, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1635.0)}**  (intraday-low fraction used: 0.195)

### Topstep Combine $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of target, flat by 15:10 CT, bots on funded: False

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 17.1% | 80.2% | 124.0 | drawdown (intraday touch) |
| 2x | 16.9% | 83.0% | 43 | drawdown (intraday touch) |
| 3x | 15.5% | 84.5% | 24.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 106, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1635.0)}**  (intraday-low fraction used: 0.195)

### Tradeify Growth $50K: target $3,000, DD $2,000 (eod, locks), DLL $0, consistency 35% of profit, flat by 15:59 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 13.1% | 81.9% | 128 | drawdown (intraday touch) |
| 2x | 11.7% | 87.5% | 45.0 | drawdown (intraday touch) |
| 3x | 9.5% | 90.2% | 25.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 106, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1635.0)}**  (intraday-low fraction used: 0.195)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 25.1% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2681
- days with intraday low <= -$1,000: **0**
- daily t-stat: **-0.77** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **81%**
