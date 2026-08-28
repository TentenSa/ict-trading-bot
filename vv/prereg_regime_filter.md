# Pre-registration: compression regime veto

**Written:** 2026-08-28, BEFORE running any test of this rule.
**Author:** Claude, at the user's instruction, after ~15 exploratory tests on 2026-08-27.
**Status at time of writing:** NOT YET RUN. No result has been seen.

## Why this document exists

On 2026-08-27 roughly fifteen distinct tests were run across time cuts, directions,
geometries and two indices. At a 5% threshold that yields ~1 spurious "significant"
result by chance alone, and two live examples of the failure already exist:

- The **at-the-lows long** (71-90% across five geometries, z~2.21) was found by
  splitting an existing sample by range position after the fact. It lost on its
  first live test and would have cost ~Rs 900 at its stop.
- The **26-Aug BANKNIFTY long** showed +5 until the edge was traced entirely to a
  term that could not exist at entry time (look-ahead).

Both were rules shaped by data already seen. This registration fixes the rule first.

## Hypothesis

**H1:** Vetoing intraday entries taken during price compression improves the outcome
of the two measured setups, by more than an equivalent-rate random veto would.

The mechanism claimed is NOT foresight about direction. It is that a compressed
market has too little range left for a fixed-point target to be reached before the
stop, so those entries are systematically worse. Trade count is the one problem
measured with certainty (75% of charges are fixed per order), so a rule whose job is
to STOP trading is the only category with a principled case.

**H0:** The veto produces no improvement beyond what randomly skipping the same
fraction of trades would produce.

## The rule, fully specified

At the moment an entry would be taken:

    lookback      = the 12 most recently CLOSED 5M bars (60 minutes)
    ref_window    = every closed 5M bar of that session up to that moment
    compression   = median(range of the 12 lookback bars)
                      < 40th percentile of (range of all ref_window bars)
    VETO the entry if compression is True. Otherwise take it unchanged.

`range` = high - low of the bar. Percentile by nearest-rank on the sorted ref_window.
If ref_window has fewer than 20 bars, do NOT veto (insufficient reference).

**These numbers are fixed: 12 bars, 40th percentile, 20-bar minimum.**
No variants will be tried. Not 10 or 15 bars. Not the 30th or 50th percentile.

## What it is applied to

Both setups already measured, unchanged in every other respect:

1. **Range-break fade, long** — entry on the first 5M close below the 09:15-11:20
   range low; stop 50, target 35. Baseline: 18/6/3, 75% of decided, BE 59%.
2. **Time-of-day short** — entry at 11:55; stop 50, target 35.
   Baseline: 37/15/5, 71% of decided, BE 59%.

Sample: ^NSEI 5M bars, 60-day retention window, all sessions with a full post-entry
leg. This is the same sample the baselines came from — the filter is the only change.

## The control that decides it

The veto trivially reduces trade count, and trading less is already known to help.
So the filter must beat a **random veto at the identical rate**: 500 random draws
vetoing the same NUMBER of trades, giving a distribution of win rates under H0. The
filter's win rate must exceed the **95th percentile** of that distribution.

Without this control the test is meaningless, because any veto looks good on cost.

## Pass criteria - ALL FOUR must hold

1. Win rate on surviving trades improves by **>= 5 percentage points** vs baseline.
2. Veto rate is **between 15% and 60%** of entries. Below 15% it is inert; above
   60% it is just "stop trading", which needs no filter.
3. Filter win rate **> 95th percentile** of the 500-draw random-veto distribution.
4. Improvement is **directionally positive in BOTH halves** of the sample split by
   date. Magnitude may differ; the sign may not.

## Fail

Anything else. Specifically, failure includes: an improvement that does not clear the
random-veto control, an improvement in one half and a decline in the other, or a
veto rate outside the band. **A failure will be reported as a failure and the filter
discarded.** It will not be re-specified and re-run.

## Committed in advance

- The test runs **once**.
- No parameter is adjusted after seeing any output.
- If it fails, no "nearly passed" variant is reported as encouraging.
- The result is written back into this file below the line, whatever it says.
- Even on a pass, this is 60 sessions and ~25-57 entries: a pass means "worth
  trading forward on paper", never "established".

---
## RESULT

**Run once, 2026-08-28, with the registered parameters unchanged. VERDICT: FAIL.**

### Setup 1 - range-break fade LONG
| | n | w/l/TO | win |
|---|---|---|---|
| baseline | 28 | 18/7/3 | 72.0% |
| after veto | 14 | 8/3/3 | **72.7%** (delta +0.7) |
| vetoed | 14 | | 50.0% of entries |

random-veto control, 500 draws at the same count: median **71.4%**, p95 **85.7%**.
split-half deltas: first **-8.3**, second **+15.4**.

- [FAIL] 1 win-rate improvement >= 5 pts (actual +0.7)
- [PASS] 2 veto rate within 15-60% (actual 50.0%)
- [FAIL] 3 beats the random-veto p95 (72.7% vs 85.7%)
- [FAIL] 4 both halves positive (signs oppose: -8.3 / +15.4)

### Setup 2 - 11:55 time-of-day SHORT
| | n | w/l/TO | win |
|---|---|---|---|
| baseline | 58 | 39/14/5 | 73.6% |
| after veto | 11 | 8/3/0 | **72.7%** (delta -0.9) |
| vetoed | 47 | | 81.0% of entries |

random-veto control: median **73.9%**, p95 **90.9%**.
split-half deltas: first **-4.4**, second **+4.2**.

- [FAIL] all four criteria.

### Interpretation

**The filter does nothing.** On setup 1 it removed HALF the entries and moved the win
rate by 0.7 points - and the random-veto control removing the same number of trades
at random produced a median of 71.4%, so 72.7% is indistinguishable from chance. On
setup 2 it vetoed 81% of entries, far outside the registered band, and made the win
rate slightly WORSE.

The split-half signs oppose each other in both setups (-8.3/+15.4 and -4.4/+4.2),
which is the signature of noise rather than a weak-but-real effect.

**What this rules out:** that compression, as defined here, carries information about
whether these entries work. It does not rule out other regime definitions - but any
future one must be registered the same way before running, and this result is the
prior it has to beat.

**What survives:** the filter DID cut trade count by 50% and 81%. That reduces
charges, which is real - but a random veto at the same rate does exactly as well, so
there is no skill in it. If the goal is fewer trades, trade fewer. Do not dress a
trade-count reduction up as a signal filter.

**Discarded. Not re-specified, not re-run.**
