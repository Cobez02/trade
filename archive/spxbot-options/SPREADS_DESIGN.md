# Defined-Risk Credit-Spreads Sleeve — Design (v1, pre-build)

Owner approval: Connor, 2026-07-30 ("research and build it").
Status: DESIGN APPROVED. Overnight exemption GRANTED (Connor, 2026-07-30):
defined-risk spreads only, weekends included, max loss ≤ $400/spread, ≤ 2
open. Long options still flatten daily. Build proceeds per §7.

## 1. Why this sleeve exists

The audit gap: the documented persistent edge in options markets is on the
SELLER side (variance risk premium), and this bot is long-single-leg by
construction. Our vol machinery is already two-sided — `timing_edge` computes
implied vs forecast and today acts only on "implied BELOW forecast → buy."
This sleeve acts on the mirror: implied RICH vs forecast → sell a
defined-risk credit spread. Same model, other side, maximum loss fixed at
entry by the long leg. Post-2012 caveat honored: naive short-vol decayed
(research note: Sharpe +0.74 pre-2012 → −0.12 after); what retains support
is FILTERED, DEFINED-RISK, MANAGED structures — which is what this is.

## 2. Structure (v1 deliberately narrow)

* Underlyings: SPY and QQQ only. Penny-class spreads, the deepest chains in
  existence, no single-name earnings gaps, index diversification thins the
  tail. Single names come later or never.
* Vertical put credit spreads (bull puts) first: short put + long put below
  it, same expiration. The put side carries the fattest VRP. Bear calls and
  iron condors are v2 candidates, not v1.
* Alpaca mechanics confirmed: `order_class="mleg"`, legs with
  `position_intent`, net-credit limit pricing, level 3 already enabled on
  the paper account, defined-risk margin = max theoretical loss.

## 3. Entry rules

* Trigger: implied vol of the short strike's expiry RICH vs our HAR-RV
  forecast by ≥ 2.0 vol points (the mirror of the buy-side edge), with the
  vol cone confirming implied sits in its upper half for that horizon.
* Trend filter: no bull puts in a downtrend (existing tech indicators);
  selling puts into a falling market is how win-rate strategies die.
* Strike: short leg ≈ one expected move away: K_short ≈ spot × (1 − k·σ·√T),
  k = 1.1 (a ~20-25-delta proxy computed without greeks, which the free feed
  does not provide). Width $2–3 (SPY) / $3–5 (QQQ).
* Credit floor: net credit ≥ 20% of width, measured on NET quotes (both
  legs' NBBO-proxy mids; the package must also pass a net-spread cost gate —
  the two-leg analogue of the 4% rule, threshold 8% of credit).
* Max loss per spread = width − credit ≤ $400. This is the number the
  per-trade cap governs for spreads (the cap caps LOSS, not premium).
* Earnings screen: not holding through underlying-level binary events —
  moot for SPY/QQQ (no earnings), kept as an assertion for any future
  single-name extension. Ex-dividend screen: exit/never-hold short ITM puts
  across ex-div dates (early-assignment magnet).

## 4. Exit rules (the documented part of the edge)

* Take-profit: buy the spread back at 50% of the credit received. This is
  the management rule with the strongest large-sample support.
* Stop: buy back if the spread's net value reaches 2.0× credit received
  (loss = 1× credit) — before max loss, always.
* Time exit: off by 2 DTE regardless (gamma week and assignment risk are
  not our trade).
* Assignment handling: if a short leg is ever assigned (stock appears in
  the account), the bot's reconcile step flattens the stock at market
  immediately and closes the orphan long leg. Detection: any equity
  position in a book that should hold only options.
* Watcher: manages the PACKAGE net value (both legs quoted, net mid),
  same confirmation-window discipline as singles. The resting disaster
  floor has no mleg stop-order equivalent at Alpaca; the floor for spreads
  IS the structure itself — max loss is capped at entry, which is a
  stronger guarantee than any resting order.

## 5. Sizing and cadence

* Dedicated allocation: $1,000 (10% of bankroll), max 2 concurrent
  spreads, max loss ≤ $400 each → worst simultaneous case −$800 (8%).
* Expected shape (must be said out loud): HIGH win rate, NEGATIVE skew.
  Many small wins (~half the credit each), occasional losses of ~1× credit,
  rare gap losses toward max. The learner must judge this sleeve on long
  windows — its per-trade win rate is flattering by construction and its
  tail arrives late. The existing n_eff clustering correction applies.
* Evidence rate: ~2–4 settled spreads/week added to the journal.

## 6. THE OPEN DECISION — overnight holding (Connor's call)

The evidence-backed version of this trade needs DAYS in the position
(5–10 DTE at entry, exits at 50% credit / 2 DTE): theta is the product and
it accrues mostly overnight. An intraday-only variant obeys the current
flat-by-close rule but pays two legs' costs twice a day for a fraction of
the decay, and has no credible evidence base — building it would be
building a worse trade to satisfy a rule written for a different risk.

The rule's PURPOSE was: no unbounded, unwatched risk while nobody is
looking (born from DRAM gapping −69% overnight as a naked long). A credit
spread inverts that premise: its worst case is fixed at entry
(width − credit), a gap through both strikes cannot exceed it, and no
monitoring can be outrun because the loss is already bounded by the long
leg. The proposal is therefore a NARROW exemption: defined-risk spreads
(and only they) may carry overnight, with max loss ≤ $400 each, ≤ 2 open,
everything else — long options — still flattens daily. Weekend carry
included (that is where 2 of 7 nights' theta lives), or excluded at
Connor's preference with a Friday time-exit.

If declined: the sleeve ships as intraday-only with the honest label
"unsupported by evidence, cost-disadvantaged, expected ~zero edge" — or
does not ship, which is the more truthful alternative.

## 7. Build plan (after §6 is decided)

1. engine: mleg order submission + package pricing + net quotes.
2. strategies: `sleeve_spreads` (trigger, strike/width selection).
3. exitrules/watcher: package-value management, 50%/2×/2-DTE rules,
   assignment reconciliation.
4. screens: net-cost gate, ex-div screen, cone check.
5. learn/reporting: spread-aware journal rows and dashboard.
6. Tests for every piece at the existing standard; paper-live Monday at
   the earliest, small allocation, its own sleeve attribution.
