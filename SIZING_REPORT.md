# Edge-proportional sizing study — verdict: flat sizing stays

Run: 2026-08-05, pre-registered in `e6c9513` before results. Question
(Connor's, ahead of Monday's live switch): "use more of it on high-
confidence days" — operationalized as sizing by the system's own measured
vol edge at entry (the only definition of confidence that survived the
override study). Cells: BASE flat $600 budget; TIER $600/$900/$1,200 at
edge <3/3–5/≥5 pts; PROP budget = 600×edge/2 clamped to [600, 1500].
14 names, six years, production gates and exits, pessimistic fills,
qty = budget ÷ contract cost.

## Result: sizing by confidence fails with the same inverted gradient

| cell | n | total | PF | maxDD | ret / deployed $ |
|---|---|---|---|---|---|
| BASE flat $600 | 807 | −$2,961 | 0.98 | −$16,684 | −0.7% |
| TIER (Connor's proposal) | 865 | **−$45,721** | 0.83 | −$62,469 | **−7.0%** |
| PROP (continuous) | 881 | **−$68,182** | 0.81 | −$90,022 | **−8.2%** |

The dose-response points the wrong way, again: the harder the sizing leans
on the edge number, the worse the outcome — ten times worse per deployed
dollar. Mechanism: the timing-edge gate is a valid *filter* (its accepted
trades beat its rejected ones), but edge *magnitude* is not a valid
*sizer* — the biggest measured edges cluster exactly where the HAR
forecast is most likely wrong (post-spike tape, where "cheap" implied vol
is the market correctly pricing danger the model hasn't caught up to).
Beyond the gate threshold, a bigger number mostly measures bigger model
error. Betting more on it multiplies the error, not the edge. This is the
override study's lesson in a second independent form: neither market
confidence nor system confidence magnitude earns extra size.

## The structural disclosure this study surfaced (matters more than the verdict)

BASE here is **negative** (−$2,961) while the published per-name sims on
the same entries summed to **+$2,824**. The difference is sizing
convention: the published sims booked one contract per trade; this study
sizes qty = budget ÷ cost, which concentrates deployment in cheap
contracts — and cheap contracts underperform per dollar. Two fidelity
gates separate this from the live bot, which sizes the same way but
PROFITS: the production screen stack (lottery/MAX screen, skew screen,
$0.50 premium floor — only the floor was replicated here; a first run
without even that floor came out −$4,042 and is disclosed, not discarded)
and multi-sleeve/slot structure. Consequence, stated plainly: **the screens
are load-bearing for live sizing** — they are what stands between
budget-scaled sizing and this study's negative baseline. Action item
adopted: port the full screen stack into the sim harness before any future
sizing research; until then, per-budget absolute levels in this study are
a lower bound, while the CELL-TO-CELL comparison (all cells share the
omissions) is the valid reading — and that comparison is unambiguous.

## Decision

**Nothing ships. Monday's live configuration is unchanged: flat $600 cap,
full screens, uniform sizing.** Connor's sizing instinct now has two
independent refutations (override study: market-confidence; this study:
system-confidence magnitude). Four cells examined across the two studies
of this question; none positive; no corrections could make the best cell
attractive. Limitations: EOD granularity, tech-sleeve entry stream only,
screens partially replicated (disclosed above), one position per symbol.
