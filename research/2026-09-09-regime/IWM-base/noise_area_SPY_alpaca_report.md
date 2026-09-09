# Backtest: noise_area on SPY (alpaca)

`instrument=SPY tick=0.01/$0.1 point=$10.0 rt_cost=$0.72 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2681,
  "trades": 2216,
  "trades_per_day": 0.83,
  "net_pnl": -21861.02,
  "mean_trade": -9.87,
  "win_rate": 0.308,
  "avg_win": 156.9,
  "avg_loss": -84.16,
  "payoff": 1.86,
  "profit_factor": 0.83,
  "mean_day": -8.15,
  "sd_day": 146.52,
  "sharpe_daily_ann": -0.88,
  "best_day": 1283.63,
  "worst_day": -386.12,
  "worst_intraday_low": -386.12,
  "max_drawdown": -24185.83,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 282,
  "flattens": 617,
  "costs_total": 28388.88,
  "t_stat_daily": -2.88
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249     208 -1233.0      -5.0   147.0 -368.0   617.0
2017   251     202 -4166.0     -16.6   140.5 -367.0   975.0
2018   251     217  1990.0       7.9   192.2 -369.0  1284.0
2019   252     207 -2245.0      -8.9   145.1 -318.0  1016.0
2020   253     199 -5848.0     -23.1   139.3 -373.0  1178.0
2021   252     210  -526.0      -2.1   137.7 -386.0   743.0
2022   251     230 -1838.0      -7.3   153.7 -383.0  1234.0
2023   250     213  -893.0      -3.6   141.5 -373.0   911.0
2024   252     202 -4515.0     -17.9   131.3 -381.0   691.0
2025   250     197 -1230.0      -4.9   147.4 -372.0   773.0
2026   170     131 -1357.0      -8.0   116.5 -380.0   451.0
```

## IN-SAMPLE — 2009 sessions
```
{
  "sessions": 2009,
  "trades": 1686,
  "trades_per_day": 0.84,
  "net_pnl": -14759.13,
  "mean_trade": -8.75,
  "win_rate": 0.311,
  "avg_win": 159.28,
  "avg_loss": -84.53,
  "payoff": 1.88,
  "profit_factor": 0.85,
  "mean_day": -7.35,
  "sd_day": 150.5,
  "sharpe_daily_ann": -0.77,
  "best_day": 1283.63,
  "worst_day": -386.12,
  "worst_intraday_low": -386.12,
  "max_drawdown": -17238.17,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 213,
  "flattens": 478,
  "costs_total": 23727.6,
  "t_stat_daily": -2.19
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 530,
  "trades_per_day": 0.79,
  "net_pnl": -7101.89,
  "mean_trade": -13.4,
  "win_rate": 0.3,
  "avg_win": 149.04,
  "avg_loss": -83.02,
  "payoff": 1.8,
  "profit_factor": 0.77,
  "mean_day": -10.57,
  "sd_day": 133.92,
  "sharpe_daily_ann": -1.25,
  "best_day": 772.52,
  "worst_day": -380.94,
  "worst_intraday_low": -380.94,
  "max_drawdown": -8352.3,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 69,
  "flattens": 139,
  "costs_total": 4661.28,
  "t_stat_daily": -2.05
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 5.4% | 93.2% | 109.0 | drawdown (intraday touch) |
| 2x | 10.1% | 89.9% | 39.0 | drawdown (intraday touch) |
| 3x | 10.2% | 89.8% | 22.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 142, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-622.0)}**  (intraday-low fraction used: 0.216)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 23.7% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2681
- days with intraday low <= -$1,000: **0**
- daily t-stat: **-2.88** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **56%**
