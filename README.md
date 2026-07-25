# SPX-Beater — autonomous options paper-trading bot

Runs **options-only** paper trades on an Alpaca paper account every hour during US
market hours and tries to beat a buy-and-hold S&P 500 (SPY) benchmark. Four
independent strategy "sleeves" compete, and a recursive-learning loop sizes up
what works and pauses/gates what doesn't. Runs free, 24/7, on **GitHub Actions** —
no server, no keeping your computer on.

| Sleeve | Signal source |
|--------|----------------|
| `wsb`  | Reddit / r/wallstreetbets sentiment |
| `news` | Alpaca market-news headline sentiment |
| `tech` | RSI / MACD / trend indicators |
| `flow` | Options call/put open-interest skew |

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
`SPXBOT_MAX_PREM` (max $/trade, default 150), `SPXBOT_MAX_OPEN` (per sleeve, 3),
`SPXBOT_MAX_SPREAD` (max bid/ask spread, 0.15), `SPXBOT_TP` (take-profit, 0.45),
`SPXBOT_SL` (stop, -0.30), `SPXBOT_DTE_MIN`/`SPXBOT_DTE_MAX` (3/12).

## Notes & caveats
- Scheduled GitHub runs can be delayed a few minutes under load — fine hourly.
- GitHub pauses schedules after 60 days of **no repo activity**; the per-run commits keep it active.
- Paper money only. Not investment advice. Paper fills don't reflect real slippage/liquidity.
