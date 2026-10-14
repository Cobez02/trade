# Event calendars (scheduled release dates, 2010-2023; development period only)
- fomc.csv — FOMC decision dates (second day of each meeting) + unscheduled meetings. Parsed 2026-10-14 from
  federalreserve.gov/monetarypolicy/fomchistorical{2010..2020}.htm and fomccalendars.htm (2021-2023). 111 rows.
- cpi.csv — CPI release dates, from the BLS news-release archive (bls.gov/bls/news-release/cpi.htm); the release date is the
  8-digit code in each archived file name (cpi_MMDDYYYY.htm). Read 2026-10-14 via a fetch tool (BLS blocks curl); validated:
  one per month, all weekdays, gaps 27-42 days, 2013 shutdown delay present (Sep-2013 CPI on 2013-10-30).
- nfp.csv — Employment Situation release dates, same source pattern (empsit_MMDDYYYY.htm); validated: one per month, Fridays
  except the known Thursday-before-July-4 and shutdown cases.
Releases are 07:30 CT (CPI, NFP) and 13:00 CT (FOMC; 13:15 before 2013), i.e. CPI/NFP fall INSIDE the 17:02->08:30 hold.
- vix_daily.csv, vix3m_daily.csv — CBOE daily OHLC for VIX and VIX3M (cdn.cboe.com/api/global/us_indices/daily_prices/
  {VIX,VIX3M}_History.csv), fetched 2026-10-14, truncated to <= 2023-12-31 so no holdout-period values sit in the repo.
  The 15:15 CT close is known before the 17:02 entry.
