# Pair backtest: SPY vs QQQ (alpaca)

z_entry 1.5, z_exit 0.5, stop 1.0 sigma, risk $200, slippage 0.045/0.019 $/share/side, regime=False

## Summary
```
{
  "sessions": 2679,
  "trades": 1209,
  "trades_per_day": 0.45,
  "net_pnl": -9326.9,
  "mean_trade": -7.71,
  "win_rate": 0.458,
  "avg_win": 115.87,
  "avg_loss": -112.24,
  "payoff": 1.03,
  "profit_factor": 0.87,
  "mean_day": -3.48,
  "sd_day": 97.59,
  "sharpe_daily_ann": -0.57,
  "best_day": 757.77,
  "worst_day": -495.77,
  "worst_intraday_low": -495.77,
  "max_drawdown": -11403.67,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "costs_total": 30174.73,
  "t_stat_daily": -1.85,
  "exits": {
    "reverted": 184,
    "stop": 241,
    "flatten": 784,
    "daily kill": 0
  }
}
```

## By year
```
      days  trades     pnl  mean_day  sd_day  worst   best
year                                                      
2016   249     117 -1264.0      -5.1    77.0 -264.0  269.0
2017   251     107 -1983.0      -7.9    69.0 -348.0  243.0
2018   249     120 -1137.0      -4.6   113.2 -447.0  352.0
2019   252     113  -746.0      -3.0    64.1 -266.0  193.0
2020   253     111 -1709.0      -6.8   139.7 -358.0  758.0
2021   252     123  -202.0      -0.8   107.4 -408.0  321.0
2022   251     113  -895.0      -3.6   124.5 -496.0  417.0
2023   250     106  -293.0      -1.2    92.0 -264.0  359.0
2024   252     116 -1126.0      -4.5    78.2 -354.0  290.0
2025   250     108 -1678.0      -6.7    78.7 -320.0  276.0
2026   170      75  1707.0      10.0   100.9 -287.0  444.0
```

## IN-SAMPLE — 2007 sessions
```
{
  "sessions": 2007,
  "trades": 910,
  "trades_per_day": 0.45,
  "net_pnl": -8229.39,
  "mean_trade": -9.04,
  "win_rate": 0.457,
  "avg_win": 119.57,
  "avg_loss": -117.35,
  "payoff": 1.02,
  "profit_factor": 0.86,
  "mean_day": -4.1,
  "sd_day": 101.51,
  "sharpe_daily_ann": -0.64,
  "best_day": 757.77,
  "worst_day": -495.77,
  "worst_intraday_low": -495.77,
  "max_drawdown": -8989.34,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "costs_total": 26227.67,
  "t_stat_daily": -1.81,
  "exits": {
    "reverted": 147,
    "stop": 187,
    "flatten": 576,
    "daily kill": 0
  }
}
```

## HOLDOUT — 672 sessions
```
{
  "sessions": 672,
  "trades": 299,
  "trades_per_day": 0.44,
  "net_pnl": -1097.51,
  "mean_trade": -3.67,
  "win_rate": 0.462,
  "avg_win": 104.71,
  "avg_loss": -96.57,
  "payoff": 1.08,
  "profit_factor": 0.93,
  "mean_day": -1.63,
  "sd_day": 84.76,
  "sharpe_daily_ann": -0.31,
  "best_day": 443.87,
  "worst_day": -354.03,
  "worst_intraday_low": -359.29,
  "max_drawdown": -3735.88,
  "days_below_-1000": 0,
  "days_below_-700": 0,
  "costs_total": 3947.06,
  "t_stat_daily": -0.5,
  "exits": {
    "reverted": 37,
    "stop": 54,
    "flatten": 208,
    "daily kill": 0
  }
}
```

## Prop-rule Monte Carlo — MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True
P(pass) 1x = **3.2%**, median days 192.0
Zero-edge control: P(pass) = 11.9%