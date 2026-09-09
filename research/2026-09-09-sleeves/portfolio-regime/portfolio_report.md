# Portfolio — S1r, S2r, S3r

## Per sleeve and combined (all sessions)

| sleeve | sessions | net | $/day | sd | Sharpe | t |
|---|---|---|---|---|---|---|
| S1r | 2679 | 24405 | 9.11 | 123.5 | 1.17 | 3.82 |
| S2r | 2679 | -3509 | -1.31 | 81.7 | -0.25 | -0.83 |
| S3r | 2679 | -10370 | -3.87 | 87.2 | -0.7 | -2.3 |
| combined | 2679 | 10526 | 3.93 | 154.1 | 0.4 | 1.32 |

## Daily P&L correlation
```
      S1r   S2r   S3r
S1r  1.00  0.05 -0.28
S2r  0.05  1.00 -0.05
S3r -0.28 -0.05  1.00
```

## IN-SAMPLE (2007 sessions)

| sleeve | net | $/day | Sharpe | t |
|---|---|---|---|---|
| S1r | 20530 | 10.23 | 1.25 | 3.52 |
| S2r | -2742 | -1.37 | -0.26 | -0.74 |
| S3r | -9731 | -4.85 | -0.84 | -2.37 |
| combined | 8057 | 4.01 | 0.39 | 1.11 |

## HOLDOUT (672 sessions)

| sleeve | net | $/day | Sharpe | t |
|---|---|---|---|---|
| S1r | 3874 | 5.77 | 0.91 | 1.49 |
| S2r | -767 | -1.14 | -0.23 | -0.37 |
| S3r | -639 | -0.95 | -0.21 | -0.34 |
| combined | 2469 | 3.67 | 0.46 | 0.75 |

## Prop-rule Monte Carlo — MyFundedFutures Core $50K: target $3,000, DD $2,000 (eod, locks), DLL $1,000, consistency 50% of profit, flat by 15:10 CT, bots on funded: True

- combined, all years: **P(pass) 33.2%** (median 137 days); zero-edge control 20.2%
- combined, holdout only: **P(pass) 35.3%**
- sessions with combined intraday low <= -$700: **2**; <= -$1,000: **0**

## Pre-registered gates

- FAIL — combined holdout Sharpe >= 1.2
- FAIL — combined holdout t >= 1.5
- FAIL — P(pass) all-years >= 65%
- FAIL — P(pass) holdout >= 55%
- PASS — zero sessions below -$1,000
- PASS — max pairwise correlation < 0.5

**Verdict: FAIL** (2/6 gates)