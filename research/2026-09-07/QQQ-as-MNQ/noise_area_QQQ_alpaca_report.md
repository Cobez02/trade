# Backtest: noise_area on QQQ (alpaca)

`instrument=QQQ tick=0.01/$0.8 point=$80.0 rt_cost=$3.04 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2679,
  "trades": 81,
  "trades_per_day": 0.03,
  "net_pnl": -878.82,
  "mean_trade": -10.85,
  "win_rate": 0.272,
  "avg_win": 180.5,
  "avg_loss": -82.2,
  "payoff": 2.2,
  "profit_factor": 0.82,
  "mean_day": -0.33,
  "sd_day": 25.61,
  "sharpe_daily_ann": -0.2,
  "best_day": 535.68,
  "worst_day": -326.4,
  "worst_intraday_low": -326.4,
  "max_drawdown": -1713.8,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 15,
  "flattens": 24,
  "costs_total": 896.8,
  "t_stat_daily": -0.66
}
```

## By year
```
      days    pnl  mean_day  sd_day  worst   best
year                                             
2016   249 -879.0      -3.5    84.1 -326.0  536.0
2017   251    0.0       0.0     0.0    0.0    0.0
2018   249    0.0       0.0     0.0    0.0    0.0
2019   252    0.0       0.0     0.0    0.0    0.0
2020   253    0.0       0.0     0.0    0.0    0.0
2021   252    0.0       0.0     0.0    0.0    0.0
2022   251    0.0       0.0     0.0    0.0    0.0
2023   250    0.0       0.0     0.0    0.0    0.0
2024   252    0.0       0.0     0.0    0.0    0.0
2025   250    0.0       0.0     0.0    0.0    0.0
2026   170    0.0       0.0     0.0    0.0    0.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 81,
  "trades_per_day": 0.04,
  "net_pnl": -878.82,
  "mean_trade": -10.85,
  "win_rate": 0.272,
  "avg_win": 180.5,
  "avg_loss": -82.2,
  "payoff": 2.2,
  "profit_factor": 0.82,
  "mean_day": -0.44,
  "sd_day": 29.59,
  "sharpe_daily_ann": -0.23,
  "best_day": 535.68,
  "worst_day": -326.4,
  "worst_intraday_low": -326.4,
  "max_drawdown": -1713.8,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 15,
  "flattens": 24,
  "costs_total": 896.8,
  "t_stat_daily": -0.66
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "trades": 0
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 0.0% | 0.0% | 362.5 | drawdown (intraday touch) |
| 2x | 0.3% | 7.7% | 294 | drawdown (intraday touch) |
| 3x | 2.1% | 27.2% | 267.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'open', 'days': 2679, 'reason': 'still running', 'balance': np.float64(-879.0)}**  (intraday-low fraction used: 0.195)

### Topstep Combine $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of target, flat by 15:10 CT, bots on funded: False

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 0.0% | 0.0% | 362.5 | drawdown (intraday touch) |
| 2x | 0.2% | 7.7% | 292 | drawdown (intraday touch) |
| 3x | 1.6% | 27.3% | 269 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'open', 'days': 2679, 'reason': 'still running', 'balance': np.float64(-879.0)}**  (intraday-low fraction used: 0.195)

### Tradeify Growth $50K: target $3,000, DD $2,000 (eod, locks), DLL $0, consistency 35% of profit, flat by 15:59 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 0.0% | 0.0% | 362.5 | drawdown (intraday touch) |
| 2x | 0.3% | 7.7% | 295 | drawdown (intraday touch) |
| 3x | 0.5% | 27.3% | 265.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'open', 'days': 2679, 'reason': 'still running', 'balance': np.float64(-879.0)}**  (intraday-low fraction used: 0.195)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 0.0% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2679
- days with intraday low <= -$1,000: **0**
- daily t-stat: **-0.66** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **51%**
