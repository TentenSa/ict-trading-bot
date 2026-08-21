# Accumulated learnings

Findings that cost real money or real backtests to establish. They live here, in
the repo, so a fresh clone carries them. Dates are when the finding was made.

---

## 1. The mechanical ICT layer has no standalone edge

Backtested 2026-08-16. Mechanical conditions only (unbroken sweep + BOS/CHoCH +
unfilled FVG + premium/discount extreme), deterministic FVG-midpoint entry,
impulse-extreme stop, impulse-opposite target, 0.1% round-trip cost, R:R >= 2:

| run | trades | hit-rate | expectancy | equity @1% risk |
|---|---|---|---|---|
| 173 NSE F&O names, 10y daily | 841 | 22.8% | −0.028R | **−30.8%** |
| 173 NSE F&O names, 2y hourly | 685 | 21.9% | −0.086R | **−48.8%** |
| NIFTY + BANKNIFTY, 10y daily | 10 | 20.0% | +0.133R | +1.2% (sample too small) |

NIFTY buy-and-hold over the same 10 years: **+181.9%**.

**This does not prove the discretionary strategy loses.** The judgement layer —
narrative, chosen entry, structural stop, confidence — is absent from the replay,
and `screen.py` says outright it does NOT produce trade signals. What it does
establish: any real edge lives entirely in the analyst's judgement, and that
remains unmeasured. Never imply a mechanical edge exists.

---

## 2. OTE limit entries do not fill — this is the real leak

Measured by `python3 vv/review.py` on 2026-08-17:

```
EXECUTION  fill rate 1/8 (12%)   median entry distance 2.61% of spot
ANALYSIS   entered at market instead: 2/3 decided reached TP1 first
```

The bias layer works; the **order-placement layer** is what loses. HINDCOPPER and
SHREECEM would both have hit target from a market entry, missing by 2.46% and
2.54%. The one signal that filled (ALKEM, 0.85% away) lost.

The 2/3 figure is **not** evidence of edge — 3 decided trades is noise. The 12%
fill rate is the finding.

**Apply:** enter at the *first* PD array price reaches, not the deepest one.
Measure the cost of the entry with `vv/costs.py` before committing. Quote the
current fill rate when placing entries, and let it set how close they go.

---

## 3. NSE options price off the forward, not spot

`vv/bs.py` fed the cash spot misprices NSE stock options badly — ALKEM 5350 PE
(2026-08-14) returned 90.19 against a market mid of 101.83, an **11.4% error**.
NSE stock options are European and price off the **futures forward**.

Back the forward out of put-call parity at the same strike, then price Black-76:

```
F = K + (CE_mid - PE_mid) * exp(r*T)
bs.price(F, K, T, iv, kind, r=0) * exp(-r*T)
```

On that chain F = 5355.31 vs spot 5368.00 (0.24% backwardation); repricing off F
closed the error to **0.9%**. When projecting forward, decay the basis to zero at
expiry: `F(t) = S(t) + basis0 * T/T0`.

**Second, independent defect** (2026-08-18): `option_greeks.iv` refreshes on a
different cadence than `bid_price`/`ask_price`, so pairing published IV with a
live mid misprices — NIFTY 24300 PE overpriced by **+1.35%**, and **+3.8%** on an
earlier poll. How to tell the two apart: a clean parity forward (consistent to
within ~1 point across strikes) plus a *uniform* error across all strikes means
the IV is stale, not the forward.

---

## 4. Displacement trades need an impulse exit

When the premise is **displacement continuation** — selling/buying the retrace
into an FVG left by an impulsive leg — the position is a momentum trade with a
short shelf life. The plan must carry a **third exit** alongside stop and target:

> **Impulse exhaustion:** the first confirmed LTF higher low (for a short) /
> lower high (for a long), or no new extreme within ~20 minutes. Whichever comes
> first with stop and target.

A session-close time stop ("flat by 15:15") is an end-of-day backstop and does
**not** cover this. Do not present it as if it does.

**When impulse exhaustion is the PRIMARY exit:** whenever the actual entry basis
makes TP1 pay less than 2:1 **on the option leg**. Underlying R:R is not the test
— on 2026-08-19 a 2.90-point worse fill took option R:R to TP1 from 2.45 to
**1.22** while underlying R:R was unchanged at 2.15.

**Why this exists:** T011 (^NSEI short, 24100 PE) peaked at **+Rs 679** four
minutes after the impulse low, then round-tripped to **−Rs 166** over 45 minutes
of chop. The plan had no rule that would have taken it off.

### 2026-08-21 — the mid-session time stop is the PRIMARY exit, and it beat discretion

Confirmed on two trades in one session, both **directionally correct**, **neither
reaching TP1**:

| | entry | exit | underlying | booked | % of planned move |
|---|---|---|---|---|---|
| BANKNIFTY 57700 PE | 211.60 | 231.60 | +59.75 pt | **+Rs 600** | 26% |
| NIFTY 24250 PE | 63.00 | 66.50 | +19.55 pt | **+Rs 228** | 31% |

Both were closed by rule, not judgement, and the rule won both times:

- **NIFTY** exited at the 13:00 IST time stop, PE **66.50**. Eight minutes later
  the same put bid **59.25** — a **Rs 470/lot** swing while the underlying was
  still 6 points in profit. The loss was pure IV collapse (8.28 -> 8.09) plus
  theta. The user held past the rule and was down ~Rs 270 on a position that was
  *still directionally right*.
- **BANKNIFTY** had its stop moved to breakeven at 12:17 after giving back a
  third of a +97.80 point excursion. That made the final 28 minutes free and
  neutralised two pullbacks that would otherwise have mattered.

**Discretion lost, repeatedly.** Recommendations to close were issued at 12:30
and 12:32 — both into temporary pullbacks that recovered within minutes — and a
"cancel it" recommendation at 12:15 that would have skipped the entire profitable
move. Three wrong reads in thirty minutes against two correct rule-driven exits.

**The generalisation:** for intraday long premium, an impulse/time stop is not a
backstop behind stop-and-target — it is the **exit that actually fires**. Across
19, 20 and 21 Aug, **zero** intraday option trades reached TP1; every resolution
came from a time stop, a structural invalidation, or a stop. Size the plan around
that, and set the time stop at the point the *impulse* should have completed
(here 13:00, ~3h after the setup), not at the session close.

A 15:30 close-out remains an end-of-day backstop and still does not cover this.

---

## 5. An entry must clear its own invalidation by more than one bar's range

NIFTY 20-Aug-2026, 24300 CE long. Entry **24200.00**, breaker invalidation
**24198.90** — **1.10 points apart**. The 11:15 5M candle traded 24198.60, which
filled the limit, and then closed 24198.75, which lost the breaker. Filled and
invalidated by the same bar; the position existed for about one minute.

Cost was only the spread and fees (~Rs 80/lot vs Rs 2,998 at the real stop), so
the damage was trivial — but the *design* was broken, not unlucky. A 5M NIFTY bar
routinely ranges 10-15 points. Any entry sitting inside one bar's range of its own
invalidation is guaranteed to do this eventually.

**Apply:** before logging, check `entry - invalidation` (long) or
`invalidation - entry` (short) against the recent median 5M bar range on that
instrument. If the gap is smaller, the setup is not tradeable **at that entry** —
move the entry away from the invalidation, or drop the trade. Do not move the
invalidation to make room; it is defined by structure, not convenience.

Related trap, same session: the fill and the invalidation being adjacent also
makes both watchers fire proximity warnings together. Paired alerts on opposite-
direction levels are a *symptom* of this flaw, not noise to be tuned out.

---

## 6. The fill-rate fix worked (measured). Whether target-reaching is the new leak is NOT yet established

`python3 vv/review.py` on 2026-08-20, after the close-entry fix from learning 2:

```
EXECUTION  fill rate 7/10 (70%)   median entry distance 1.96% of spot
           of filled: 0 target, 1 stop
GAP        4 of 10 signals were directionally right; 7 actually traded.
```

**What is established:** fill rate 12% -> 70%, median entry distance 2.61% ->
1.96%. That is a real before/after on a change actually made, and learning 2 is
fixed. Stop quoting the 1/8 figure as the live problem.

**What is NOT established:** `of filled: 0 target` is suggestive, nothing more.
The whole log contains **one decisive resolution**. Concluding from this that
"reaching target is the new binding constraint" would repeat exactly the error
learning 2 warns about — treating a handful of outcomes as a finding. It is an
open question, not a result.

Confounder to rule out before believing it: the 20-Aug session realised **41.6
points against a ~90-point straddle-implied move**. On a day that does not
deliver its priced range, no 2R intraday target is reachable in either
direction, so 0-for-2 that session says more about the tape than the method.

**Re-check after 5 decisive resolutions.** If `of filled: 0 target` still holds
then, it is a finding and belongs in its own section with the evidence.

**Apply now (independent of the above):** re-run the straddle check (learning 4)
*during* the session, not only at entry. If realised range is tracking far under
the implied move by midday, targets set at the open are no longer reachable and
open positions should be scratched on time rather than held to a stop that will
not come.

---

## 7. Judge an array's invalidation on the timeframe that produced it

NIFTY 20-Aug-2026, 24300 CE long. The setup rested on a **15M** order block
(18-Aug 14:45 candle), but its invalidation was written as *a 5M close below
24198.90*. Two 5M bars closed under it — 24198.75 and 24198.10, breaking it by
0.15 and 0.80 points — and I exited.

The 15M bar containing both of those closes **closed at 24201.00, above the
level**. On the timeframe that defined the array, the invalidation never fired.

```
11:15 15M   H 24207.70  L 24194.20  C 24201.00   <- breaker intact
```

Exit at ~24199.85 (CE bid 76.05). Thirty-eight minutes later spot was 24212.40
and the CE bid 84.60 — **+Rs 543/lot held, versus -Rs 80 taken. The premature
exit cost ~Rs 623/lot.** The 24173 stop was never approached; the lowest print
after the exit was 24194.20, 21 points clear.

**Apply:**
- The invalidation timeframe must match the array's timeframe. A 15M order block
  is invalidated by a **15M** close, not a 5M one. Using a faster timeframe
  converts ordinary noise into a false exit signal.
- Compounds badly with learning 5: an invalidation inside one bar's range of the
  entry, judged on a fast timeframe, is near-certain to fire spuriously.
- Following a pre-committed rule was still correct. The failure was in *writing*
  the rule, not in obeying it — fix the specification, do not start improvising
  exits.

---

## 8. Recurring failure modes already guarded

- **`cost_dominated_risk`** — the earliest signals all died with stops too tight
  against fees. Guarded by `verify_signal.py` check `14-cost-vs-risk`.
- **No order block within reach of the FVG** — an honest stop then drops R:R
  below threshold (ASTRAL 2026-08-16 failed `9-stop-structure` at R:R 1.32).
  Rejected setups are *not* captured in `signals_log.jsonl`; state the failing
  check in the reply.
- **Expiry-day pins** — NIFTY weeklies expire Tuesday. On expiry, pull the 0DTE
  chain and compute max pain and OI walls. On 2026-08-18 max pain 24250 vs spot
  24228, 41.3m calls at 24300 and 38.4m puts at 24200 — that pin *was* the
  61-point range.
- **Targets beyond the straddle** — the 0DTE ATM straddle is the market's own
  price for the entire remaining move. On 2026-08-18 it was 69.35 points while
  the only short target clearing 1:2 was 92 points away (1.33x). **A target
  beyond the straddle is not a today trade, whatever its R:R says.** This is the
  cleanest NO_TRADE argument available, and it is arithmetic, not opinion.
