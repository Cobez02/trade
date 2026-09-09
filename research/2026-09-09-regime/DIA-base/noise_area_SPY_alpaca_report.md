# Backtest: noise_area on SPY (alpaca)

`instrument=SPY tick=0.01/$0.1 point=$10.0 rt_cost=$1.24 | bars=30m lookback=14d mult=1.0 trail=band | risk/trade=$200 kill=$700 cap=$1200 maxq=5 | flatten 14:52 CT, no entries after 14:00 CT`

## Summary (all sessions)
```
{
  "sessions": 2681,
  "trades": 2078,
  "trades_per_day": 0.78,
  "net_pnl": -39589.11,
  "mean_trade": -19.05,
  "win_rate": 0.281,
  "avg_win": 165.09,
  "avg_loss": -91.03,
  "payoff": 1.81,
  "profit_factor": 0.71,
  "mean_day": -14.77,
  "sd_day": 142.72,
  "sharpe_daily_ann": -1.64,
  "best_day": 1461.96,
  "worst_day": -376.72,
  "worst_intraday_low": -376.72,
  "max_drawdown": -39862.15,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 333,
  "flattens": 575,
  "costs_total": 41153.12,
  "t_stat_daily": -5.36
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst    best
year                                                       
2016   249     205 -7765.0     -31.2   142.3 -366.0  1225.0
2017   251     167 -6375.0     -25.4   126.9 -363.0   595.0
2018   251     205  -758.0      -3.0   180.3 -370.0  1462.0
2019   252     162 -3878.0     -15.4   102.7 -366.0   611.0
2020   253     202 -4060.0     -16.0   149.6 -376.0  1007.0
2021   252     177 -1798.0      -7.1   119.5 -354.0   664.0
2022   251     211 -2850.0     -11.4   179.4 -371.0  1407.0
2023   250     211 -5012.0     -20.0   145.4 -377.0   676.0
2024   252     217 -3661.0     -14.5   138.3 -369.0   615.0
2025   250     199 -1346.0      -5.4   141.9 -369.0   626.0
2026   170     122 -2085.0     -12.3   115.7 -373.0   633.0
```

## IN-SAMPLE — 2009 sessions
```
{
  "sessions": 2009,
  "trades": 1540,
  "trades_per_day": 0.77,
  "net_pnl": -32496.98,
  "mean_trade": -21.1,
  "win_rate": 0.273,
  "avg_win": 169.27,
  "avg_loss": -92.49,
  "payoff": 1.83,
  "profit_factor": 0.69,
  "mean_day": -16.18,
  "sd_day": 145.45,
  "sharpe_daily_ann": -1.77,
  "best_day": 1461.96,
  "worst_day": -376.72,
  "worst_intraday_low": -376.72,
  "max_drawdown": -32770.03,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 263,
  "flattens": 417,
  "costs_total": 33941.28,
  "t_stat_daily": -4.98
}
```

## HOLDOUT (out-of-sample) — 672 sessions
```
{
  "sessions": 672,
  "trades": 538,
  "trades_per_day": 0.8,
  "net_pnl": -7092.13,
  "mean_trade": -13.18,
  "win_rate": 0.305,
  "avg_win": 154.4,
  "avg_loss": -86.67,
  "payoff": 1.78,
  "profit_factor": 0.78,
  "mean_day": -10.55,
  "sd_day": 134.12,
  "sharpe_daily_ann": -1.25,
  "best_day": 632.88,
  "worst_day": -372.88,
  "worst_intraday_low": -372.88,
  "max_drawdown": -7988.37,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "stop_outs": 70,
  "flattens": 158,
  "costs_total": 7211.84,
  "t_stat_daily": -2.04
}
```

## Prop-rule Monte Carlo (block bootstrap of this backtest's daily P&L)

Scale = multiple of the sizing above. P(pass) is the share of bootstrapped
paths that hit the target before touching the trailing floor (intraday-touch aware).

### MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

| scale | P(pass) | P(fail) | median days | top fail reason |
|---|---|---|---|---|
| 1x | 1.4% | 98.4% | 87.0 | drawdown (intraday touch) |
| 2x | 3.5% | 96.5% | 34.0 | drawdown (intraday touch) |
| 3x | 5.7% | 94.3% | 20.0 | drawdown (intraday touch) |

This exact history replayed once at 1x: **{'result': 'fail', 'days': 75, 'reason': 'drawdown (intraday touch)', 'balance': np.float64(-1727.0)}**  (intraday-low fraction used: 0.194)

Zero-edge control (same daily P&L, mean removed) on MyFundedFutures Core $50K: P(pass) = 23.1% — anything close to this number is luck, not edge.

## Falsification gates (from the research brief)

- days with intraday low <= -$700: **0** of 2681
- days with intraday low <= -$1,000: **0**
- daily t-stat: **-5.36** (want > 2 on the holdout, not just in-sample)
- costs as share of gross: **51%**
