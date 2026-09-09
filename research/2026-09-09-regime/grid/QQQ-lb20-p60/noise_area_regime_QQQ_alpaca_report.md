# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 974,
  "trades_per_day": 0.36,
  "net_pnl": 11195.47,
  "mean_trade": 11.49,
  "win_rate": 0.381,
  "avg_win": 167.45,
  "avg_loss": -84.46,
  "payoff": 1.98,
  "profit_factor": 1.22,
  "mean_day": 4.18,
  "sd_day": 103.55,
  "sharpe_daily_ann": 0.64,
  "best_day": 984.24,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -2148.96,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 125,
  "flattens": 284,
  "costs_total": 4554.68,
  "t_stat_daily": 2.09
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst   best
year                                                      
2016   249      89  -740.0      -3.0    90.6 -275.0  628.0
2017   251      24  -134.0      -0.5    40.1 -314.0  256.0
2018   249     121  6110.0      24.5   156.1 -381.0  966.0
2019   252      83  -373.0      -1.5    81.0 -381.0  499.0
2020   253     153  1300.0       5.1   146.6 -387.0  851.0
2021   252      81  2112.0       8.4    91.6 -372.0  596.0
2022   251     213  2862.0      11.4   158.6 -384.0  984.0
2023   250      62  -756.0      -3.0    70.5 -384.0  443.0
2024   252      32    -5.0      -0.0    67.7 -231.0  653.0
2025   250      55  1060.0       4.2    69.8 -373.0  406.0
2026   170      61  -241.0      -1.4    77.7 -292.0  374.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 826,
  "trades_per_day": 0.41,
  "net_pnl": 10381.71,
  "mean_trade": 12.57,
  "win_rate": 0.383,
  "avg_win": 169.22,
  "avg_loss": -84.49,
  "payoff": 2.0,
  "profit_factor": 1.24,
  "mean_day": 5.17,
  "sd_day": 112.33,
  "sharpe_daily_ann": 0.73,
  "best_day": 984.24,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -2148.96,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 103,
  "flattens": 241,
  "costs_total": 4305.78,
  "t_stat_daily": 2.06
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 148,
  "trades_per_day": 0.22,
  "net_pnl": 813.76,
  "mean_trade": 5.5,
  "win_rate": 0.372,
  "avg_win": 157.29,
  "avg_loss": -84.27,
  "payoff": 1.87,
  "profit_factor": 1.1,
  "mean_day": 1.21,
  "sd_day": 71.02,
  "sharpe_daily_ann": 0.27,
  "best_day": 653.2,
  "worst_day": -372.86,
  "worst_intraday_low": -372.86,
  "max_drawdown": -1169.77,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 22,
  "flattens": 43,
  "costs_total": 248.9,
  "t_stat_daily": 0.44
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 34.1% | 19.7% | 247 | drawdown (intraday touch) |
| 2x | 46.9% | 51.6% | 112 | drawdown (intraday touch) |
| 3x | 38.4% | 61.5% | 61 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 360, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1041.0)}**  (intraday-low fraction used: 0.215)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 11.7% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **2.09** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **29%**
