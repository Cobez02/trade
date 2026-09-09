# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 1203,
  "trades_per_day": 0.45,
  "net_pnl": 10701.34,
  "mean_trade": 8.9,
  "win_rate": 0.379,
  "avg_win": 166.0,
  "avg_loss": -87.01,
  "payoff": 1.91,
  "profit_factor": 1.16,
  "mean_day": 3.99,
  "sd_day": 113.49,
  "sharpe_daily_ann": 0.56,
  "best_day": 984.24,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -3028.87,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 162,
  "flattens": 351,
  "costs_total": 5602.34,
  "t_stat_daily": 1.82
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst   best
year                                                      
2016   249      88  -782.0      -3.1    95.6 -384.0  628.0
2017   251      48  -950.0      -3.8    59.7 -314.0  377.0
2018   249     138  6010.0      24.1   160.4 -381.0  966.0
2019   252     101 -1643.0      -6.5    84.1 -381.0  499.0
2020   253     153  1340.0       5.3   146.1 -387.0  851.0
2021   252     109  1934.0       7.7   103.6 -372.0  596.0
2022   251     213  2862.0      11.4   158.6 -384.0  984.0
2023   250      98  1079.0       4.3   104.4 -384.0  662.0
2024   252      80   540.0       2.1   104.7 -375.0  653.0
2025   250      84   355.0       1.4    84.4 -373.0  406.0
2026   170      91   -44.0      -0.3    91.3 -292.0  374.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 948,
  "trades_per_day": 0.47,
  "net_pnl": 9849.63,
  "mean_trade": 10.39,
  "win_rate": 0.378,
  "avg_win": 170.12,
  "avg_loss": -86.53,
  "payoff": 1.97,
  "profit_factor": 1.19,
  "mean_day": 4.91,
  "sd_day": 119.3,
  "sharpe_daily_ann": 0.65,
  "best_day": 984.24,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -3028.87,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 123,
  "flattens": 276,
  "costs_total": 5082.88,
  "t_stat_daily": 1.84
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 255,
  "trades_per_day": 0.38,
  "net_pnl": 851.71,
  "mean_trade": 3.34,
  "win_rate": 0.384,
  "avg_win": 150.96,
  "avg_loss": -88.81,
  "payoff": 1.7,
  "profit_factor": 1.06,
  "mean_day": 1.27,
  "sd_day": 93.96,
  "sharpe_daily_ann": 0.21,
  "best_day": 653.2,
  "worst_day": -375.42,
  "worst_intraday_low": -375.42,
  "max_drawdown": -1448.96,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 39,
  "flattens": 75,
  "costs_total": 519.46,
  "t_stat_daily": 0.35
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 35.6% | 30.3% | 229 | drawdown (intraday touch) |
| 2x | 41.6% | 58.1% | 88.0 | drawdown (intraday touch) |
| 3x | 33.8% | 66.1% | 46 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 360, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1041.0)}**  (intraday-low fraction used: 0.218)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 15.0% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **1.82** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **34%**
