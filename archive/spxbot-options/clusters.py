"""Extract insider cluster-buy events from SEC structured Form 3/4/5 datasets."""
import zipfile, glob, json, collections, datetime as dt
import pandas as pd

BUY_CODE = "P"                 # open-market purchase
WINDOW_D = 10                  # cluster window
MIN_INSIDERS = 2               # distinct buyers
MIN_VALUE = 200_000.0          # aggregate $ floor
DEDUP_D = 30                   # one event per ticker per 30 days

buys = []                      # (ticker, filing_date, owner_cik, value)
for zp in sorted(glob.glob("*_form345.zip")):
    z = zipfile.ZipFile(zp)
    sub = pd.read_csv(z.open("SUBMISSION.tsv"), sep="\t", low_memory=False,
                      usecols=["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                               "ISSUERTRADINGSYMBOL"])
    tr = pd.read_csv(z.open("NONDERIV_TRANS.tsv"), sep="\t", low_memory=False,
                     usecols=["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_SHARES",
                              "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"])
    ro = pd.read_csv(z.open("REPORTINGOWNER.tsv"), sep="\t", low_memory=False,
                     usecols=["ACCESSION_NUMBER", "RPTOWNERCIK"])
    tr = tr[(tr.TRANS_CODE == BUY_CODE) & (tr.TRANS_ACQUIRED_DISP_CD == "A")].copy()
    tr["value"] = pd.to_numeric(tr.TRANS_SHARES, errors="coerce") * \
                  pd.to_numeric(tr.TRANS_PRICEPERSHARE, errors="coerce")
    agg = tr.groupby("ACCESSION_NUMBER")["value"].sum().reset_index()
    own = ro.groupby("ACCESSION_NUMBER")["RPTOWNERCIK"].first().reset_index()
    m = agg.merge(sub, on="ACCESSION_NUMBER").merge(own, on="ACCESSION_NUMBER")
    m = m[m.DOCUMENT_TYPE.astype(str).str.startswith("4")]
    for _, r in m.iterrows():
        t = str(r.ISSUERTRADINGSYMBOL or "").strip().upper()
        if not t or t in ("NONE", "N/A", "NA") or len(t) > 5 or not t.isalpha():
            continue
        v = float(r.value or 0)
        if v <= 0:
            continue
        buys.append((t, str(r.FILING_DATE).strip(), int(r.RPTOWNERCIK), round(v, 2)))
    print(zp, "purchase filings:", len(m))

buys.sort()
by_ticker = collections.defaultdict(list)
for t, d, cik, v in buys:
    try:
        # SEC TSVs date as DD-MON-YYYY (e.g. 12-JAN-2024)
        by_ticker[t].append((dt.datetime.strptime(d, "%d-%b-%Y").date(), cik, v))
    except ValueError:
        try:
            by_ticker[t].append((dt.date.fromisoformat(d), cik, v))
        except ValueError:
            continue

events = []
for t, rows in by_ticker.items():
    rows.sort()
    last_event = None
    for i in range(len(rows)):
        d0 = rows[i][0]
        w = [r for r in rows if 0 <= (d0 - r[0]).days <= WINDOW_D]
        ciks = {r[1] for r in w}
        val = sum(r[2] for r in w)
        if len(ciks) >= MIN_INSIDERS and val >= MIN_VALUE:
            if last_event and (d0 - last_event).days < DEDUP_D:
                continue
            last_event = d0
            events.append({"ticker": t, "date": d0.isoformat(),
                           "n_insiders": len(ciks), "value": round(val, 2)})

events.sort(key=lambda e: e["date"])
json.dump(events, open("insider_events.json", "w"), indent=1)
print(f"\ntotal open-market purchase filings: {len(buys):,}")
print(f"cluster events: {len(events):,} across {len({e['ticker'] for e in events}):,} tickers")
import collections as C
yr = C.Counter(e["date"][:4] for e in events)
print("by year:", dict(sorted(yr.items())))
