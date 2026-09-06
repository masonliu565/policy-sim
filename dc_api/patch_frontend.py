"""
Teach the Juniper atlas's breakdown table to label itself from the data.

The atlas ships one breakdown renderer, written for a health survey: the column
header is the literal string "Cost barriers", the sample line says "valid
yes/no responses", and the disclosure summary says "Survey responses". Our
policy answers put a different thing in that table -- which household groups a
transfer reaches, and by how much -- so left alone the table would print
headers that describe numbers it is not showing. A table with a lying header is
worse than no table.

So exactly three strings become data-driven, each falling back to the atlas's
own wording when a response does not supply one. Nothing else is touched: no
layout, no styling, no behaviour, and every existing health answer renders
byte-identically.

This runs after the clone (see setup_frontend.sh) because frontend/ is not
vendored. It is idempotent, and it fails loudly rather than quietly half-
applying if upstream moves the lines out from under it.

    python dc_api/patch_frontend.py [frontend_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

EDITS = {
    "types.ts": [
        # An answer may carry its own formatted headline and label.
        ('  estimate: { kind: string; value: number; unit: string; populationShare?: number; denominator?: number } | null;',
         '  estimate: { kind: string; value: number; unit: string; populationShare?: number; denominator?: number; displayValue?: string; label?: string } | null;'),
        # ...and its own formatted interval.
        ('method: string; limitations: string } | null;',
         'method: string; limitations: string; displayRange?: string } | null;'),
        ("  breakdowns: {label: string; groups: SurveyGroup[]}[];",
         "  breakdowns: {label: string; groups: SurveyGroup[]; columns?: string[]}[];"),
        ("  publicationRule: {description: string};\n  limitations: string[];",
         "  publicationRule: {description: string};\n  limitations: string[];\n"
         "  sampleText?: string;\n  summaryLabel?: string;"),
    ],
    "presentation.ts": [
        ("    sample: `${formatNumber(overall.validRecords)} valid yes/no "
         "responses of ${formatNumber(overall.sampleRecords)} respondents; "
         "${formatNumber(overall.missingRecords)} excluded responses.`,",
         "    sample: analysis.sampleText ?? `${formatNumber(overall.validRecords)} "
         "valid yes/no responses of ${formatNumber(overall.sampleRecords)} "
         "respondents; ${formatNumber(overall.missingRecords)} excluded responses.`,\n"
         "    summaryLabel: analysis.summaryLabel ?? 'Survey responses & breakdowns',"),
        ("      label: breakdown.label,\n      rows:",
         "      label: breakdown.label,\n"
         "      columns: breakdown.columns ?? ['Group', 'Valid responses', "
         "'Cost barriers', '95% interval'],\n      rows:"),
    ],
    "chat.ts": [
        # The headline formatter rounds anything that is not a percent
        # to a whole number, so a -1.33 point change showed as "-1".
        ("card.append(el('div',number(estimate.value,estimate.unit==='percent'),'chat-estimate'),\n        el('div',estimateLabel(estimate.kind),'chat-estimate-label'));",
         "card.append(el('div',estimate.displayValue??number(estimate.value,estimate.unit==='percent'),'chat-estimate'),\n        el('div',estimate.label??estimateLabel(estimate.kind),'chat-estimate-label'));"),
        # The interval had the same problem: -7.25 to 4.56 printed
        # as "-7" to "5", which is not the interval we computed.
        ("if(result.uncertainty?.level)card.append(el('p',`${Math.round(result.uncertainty.level*100)}% uncertainty interval: ${number(result.uncertainty.lower,result.estimate?.unit==='percent')}–${number(result.uncertainty.upper,result.estimate?.unit==='percent')}`,'chat-interval'));",
         "if(result.uncertainty?.level)card.append(el('p',`${Math.round(result.uncertainty.level*100)}% uncertainty interval: ${result.uncertainty.displayRange??`${number(result.uncertainty.lower,result.estimate?.unit==='percent')}–${number(result.uncertainty.upper,result.estimate?.unit==='percent')}`}`,'chat-interval'));"),
        ("el('summary','Survey responses & breakdowns')",
         "el('summary',view.summaryLabel)"),
        ("for(const label of ['Group','Valid responses','Cost barriers','95% interval'])",
         "for(const label of breakdown.columns)"),
    ],
}


def main(root: Path) -> int:
    src = root / "city" / "src" / "policy"
    if not src.is_dir():
        print(f"ERROR: {src} not found. Run dc_api/setup_frontend.sh first.")
        return 1
    for name, edits in EDITS.items():
        path = src / name
        text = path.read_text(encoding="utf-8")
        before = text
        for old, new in edits:
            if new in text:
                continue                      # already applied
            if old not in text:
                print(f"ERROR: {name} no longer contains the line this patch "
                      f"expects. Upstream changed; re-read it before forcing.\n"
                      f"  wanted: {old[:80]}...")
                return 1
            text = text.replace(old, new, 1)
        if text != before:
            path.write_text(text, encoding="utf-8")
            print(f"patched {name}")
        else:
            print(f"{name} already patched")
    return 0


if __name__ == "__main__":
    arg = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "frontend"
    raise SystemExit(main(arg))
