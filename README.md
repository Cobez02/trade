# SPX-Beater — autonomous options paper-trading bot

Runs **options-only** paper trades on an Alpaca paper account every hour during US
market hours and tries to beat a buy-and-hold S&P 500 (SPY) benchmark. Strategy
"sleeves" compete under a learning loop that **sizes** what works up or down
(0.25×–1.25×) — it never pauses or gates anything, because a blocked setup can
never earn the evidence that would clear it. Runs free, 24/7, on **GitHub
Actions** — no server, no keeping your computer on.

| Sleeve | Signal source | Status |
|--------|----------------|--------|
| `news` | Alpaca market-news headline sentiment | active |
| `tech` | RSI / MACD / trend indicators | active |
| `wsb`  | Reddit / r/wallstreetbets sentiment | retired as entry; survives as a crowd **veto** on hyped names |
| `flow` | Options call/put open-interest skew | retired — OI is unsigned; no signal without open/close data |

**Read `STRATEGY.md`** for what the program trades and the evidence behind every
rule, and **`ASSESSMENT.md`** for the honest math on the 10x goal before any
real-money decision. Both are part of the deliverable, not decoration.

It is **stateless**: every run reconstructs open positions and the full learning
journal from Alpaca's own order history (each trade's signal features are encoded
in its `client_order_id`), so nothing breaks if a run is missed. `state.json` is
committed back each run only to keep a nice equity-curve history.

---

## One-time setup (~10 minutes, no command line needed)

### 1. Create a **private** repository
On github.com: **New repository** → name it e.g. `spx-beater` → set **Private**
(keep your trades and keys out of public view) → Create.

### 2. Upload these files
On the empty repo page click **uploading an existing file**, then drag in
*everything from this folder*, including the `.github/workflows/trade.yml` file
(keep that folder path intact). Commit.

> If drag-and-drop flattens the folder, create the workflow manually: **Add file →
> Create new file**, name it exactly `.github/workflows/trade.yml`, and paste the
> contents of the `trade.yml` included here.

### 3. Add your Alpaca paper keys as secrets
**Settings → Secrets and variables → Actions → New repository secret.** Add two:

- `ALPACA_API_KEY`  → your paper key id
- `ALPACA_SECRET_KEY` → your paper secret

(These are encrypted by GitHub and never appear in logs or the repo.)

### 4. Turn on Actions
Open the **Actions** tab. If prompted, click **"I understand my workflows, enable
them."** You'll see **SPX-Beater options bot**.

### 5. Test it now
Actions → **SPX-Beater options bot** → **Run workflow** → Run. Open the run and
check the **Summary** — you'll see the digest. Outside market hours it records
signals but places no orders (that's correct). During market hours it trades.

### 6. (Optional) Live dashboard page
**Settings → Pages → Build from branch → `main` / `root` → Save.** After the next
run your dashboard is live at
`https://<your-username>.github.io/<repo>/dashboard.html`.

That's it — from now on it runs itself hourly, 10:00–16:00 ET on weekdays.

---

## Command-line quickstart (if you have the `gh` CLI)
```bash
gh repo create spx-beater --private --source=. --remote=origin --push
gh secret set ALPACA_API_KEY   --body "YOUR_PAPER_KEY_ID"
gh secret set ALPACA_SECRET_KEY --body "YOUR_PAPER_SECRET"
gh workflow run "SPX-Beater options bot"
```

## Where to see results
- **Actions tab → any run → Summary** — the text digest (portfolio vs S&P, per-sleeve P&L, activity).
- **`dashboard.html`** in the repo (or the Pages URL) — the full visual dashboard, updated each run.
- **`state.json`** history in the commit log — the equity curve accumulates here.

## Tuning (optional)
Behaviour is env-overridable in `trade.yml` (add under the `env:` block):
`SPXBOT_MAX_PREM` (max $/trade, default 350), `SPXBOT_MAX_OPEN` (per sleeve, 2),
`SPXBOT_MAX_SPREAD` (max bid/ask spread, **0.04** — see STRATEGY.md §2 before
raising it; at 0.15 the round trip costs 10.9% of premium), `SPXBOT_TP`
(trail arms at +0.25), `SPXBOT_SL` (working stop, -0.30),
`SPXBOT_DTE_MIN`/`SPXBOT_DTE_MAX` (3/12).

## Tests
Eight suites, no network, no orders: `for t in test_*.py; do python3 $t; done`
(1,279 named checks). They pin the statistics (`test_stats.py` reproduces
published Deflated-Sharpe worked examples to the digit), the screens, the
execution math, the exit rules, the learning loop's "size, never block"
property, the watcher's stop machinery, and the sleeve retirement.

## Notes & caveats
- Scheduled GitHub runs can be delayed a few minutes under load — fine hourly.
- GitHub pauses schedules after 60 days of **no repo activity**; the per-run commits keep it active.
- Paper money only. Not investment advice. Paper fills don't reflect real slippage/liquidity.
