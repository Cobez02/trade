# Overnight sleeve: DIA (alpaca), notional $23,000, slip 0.03/share/side, weekend=no

## Summary
```
{
  "nights": 2145,
  "net_pnl": 6841.81,
  "mean_night": 3.19,
  "sd_night": 150.65,
  "win_rate": 0.529,
  "profit_factor": 1.07,
  "sharpe_ann": 0.34,
  "t_stat": 0.98,
  "worst_night": -1572.15,
  "best_night": 1380.37,
  "max_drawdown": -3881.26,
  "nights_below_-700": 5,
  "nights_below_-1000": 2
}
```

## By year
```
      count     sum  mean    std     min
year                                    
2016    201 -2912.7 -14.5  124.3  -684.1
2017    200  1647.5   8.2   58.5  -204.8
2018    199   413.5   2.1  136.1  -489.4
2019    201  2365.9  11.8  110.3  -386.6
2020    204  4428.6  21.7  323.4 -1572.2
2021    203  1233.8   6.1   97.6  -497.8
2022    200 -1670.4  -8.4  169.7  -595.4
2023    199  -410.3  -2.1   92.0  -382.6
2024    200  1372.4   6.9   99.2  -284.7
2025    200 -1294.4  -6.5  134.5  -604.4
2026    138  1667.9  12.1  134.5  -428.3
```

## IN-SAMPLE
```
{
  "nights": 1607,
  "net_pnl": 5095.92,
  "mean_night": 3.17,
  "sd_night": 158.98,
  "win_rate": 0.521,
  "profit_factor": 1.07,
  "sharpe_ann": 0.32,
  "t_stat": 0.8,
  "worst_night": -1572.15,
  "best_night": 1380.37,
  "max_drawdown": -3782.43,
  "nights_below_-700": 5,
  "nights_below_-1000": 2
}
```

## HOLDOUT
```
{
  "nights": 538,
  "net_pnl": 1745.89,
  "mean_night": 3.25,
  "sd_night": 122.45,
  "win_rate": 0.55,
  "profit_factor": 1.08,
  "sharpe_ann": 0.42,
  "t_stat": 0.61,
  "worst_night": -604.39,
  "best_night": 835.73,
  "max_drawdown": -3696.25,
  "nights_below_-700": 0,
  "nights_below_-1000": 0
}
```

## TradeDay $50K static: target $3,000, DD $2,000 (static, locks), DLL $0, consistency 30% of profit, flat by overnight ok CT, bots on funded: True
P(pass) 1x = **28.1%** (median 209.0 days); zero-edge control 16.8%; this history: {'result': 'fail', 'days': 81, 'reason': 'drawdown (intraday touch)', 'balance': -2000}

## TradeDay $100K static: target $6,000, DD $3,000 (static, locks), DLL $0, consistency 30% of profit, flat by overnight ok CT, bots on funded: True
P(pass) 1x = **4.5%** (median 259.5 days); zero-edge control 2.0%; this history: {'result': 'fail', 'days': 120, 'reason': 'drawdown (intraday touch)', 'balance': -3000}

## MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True
P(pass) 1x = **31.7%** (median 185 days); zero-edge control 20.1%; this history: {'result': 'fail', 'days': 81, 'reason': 'drawdown (intraday touch)', 'balance': -1954.0}