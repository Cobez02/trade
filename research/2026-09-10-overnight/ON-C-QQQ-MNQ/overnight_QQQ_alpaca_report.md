# Overnight sleeve: QQQ (alpaca), notional $50,000, slip 0.019/share/side, weekend=no

## Summary
```
{
  "nights": 2142,
  "net_pnl": 48799.46,
  "mean_night": 22.78,
  "sd_night": 406.74,
  "win_rate": 0.564,
  "profit_factor": 1.18,
  "sharpe_ann": 0.89,
  "t_stat": 2.59,
  "worst_night": -3391.38,
  "best_night": 2739.34,
  "max_drawdown": -10236.22,
  "nights_below_-700": 85,
  "nights_below_-1000": 31
}
```

## By year
```
      count      sum  mean    std     min
year                                     
2016    201  -4776.6 -23.8  318.3 -1833.1
2017    200   3865.2  19.3  166.2  -514.4
2018    196   2446.9  12.5  371.3 -1680.9
2019    201   6793.0  33.8  290.6 -1072.1
2020    204  16373.4  80.3  674.6 -3391.4
2021    203   5931.5  29.2  320.9  -913.3
2022    200  -5629.0 -28.1  571.3 -1610.1
2023    199   3922.0  19.7  336.7 -1099.8
2024    200  12406.6  62.0  333.5  -960.2
2025    200   4099.7  20.5  411.3 -2073.6
2026    138   3366.8  24.4  439.2 -1510.7
```

## IN-SAMPLE
```
{
  "nights": 1604,
  "net_pnl": 28926.43,
  "mean_night": 18.03,
  "sd_night": 411.63,
  "win_rate": 0.557,
  "profit_factor": 1.14,
  "sharpe_ann": 0.7,
  "t_stat": 1.75,
  "worst_night": -3391.38,
  "best_night": 2739.34,
  "max_drawdown": -10236.22,
  "nights_below_-700": 64,
  "nights_below_-1000": 26
}
```

## HOLDOUT
```
{
  "nights": 538,
  "net_pnl": 19873.03,
  "mean_night": 36.94,
  "sd_night": 391.44,
  "win_rate": 0.584,
  "profit_factor": 1.31,
  "sharpe_ann": 1.5,
  "t_stat": 2.19,
  "worst_night": -2073.63,
  "best_night": 1697.1,
  "max_drawdown": -7375.87,
  "nights_below_-700": 21,
  "nights_below_-1000": 5
}
```

## TradeDay $50K static: target $3,000, DD $2,000 (static, locks), DLL $0, consistency 30% of profit, flat by overnight ok CT, bots on funded: True
P(pass) 1x = **57.0%** (median 67 days); zero-edge control 30.5%; this history: {'result': 'fail', 'days': 9, 'reason': 'drawdown (intraday touch)', 'balance': -2000}

## TradeDay $100K static: target $6,000, DD $3,000 (static, locks), DLL $0, consistency 30% of profit, flat by overnight ok CT, bots on funded: True
P(pass) 1x = **59.1%** (median 137 days); zero-edge control 26.4%; this history: {'result': 'fail', 'days': 11, 'reason': 'drawdown (intraday touch)', 'balance': -3000}

## MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True
P(pass) 1x = **48.3%** (median 42.0 days); zero-edge control 29.8%; this history: {'result': 'fail', 'days': 9, 'reason': 'drawdown (intraday touch)', 'balance': -1807.0}