"""
The District's individual income tax schedule, and what a change to it costs.

WHY THIS EXISTS. Asked to simulate a 5% income tax increase, the service could
only say that no tax rates were in the evidence. That was correct -- the
reasoning layer is forbidden from supplying a rate from memory, and a rate
recalled by a language model is exactly the kind of number this project refuses
to print. The fix is not to relax the rule. It is to go and get the schedule
from the body that publishes it, and then compute the answer.

SOURCE. DC Office of Tax and Revenue, individual income tax rate schedule for
tax years beginning after 31 December 2021:
    https://otr.cfo.dc.gov/page/dc-individual-and-fiduciary-income-tax-rates
Cross-checked against the 2024 D-40 booklet, page 4 (rate schedule) and page 10
(standard deduction):
    https://otr.cfo.dc.gov/sites/default/files/dc/sites/otr/publication/
    attachments/2024_D40_Booklet_011525.pdf
Statutory authority: DC Code Title 47, Chapter 18. Retrieved 2026-09-06.

The schedule below is self-checking: each bracket's base amount must equal the
tax accumulated by the brackets under it, and BRACKETS_ARE_CONSISTENT asserts
it at import. A transcription slip in any rate or threshold breaks that
identity, so it cannot pass silently.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# (lower bound of bracket, tax at that bound, marginal rate above it)
BRACKETS: List[Tuple[float, float, float]] = [
    (0.0,          0.0,      0.0400),
    (10_000.0,     400.0,    0.0600),
    (40_000.0,     2_200.0,  0.0650),
    (60_000.0,     3_500.0,  0.0850),
    (250_000.0,    19_650.0, 0.0925),
    (500_000.0,    42_775.0, 0.0975),
    (1_000_000.0,  91_525.0, 0.1075),
]

# DC standard deduction, tax year 2024. D-40 booklet p.10; conforms to the
# federal amounts. DC has had no personal exemption since tax year 2018.
STANDARD_DEDUCTION: Dict[str, float] = {
    "single": 14_600.0,
    "head_of_household": 21_900.0,
    "joint": 29_200.0,
}

# The ACS records a household, not a tax unit. This is the mapping used, and it
# is an approximation: a household containing two unmarried adults is treated
# as one joint filer, and a multi-generational household as a single filer.
FILING_STATUS: Dict[str, str] = {
    "single_no_kids": "single",
    "single_parent": "head_of_household",
    "couple_no_kids": "joint",
    "couple_with_kids": "joint",
    "other": "single",
}


def _check() -> bool:
    """Each base amount is the tax accumulated below it."""
    for i in range(1, len(BRACKETS)):
        lo_prev, base_prev, rate_prev = BRACKETS[i - 1]
        lo, base, _ = BRACKETS[i]
        if abs(base_prev + rate_prev * (lo - lo_prev) - base) > 1e-6:
            return False
    return True


BRACKETS_ARE_CONSISTENT = _check()
assert BRACKETS_ARE_CONSISTENT, (
    "The DC rate schedule in model/dc_tax.py is internally inconsistent: a "
    "bracket's base amount does not equal the tax accumulated below it. One of "
    "the rates or thresholds has been mistranscribed.")


def shift_rates(points: float) -> List[Tuple[float, float, float]]:
    """The schedule with every marginal rate moved by `points` percentage
    points, rebuilt so the base amounts stay consistent with the new rates."""
    out: List[Tuple[float, float, float]] = []
    base = 0.0
    for i, (lo, _, rate) in enumerate(BRACKETS):
        new_rate = max(0.0, rate + points / 100.0)
        if i:
            lo_prev, _, r_prev = out[i - 1]
            base = base + r_prev * (lo - lo_prev)
        out.append((lo, base, new_rate))
    return out


def liability(taxable, schedule: Optional[List[Tuple[float, float, float]]] = None):
    """DC tax on an array of taxable incomes. Vectorized; negatives clamp to 0."""
    import numpy as np

    sched = schedule or BRACKETS
    t = np.maximum(np.asarray(taxable, dtype=float), 0.0)
    out = np.zeros_like(t)
    for lo, base, rate in sched:
        hit = t > lo
        out[hit] = base + rate * (t[hit] - lo)
    return out


def taxable_income(gross_taxable, filing_status):
    """Gross taxable income less the standard deduction for each filer."""
    import numpy as np

    ded = np.array([STANDARD_DEDUCTION[FILING_STATUS.get(s, "single")]
                    for s in filing_status], dtype=float)
    return np.maximum(np.asarray(gross_taxable, dtype=float) - ded, 0.0)


def describe(points: Optional[float] = None, proportional: Optional[float] = None) -> str:
    if points is not None:
        return (f"every DC marginal rate moved by {points:+.2f} percentage "
                f"points (the bottom rate goes from "
                f"{100 * BRACKETS[0][2]:.2f}% to "
                f"{100 * max(0.0, BRACKETS[0][2] + points / 100):.2f}%, the top "
                f"from {100 * BRACKETS[-1][2]:.2f}% to "
                f"{100 * max(0.0, BRACKETS[-1][2] + points / 100):.2f}%)")
    if proportional is not None:
        return (f"every DC tax bill scaled by {1 + proportional / 100:.3f}, "
                f"i.e. {proportional:+.1f}% more tax owed at the same income")
    return "the DC schedule unchanged"
