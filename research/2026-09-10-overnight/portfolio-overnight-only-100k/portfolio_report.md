# Portfolio — ONA, ONB

## Per sleeve and combined (all sessions)

| sleeve | sessions | net | $/day | sd | Sharpe | t |
|---|---|---|---|---|---|---|
| ONA | 2683 | 3100 | 1.16 | 90.6 | 0.2 | 0.66 |
| ONB | 2683 | 6842 | 2.55 | 134.7 | 0.3 | 0.98 |
| combined | 2683 | 9941 | 3.71 | 219.5 | 0.27 | 0.87 |

## Daily P&L correlation
```
      ONA   ONB
ONA  1.00  0.89
ONB  0.89  1.00
```

## IN-SAMPLE (2011 sessions)

| sleeve | net | $/day | Sharpe | t |
|---|---|---|---|---|
| ONA | 1824 | 0.91 | 0.16 | 0.45 |
| ONB | 5096 | 2.53 | 0.28 | 0.8 |
| combined | 6919 | 3.44 | 0.24 | 0.68 |

## HOLDOUT (672 sessions)

| sleeve | net | $/day | Sharpe | t |
|---|---|---|---|---|
| ONA | 1276 | 1.9 | 0.32 | 0.53 |
| ONB | 1746 | 2.6 | 0.38 | 0.61 |
| combined | 3022 | 4.5 | 0.37 | 0.6 |

## Prop-rule Monte Carlo — TradeDay $100K static: target $6,000, DD $3,000 (static, locks), DLL $0, consistency 30% of profit, flat by overnight ok CT, bots on funded: True

- combined, all years: **P(pass) 20.2%** (median 200 days); zero-edge control 12.1%
- combined, holdout only: **P(pass) 22.4%**
- sessions with combined intraday low <= -$700: **22**; <= -$1,000: **9**

## Pre-registered gates

- FAIL — combined holdout Sharpe >= 1.2
- FAIL — combined holdout t >= 1.5
- FAIL — P(pass) all-years >= 65%
- FAIL — P(pass) holdout >= 55%
- FAIL — zero sessions below -$1,000
- FAIL — max pairwise correlation < 0.5

**Verdict: FAIL** (0/6 gates)