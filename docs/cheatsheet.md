# Cheat sheet — for the person delivering the pitch

You explain it. The model defends it. Read this once before you go on.

---

## The one-sentence version

> We simulate a policy on 30,000 real US households, we say how uncertain we
> are, and we tested ourselves against a policy that already happened — without
> tuning to make it fit.

---

## Every number on screen, in plain words

| On screen | What it actually is |
|---|---|
| **child poverty rate** | Share of *children* below the official Census poverty threshold for their family size and number of kids. Weighted by children, not households. |
| **overall poverty rate** | Same, but share of *people*. |
| **median disposable income** | The middle household's income after the policy. Half of households are below it. |
| **annual cost** | Total dollars paid out in a year, national. |
| **baseline** | The same number in a world with no policy. Everything is a comparison to this. |
| **support** | Estimated share who'd back the policy, built from real polls reweighted onto our population. |
| **"insufficient evidence"** | We have poverty numbers for this group but no polling that speaks to it. We refuse to guess. |

## How to explain p05 / p95 without looking at me

> "We ran it 500 times with different assumptions. The middle line is the
> typical answer. The band is where 90% of the runs landed — 5% came in below
> the bottom, 5% above the top. A wide band means the assumptions matter a lot
> here. A narrow band means they don't."

If someone asks "is that a confidence interval?" — say:

> "It's an uncertainty band over our assumptions, not a sampling confidence
> interval. It answers 'how much does the answer move when we don't know the
> take-up rate?', not 'how much would it move with a different survey sample.'"

That distinction is real and saying it will land well.

## The metro numbers

Six metros: New York, Houston, Detroit, San Francisco, Phoenix, Atlanta.

- They come from the **same code** as the national numbers, run on that metro's
  households. Not scaled down from national.
- **Their bands are about 4× wider than national. That is correct, not a bug.**
  2,000 households per metro vs 30,000 nationally. If someone says the metro
  bands look wide, agree with them: "yes — a quarter of the data, twice the
  noise. We'd rather show that than hide it."
- The PUMA→metro mapping comes from real Census crosswalk files, not guessed
  ZIP ranges. 11 geographic areas straddle a metro boundary; we assign them by
  majority population and the net error is −0.14%.

## The three limitations to name — before anyone finds them

Say these yourself. Volunteering them is the whole credibility play.

**1. One parameter can't be sourced, because the literature doesn't estimate it.**
Take-up and MPC now have real citations (BLS WP 601; *Review of Income and
Wealth* 2026). The labor supply elasticity does not, and the reason is the
interesting part: our model uses *dollars of earnings lost per dollar
transferred*, and **no paper reports that quantity.** Corinth et al. estimate
participation elasticities and project 1.46M workers exiting but publish no
aggregate dollar figure; Ananat et al. estimate a per-dollar employment effect
and find essentially zero; Schanzenbach & Strain find zero overall but −4.5pp for
unmarried women with low education. Say: *"that's a specification problem in our
model, not a gap in the literature, and we've labelled the number as unsourced
rather than attaching a citation that doesn't support it."*

If pressed on why it matters: it dominates the sensitivity analysis. The thing
our answer depends on most is the thing nobody has measured in our units.

**2. We only have public opinion data on one policy.**
Real finding, worth stating: no academic, government or university source
publishes public *support* crosstabs for the 2021 CTC. Census, NBER, Columbia
and Urban Institute all measure poverty and receipt — not opinion. So we hold
opinion evidence for exactly one policy. Every other scenario shows "outside the
evidence base" rather than a fabricated number. Say: *"four of our five
scenarios decline to give an opinion estimate. That's the feature."*

**3. Our poverty measure isn't the one the government headlines.**
We compute household money income against official Census thresholds (OPM-style).
The famous 2021 result is on an SPM basis, which counts more transfers and
adjusts for local cost of living. Different measure, different baseline. This is
why our backtest missed, and we wrote that down *before* running it.

## The backtest — the most important slide

**We missed. Say so first.**

- Predicted change in child poverty: **−3.20 points** [−3.80, −2.60]
- Actually observed: **−4.50 points** (Census SPM, 2020→2021)
- **Outside our interval by 0.7 points.**

**And then the strongest fact we have:** we later sourced every parameter we
could from the literature and re-ran it. The prediction moved by **0.01
points**. The miss survives the entire declared parameter space — so it is not a
tuning problem and could not have been rescued by one. That points squarely at
the measure mismatch (our OPM-style baseline is 13.89%; the SPM 2020 baseline is
9.7%) which we had registered in advance.

Then the line that matters:

> "We wrote the prediction down and committed it to git before we wrote the
> code that produces it. You can check the commit order. When it missed, we
> reported the miss instead of touching a parameter. Even the most favourable
> corner of our declared parameter range doesn't reach −4.5 — so this isn't a
> tuning problem, it's a measure mismatch we'd flagged in advance."

We also got a **pre-registered expectation wrong** — we predicted we'd
over-shoot and we under-shot. Say that too. It costs nothing and it's the most
honest thing on the deck.

**Backtest 2** (opinion): we held out an entire October 2021 poll and predicted
it from July data. All misses point the same direction — we over-predict support.
Reason: support genuinely fell between July and October, and our model has no
time dimension. *"It's not mis-weighted. It's answering a question about July."*

We then replaced the lookup with a proper hierarchical model (MRP) and re-ran
the same holdout:

| | old ladder | MRP |
|---|---|---|
| mean absolute gap | 3.36 pts | **2.06 pts** |
| inside 90% interval | 2/8 | **4/8** |

It also estimates a **survey house effect** — YouGov reads 51% nationally,
Morning Consult 54% on a differently worded question — and removes it, so we
report the public rather than one pollster's instrument.

**Volunteer this caveat:** MRP was built after seeing the ladder miss on that
same holdout, so 2.06 is indicative, not a clean out-of-sample number. Saying so
before you're asked is worth more than the number is.

## If you get asked something you don't know

> "I don't want to guess at that — it's in the model and [teammate] can give you
> the exact figure."

Never invent a number on stage. The entire pitch is that we don't do that.

## Numbers worth having in your head

- **30,000** households, sampled from **1.6 million** ACS 2024 records
- Weighted totals reproduce published Census figures: households **−0.00%**,
  population **−0.04%**, median income **−0.35%**
- Engine runs 30k × 500 seeds in **under 1 second**
- Baseline child poverty **13.89%**; 2021 CTC brings it to **10.68%**
- 2021 CTC annual cost **$162B**
- Estimated national support for the 2021 CTC **52.5%** [51.1, 54.5]
- Four of five scenarios report **no** support estimate — outside the evidence base

## The one thing not to say

Don't say "our model proves". Say "our model estimates, and here's how wrong it
was last time we checked."
