# TSLA cap study — should MAX_PREMIUM_PER_TRADE be raised or removed?

Run: 2026-07-31, prompted by Connor's ask to "remove any cap that may block
buying TSLA." ThetaData NBBO EOD, TSLA only, 2020-10-01 → 2026-07-24 (1,459
sim days, 775 expirations). The production sim (`sim_singles`, unmodified —
same RSI/MACD entries, same vol gate, same exits, pessimistic fills) replayed
at three cap levels; the cap was monkeypatched per cell, so the $600 cell IS
the shipped configuration on this symbol.

## Result

| cap | n | total | PF | win rate | avg/trade | mean ret/premium | maxDD |
|---|---|---|---|---|---|---|---|
| **$600 (production)** | 42 | **+$942** | **1.14** | 36% | +$22 | **+1.6%** | −$2,569 |
| $900 | 74 | **−$3,535** | 0.79 | 30% | −$48 | −8.1% | −$4,743 |
| uncapped | 106 | −$159 | 1.00 | 33% | −$2 | −3.2% | **−$9,331** |

Every loosening is worse than production on every metric that matters: total
P/L flips from +$942 to negative, per-trade expectancy flips from +1.6% of
premium to −3% to −8%, and max drawdown scales with the cap — the uncapped
cell's worst stretch (−$9,331) would be 93% of the starting bankroll. The
trades a higher cap admits are, as a class, the expensive near-ATM contracts
bought on high-IV days — precisely the days when the ask is rich because vol
is bid, which is when long premium underperforms. The $600 cap has been
functioning not just as a survivability governor but as an accidental
overpaying filter.

## Caveats, honestly stated

n=42 in the production cell is a small sample (PSR-grade proof is not
claimed). Cells are not strictly nested — one-position-at-a-time means an
admitted rich trade occupies the slot and reshuffles later entries — so read
cell totals, not trade-by-trade subtractions. The sim rejects a day outright
when the single ~1.5%-OTM target strike busts the cap, while the live
engine's budget-fit scan can substitute a cheaper strike; the $600 cell
therefore *understates* what live cap-600 actually trades. None of these
caveats run in the direction of "raise the cap": the burden of proof was on
the raise, and the raise lost money in both tested forms.

## Decision (owner-confirmed path: evidence first)

**The cap stays at $600.** No config change was needed — TSLA was never
blocked wholesale: on 2026-07-30's chain, a dozen quality strikes (2.4–3%
spreads) fit under the cap from +1.3% OTM upward, and TSLA tops today's
signal board (RSI 14.8 oversold-bounce setup) under existing rules. The
premise that triggered this study ("TSLA on a steady 5-day rise post-
earnings") was also factually inverted — TSLA fell 17.5% after the Jul 23
earnings gap and bounced once — which is its own argument for letting the
signals, not the headlines, pick the trades.
