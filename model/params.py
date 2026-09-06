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

# ---------------------------------------------------------------------------
# TWO PARAMETER SETS, AND WHY BOTH EXIST
# ---------------------------------------------------------------------------
# PREREGISTERED holds the values frozen in docs/backtest.md at commit f4bb17e,
# before model/backtest.py existed. They are NEVER edited. The A6 headline
# result is, and stays, the result under these values.
#
# SOURCED holds literature-backed values obtained AFTER the backtest had already
# run and missed. Swapping them in moves the prediction, and it moves it toward
# a better fit. That is precisely the situation the pre-registration exists to
# guard against, so both sets are kept, both are reported, and the improvement
# is labelled post-hoc rather than presented as the headline. See the POST-HOC
# section of docs/backtest.md.
# ---------------------------------------------------------------------------

PREREGISTERED = {
    "take_up_rate": (0.90, 0.80, 0.97),
    "labor_supply_elasticity": (0.10, 0.00, 0.25),
    "marginal_propensity_to_consume": (0.50, 0.30, 0.70),
}

# --- take-up rate ----------------------------------------------------------
# SOURCE: Schild, Collyer, Garner, Kaushal, Lee, Waldfogel & Wimer, "Effects of
# the Expanded Child Tax Credit on Household Spending", BLS Working Paper 601
# (2023), p.13: the monthly payments "reached between roughly 88.5 percent and
# 91 percent of eligible children", attributing to Curran (2022) and Parolin,
# Collyer et al. (2021).
# https://www.bls.gov/osmr/research-papers/2023/pdf/ec230010.pdf
#
# The range is widened to 0.85-0.93 rather than taken literally as 0.885-0.91,
# because the denominator was never published: those same authors state that
# data on the number of ELIGIBLE children were not publicly available.
# Self-reported coverage is much lower -- Parolin et al. (NBER WP 29285) find
# 66% of children in households reporting receipt, Urban Institute 57% of
# adults with children -- but those are coverage rates over ALL households with
# children, not take-up among the eligible, and their authors warn against that
# reading. Pilkauskas & Michelmore (U. Michigan Poverty Solutions, Dec 2021) is
# the only estimate isolating the non-filer problem: 68% of very-low-income
# parents received the October payment, 21% missing it for non-valid reasons.
TAKE_UP_RATE = (0.90, 0.85, 0.93)

# --- labor supply elasticity ----------------------------------------------
# READ THIS BEFORE CITING THIS PARAMETER.
#
# Our functional form is  earnings change = -elasticity * transfer, i.e. DOLLARS
# OF EARNINGS LOST PER DOLLAR TRANSFERRED. No paper in this literature reports
# that quantity. That is a specification problem, not a search problem, and it
# is recorded here rather than covered with a citation that does not support the
# number.
#
# What the literature actually estimates:
#
# (a) Corinth, Meyer, Stadnicki & Wu, NBER WP 29366 (2021, rev. 2022) --
#     PARTICIPATION elasticities with respect to the return to work: 0.75 for
#     single mothers receiving EITC, 0.25 for other tax units with children
#     (income elasticities -0.085 and -0.05). They project 1.46M workers exiting,
#     2.6% of working parents. They do NOT publish an aggregate dollar earnings
#     loss, so their result cannot be converted into this coefficient without
#     inventing the missing aggregates.
#     https://www.nber.org/system/files/working_papers/w29366/w29366.pdf
#
# (b) Ananat, Glasner, Hamilton & Parolin, NBER WP 29823 (2022) -- reduced-form
#     employment effect per dollar: +0.1pp employment per additional $100/month
#     (s.e. 0.1pp) and +0.2pp labour force participation (s.e. 0.1pp), n=504,364
#     CPS. The point estimate is positive and statistically indistinguishable
#     from zero. They explicitly test and reject the income gradient that
#     Corinth et al.'s elasticities imply.
#     https://www.nber.org/system/files/working_papers/w29823/w29823.pdf
#
# (c) Schanzenbach & Strain, NBER WP 32552 (2024) -- no significant reduction for
#     parents overall (-0.8pp, n.s.), but a significant -4.5pp for unmarried
#     women with low education.
#     https://www.nber.org/system/files/working_papers/w32552/w32552.pdf
#
# CENTRAL VALUE 0.0 follows (b), the only estimate expressible in units close to
# ours. The UPPER BOUND 0.25 is retained from the pre-registration and is NOT
# sourced in these units: it is kept deliberately so the model does not assume
# the null, and it stands in for the Corinth-side view that cannot be converted.
# Do not present the upper bound as sourced.
LABOR_SUPPLY_ELASTICITY = (0.00, 0.00, 0.25)

# --- marginal propensity to consume ---------------------------------------
# SOURCE: Schild, Collyer, Garner, Kaushal, Lee, Waldfogel & Wimer, "Spending
# Response to the Expanded Child Tax Credit", Review of Income and Wealth (2026),
# DOI 10.1111/roiw.70068: $44 spent per $100 received (housing $28, food $12).
#
# IMPORTANT: the widely circulated figure is $75 per $100, from the SUPERSEDED
# working papers (NBER WP 31412 and BLS WP 601, both 2023). The published version
# revised it DOWN by 41%. The high end of the range below is that obsolete
# figure, retained only because a reviewer may cite it at you. The low end is
# JPMorgan Chase Institute (Wheat, Deadman & Sullivan, 2022): 21% spent in the
# first week after the November payment, from a ~460,000-household banking panel
# -- a different measurement window, not a corroboration of the $44.
# This parameter is REPORTED ONLY; it does not enter poverty or cost.
MARGINAL_PROPENSITY_TO_CONSUME = (0.44, 0.21, 0.75)

# --- effective tax rate for the non-refundable cap -------------------------
# SOURCE: statutory 12% marginal bracket, IRC Sec. 1(j) as in effect for tax
# year 2024. That is a MARGINAL rate being used to approximate a TOTAL
# liability, which is the wrong object.
#
# Measured average effective individual income tax rates are far lower. JCT,
# "Overview of the Federal Tax System as in Effect for 2022" (JCX-14-22), Table
# A-6: -3.3% for $30-40k, -0.7% for $40-50k, +2.4% for $50-75k, +5.0% for
# $75-100k on expanded income -- negative at the bottom precisely because the
# outlay portion of refundable credits is included. A flat 12% therefore
# OVERSTATES liability and so UNDER-BINDS the non-refundable credit cap. No
# source was found giving an average effective rate specifically for households
# with children; CBO's household-type tables are the likely home and were
# unreachable (cbo.gov returned 403 to every retrieval method tried). This
# remains an open citation. None of the five demo scenarios exercise it -- all
# are fully refundable.
EFFECTIVE_TAX_RATE = 0.12

STANDARD_DEDUCTION_SINGLE = 14_600     # SOURCE: IRS Rev. Proc. 2023-34, tax year 2024
STANDARD_DEDUCTION_JOINT = 29_200      # SOURCE: IRS Rev. Proc. 2023-34, tax year 2024

SOURCED = {
    "take_up_rate": TAKE_UP_RATE,
    "labor_supply_elasticity": LABOR_SUPPLY_ELASTICITY,
    "marginal_propensity_to_consume": MARGINAL_PROPENSITY_TO_CONSUME,
}

# Which set the engine uses. backtest.py runs BOTH and reports both.
PARAM_SET = "sourced"

UNCERTAIN_PARAMS = dict(SOURCED if PARAM_SET == "sourced" else PREREGISTERED)


def use_param_set(name):
    """Switch parameter sets in place. backtest.py uses this to report both."""
    global PARAM_SET
    if name not in ("sourced", "preregistered"):
        raise ValueError(name)
    PARAM_SET = name
    UNCERTAIN_PARAMS.clear()
    UNCERTAIN_PARAMS.update(SOURCED if name == "sourced" else PREREGISTERED)
    return UNCERTAIN_PARAMS

# Parameters with no citation yet. Surfaced into every scenario's warnings.
# What still lacks a citation after the literature pass. Surfaced into every
# scenario's warnings so the caveat travels with the number.
UNSOURCED = [
    "labor_supply_elasticity upper bound (0.25): the literature reports "
    "participation elasticities, not earnings lost per dollar transferred, so "
    "no published estimate exists in this functional form",
    "effective_tax_rate (non-refundable cap): a statutory MARGINAL rate used to "
    "approximate TOTAL liability; JCT measured average effective rates are far "
    "lower, so this under-binds the cap",
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
