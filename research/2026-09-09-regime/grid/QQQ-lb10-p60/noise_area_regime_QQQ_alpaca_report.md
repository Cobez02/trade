# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 945,
  "trades_per_day": 0.35,
  "net_pnl": 13073.66,
  "mean_trade": 13.83,
  "win_rate": 0.396,
  "avg_win": 167.43,
  "avg_loss": -86.77,
  "payoff": 1.93,
  "profit_factor": 1.26,
  "mean_day": 4.88,
  "sd_day": 103.65,
  "sharpe_daily_ann": 0.75,
  "best_day": 984.24,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -2489.62,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 122,
  "flattens": 276,
  "costs_total": 4254.1,
  "t_stat_daily": 2.44
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst   best
year                                                      
2016   249      68   477.0       1.9   102.5 -384.0  805.0
2017   251      35  -568.0      -2.3    47.7 -215.0  264.0
2018   249     116  6318.0      25.4   153.7 -381.0  966.0
2019   252      80 -1124.0      -4.5    71.7 -381.0  317.0
2020   253     144  1054.0       4.2   144.4 -387.0  851.0
2021   252      73  2430.0       9.6    88.6 -223.0  596.0
2022   251     208  2485.0       9.9   156.0 -384.0  984.0
2023   250      50  1009.0       4.0    73.4 -191.0  662.0
2024   252      44   284.0       1.1    69.2 -375.0  653.0
2025   250      65   264.0       1.1    73.1 -373.0  406.0
2026   170      62   446.0       2.6    80.9 -292.0  374.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 774,
  "trades_per_day": 0.39,
  "net_pnl": 12080.01,
  "mean_trade": 15.61,
  "win_rate": 0.394,
  "avg_win": 171.97,
  "avg_loss": -86.08,
  "payoff": 2.0,
  "profit_factor": 1.3,
  "mean_day": 6.02,
  "sd_day": 111.91,
  "sharpe_daily_ann": 0.85,
  "best_day": 984.24,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -2489.62,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 96,
  "flattens": 227,
  "costs_total": 3946.68,
  "t_stat_daily": 2.41
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 171,
  "trades_per_day": 0.25,
  "net_pnl": 993.65,
  "mean_trade": 5.81,
  "win_rate": 0.404,
  "avg_win": 147.39,
  "avg_loss": -89.96,
  "payoff": 1.64,
  "profit_factor": 1.11,
  "mean_day": 1.48,
  "sd_day": 73.56,
  "sharpe_daily_ann": 0.32,
  "best_day": 653.2,
  "worst_day": -375.42,
  "worst_intraday_low": -375.42,
  "max_drawdown": -902.81,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 26,
  "flattens": 49,
  "costs_total": 307.42,
  "t_stat_daily": 0.52
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 38.9% | 15.6% | 253.0 | drawdown (intraday touch) |
| 2x | 51.6% | 47.2% | 117 | drawdown (intraday touch) |
| 3x | 41.4% | 58.2% | 66 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'pass', 'days': 631, 'reason': 'target', 'balance': np.float64(3257.0)}**  (intraday-low fraction used: 0.213)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 11.5% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **2.44** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **25%**
