# Pre-registration — 2021 expanded CTC backtest

**Committed before `model/backtest.py` exists.** Check `git log`: this file's
commit precedes the commit that introduces the backtest code. That ordering is
the point. If the specification and the result arrive in the same commit,
nothing has been registered.

Everything below is fixed as of this commit. Nothing here gets changed after
seeing a result.

---

## 1. What is being predicted

**Backtest 1 — material impact.** The change in the child poverty rate caused by
the 2021 expanded Child Tax Credit, reported as median with p05/p95 across 500
simulation seeds.

**Backtest 2 — opinion.** The subgroup support percentages in the held-out
Morning Consult/POLITICO National Tracking Poll **#2110009 (October 2021)**,
predicted by poststratification using only the non-holdout evidence.

## 2. Parameters, frozen

Values as committed in `model/params.py` at this commit. `(central, low, high)`;
low/high bound the Latin hypercube.

| parameter | central | low | high | sourced? |
|---|---|---|---|---|
| `take_up_rate` | 0.90 | 0.80 | 0.97 | **no — TODO** |
| `labor_supply_elasticity` | 0.10 | 0.00 | 0.25 | **no — TODO** |
| `marginal_propensity_to_consume` | 0.50 | 0.30 | 0.70 | **no — TODO** |
| `effective_tax_rate` | 0.12 | — | — | **no — TODO** |

Other fixed quantities:

- **Seed** `20260905`, **500 seeds**, Latin hypercube via `scipy.stats.qmc`.
- **Population**: `data/processed/us_households.parquet`, 30,000 households,
  stratified PPS sample of ACS **2024** 1-Year PUMS.
- **Poverty thresholds**: official Census 2024 thresholds by family size ×
  related children (`data/processed/poverty_thresholds_2024.csv`).
- **Policy spec** (ARPA 2021 §9611): $3,600 per child under 6, $3,000 ages 6–17,
  fully refundable, phaseout from $75,000 single / $150,000 joint at 5%.
- **Evidence gates**: `MIN_EVIDENCE_N = 300`, `MIN_GROUP_COVERAGE = 0.60`.

## 3. Evidence used, and evidence withheld

Backtest 2 trains on **non-holdout records only**:

- Economist/YouGov, July 17–20 2021 (national, 3 income bands, 4 regions)
- Morning Consult/POLITICO #2107068, July 2021 (national, 3 income bands, 4 regions)
- Data for Progress household-type records — present but **excluded by the
  `MIN_EVIDENCE_N` gate**, because no subgroup N is published

Withheld entirely (`holdout: true`, 8 records): **Morning Consult/POLITICO
#2110009, October 2021** — national, 3 income bands, 4 census regions.

This is an out-of-sample test in *time* (July → October) within a mixed set of
houses. It is not a test across pollsters in isolation.

## 4. The comparison target for Backtest 1

The published change in the **Supplemental Poverty Measure** child poverty rate
between 2020 and 2021, from the Census Bureau's *Poverty in the United States:
2021* (P60-277). The figure will be read from the published Census table at run
time and cited with its table id — not typed in from memory.

## 5. Known mismatches, stated in advance

These are registered *before* seeing any result, so they cannot be produced
afterwards as excuses:

1. **Population year.** The simulation runs on ACS **2024** households. The
   observed change happened in **2021**. Household composition, incomes and
   thresholds all differ. This alone can move the predicted change by a
   meaningful amount.
2. **Poverty measure.** The engine computes an **OPM-style** measure: household
   money income against official Census thresholds. The published 2021 change is
   on an **SPM** basis, which counts taxes and transfers (including the CTC
   itself) and uses geographically adjusted thresholds. These are different
   measures of different things. The comparison is still worth making, but a
   miss here is at least as likely to be measure mismatch as model error.
3. **Income unit.** Poverty is computed at the **household** level, not the
   family level. Households containing unrelated adults pool income, which
   understates measured poverty relative to the official family-based measure.
4. **Counterfactual.** The engine's baseline is *no credit at all*, so the
   modelled transfer is the **entire** credit, not the increment over prior-law
   CTC. The published 2021 change reflects the **expansion** relative to the
   $2,000 TCJA credit. Predicted magnitude should therefore be expected to
   exceed the observed change.
5. **Question wording (Backtest 2).** YouGov asks approve/disapprove; Morning
   Consult asks support/oppose with a "$300 a month" framing. House effects are
   inside the prediction error and are not separable from model error.

## 6. Commitments

- **No parameter will be adjusted to improve the fit.** Not the take-up rate,
  not the elasticity, not the thresholds, not the gates, not the seed.
- If a prediction misses, the miss is reported as a miss, with a sensitivity
  decomposition showing which parameter the result is most sensitive to.
- The interval is **not** widened after the fact to contain the observed value.
- If the observed value falls outside the interval, that is the finding.

Any change to this file after the backtest has run must be an *addition* in a
later commit, clearly marked as post-hoc, leaving the text above intact.

---

# POST-HOC — results

**Added after the backtest ran.** Everything above this line is the
pre-registration and is unchanged. Nothing above was edited in light of what
follows; `git log -p docs/backtest.md` shows that.

## Backtest 1 — missed

| | change in child poverty rate |
|---|---|
| predicted | **−3.20 pts**, 90% interval [−3.80, −2.60] |
| observed | **−4.50 pts** (SPM 9.7% → 5.2%, Census P60-277 Table B-3) |
| verdict | **observed falls OUTSIDE the interval**, by 0.70 pts |

No parameter was adjusted. The interval was not widened.

Sensitivity, one-at-a-time across each parameter's full declared range:

| parameter | range | change at low | change at high | swing |
|---|---|---|---|---|
| `labor_supply_elasticity` | 0.00 – 0.25 | −3.77 pts | −2.72 pts | **1.05 pts** |
| `take_up_rate` | 0.80 – 0.97 | −2.84 pts | −3.66 pts | 0.81 pts |
| `marginal_propensity_to_consume` | 0.30 – 0.70 | −3.31 pts | −3.31 pts | 0.00 pts |

The result is most sensitive to `labor_supply_elasticity` — which is one of the
three parameters with **no citation**. Note also that even the most favourable
corner of the declared parameter space (elasticity at 0, take-up at 0.97) does
not reach −4.50. The miss is not attributable to parameter choice alone.

**A pre-registered expectation that turned out wrong.** Registered mismatch #4
predicted the modelled change would *exceed* the observed change, because the
engine models the entire credit rather than the increment over the $2,000 TCJA
credit. The opposite happened: the model under-predicts. That directional call
was wrong, and the likeliest reason is registered mismatch #2 — SPM counts the
full set of taxes and transfers and uses geographically adjusted thresholds, and
the SPM 2020 child baseline (9.7%) is far below this model's OPM-style baseline
(13.89%). Different baselines make the same policy move a different number of
points. Saying this now, having got the direction wrong, is the point of having
written it down first.

## Backtest 2 — mostly missed, in one direction

Held-out poll: Morning Consult/POLITICO #2110009, October 2021. Predicted from
July evidence only.

- **2 of 8** observed values fell inside the predicted interval
- **mean absolute gap 3.36 points**
- **every single gap is positive** — the model over-predicts support in all 8
  subgroups

The systematic direction is the finding. Support for the CTC genuinely declined
between July and October 2021 (Morning Consult's own national number moved
0.54 → 0.50 on an identical question in the same house). The model has **no time
dimension** — it poststratifies July crosstabs onto a population and has no way
to know that opinion moved. It is not mis-weighted; it is answering a question
about July.

The two hits are `50k_to_100k` (gap 0.2 pts) and `Northeast` (gap 1.1 pts).

Regional predictions are near-flat (53.9–54.1) against observed values spanning
48–53. This was called in advance: every population cell resolves at the
`income_band` level of the fallback ladder, so the region crosstabs never enter
the model, and the regional spread in the output is population composition
rather than measured regional opinion.

## What we would fix, and are not fixing tonight

Listed so it is on the record as a known limitation rather than discovered by a
judge:

1. Give the fallback ladder a way to combine evidence across dimensions instead
   of short-circuiting at the most specific level, so region crosstabs
   contribute.
2. Add a time dimension, or restrict the evidence base to a single fielding
   window and state the window on the output.
3. Get citations for the three TODO parameters, starting with
   `labor_supply_elasticity`, which dominates the sensitivity.
4. Compute poverty on an SPM-comparable basis, or stop comparing against SPM.

---

# POST-HOC, PART 2 — sourced parameters and a better model

**Added later still.** Both changes below were made *after* the results above
were known. Neither replaces the headline. Both are reported with the direction
of their effect stated plainly.

## Parameters now have citations. The miss survives them.

The three TODO parameters were sourced from the literature after the backtest
had already run and missed:

| parameter | pre-registered | sourced | source |
|---|---|---|---|
| `take_up_rate` | 0.90 [0.80, 0.97] | 0.90 [0.85, 0.93] | Schild et al., BLS WP 601 (2023): payments reached 88.5–91% of eligible children |
| `labor_supply_elasticity` | 0.10 [0.00, 0.25] | 0.00 [0.00, 0.25] | Ananat et al., NBER WP 29823 (2022): +0.1pp employment per $100/mo, s.e. 0.1pp — indistinguishable from zero |
| `marginal_propensity_to_consume` | 0.50 [0.30, 0.70] | 0.44 [0.21, 0.75] | Schild et al., *Review of Income and Wealth* (2026), DOI 10.1111/roiw.70068 |

Result:

| | predicted | interval | observed inside? |
|---|---|---|---|
| pre-registered | −3.20 pts | [−3.80, −2.60] | no |
| literature-sourced | −3.21 pts | [−3.66, −2.71] | no |

**Sourcing the parameters moved the prediction by 0.01 points.** The miss is not
a parameter problem. The uncertainty draws sample each parameter's declared
*range*, and the elasticity range was already [0.00, 0.25] — sourcing changed
its central value, not its span. The prediction is therefore robust across the
entire declared parameter space, and the −4.50 observed value lies outside all
of it. That is much stronger evidence for the registered OPM-vs-SPM measure
mismatch than the original single run was.

**One parameter could not be sourced, and this is the more interesting finding.**
Our functional form is *earnings change = −elasticity × transfer* — dollars of
earnings lost per dollar transferred. **No paper in this literature reports that
quantity.** Corinth et al. (NBER WP 29366) estimate participation elasticities
with respect to the return to work (0.75 for single mothers on EITC, 0.25
otherwise) and project 1.46M workers exiting, but publish no aggregate dollar
earnings loss, so their result cannot be converted into our coefficient without
inventing the missing aggregates. Ananat et al. estimate a reduced-form
employment effect per dollar and find essentially zero. Schanzenbach & Strain
find no significant effect overall but −4.5pp for unmarried women with low
education.

So the upper bound of 0.25 remains **unsourced by construction** — it is retained
so the model does not assume the null, and it is labelled as unsourced in
`params.py` and in every scenario's warnings. This is a specification problem in
our model, not a gap in the literature, and it is recorded as such.

Similarly, the 12% effective tax rate is a statutory *marginal* bracket being
used to approximate *total* liability. JCT (JCX-14-22, Table A-6) puts average
effective individual income tax rates at −3.3% to +5.0% across $30k–$100k on
expanded income. Our 12% overstates liability and therefore under-binds the
non-refundable credit cap. None of the five demo scenarios exercise it — all are
fully refundable — but it is an open citation.

## The fallback ladder was replaced with MRP

`model/mrp.py` fits a hierarchical model on the crosstabs: level effects with
partial pooling, plus an explicit survey **house effect**, sampled by collapsed
Gibbs with the level effects integrated out.

Re-run against the **same held-out October 2021 poll**:

| | fallback ladder | MRP |
|---|---|---|
| mean absolute gap | 3.36 pts | **2.06 pts** |
| observed inside 90% interval | 2 / 8 | **4 / 8** |
| closer on | — | **6 of 8 subgroups** |

Three things changed, all of which the A6 write-up had already identified as
defects:

1. **Thin crosstabs are now used.** The n=252 Northeast and n=287 $100k+ records
   were previously discarded by the `MIN_EVIDENCE_N` gate. Partial pooling uses
   them at a weight set by their own precision.
2. **Region evidence now enters.** Under the ladder every cell resolved at
   `income_band` and the region crosstabs never contributed, so regional
   predictions were flat (53.9–54.1) against observed values spanning 48–53.
   MRP predicts Midwest 0.497 and West 0.551, tracking the real spread.
3. **A house effect is estimated and removed.** YouGov reads 0.51 nationally,
   Morning Consult 0.54 on a differently worded item. MRP recovers a 0.098-logit
   gap ≈ 2.5 points and attributes it to the instrument rather than to the
   public. Population estimates use the average instrument, not either pollster.

**CAVEAT, and it matters.** MRP was built *after* seeing the ladder's performance
on this holdout. The design decisions came from structural defects visible
without it — discarded records, unused dimensions, unmodelled house effects —
but a second look at the same test set is still a second look. Treat 2.06 pts as
indicative rather than as a clean out-of-sample result until it is re-tested
against a poll neither method has seen.

**What MRP does not fix:** the residual bias is still one-directional. Every
training record was fielded in July 2021, so no time effect is identified, and
support genuinely fell by October. The model is answering a question about July.
