"""
Model parameters.

=============================================================================
CITATION RULE -- read before adding anything to this file.
=============================================================================
Every coefficient, elasticity, rate, threshold and range in this module MUST
carry a source comment directly above it. A source is one of:

    - a named published study or dataset, with year and a URL or DOI
    - a statutory citation (e.g. IRC Sec. 24, ARPA Sec. 9611)
    - an official agency table (Census, CBO, JCT, HHS), with the table id

If you do not have a source, write the number and mark it:

    # SOURCE: TODO -- no citation yet. Placeholder, do not present as sourced.

DO NOT invent a citation. A fabricated reference is worse than a missing one:
it survives review, and every downstream number inherits it silently.

Uncertainty parameters are declared as (central, low, high) triples. `low` and
`high` bound the Latin hypercube draw in engine.py; `central` is the value used
for deterministic runs and for the sensitivity decomposition in backtest.py.

HONESTY NOTE FOR THE PITCH: three of the parameters below are currently
UNSOURCED. They are marked TODO and they are listed in every scenario's
`warnings` array, so the number on screen carries its own caveat. Say this out
loud rather than letting someone find it.
=============================================================================
"""

import pathlib

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Poverty thresholds
# ---------------------------------------------------------------------------
# SOURCE: U.S. Census Bureau, "Poverty Thresholds for 2024 by Size of Family and
# Number of Related Children Under 18 Years" (thresh24.xlsx).
# https://www2.census.gov/programs-surveys/cps/tables/time-series/historical-poverty-thresholds/thresh24.xlsx
# Parsed by model/build_poverty_thresholds.py into a tracked CSV; not hand-typed.
POVERTY_THRESHOLD_CSV = REPO / "data" / "processed" / "poverty_thresholds_2024.csv"


def load_poverty_thresholds():
    """(family_size, n_children) -> annual threshold in 2024 dollars."""
    df = pd.read_csv(POVERTY_THRESHOLD_CSV, comment="#")
    return {(int(r.family_size), int(r.related_children_under_18)): float(r.threshold_usd)
            for r in df.itertuples()}


# ---------------------------------------------------------------------------
# Uncertainty parameters -- (central, low, high)
# ---------------------------------------------------------------------------

# Share of eligible households that actually receive the credit. The 2021
# advance CTC reached the large majority of eligible children automatically via
# prior-year tax returns, with the shortfall concentrated among non-filers.
# SOURCE: TODO -- no citation yet. Placeholder, do not present as sourced.
# The range is deliberately wide because the non-filer gap is the single
# biggest driver of the cost and poverty estimates.
TAKE_UP_RATE = (0.90, 0.80, 0.97)

# Extensive-margin labor supply elasticity: proportional reduction in earnings
# per dollar of unconditional transfer. Enters as
#     earnings change = -elasticity * transfer
# applied only to households with positive earned income.
# SOURCE: TODO -- no citation yet. Placeholder, do not present as sourced.
# The literature on the 2021 CTC specifically is contested; the low end (0.0)
# encodes "no detectable employment effect", which is itself a published claim.
LABOR_SUPPLY_ELASTICITY = (0.10, 0.00, 0.25)

# Marginal propensity to consume out of the transfer. Reported as an aggregate
# consumption response. NOTE: this parameter does NOT enter the poverty or cost
# calculations -- it is drawn and reported only. It is included because the
# build spec asked for it; do not let it appear to drive the headline numbers.
# SOURCE: TODO -- no citation yet. Placeholder, do not present as sourced.
MARGINAL_PROPENSITY_TO_CONSUME = (0.50, 0.30, 0.70)

# Effective marginal tax rate used to approximate tax liability when a credit
# is NOT fully refundable, so the credit can be capped at liability.
# SOURCE: TODO -- no citation yet. Placeholder, do not present as sourced.
# This is a crude stand-in for a tax calculator and is stated in warnings.
EFFECTIVE_TAX_RATE = 0.12
STANDARD_DEDUCTION_SINGLE = 14_600     # SOURCE: IRS Rev. Proc. 2023-34, tax year 2024
STANDARD_DEDUCTION_JOINT = 29_200      # SOURCE: IRS Rev. Proc. 2023-34, tax year 2024

UNCERTAIN_PARAMS = {
    "take_up_rate": TAKE_UP_RATE,
    "labor_supply_elasticity": LABOR_SUPPLY_ELASTICITY,
    "marginal_propensity_to_consume": MARGINAL_PROPENSITY_TO_CONSUME,
}

# Parameters with no citation yet. Surfaced into every scenario's warnings.
UNSOURCED = [
    "take_up_rate",
    "labor_supply_elasticity",
    "marginal_propensity_to_consume",
    "effective_tax_rate (non-refundable cap)",
]

PARAMS = {
    "uncertain": UNCERTAIN_PARAMS,
    "effective_tax_rate": EFFECTIVE_TAX_RATE,
    "standard_deduction_single": STANDARD_DEDUCTION_SINGLE,
    "standard_deduction_joint": STANDARD_DEDUCTION_JOINT,
    "poverty_threshold_csv": str(POVERTY_THRESHOLD_CSV),
    "unsourced": UNSOURCED,
}

# ---------------------------------------------------------------------------
# Evidence quality gate
# ---------------------------------------------------------------------------
# Minimum subgroup sample size for a survey crosstab to be used as a predictor
# weight in poststratification. Below this the record's own standard error
# dominates the output interval while carrying almost no information: at
# n = 100 and p = 0.5 the standard error is 5.0 points; at n = 300 it is 2.9.
# Records below the threshold are EXCLUDED and named in warnings, never
# silently down-weighted.
MIN_EVIDENCE_N = 300

# Minimum share of a group's households (by weight) that must sit in cells with
# real evidence before a support number is reported for that group.
#
# WHY THIS EXISTS: aggregating over only the covered cells silently reweights a
# group to its covered subset. With evidence for Q1 alone, "couple_no_kids
# support = 74%" is really "Q1 couple_no_kids support = 74%" wearing a broader
# label. That is imputation by omission. Below this threshold the group reports
# evidence_status "insufficient_evidence" instead.
MIN_GROUP_COVERAGE = 0.60

# ---------------------------------------------------------------------------
# Low-sample threshold
# ---------------------------------------------------------------------------
# A group whose interval is reported but flagged `low_sample: true` in the
# output, so the app can mark it visibly rather than hiding or dropping it.
# Chosen as the point where a 50% share has a standard error above ~5 points.
LOW_SAMPLE_N = 100
