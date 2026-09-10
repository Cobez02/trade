# Overnight sleeve: IWM (alpaca), notional $12,000, slip 0.035/share/side, weekend=no

## Summary
```
{
  "nights": 2145,
  "net_pnl": 3099.54,
  "mean_night": 1.45,
  "sd_night": 101.34,
  "win_rate": 0.524,
  "profit_factor": 1.04,
  "sharpe_ann": 0.23,
  "t_stat": 0.66,
  "worst_night": -878.41,
  "best_night": 694.73,
  "max_drawdown": -3227.31,
  "nights_below_-700": 1,
  "nights_below_-1000": 0
}
```

## By year
```
      count     sum  mean    std    min
year                                   
2016    201 -1964.0  -9.8   75.3 -513.2
2017    200     5.9   0.0   45.9 -207.6
2018    199  -298.9  -1.5   56.3 -205.1
2019    201   899.6   4.5   58.4 -193.2
2020    204  2655.5  13.0  196.3 -878.4
2021    203  1733.5   8.5   84.7 -346.1
2022    200 -1070.4  -5.4  110.9 -311.8
2023    199  -137.7  -0.7   86.6 -321.4
2024    200  2058.7  10.3  110.8 -435.3
2025    200 -1359.1  -6.8  105.1 -571.8
2026    138   576.4   4.2   94.0 -322.4
```

## IN-SAMPLE
```
{
  "nights": 1607,
  "net_pnl": 1823.54,
  "mean_night": 1.13,
  "sd_night": 100.24,
  "win_rate": 0.523,
  "profit_factor": 1.04,
  "sharpe_ann": 0.18,
  "t_stat": 0.45,
  "worst_night": -878.41,
  "best_night": 694.73,
  "max_drawdown": -3227.31,
  "nights_below_-700": 1,
  "nights_below_-1000": 0
}
```

## HOLDOUT
```
{
  "nights": 538,
  "net_pnl": 1276.0,
  "mean_night": 2.37,
  "sd_night": 104.54,
  "win_rate": 0.528,
  "profit_factor": 1.07,
  "sharpe_ann": 0.36,
  "t_stat": 0.53,
  "worst_night": -571.78,
  "best_night": 656.99,
  "max_drawdown": -2683.7,
  "nights_below_-700": 0,
  "nights_below_-1000": 0
}
```

## TradeDay $50K static: target $3,000, DD $2,000 (static, locks), DLL $0, consistency 30% of profit, flat by overnight ok CT, bots on funded: True
P(pass) 1x = **12.2%** (median 255.0 days); zero-edge control 7.7%; this history: {'result': 'fail', 'days': 112, 'reason': 'drawdown (intraday touch)', 'balance': -2000}

## TradeDay $100K static: target $6,000, DD $3,000 (static, locks), DLL $0, consistency 30% of profit, flat by overnight ok CT, bots on funded: True
P(pass) 1x = **0.1%** (median 300 days); zero-edge control 0.1%; this history: {'result': 'fail', 'days': 1069, 'reason': 'drawdown (intraday touch)', 'balance': -3000}

## MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True
P(pass) 1x = **12.1%** (median 250 days); zero-edge control 7.6%; this history: {'result': 'fail', 'days': 110, 'reason': 'drawdown (intraday touch)', 'balance': -1965.0}