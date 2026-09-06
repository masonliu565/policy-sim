# Output contract — `/scenarios/*.json`

For Track B. This is the shape the model emits. Everything in the STEP 0 stub is
unchanged; the new keys are **additive**, so nothing you already wrote breaks.

Read the three "states" section first — that's the part that changes what you
render.

---

## Top level

```jsonc
{
  "policy_id": "ctc_2021",
  "label": "Expanded Child Tax Credit (2021)",
  "n_seeds": 500,
  "policy_spec": { ...the levers this run used... },

  "in_support": true,              // NEW — see state 3
  "nearest_policies": [ ... ],     // NEW — populated always, matters when false

  "impact":  { ...4 outcomes... },
  "by_metro": [ ... ],             // NEW
  "opinion": { "overall_support": ..., "by_group": [ ... ] },
  "induced_consumption_usd": { ... },
  "warnings": [ "...", "..." ]
}
```

## `impact` — unchanged from the stub

Exactly four outcomes, each with **all four** of `baseline`, `median`, `p05`,
`p95`:

| key | units |
|---|---|
| `child_poverty_rate` | fraction (0.107 = 10.7%) |
| `overall_poverty_rate` | fraction |
| `median_disposable_income` | dollars |
| `annual_cost_usd` | dollars |

`baseline` is the no-policy world. `median/p05/p95` are across simulation seeds.

## `opinion.overall_support` — unchanged shape, but **may be `null`**

`{median, p05, p95}` — note there is **no `baseline` key** here, unlike
`impact`. That asymmetry is deliberate; don't "fix" it.

It is `null` when there isn't enough evidence to support a national number. Show
the state, not a zero.

---

# The three states

## State 1 — `low_sample: true`

Present on entries in `opinion.by_group` and on `by_metro` entries.

```jsonc
{ "group": "single_parent", "sample_n": 87, "low_sample": true,
  "support": { "median": 0.71, "p05": 0.58, "p95": 0.83 } }
```

The interval is **real** — render it normally, then mark it visibly as low
confidence. Do not hide it, do not drop it, do not widen it yourself. The band is
already wider because the sample is thin; that's the honest signal.

Triggered when the group's sample is under 100 households.

## State 2 — `evidence_status: "insufficient_evidence"`

Present on every entry in `opinion.by_group`.

```jsonc
{ "group": "Q3", "group_type": "income_quintile",
  "households_weighted": 26512443.0,
  "disposable_income_delta": 1180.0,
  "pct_better_off": 0.42,
  "support": null,
  "evidence_status": "insufficient_evidence",
  "evidence_coverage": 0.0,
  "evidence_ids": [] }
```

**Render the material impact numbers normally** — `households_weighted`,
`disposable_income_delta`, `pct_better_off` are all fully computed and
trustworthy. Only the *support* estimate is missing.

In the support slot show the literal text **“insufficient evidence”**. Not a
zero. Not a blank cell. Not a dash. Explicit words.

`evidence_coverage` tells you *why* — it's the share of that group's households
sitting in cells backed by real survey data. A group at 0.12 coverage is not
"12% supportive"; it means 88% of the group has no evidence behind it. Useful as
a tooltip, dangerous as a headline.

Under MRP this is 1.0 when the model contains a dimension informed by evidence
and 0.0 when it does not. `household_type` groups currently sit at 0.0 — no
source publishes a parents-vs-non-parents crosstab with a subgroup sample size,
so that dimension is excluded from the model rather than imputed, and those
groups report `insufficient_evidence`. Their material impact numbers are still
fully valid; render them normally.

Values of `evidence_status`:

| value | meaning |
|---|---|
| `"ok"` | `support` is a real `{median,p05,p95}` |
| `"insufficient_evidence"` | `support` is `null`; show the text |
| `"out_of_support"` | `support` is `null` because the whole policy is out of support — see state 3 |

Each `by_group` entry also carries `"method"` — currently `"mrp"`, the
hierarchical model in `model/mrp.py`. The older `"ladder"` path still exists and
is used by the backtest for comparison, but is not what the demo reads. You do
not need to branch on it; it is there so a number on screen can be traced to the
method that produced it.


## State 3 — `in_support: false`

Top-level. The proposal lies outside the evidence base entirely: we have no
historical policy close enough in lever-space to say anything about opinion.

```jsonc
{
  "in_support": false,
  "nearest_policies": [
    { "policy_id": "ctc_2021", "label": "Expanded Child Tax Credit (2021)",
      "year": 2021, "distance": 3.4,
      "source": "American Rescue Plan Act of 2021, Pub. L. 117-2, Sec. 9611" },
    { "policy_id": "eip_2021", "...": "..." }
  ],
  "opinion": { "overall_support": null, "by_group": [ /* all out_of_support */ ] }
}
```

When this is false:

- **All** `opinion` numbers are `null`. Every `by_group` entry carries
  `evidence_status: "out_of_support"`.
- `impact` is **still fully valid** — the microsimulation doesn't need opinion
  evidence. Render the material impact exactly as normal.
- Show `nearest_policies` as "here's what we do have data on". They're ordered
  nearest-first and each carries a real statutory citation you can display.
- `distance` is a normalised lever-space distance; the cutoff is 1.5. It's a
  reasonable thing to show as "how far outside" but don't present it as a
  probability.

Make this look deliberate. It's the most defensible state in the whole app —
the model declining to answer is the feature.

---

## `by_metro`

```jsonc
{
  "metro": "Houston",
  "households_weighted": 2725020.0,
  "sample_n": 2000,
  "low_sample": false,
  "impact": { ...same four outcomes, same baseline/median/p05/p95... },
  "top_subgroups": [
    { "group_type": "household_type", "group": "couple_with_kids",
      "disposable_income_delta": 4820.0,
      "disposable_income_delta_p05": 4310.0,
      "disposable_income_delta_p95": 5290.0,
      "households_weighted": 512000.0, "sample_n": 340, "low_sample": false }
  ]
}
```

Six metros: New York, Houston, Detroit, San Francisco, Phoenix, Atlanta.

- `impact` has the **identical shape** to the national `impact`. Same code path,
  computed on the metro subpopulation — not derived from national numbers.
- `top_subgroups` is the 3 most-affected subgroups **within that metro**, ranked
  by weighted disposable income change, highest first.
- **Metro intervals are legitimately ~3.9× wider than national ones** (2,000
  households vs 30,000). If they look wide, they are supposed to. Don't rescale
  them to match the national chart's axis.

Households outside all six metros have `metro = null` and appear in no
`by_metro` entry.

---

## `warnings`

Array of plain strings, always present, sometimes long. At minimum it names:
unsourced parameters, evidence coverage, any fallback the poststratification
had to make, and the metro interval-width caveat.

Worth surfacing somewhere in the UI. It is the honest part.

---

## Things that will bite you

1. `opinion.overall_support` can be `null`. Handle it before it renders as
   "None".
2. `support` inside `by_group` can be `null` independently of the top-level
   `in_support`.
3. Rates are fractions, dollars are raw (`161065089399.4`, not `161.07`).
4. `evidence_ids` is always an array — possibly empty, never `null`, never
   absent.
5. `by_metro` may be a shorter list than 6 if a metro had no sampled households
   (shouldn't happen; handle it anyway).
