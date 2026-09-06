"""
Model parameters.

=============================================================================
CITATION RULE — read before adding anything to this file.
=============================================================================
Every coefficient, elasticity, rate, threshold and range in this module MUST
carry a source comment directly above it. A source is one of:

    - a named published study or dataset, with year and a URL or DOI
    - a statutory citation (e.g. IRC Sec. 24, ARPA Sec. 9611)
    - an official agency table (Census, CBO, JCT, HHS), with the table id

If you do not have a source, write the number and mark it:

    # SOURCE: TODO — no citation yet. Placeholder, do not present as sourced.

DO NOT invent a citation. A fabricated reference is worse than a missing one:
it survives review, and every downstream number inherits it silently.

Uncertainty parameters are declared as (central, low, high) triples. `low` and
`high` bound the Latin hypercube draw in engine.py; `central` is the value used
for deterministic runs and for the sensitivity decomposition in backtest.py.
=============================================================================
"""

# Populated in A4. Every entry added here needs a source comment per the rule above.
PARAMS = {}
