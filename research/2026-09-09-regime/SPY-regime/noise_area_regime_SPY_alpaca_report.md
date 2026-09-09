# Backtest: noise_area on SPY (alpaca)

`instrument=SPY tick=0.01/$0.1 point=$10.0 rt_cost=$0.90 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2681,
  "trades": 1188,
  "trades_per_day": 0.44,
  "net_pnl": -2140.17,
  "mean_trade": -1.8,
  "win_rate": 0.332,
  "avg_win": 169.1,
  "avg_loss": -86.61,
  "payoff": 1.95,
  "profit_factor": 0.97,
  "mean_day": -0.8,
  "sd_day": 118.66,
  "sharpe_daily_ann": -0.11,
  "best_day": 1323.75,
  "worst_day": -384.0,
  "worst_intraday_low": -384.0,
  "max_drawdown": -6841.62,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 188,
  "flattens": 297,
  "costs_total": 11814.3,
  "t_stat_daily": -0.35
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249      88 -1858.0      -7.5    83.1 -368.0   498.0
2017   251      13  -852.0      -3.4    26.1 -263.0    34.0
2018   251     157  2196.0       8.7   179.1 -372.0  1165.0
2019   252     101 -2172.0      -8.6    99.7 -372.0   798.0
2020   253     162 -1261.0      -5.0   153.5 -380.0  1046.0
2021   252      99  3246.0      12.9    97.8 -365.0   608.0
2022   251     206  4239.0      16.9   192.2 -377.0  1324.0
2023   250     115 -1278.0      -5.1   102.1 -384.0   943.0
2024   252      83 -2282.0      -9.1    87.9 -379.0   638.0
2025   250      95 -1294.0      -5.2    82.6 -346.0   432.0
2026   170      69  -824.0      -4.8    89.0 -331.0   442.0
```

## IN-SAMPLE — 2009 sessions
```
{
  "sessions": 2009,
  "trades": 941,
  "trades_per_day": 0.47,
  "net_pnl": 2259.77,
  "mean_trade": 2.4,
  "win_rate": 0.336,
  "avg_win": 178.16,
  "avg_loss": -86.46,
  "payoff": 2.06,
  "profit_factor": 1.04,
  "mean_day": 1.12,
  "sd_day": 127.67,
  "sharpe_daily_ann": 0.14,
  "best_day": 1323.75,
  "worst_day": -384.0,
  "worst_intraday_low": -384.0,
  "max_drawdown": -4758.07,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 147,
  "flattens": 244,
  "costs_total": 10280.7,
  "t_stat_daily": 0.39
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 247,
  "trades_per_day": 0.37,
  "net_pnl": -4399.94,
  "mean_trade": -17.81,
  "win_rate": 0.316,
  "avg_win": 132.41,
  "avg_loss": -87.15,
  "payoff": 1.52,
  "profit_factor": 0.7,
  "mean_day": -6.55,
  "sd_day": 86.06,
  "sharpe_daily_ann": -1.21,
  "best_day": 638.4,
  "worst_day": -378.8,
  "worst_intraday_low": -378.8,
  "max_drawdown": -4999.14,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 41,
  "flattens": 53,
  "costs_total": 1533.6,
  "t_stat_daily": -1.97
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 13.4% | 64.0% | 201.0 | drawdown (intraday touch) |
| 2x | 19.7% | 79.7% | 76 | drawdown (intraday touch) |
| 3x | 17.6% | 82.1% | 43.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 186, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1635.0)}**  (intraday-low fraction used: 0.199)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 16.7% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2681
- days with intraday low <= -$1,000: **0**
- daily t-stat: **-0.35** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **85%**
