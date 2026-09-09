# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 938,
  "trades_per_day": 0.35,
  "net_pnl": 10727.69,
  "mean_trade": 11.44,
  "win_rate": 0.39,
  "avg_win": 162.26,
  "avg_loss": -85.07,
  "payoff": 1.91,
  "profit_factor": 1.22,
  "mean_day": 4.0,
  "sd_day": 100.87,
  "sharpe_daily_ann": 0.63,
  "best_day": 984.24,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -2228.92,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 119,
  "flattens": 270,
  "costs_total": 4213.06,
  "t_stat_daily": 2.05
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst   best
year                                                      
2016   249      74  -686.0      -2.8    92.3 -384.0  628.0
2017   251      31  -163.0      -0.6    48.6 -215.0  377.0
2018   249     115  6065.0      24.4   154.4 -381.0  966.0
2019   252      76  -489.0      -1.9    75.0 -381.0  499.0
2020   253     149  1283.0       5.1   144.0 -387.0  851.0
2021   252      71  2399.0       9.5    81.9 -191.0  596.0
2022   251     213  2862.0      11.4   158.6 -384.0  984.0
2023   250      52  -205.0      -0.8    56.0 -307.0  443.0
2024   252      42  -324.0      -1.3    64.1 -375.0  586.0
2025   250      53    84.0       0.3    61.1 -373.0  334.0
2026   170      62   -98.0      -0.6    81.5 -292.0  374.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 781,
  "trades_per_day": 0.39,
  "net_pnl": 11066.04,
  "mean_trade": 14.17,
  "win_rate": 0.392,
  "avg_win": 167.55,
  "avg_loss": -84.64,
  "payoff": 1.98,
  "profit_factor": 1.28,
  "mean_day": 5.51,
  "sd_day": 109.72,
  "sharpe_daily_ann": 0.8,
  "best_day": 984.24,
  "worst_day": -387.02,
  "worst_intraday_low": -387.02,
  "max_drawdown": -2228.92,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 95,
  "flattens": 229,
  "costs_total": 3939.08,
  "t_stat_daily": 2.25
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 157,
  "trades_per_day": 0.23,
  "net_pnl": -338.35,
  "mean_trade": -2.16,
  "win_rate": 0.382,
  "avg_win": 135.3,
  "avg_loss": -87.18,
  "payoff": 1.55,
  "profit_factor": 0.96,
  "mean_day": -0.5,
  "sd_day": 67.73,
  "sharpe_daily_ann": -0.12,
  "best_day": 585.72,
  "worst_day": -375.42,
  "worst_intraday_low": -375.42,
  "max_drawdown": -1228.41,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 24,
  "flattens": 41,
  "costs_total": 273.98,
  "t_stat_daily": -0.19
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 31.8% | 19.2% | 251.0 | drawdown (intraday touch) |
| 2x | 47.3% | 51.0% | 117.0 | drawdown (intraday touch) |
| 3x | 38.5% | 61.1% | 65.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'pass', 'days': 704, 'reason': 'target', 'balance': np.float64(3132.0)}**  (intraday-low fraction used: 0.212)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 11.9% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **2.05** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **28%**
