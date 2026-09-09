# Backtest: lasthalf on SPY (alpaca)

`instrument=SPY tick=0.01/$0.1 point=$10.0 rt_cost=$0.90 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:58 CT, no entries after 14:35 CT`

## Summary (all sessions)
```
{
  "sessions": 2681,
  "trades": 1265,
  "trades_per_day": 0.47,
  "net_pnl": -27727.04,
  "mean_trade": -21.92,
  "win_rate": 0.404,
  "avg_win": 110.28,
  "avg_loss": -111.51,
  "payoff": 0.99,
  "profit_factor": 0.67,
  "mean_day": -10.34,
  "sd_day": 101.42,
  "sharpe_daily_ann": -1.62,
  "best_day": 1266.2,
  "worst_day": -197.4,
  "worst_intraday_low": -197.4,
  "max_drawdown": -27748.57,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 291,
  "flattens": 974,
  "costs_total": 27680.4,
  "t_stat_daily": -5.28
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249     113 -4670.0     -18.8    75.1 -185.0   403.0
2017   251     116 -7868.0     -31.3    77.9 -180.0   295.0
2018   251     121 -1300.0      -5.2   126.2 -193.0   988.0
2019   252     107 -1916.0      -7.6    80.3 -186.0   416.0
2020   253     124 -1004.0      -4.0   146.3 -197.0  1266.0
2021   252     101 -4437.0     -17.6    87.8 -192.0   405.0
2022   251     126  -912.0      -3.6    93.6 -193.0   355.0
2023   250     127 -3094.0     -12.4    81.9 -190.0   385.0
2024   252     117   182.0       0.7   126.3 -190.0   943.0
2025   250     128 -1565.0      -6.3    94.0 -193.0   400.0
2026   170      85 -1142.0      -6.7    93.0 -191.0   654.0
```

## IN-SAMPLE — 2009 sessions
```
{
  "sessions": 2009,
  "trades": 935,
  "trades_per_day": 0.47,
  "net_pnl": -25202.46,
  "mean_trade": -26.95,
  "win_rate": 0.385,
  "avg_win": 107.97,
  "avg_loss": -111.43,
  "payoff": 0.97,
  "profit_factor": 0.61,
  "mean_day": -12.54,
  "sd_day": 99.44,
  "sharpe_daily_ann": -2.0,
  "best_day": 1266.2,
  "worst_day": -197.4,
  "worst_intraday_low": -197.4,
  "max_drawdown": -25226.19,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 228,
  "flattens": 707,
  "costs_total": 23297.4,
  "t_stat_daily": -5.65
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 330,
  "trades_per_day": 0.49,
  "net_pnl": -2524.58,
  "mean_trade": -7.65,
  "win_rate": 0.458,
  "avg_win": 115.8,
  "avg_loss": -111.79,
  "payoff": 1.04,
  "profit_factor": 0.87,
  "mean_day": -3.76,
  "sd_day": 106.85,
  "sharpe_daily_ann": -0.56,
  "best_day": 943.0,
  "worst_day": -193.2,
  "worst_intraday_low": -193.2,
  "max_drawdown": -4059.59,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 63,
  "flattens": 267,
  "costs_total": 4383.0,
  "t_stat_daily": -0.91
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 0.7% | 97.5% | 128.0 | drawdown (intraday touch) |
| 2x | 2.9% | 97.0% | 52 | drawdown (intraday touch) |
| 3x | 4.4% | 95.6% | 31.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 121, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-2000.0)}**  (intraday-low fraction used: 0.229)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 13.9% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2681
- days with intraday low <= -$1,000: **0**
- daily t-stat: **-5.28** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **50%**
