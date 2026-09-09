# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 1423,
  "trades_per_day": 0.53,
  "net_pnl": 16570.67,
  "mean_trade": 11.64,
  "win_rate": 0.376,
  "avg_win": 171.32,
  "avg_loss": -84.56,
  "payoff": 2.03,
  "profit_factor": 1.22,
  "mean_day": 6.19,
  "sd_day": 125.77,
  "sharpe_daily_ann": 0.78,
  "best_day": 984.24,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -3712.47,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 181,
  "flattens": 431,
  "costs_total": 7046.72,
  "t_stat_daily": 2.55
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst   best
year                                                      
2016   249     103   379.0       1.5   111.7 -384.0  805.0
2017   251      74  -863.0      -3.4    75.5 -314.0  517.0
2018   249     151  6243.0      25.1   168.7 -384.0  966.0
2019   252     127 -2337.0      -9.3    97.8 -382.0  520.0
2020   253     164  1756.0       6.9   154.8 -387.0  851.0
2021   252     130  4626.0      18.4   125.7 -372.0  634.0
2022   251     213  2862.0      11.4   158.6 -384.0  984.0
2023   250     142  2634.0      10.5   134.1 -384.0  864.0
2024   252     116  1384.0       5.5   122.6 -375.0  653.0
2025   250     102  -207.0      -0.8    87.9 -373.0  406.0
2026   170     101    94.0       0.6    99.9 -292.0  374.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 1104,
  "trades_per_day": 0.55,
  "net_pnl": 15299.66,
  "mean_trade": 13.86,
  "win_rate": 0.376,
  "avg_win": 176.34,
  "avg_loss": -84.01,
  "payoff": 2.1,
  "profit_factor": 1.26,
  "mean_day": 7.62,
  "sd_day": 132.0,
  "sharpe_daily_ann": 0.92,
  "best_day": 984.24,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -3712.47,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 135,
  "flattens": 330,
  "costs_total": 6350.94,
  "t_stat_daily": 2.59
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 319,
  "trades_per_day": 0.47,
  "net_pnl": 1271.01,
  "mean_trade": 3.98,
  "win_rate": 0.376,
  "avg_win": 153.94,
  "avg_loss": -86.44,
  "payoff": 1.78,
  "profit_factor": 1.07,
  "mean_day": 1.89,
  "sd_day": 104.87,
  "sharpe_daily_ann": 0.29,
  "best_day": 653.2,
  "worst_day": -375.42,
  "worst_intraday_low": -375.42,
  "max_drawdown": -1866.91,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 46,
  "flattens": 101,
  "costs_total": 695.78,
  "t_stat_daily": 0.47
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 51.5% | 26.1% | 211 | drawdown (intraday touch) |
| 2x | 48.2% | 51.7% | 75.0 | drawdown (intraday touch) |
| 3x | 38.5% | 61.5% | 41 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 483, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-291.0)}**  (intraday-low fraction used: 0.216)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 18.7% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **2.55** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **30%**
