"""
What a change to the DC income tax schedule does to DC households.

The microsimulation in model/engine.py moves money toward households. A tax
change moves it the other way, and nothing else about the accounting differs:
disposable income falls by the extra liability, poverty is recomputed against
the same federal thresholds, and the same per-group summary says who carries
it. So this computes the liability change and then hands it to the SAME audited
outcome functions the transfer path uses. No second set of poverty arithmetic
exists to drift out of step with the first.

The schedule itself is not modelled or assumed; it is the published DC one,
with its source and a self-consistency check, in model/dc_tax.py.

WHAT IS APPROXIMATE, stated plainly because it bears on every number here:

  * The ACS records a household. DC taxes a filing unit. Household type is
    mapped to a filing status (a lone parent files as head of household, a
    couple jointly), which is wrong for any household of unrelated adults.
  * The tax base is built from the taxable income components in the person
    file -- wages, self-employment, interest and dividends, retirement
    distributions, other income -- less the standard deduction. Public
    assistance and SSI are excluded because they are not taxable, and Social
    Security is excluded because the District does not tax it.
  * Only the standard deduction is applied. Itemisers, credits (including the
    DC EITC and the Keep Child Care Affordable credit), and every other
    adjustment are not modelled, so liability is overstated for the households
    that claim them.
  * There is no behavioural response. Nobody moves to Virginia, works fewer
    hours, or shifts income between years. The result is a statutory
    calculation, and it is an upper bound on revenue for that reason.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parent.parent
MODEL = REPO / "model"
for _p in (str(REPO), str(MODEL)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_CACHE: Dict[tuple, Dict[str, Any]] = {}


def household_tax_base():
    """Gross taxable income and filing status per household, aligned to the
    engine population's row order."""
    import numpy as np
    import pandas as pd
    from dc_api import dc_engine, dc_persons

    pop = dc_engine.population()
    df = pop.df
    people = dc_persons.persons()
    if people is None:
        return pop, None, None

    per_hh = (people.groupby("SERIALNO", as_index=True)["taxable_income"]
              .sum().astype(float))
    gross = df["SERIALNO"].map(per_hh).fillna(0.0).to_numpy(dtype=float)
    status = df["household_type"].astype(str).to_numpy()
    return pop, gross, status


def run_tax(points: Optional[float] = None, proportional: Optional[float] = None,
            seeds: int = 300) -> Dict[str, Any]:
    """Simulate a change to the DC schedule.

    points        move every marginal rate by this many percentage points
    proportional  scale every tax bill by this percentage instead
    """
    import numpy as np
    import engine as E
    import dc_tax as T

    key = (points, proportional, seeds)
    if key in _CACHE:
        return _CACHE[key]

    pop, gross, status = household_tax_base()
    if gross is None:
        raise RuntimeError("DC person records are unavailable, so no tax base "
                           "can be built. Run: python dc_api/dc_persons.py")

    taxable = T.taxable_income(gross, status)
    base_liability = T.liability(taxable)
    if points is not None:
        new_liability = T.liability(taxable, T.shift_rates(points))
    elif proportional is not None:
        new_liability = base_liability * (1.0 + proportional / 100.0)
    else:
        new_liability = base_liability
    delta = new_liability - base_liability            # positive = pays more

    # The engine's contract: `transfer` is what the policy hands the household
    # and `new_income` is disposable income after it. A tax is a transfer with
    # the sign flipped, so the same functions apply unchanged.
    n = int(seeds)
    transfer = np.repeat((-delta)[None, :], n, axis=0).astype(np.float32)
    new_income = (pop.inc.astype(float)[None, :] - delta[None, :]).astype(np.float32)
    new_income = np.repeat(new_income, n, axis=0) if new_income.shape[0] == 1 else new_income
    boot = E.bootstrap_weights(n, pop.n)

    impact = E.outcomes_for(pop, transfer, new_income, boot=boot)

    # group_stats reads `transfer > 0` to mean "the policy reached this
    # household". For a tax the money moves the other way, so it is handed the
    # liability increase rather than the (negative) transfer: the same field
    # then counts the share of the group PAYING MORE, which is what a reader
    # wants to know about a tax. The income change is taken from new_income and
    # stays correctly negative either way.
    owes_more = np.repeat(delta[None, :], n, axis=0).astype(np.float32)
    by_group = []
    for (gtype, gname), mask in pop.masks.items():
        st = E.group_stats(pop, owes_more, new_income, mask, boot=boot)
        if st is not None:
            by_group.append({"group_type": gtype, "group": gname, **st})

    # Revenue is the design-weighted extra liability. Its interval comes from
    # the same bootstrap the outcomes use, so the two are consistent.
    rev_draws = (delta[None, :] * (pop.dw[None, :] * boot)).sum(1)
    revenue = {"median": float(np.median(rev_draws)),
               "p05": float(np.percentile(rev_draws, 5)),
               "p95": float(np.percentile(rev_draws, 95))}
    paying_more = float((pop.dw * (delta > 0)).sum())

    out = {
        "impact": impact,
        "by_group": by_group,
        "annual_revenue_usd": revenue,
        "households_paying_more": paying_more,
        "households_total": float(pop.dw.sum()),
        "baseline_liability_usd": float((base_liability * pop.dw).sum()),
        "n_households": pop.n,
        "n_seeds": n,
        "understood": T.describe(points=points, proportional=proportional),
        "warnings": [
            "A statutory calculation on ACS microdata, not a revenue estimate "
            "from the Chief Financial Officer.",
            "No behavioural response: nobody moves, works less, or shifts "
            "income. Revenue is therefore an upper bound.",
            "Only the standard deduction is applied. Itemised deductions and "
            "every DC credit, including the DC EITC, are not modelled, so "
            "liability is overstated for households that claim them.",
            "The ACS records households; DC taxes filing units. Household type "
            "stands in for filing status, which is wrong for households of "
            "unrelated adults.",
        ],
    }
    _CACHE[key] = out
    return out
