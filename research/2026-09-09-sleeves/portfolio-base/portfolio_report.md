# Portfolio — S1, S2, S3

## Per sleeve and combined (all sessions)

| sleeve | sessions | net | $/day | sd | Sharpe | t |
|---|---|---|---|---|---|---|
| S1 | 2679 | 14268 | 5.33 | 150.4 | 0.56 | 1.83 |
| S2 | 2679 | -12708 | -4.74 | 94.6 | -0.8 | -2.59 |
| S3 | 2679 | -9327 | -3.48 | 97.6 | -0.57 | -1.85 |
| combined | 2679 | -7767 | -2.9 | 182.0 | -0.25 | -0.82 |

## Daily P&L correlation
```
      S1    S2    S3
S1  1.00  0.03 -0.28
S2  0.03  1.00 -0.04
S3 -0.28 -0.04  1.00
```

## IN-SAMPLE (2007 sessions)

| sleeve | net | $/day | Sharpe | t |
|---|---|---|---|---|
| S1 | 11660 | 5.81 | 0.6 | 1.69 |
| S2 | -12654 | -6.3 | -1.07 | -3.02 |
| S3 | -8229 | -4.1 | -0.64 | -1.81 |
| combined | -9223 | -4.6 | -0.39 | -1.11 |

## HOLDOUT (672 sessions)

| sleeve | net | $/day | Sharpe | t |
|---|---|---|---|---|
| S1 | 2608 | 3.88 | 0.44 | 0.72 |
| S2 | -54 | -0.08 | -0.01 | -0.02 |
| S3 | -1098 | -1.63 | -0.31 | -0.5 |
| combined | 1456 | 2.17 | 0.2 | 0.33 |

## Prop-rule Monte Carlo — MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

- combined, all years: **P(pass) 15.7%** (median 82.0 days); zero-edge control 21.3%
- combined, holdout only: **P(pass) 27.8%**
- sessions with combined intraday low <= -$700: **2**; <= -$1,000: **0**

## Pre-registered gates

- FAIL — combined holdout Sharpe >= 1.2
- FAIL — combined holdout t >= 1.5
- FAIL — P(pass) all-years >= 65%
- FAIL — P(pass) holdout >= 55%
- PASS — zero sessions below -$1,000
- PASS — max pairwise correlation < 0.5

**Verdict: FAIL** (2/6 gates)