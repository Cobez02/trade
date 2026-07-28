# ASSESSMENT — the honest math on this program, the 10x goal, and the income goal

*Written 2026-07-28, at the owner's request that the program reflect the best
available trading knowledge. That request cuts both ways: the same literature
that improved the machinery also produces the numbers below, and it would be a
betrayal of the assignment to deliver the machinery and soften the numbers.
Every figure is reproduced by `test_stats.py`/`test_screens.py` or computed in
`research/data/` and `research/notes/` from the cited papers. I am not a
licensed financial advisor; this document reports base rates and arithmetic,
and the decisions belong to the owner.*

---

## 1. What this program does, in the literature's terms

The program buys short-dated, slightly-OTM single-leg equity options and
day-trades them. The out-of-sample record for exactly that activity, from
real fills rather than models:

Bogousslavsky & Muravyev, *The Anatomy of Retail Option Trading* (2020–2022;
5,182 traders, ~0.9M trades, ~$15B notional): average return per option
trade **−0.93%**; on option **purchases −3.95% per trade**; 0DTE materially
worse; the median trader's average trade **−2.5%**. de Silva, Smith & So,
*Losing is Optional* (Review of Finance, 2026): retail losses of **5–9%
per trade** on earnings-announcement options, 10–14% when expected
volatility is high. Boyer & Vorkink's ex-ante-skew sorts put 7-day high-skew
**calls near −35% per week** and the top skew quintile of **puts near −60%
per week** — at the *midpoint*, before paying any spread. The bot's Tier-1
screens (STRATEGY.md §4) exist to keep it out of the worst of those cells,
and its execution rules cut the cost floor from ~10.9% to ~1.6% of premium
per round trip. Screens and costs move the expectancy toward zero; nothing
in the library moves it convincingly *above* zero for an options *buyer* at
retail data access. The one robustly documented premium in options — the
variance risk premium — is earned by the *seller*, and its index form ran a
Sharpe of **+0.74 before 2012 and −0.12 after**; harvesting it requires
margin, defined-risk spreads, and a loss profile that is the mirror image of
this program's (rare, large, clustered losses), which is why it is out of
scope without explicit owner sign-off.

Base rates for the humans attempting this: Barber, Lee, Liu & Odean's
complete Taiwan record — the cleanest full-population day-trading dataset in
existence — finds that in any year about **fewer than 1% of day traders**
earn reliably positive abnormal returns net of costs, and most of the
aggregate losses transfer to market makers and institutions. "Perform like
the top 1%" is therefore, on its face, a request to be the exception in a
population where the exception is roughly one in a hundred *of the people
who tried* — most of whom also believed they would be the exception.

## 2. The 10x challenge ($10,000 → $100,000), quantified

Gambler's-ruin arithmetic with the Itô correction (λ = 2μ/σ² − 1; verified
in `test_stats.py` against the closed form). The numbers that matter:

A **fair coin** — zero edge, any volatility — turns $10k into $100k before
falling to $1k with probability **9.09%** (exactly 9/99), and before $0 with
probability 10%. This is the free benchmark. Any strategy claiming to "beat
the challenge" must beat *that*, and the challenge's popularity does not
change the denominator: it is popular precisely because one attempt in
about eleven 10x's by luck alone, and the winners post.

At the **documented retail-purchase edge (−3.95% per trade)**, from note 04's
exact value iteration (Dubins–Savage, reproduced numerically to the digit):

| staking | P(10x) at −3.95%/trade | P(10x) at 0% | P(10x) at +2% |
|---|---|---|---|
| $100 fixed stakes | ~1×10⁻²⁸ (zero) | 10.0% | **98.2%** |
| $500 (≈ this bot's sizing) | **0.00005%** | 10.0% | 55.1% |
| $2,500 | 1.6% | 10.0% | 18.5% |
| all-in each trade (bold play) | **8.6%** | 10.0% | 11.9% |

Read the first column downward and the last column upward — that inversion
is the entire strategic content of the challenge. **With a negative edge,
careful sizing is what kills you** (the drift grinds you down over many
small trades; P(10x) at this bot's conservative sizing is five in ten
million), and the *only* way to approach even the coin-flip benchmark is
maximum recklessness — bet everything, minimize time in the market — which
buys its 8.6% by making the other 91.4% a near-certain total loss. With a
positive edge the same move destroys value. There is no sizing cleverness
that rescues a negative edge; Kelly's optimal bet at negative expectancy is
zero.

To hit 10x within a year *by skill* (50% probability): required Sharpe is
**1.802** by the first-passage route or **2.146** by the expectation route
(T = 2·ln10/SR²) — sustained, net of costs. For scale, long-run
buy-and-hold equities run ~0.4; famous discretionary traders' careers
cluster near 1; the handful of firms that sustain >2 (Renaissance-class) do
it with infrastructure this program does not have. And demonstrating such a
Sharpe *statistically* is its own barrier: at the bot's currently measured
per-trade Sharpe (+0.044 on 7 trades — which are n_eff ≈ 1–1.7 observations
after same-day clustering), the Minimum Track Record Length to distinguish
it from zero is **~1,400 settled trades**. Four days of live history is, in
evidentiary terms, nothing at all — and everything the dashboard shows
today is consistent with a coin.

## 3. The income goal ($200k/yr "luxury lifestyle"), quantified

Withdrawals convert a multiplicative process into multiplicative-minus-
additive and destroy the "it recovers eventually" property; sequence risk,
not mean return, sets the sustainable rate. From note 04's jump-diffusion
Monte Carlo (200k paths, 30 years, 5% failure tolerance): supporting
$200,000/yr requires roughly **$3.1M** from a high-quality, positively-
skewed return stream, ~$6.4–7.2M from a passive 60/40, and **$17.1M** from
an "aggressive trader" profile (SR ≈ 0.5 with realistic jumps) — the higher
mean does not compensate for the variance and the left tail. From the
$100,000 that a successful 10x would produce, a 4% withdrawal rate supports
**$4,000/yr**. The 10x problem and the income problem are two and a half
orders of magnitude apart, and chaining them ("10x, then live off it")
multiplies their probabilities.

Putting the chain together honestly — P(10x by skill-or-luck at this
program's actual sizing and measured edge) × P(the edge was real and
persists) × P(scaling from $100k to multi-million against capacity,
slippage and regime change) — the best estimate produced in the research
phase for *P(10x and then sustainably fund the lifestyle from trading)* is
about **0.1%, with a 90% credible range of 0.02%–1%**. The single largest
term is the first one, and the single fastest way to raise it is not a
better signal but a positive edge of any size — which is the one thing the
literature says a retail options *buyer* should not expect to have.

## 4. What the engineering fixed, and what it cannot fix

The research phase found and fixed real, material defects: the learning loop
was 56–59% likely to make the bot worse (hard gates at n=5 with a 33.7%
per-bucket false-fire rate at break-even, family-wise 0.9991 across 17
buckets, absorbing states that had already made the bot structurally
short-only; replaced by bounded sizing, shrinkage, clustering-corrected
sample counts and collinearity dedup — the redesign Monte-Carlos to a
~7× smaller expected harm, though still not a positive edge). Execution
gave away most of a spread per trade (blind ask×1.03; the mid mislabeled as
the ask; an OI fallback that bought the *least* liquid contract exactly when
liquidity vanished; sell-stops electable by quote noise and converting to
market orders in empty books). Ex-ante skew came out with the wrong sign on
the exact lottery contracts it screens. All of that is now fixed, tested,
and documented in STRATEGY.md.

None of it changes the sign of the underlying trade. Engineering can stop a
program from *donating* money to the market; it cannot make buying
short-dated retail options positive-EV when the measured population result
for that activity is −0.93% to −3.95% per trade. The program as it now
stands is the best version of a strategy class whose documented expectation
is negative. The honest statement of what has been built: a rigorous,
self-measuring instrument that will find an edge if one exists in its
sleeves, will prove it with defensible statistics if found, and will lose
slowly and transparently if not.

## 5. The counter-evidence, stated fairly

The strongest results *against* the pessimistic reading, on the record:
Barber, Lee, Liu & Odean formally reject luck for their top decile of day
traders (p<0.01) — persistent skill exists, it is just rare. Grinblatt,
Keloharju & Linnainmaa find IQ predicts trading performance, so the
population average is not everyone's expectation. Bauer, Cosemans &
Eichholtz find the top decile of retail *option* traders beats the bottom
by ~4.86%/month — dispersion, again, is real. The Cboe-funded critique of
the retail-loss studies (Amaya et al.) makes legitimate methodological
points about sample windows, though its own headline statistic (t = 0.29)
is a failure to reject zero, not evidence of profit. And prop-firm/challenge
culture's legal record cuts both ways: the CFTC's case against My Forex
Funds was **dismissed with prejudice in 2025, with ~$3.1M in sanctions
against the CFTC** — the fraud allegations were never adjudicated, and it
would be wrong to cite them as fact. The fair synthesis: persistent trading
skill exists and is measurable; its base rate is on the order of 1%, its
documented carriers are not selected by wanting it, and nothing in this
program's four days of history distinguishes it from the other 99%.

## 6. Before any real dollar

The paper account is the correct venue until the program *earns* its way
out, and the bar is known in advance, not negotiated afterward: an
activation-scale journal (≥100 settled trades over ≥40 distinct days,
n_eff ≥ 60), a Probabilistic Sharpe Ratio ≥ 0.95 against zero on the daily
series, and a Deflated Sharpe that survives the number of configurations
tried — the same thresholds the learning loop applies to itself
(STRATEGY.md §7). At the current trade rate that is months of paper
evidence, and that is the point: the market will still be there, and $10k
deployed after proof costs nothing but time, while $10k deployed before
proof buys a 0.00005%-class lottery ticket at the documented edge. If the
goal is a luxury income, the base-rate-honest paths run through capital
accumulation (income → savings rate → diversified compounding), with this
program as a bounded, measured experiment on the side — not the engine.

*This is a sensitive and personal set of topics — money, risk, and life
plans. The math above is general information, not personalized financial
advice; a licensed advisor who can see the whole financial picture is the
right counterpart for the real-money decision.*
