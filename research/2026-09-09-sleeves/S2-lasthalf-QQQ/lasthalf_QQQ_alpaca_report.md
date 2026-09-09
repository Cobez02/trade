# Backtest: lasthalf on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.1 point=$10.0 rt_cost=$0.38 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:58 CT, no entries after 14:35 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 1341,
  "trades_per_day": 0.5,
  "net_pnl": -12707.88,
  "mean_trade": -9.48,
  "win_rate": 0.453,
  "avg_win": 100.22,
  "avg_loss": -100.46,
  "payoff": 1.0,
  "profit_factor": 0.83,
  "mean_day": -4.74,
  "sd_day": 94.63,
  "sharpe_daily_ann": -0.8,
  "best_day": 965.79,
  "worst_day": -198.24,
  "worst_intraday_low": -198.24,
  "max_drawdown": -15917.16,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 225,
  "flattens": 1113,
  "costs_total": 13961.96,
  "t_stat_daily": -2.59
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst   best
year                                                      
2016   249     113 -1795.0      -7.2    57.5 -189.0  250.0
2017   251     129 -3466.0     -13.8    61.9 -190.0  271.0
2018   249     119   788.0       3.2   124.0 -196.0  928.0
2019   252     117 -2340.0      -9.3    78.4 -193.0  367.0
2020   253     139  1650.0       6.5   138.6 -196.0  966.0
2021   252     105 -3435.0     -13.6    81.5 -197.0  281.0
2022   251     136  -876.0      -3.5    89.2 -198.0  286.0
2023   250     143 -3180.0     -12.7    84.4 -196.0  262.0
2024   252     133 -1466.0      -5.8   106.3 -195.0  762.0
2025   250     124  -353.0      -1.4    91.8 -198.0  332.0
2026   170      83  1765.0      10.4    93.9 -196.0  625.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 1001,
  "trades_per_day": 0.5,
  "net_pnl": -12653.85,
  "mean_trade": -12.64,
  "win_rate": 0.449,
  "avg_win": 95.73,
  "avg_loss": -100.79,
  "payoff": 0.95,
  "profit_factor": 0.77,
  "mean_day": -6.3,
  "sd_day": 93.43,
  "sharpe_daily_ann": -1.07,
  "best_day": 965.79,
  "worst_day": -197.73,
  "worst_intraday_low": -197.73,
  "max_drawdown": -12715.43,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 174,
  "flattens": 824,
  "costs_total": 12280.84,
  "t_stat_daily": -3.02
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 340,
  "trades_per_day": 0.51,
  "net_pnl": -54.03,
  "mean_trade": -0.16,
  "win_rate": 0.468,
  "avg_win": 112.87,
  "avg_loss": -99.45,
  "payoff": 1.13,
  "profit_factor": 1.0,
  "mean_day": -0.08,
  "sd_day": 97.99,
  "sharpe_daily_ann": -0.01,
  "best_day": 762.24,
  "worst_day": -198.24,
  "worst_intraday_low": -198.24,
  "max_drawdown": -3851.45,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 51,
  "flattens": 289,
  "costs_total": 1681.12,
  "t_stat_daily": -0.02
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 2.4% | 82.7% | 185 | drawdown (intraday touch) |
| 2x | 9.4% | 90.4% | 73.0 | drawdown (intraday touch) |
| 3x | 10.6% | 89.3% | 40 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 257, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1975.0)}**  (intraday-low fraction used: 0.267)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 11.5% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **-2.59** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **52%**
