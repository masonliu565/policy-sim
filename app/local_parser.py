"""
Offline policy parser. No network, no API key, no model call.

The app has to work with nothing but Python and this repository. An LLM parser
is a nice affordance for oddly phrased input, but needing one to read
"$300 a month per child" means the product stops working on a laptop with no key
or no wifi. That is not acceptable for the thing the whole demo rests on.

Returns the same lever names the engine takes, plus a plain-English list of what
it understood, so a misread is visible on screen instead of silent. Nothing is
guessed: if no amount is found, it says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

EMPTY: Dict[str, object] = {
    "credit_per_child_under_6": 0.0,
    "credit_per_child_6_to_17": 0.0,
    "fully_refundable": True,
    "phaseout_start_single": None,
    "phaseout_start_joint": None,
    "phaseout_rate": 0.0,
    "flat_transfer_per_adult": 0.0,
}

# The suffix needs a word boundary after it. Without one, the "m" in "monthly"
# was read as "million" and "$400 monthly per child" became $4.8 billion a year.
NUM = r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(k|thousand|m|million)?\b"
BAND_6_17 = r"(?:for\s*)?(?:ages?\s*)?6\s*(?:-|to|through|–)\s*17"


@dataclass
class LocalParse:
    levers: Dict[str, object] = field(default_factory=lambda: dict(EMPTY))
    understood: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    ok: bool = True

    @property
    def is_empty(self) -> bool:
        return not (self.levers["credit_per_child_under_6"]
                    or self.levers["credit_per_child_6_to_17"]
                    or self.levers["flat_transfer_per_adult"])


def _money(value: str, suffix: Optional[str]) -> float:
    n = float(value.replace(",", ""))
    if suffix:
        s = suffix.lower()
        if s in ("k", "thousand"):
            n *= 1_000
        elif s in ("m", "million"):
            n *= 1_000_000
    return n


def _is_monthly(text: str, span: Tuple[int, int]) -> bool:
    """
    Was the amount stated per month?

    Checks the matched phrase ITSELF as well as a short tail. In
    "$300 a month per kid" the words "a month" fall INSIDE the match, so looking
    only after it missed the conversion and turned $3,600/year into $300/year.
    """
    window = text[span[0]:span[1] + 34]
    return bool(re.search(r"\b(?:a|per|each|every)\s*(?:month|mo)\b|monthly", window))


def _amount_before(text: str, at: int, window: int = 46):
    """
    The dollar amount nearest BEFORE a phrase.

    Scanning forward from the first number read "$3,600 per child under 6 and
    $3,000 for ages 6-17" as $3,600 for BOTH bands. The amount belonging to a
    band is the one closest to it, reading leftward.
    """
    start = max(0, at - window)
    seg = text[start:at]
    found = list(re.finditer(NUM, seg))
    if not found:
        return None, None
    m = found[-1]
    return _money(m.group(1), m.group(2)), (start + m.start(), start + m.end())


def parse(text: str) -> LocalParse:
    out = LocalParse()
    raw = (text or "").strip()
    if not raw:
        out.ok = False
        out.notes.append("Enter a policy description first.")
        return out
    low = raw.lower()

    has_older_band = re.search(BAND_6_17, low) is not None

    # ---- explicit "under N" band ------------------------------------------
    under = re.search(NUM + r"[^.;]{0,40}?\bunder\s*(?:age\s*)?(\d+)", low)
    if under:
        amt = _money(under.group(1), under.group(2))
        if _is_monthly(low, under.span()):
            amt *= 12
        cutoff = int(under.group(3))
        out.levers["credit_per_child_under_6"] = amt
        if cutoff > 6 and not has_older_band:
            # "under 18" with no second band means one rate for every child.
            # "under 6" means exactly what it says: older children get nothing.
            out.levers["credit_per_child_6_to_17"] = amt
        out.understood.append(f"${amt:,.0f} a year per child under {cutoff}")

    # ---- explicit 6-17 band -----------------------------------------------
    if has_older_band:
        anchor = re.search(BAND_6_17, low)
        amt, span = _amount_before(low, anchor.start())
        if amt is not None:
            if _is_monthly(low, span):
                amt *= 12
            out.levers["credit_per_child_6_to_17"] = amt
            out.understood.append(f"${amt:,.0f} a year per child aged 6-17")

    # ---- a single per-child amount, no age split --------------------------
    if not under and not has_older_band:
        m = re.search(NUM + r"[^.;]{0,26}?\b(?:per|a|each|every|/)\s*"
                            r"(?:child|kid|children|kids)\b", low)
        if m is None:
            m = re.search(r"\b(?:per|a|each|every)\s*(?:child|kid)\b[^.;]{0,22}?" + NUM, low)
        if m:
            amt = _money(m.group(1), m.group(2))
            if _is_monthly(low, m.span()):
                amt *= 12
            out.levers["credit_per_child_under_6"] = amt
            out.levers["credit_per_child_6_to_17"] = amt
            out.understood.append(f"${amt:,.0f} a year per child, every age")

    # ---- flat per-adult transfer ------------------------------------------
    m = re.search(NUM + r"[^.;]{0,26}?\b(?:per|a|each|every|/)\s*"
                        r"(?:adult|person|worker)\b", low)
    if m:
        amt = _money(m.group(1), m.group(2))
        if _is_monthly(low, m.span()):
            amt *= 12
        out.levers["flat_transfer_per_adult"] = amt
        out.understood.append(f"${amt:,.0f} a year per adult")

    # ---- phase-out ---------------------------------------------------------
    if re.search(r"\bno\s+phase\s*-?\s*out|\bwithout\s+a?\s*phase", low):
        out.understood.append("no phase-out")
    else:
        m = re.search(r"(?:phase[sd]?\s*(?:it\s*)?out|phased?\s*out|phaseout|taper"
                      r"|withdraw|starts?\s*to\s*fall)[^.;]{0,44}?" + NUM, low)
        if m is None:
            m = re.search(r"\b(?:over|above|beyond|past)\s*" + NUM, low)
        if m:
            start = _money(m.group(1), m.group(2))
            out.levers["phaseout_start_single"] = start
            out.levers["phaseout_start_joint"] = start * 2
            out.understood.append(
                f"phases out from ${start:,.0f} single, ${start * 2:,.0f} joint")
            out.notes.append("The joint threshold was not stated, so it is set to "
                             "double the single one, which is the usual design.")
            r = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:%|percent)", low)
            rate = float(r.group(1)) / 100 if r else 0.05
            out.levers["phaseout_rate"] = rate
            out.understood.append(f"phase-out rate {rate:.0%}")
            if not r:
                out.notes.append("No phase-out rate was stated, so 5% is assumed "
                                 "(the rate the 2021 CTC used).")

    # ---- refundability -----------------------------------------------------
    if re.search(r"\bnon-?refundable\b", low):
        out.levers["fully_refundable"] = False
        out.understood.append("not refundable")
    elif re.search(r"\brefundable\b", low):
        out.levers["fully_refundable"] = True
        out.understood.append("fully refundable")

    out.ok = not out.is_empty
    if out.is_empty:
        out.notes.append(
            'No amount per child or per adult was found. Try something like '
            '"$300 a month per child, phased out over $150k".')
    return out
