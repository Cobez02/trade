# RED-TEAM CHECKLIST — run against a test's code before any candidate touches the holdout (PROGRAM.md §0)
Written 2026-10-14 (P0-4). A separate session answers every line in writing in the prereg's "Red-team" section. "n/a" needs a reason.

## 1. Look-ahead
- [ ] Every signal uses only bars whose timestamp is strictly before the entry bar (17:02 CT for overnight tests). Daily closes
      (VIX, cash indices) are the 15:00-15:15 CT prints, not next-day values; no `shift(-1)`, no same-day resampled closes.
- [ ] Event calendars are the SCHEDULED dates as known in advance (BLS/Fed schedules), not realised dates back-filled from the data.
- [ ] Continuous-contract rolls (.v.0 switches by volume at the UTC day boundary, i.e. INSIDE the 17:02->08:30 window): any
      entry/exit pair that straddles a roll mixes two contracts. Quantify roll-night P&L separately; it should not drive the mean.
- [ ] Stop fills: a stop is filled at the level if the bar's LOW touches it, at the OPEN if the bar gaps through. Never at the close,
      never at the next bar. Limit-down reopens are modelled as a gap fill, not as the stop level.
- [ ] No parameter was chosen after seeing the scoreboard line. The headline parameter is the one named in the prereg.

## 2. Survivorship / selection
- [ ] Symbol universe fixed before the run (ES NQ RTY YM for Phase 1). No symbol added because it "looked better".
- [ ] Sample start is the data's start, not a start chosen to exclude a bad year. If a sub-period is reported, the full period is too.
- [ ] Conditioning subsets are defined by a rule stated in the prereg with its direction (hold / avoid) declared BEFORE the run.
      The complement is reported in notes. One scoreboard line per symbol for the pre-declared subset, never for the best subset.

## 3. Label leakage
- [ ] The conditioning variable is not a function of the night's own return (e.g. "big move nights" defined on the night itself).
- [ ] Day-session returns used as a signal end at 14:59 CT at the latest; the night starts 17:02.
- [ ] Vol regimes use TRAILING windows that end before the entry bar (the September regime leak was a centred window).

## 4. Costs
- [ ] Costs are the fixed PROGRAM.md §0 model (micro: $1.00 + 1 tick per side). Not changed, not "refined", not sized away.
- [ ] Cost share = costs / gross (no-stop gross). Reported even when it fails. A pass with cost share > 40% is not a pass.
- [ ] The ratio that matters for every overnight subset: GROSS $/night in the subset vs the micro round trip ($4.50 MES,
      $3.00 MNQ/M2K/MYM). Predicted in the prereg, reported in notes.

## 5. Statistics
- [ ] N on the scoreboard counts every line ever written, including this test's. DSR uses that N.
- [ ] PBO is computed over the pre-declared grid only (no post-hoc variants added to the matrix).
- [ ] Minimum track: >= 400 nights or >= 5 years. Subsets below that are reported but not gated.
- [ ] Skew/kurtosis are passed to DSR from the net daily series (stops make the distribution non-normal).

## 6. Known open items (carry into every overnight test until fixed)
- .v.0 roll inside the window (see §1). Fix = per-contract stitching with a roll rule that does not straddle the window.
- 17:02 bar open as the entry: real fills at 17:00:00-17:02 reopen are wider-spread than 1 tick on thin nights (Sundays, holidays).
- Holiday nights: the harness only skips nights with a missing 17:02 or 08:30 bar; early-close / half-session days are included.
