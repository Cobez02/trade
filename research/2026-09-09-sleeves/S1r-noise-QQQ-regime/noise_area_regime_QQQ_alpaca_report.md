# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 1236,
  "trades_per_day": 0.46,
  "net_pnl": 24404.94,
  "mean_trade": 19.75,
  "win_rate": 0.402,
  "avg_win": 176.69,
  "avg_loss": -85.81,
  "payoff": 2.06,
  "profit_factor": 1.38,
  "mean_day": 9.11,
  "sd_day": 123.5,
  "sharpe_daily_ann": 1.17,
  "best_day": 1587.92,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -2497.67,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 156,
  "flattens": 396,
  "costs_total": 5783.98,
  "t_stat_daily": 3.82
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249      92  1109.0       4.5   110.2 -384.0   805.0
2017   251      57  1192.0       4.7   119.1 -314.0  1588.0
2018   249     140  7851.0      31.5   165.7 -381.0   966.0
2019   252     103  -860.0      -3.4    89.9 -381.0   520.0
2020   253     154  2361.0       9.3   152.9 -387.0   851.0
2021   252     110  3374.0      13.4   108.4 -238.0   596.0
2022   251     213  2862.0      11.4   158.6 -384.0   984.0
2023   250     102  2641.0      10.6   116.1 -384.0   670.0
2024   252      85  2536.0      10.1   113.5 -375.0   653.0
2025   250      85   154.0       0.6    84.7 -373.0   406.0
2026   170      95  1185.0       7.0   102.1 -292.0   432.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 971,
  "trades_per_day": 0.48,
  "net_pnl": 20530.47,
  "mean_trade": 21.14,
  "win_rate": 0.4,
  "avg_win": 180.86,
  "avg_loss": -85.15,
  "payoff": 2.12,
  "profit_factor": 1.41,
  "mean_day": 10.23,
  "sd_day": 130.27,
  "sharpe_daily_ann": 1.25,
  "best_day": 1587.92,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -2497.67,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 119,
  "flattens": 310,
  "costs_total": 5255.78,
  "t_stat_daily": 3.52
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 265,
  "trades_per_day": 0.39,
  "net_pnl": 3874.47,
  "mean_trade": 14.62,
  "win_rate": 0.411,
  "avg_win": 161.87,
  "avg_loss": -88.27,
  "payoff": 1.83,
  "profit_factor": 1.28,
  "mean_day": 5.77,
  "sd_day": 100.54,
  "sharpe_daily_ann": 0.91,
  "best_day": 653.2,
  "worst_day": -375.42,
  "worst_intraday_low": -375.42,
  "max_drawdown": -947.98,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 37,
  "flattens": 86,
  "costs_total": 528.2,
  "t_stat_daily": 1.49
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 67.2% | 12.7% | 210 | drawdown (intraday touch) |
| 2x | 61.7% | 37.9% | 85 | drawdown (intraday touch) |
| 3x | 49.2% | 50.6% | 47.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'pass', 'days': 373, 'reason': 'target', 'balance': np.float64(3228.0)}**  (intraday-low fraction used: 0.216)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 16.9% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **3.82** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **19%**
