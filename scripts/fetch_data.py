"""Download and cache the history the backtester needs.

  DATABENTO_API_KEY=... python -m scripts.fetch_data databento MES.v.0 2019-05-06 2026-09-05
  DATABENTO_API_KEY=... python -m scripts.fetch_data databento MNQ.v.0 2019-05-06 2026-09-05
  ALPACA_API_KEY=... ALPACA_SECRET_KEY=... python -m scripts.fetch_data alpaca SPY 2020-01-01 2026-09-05
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import bars as D

if __name__ == "__main__":
    src, sym, start, end = sys.argv[1:5]
    df = D.load(src, sym, start, end)
    print(f"{sym}: {len(df):,} bars  {df.index[0]} .. {df.index[-1]}")
