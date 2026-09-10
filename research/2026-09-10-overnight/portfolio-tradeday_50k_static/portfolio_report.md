# Portfolio — S1, ONA, ONB

## Per sleeve and combined (all sessions)

| sleeve | sessions | net | $/day | sd | Sharpe | t |
|---|---|---|---|---|---|---|
| S1 | 2683 | 14268 | 5.32 | 150.3 | 0.56 | 1.83 |
| ONA | 2683 | 3100 | 1.16 | 90.6 | 0.2 | 0.66 |
| ONB | 2683 | 6842 | 2.55 | 134.7 | 0.3 | 0.98 |
| combined | 2683 | 24209 | 9.02 | 268.1 | 0.53 | 1.74 |

## Daily P&L correlation
```
       S1   ONA   ONB
S1   1.00  0.01  0.02
ONA  0.01  1.00  0.89
ONB  0.02  0.89  1.00
```

## IN-SAMPLE (2011 sessions)

| sleeve | net | $/day | Sharpe | t |
|---|---|---|---|---|
| S1 | 11660 | 5.8 | 0.6 | 1.69 |
| ONA | 1824 | 0.91 | 0.16 | 0.45 |
| ONB | 5096 | 2.53 | 0.28 | 0.8 |
| combined | 18580 | 9.24 | 0.53 | 1.5 |

## HOLDOUT (672 sessions)

| sleeve | net | $/day | Sharpe | t |
|---|---|---|---|---|
| S1 | 2608 | 3.88 | 0.44 | 0.72 |
| ONA | 1276 | 1.9 | 0.32 | 0.53 |
| ONB | 1746 | 2.6 | 0.38 | 0.61 |
| combined | 5629 | 8.38 | 0.54 | 0.89 |

## Prop-rule Monte Carlo — TradeDay $50K static: target $3,000, DD $2,000 (static, locks), DLL $0, consistency 30% of profit, flat by overnight ok CT, bots on funded: True

- combined, all years: **P(pass) 51.0%** (median 95 days); zero-edge control 30.7%
- combined, holdout only: **P(pass) 51.3%**
- sessions with combined intraday low <= -$700: **28**; <= -$1,000: **10**

## Pre-registered gates

- FAIL — combined holdout Sharpe >= 1.2
- FAIL — combined holdout t >= 1.5
- FAIL — P(pass) all-years >= 65%
- FAIL — P(pass) holdout >= 55%
- FAIL — zero sessions below -$1,000
- FAIL — max pairwise correlation < 0.5

**Verdict: FAIL** (0/6 gates)