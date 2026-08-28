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

---

## 9. NIFTY gaps up and grinds down — the single most robust finding here

Measured 2026-08-27 over **2,464 sessions (10 years)**. Total index move +15,600:

| | sum | mean/day | positive |
|---|---|---|---|
| **INTRADAY** (open→close) | **−22,155** | −8.99 | **47.0%** |
| **OVERNIGHT** (close→open) | **+37,779** | +15.34 | **67.0%** |

**Every rupee of the index's decade of gains was made while the market was shut.**
Open to close it has bled 22,155 points. This is the mechanism behind a week of
otherwise-confusing measurements: intraday SHORT entries measured +12 to +14 over
break-even at both 11:55 and 12:50 (one effect, not two discoveries), intraday
LONG entries measured negative in every geometry, and every long-premium overnight
structure priced negative because the forward basis already charges for the drift.

**Apply:** start direction from which side of the clock the trade lives on.
Intraday favours shorts; a long needs a specific setup to overcome the structure.
Overnight favours longs, but it is already priced into the forward, so it is not free.
**Does not license anything about the next 90 minutes** — it is distributional over
thousands of days. A 71-74% intraday short still loses one time in four.

## 10. Fades beat chases on NIFTY — and BANKNIFTY is the opposite

Same test, both indices, ~58 sessions of 5M, entry on the first 5M close beyond
the 09:15–11:20 range:

| | NIFTY | BANKNIFTY |
|---|---|---|
| **fade** the break | **68–75%** (edge +8 to +16) | 50–59% (edge −7 to −1) |
| **chase** the break | 25–32% (edge −8 to −15) | **60–68%** (edge +1 to +8) |

The sign flips on every geometry tested. NIFTY reverts at its range edges;
BANKNIFTY trends through them. Neither BNF row is significant (best z = 0.78).

**An edge is a property of an instrument, not of a chart pattern.** Before reusing
any measured setup on another index, re-run it there and check the SIGN first.

BANKNIFTY is structurally harder regardless: 5M p90 bar 95.3 vs NIFTY's 22.45
(4x the noise, so 150–200pt stops), often no weekly so the nearest expiry is a
30+ DTE monthly at ~₹27,000 a lot, and overnight gaps of median 203.6 / p75 419.1.

## 11. Mean-reversion fades need the OPPOSITE exit to displacement trades

Measured 2026-08-26 on 24 decided fade instances, minutes from entry:

| | n | median | p75 | max |
|---|---|---|---|---|
| **losses** | 6 | **32 min** | 40 | **85** |
| **wins** | 18 | 62 min | 140 | **235** |

Every loss was over inside 85 minutes; five of eighteen winners landed after 105.
**A 14:00 hard time stop would have cut 28% of the winners and saved nothing.**
Conditional on surviving 45 minutes the trade went on to win 10 of 11 decided.

This **inverts** learning #4. A momentum trade that has not moved is spent; a fade
that has not yet reverted is merely unfinished. Before writing any time stop, ask
which family the trade is in, then check whether losses resolve faster than wins —
if they do, a clock cut is pure winner-selection against you.

## 12. Options track the FORWARD, and the basis compresses on rallies

Measured live 2026-08-25, NIFTY 1-Sep 24300 CE across a closing ramp:

| | 15:16 | 15:38 | change |
|---|---|---|---|
| spot | 24260.05 | 24334.55 | **+74.50** |
| parity forward | 24359.25 | 24392.69 | **+33.44** |
| basis | +96.80 | +58.14 | **−38.66** |
| 24300 CE mid | 148.70 | 165.93 | +17.23 |

**Effective delta vs SPOT 0.231. Effective delta vs FORWARD 0.515, against a stated
0.508.** The option tracked its forward perfectly; the forward moved less than half
of what spot did.

Any expectancy model that applies historical **spot** moves to a forward-priced
option and assumes the basis only decays with time is **overstating the edge,
roughly 2x for a fast move.** Treat such backtests as an upper bound. The term
structure is also concave, not linear (25-Aug: 0d +7.15, 7d +100.11, 14d +123.19,
35d +202.69), so linear basis decay overstates overnight bleed too.

## 13. Fee drag sets a minimum position size

Measured across the trades of 2026-08-25 (one lot, `vv/costs.py`):

| debit/lot | round-trip fees | % of debit |
|---|---|---|
| ₹1,274 | ₹49.28 | **3.87%** |
| ₹3,146 | ₹52.23 | 1.66% |
| ₹9,717 | ₹70.51 | **0.73%** |

Brokerage is ₹20/order flat, so a round trip costs **₹50–70 almost regardless of
size**. Broker statement decomposition (18–27 Aug): **75% of charges are FIXED per
order**, only 25% proportional to turnover.

**Treat ~₹6,000 debit as the floor for a single-lot index round trip.** It compounds
per session: on 25-Aug four round trips cost ₹234 against −₹81 of gross, and over
18–27 Aug charges were **79% of the total loss**.

**Sizing up does NOT fix this.** Position size multiplies whatever expectancy you
have; if gross is negative, 2x size loses more, not less. The lever is *fewer
trades* — 12 → 4 took a modelled −₹770 to −₹257 with no forecasting skill required.

## 14. Never condition on a fact that does not exist at entry time

On 2026-08-26 a BANKNIFTY long was recommended for **12:30 entry** on a setup
requiring a **strong close**. Splitting it:

| version | n | won | vs BE 40% |
|---|---|---|---|
| **knowable at 12:30** (new high + gap up) | 466 | 40% | **+0** |
| + strong close *(not knowable)* | 184 | 45% | +5 |
| the other 61% | 282 | 37% | −3 |

Only 39% of gap-up new-high days go on to close strong. The whole "edge" lived in
an unknowable term. **Every condition must be evaluable from data stamped at or
before the entry timestamp.** The tell: a backtest that enters at the CLOSE while
the recommendation enters intraday — make the simulated entry the same instant as
the recommended one and the bias disappears.

**Corollary (2026-08-28):** the same error recurs when *reading* a close-dependent
rule early. A bucket read at 15:26 off the 15:20 bar called "no trade"; the 15:25
bar carried +33.20 points and the close landed in the paying bucket. **A rule whose
input is the close must be read after the close settles.**

## 15. Entry instructions need a timestamp

On 2026-08-27 a leg was called with the entry written as *"24286 (at the close)"*.
24286 was the **15:05** price; the actual close was **24207.75**, 78 points lower.
Acting immediately: +17% to +37%. Following the words literally: buying ~163 instead
of 118.70, after the move, with the target already passed.

**An entry carries a price, the clock time that price was read, and a validity
window.** Never write "at the close", "at market" or "now" as if they were the same
instant as the quote — especially in the last hour.

## 16. A fill exists only when the user states it or a broker document shows it

On 2026-08-27 the broker P&L statement was reconciled against
`journal_annotations.json` for the first time. The journal had been reporting
**+₹743 net when the true figure was −₹962 — overstated by ₹1,701.**

| error | effect |
|---|---|
| **T015 BANKNIFTY 57700 PE — never executed** | +₹600 fictitious |
| **T014 exit recorded 66.50, actually 59.30** | a **win** that was a **−₹240 loss** |
| a whole leg missing from the journal | −₹582 unrecorded |
| three entry/exit prices wrong | −₹121 |

Every one of those rows carried the words **"ACTUAL fills"**. They were
watcher-stamped quotes and same-minute reconstruction. T014's own note recorded the
put bidding 59.25 eight minutes after the exit it claimed at 66.50 — the
contradiction was sitting in the file.

**Only a stated fill or a broker document may populate `premium_entry`/
`premium_exit`.** A live quote is a quote. A watcher stamp is a quote. Unconfirmed
stays null and stays out of the P&L. Reconcile against the statement before quoting
any cumulative figure. `backtest.py log` now snapshots the signal fields into
`signals_log.jsonl` so the journal survives signal-file deletion.

## 17. Pre-register any filter before testing it

After ~15 exploratory tests in two days, a compression-regime veto was written to
`vv/prereg_regime_filter.md` **before** being run — rule, thresholds, sample, pass
and fail criteria all fixed in writing, plus a **random-veto control** (500 draws
skipping the same number of trades; the filter had to beat the 95th percentile).

**It failed.** On the fade it removed half the entries and moved the win rate 0.7
points, against a random-veto median of 71.4%. Split-half deltas opposed in sign in
both setups (−8.3/+15.4 and −4.4/+4.2) — the signature of noise.

Without the control it would have looked like a win: *"win rate held while trades
halved, charges down."* **If the goal is fewer trades, trade fewer — do not dress a
trade-count reduction up as a signal filter.** Discarded, not re-specified.

**Why this matters:** at a 5% threshold ~15 tests yields one spurious "significant"
result by chance. The highest z-score produced all fortnight (2.21, an at-the-lows
long found by splitting an existing sample) **lost on its first live test.** Every
edge measured here sits at z = 0.8–2.2; none is established.
