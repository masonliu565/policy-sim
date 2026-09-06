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

SUBDIR = {"main.ts": "city/src", "index.html": "city"}

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
        # Nothing should reach this branch -- every answer now carries an
        # estimate -- but the string itself must not exist in a product
        # whose contract is that it never tells you it needs more information.
        ("result.status==='needs_clarification'?'Let’s narrow that down.':'More evidence is needed.'",
         "result.status==='needs_clarification'?'Let’s narrow that down.':'What the evidence supports'"),
        ('<span>Ask Juniper</span><span class="launcher-hint">Explore the evidence</span>',
         '<span>Policy Sim</span><span class="launcher-hint">Ask a policy question</span>'),
        ('<h2 id="chat-title">Ask Juniper</h2><p>Questions, grounded in DC.</p>',
         '<h2 id="chat-title">Policy Sim</h2><p>Policy questions, grounded in DC data.</p>'),
        ('aria-label="Conversation with Juniper"',
         'aria-label="Policy Sim conversation"'),
        ('<span class="eyebrow">A CLOSER LOOK AT YOUR CITY</span><h3>Every question<br>starts somewhere.</h3><p>Ask about DC’s households, city services, or health. I’ll bring the evidence into view.</p>',
         '<span class="eyebrow">POLICY, MEASURED</span><h3>What should<br>we change?</h3><p>Describe a policy for DC and this simulates who it reaches, from ACS microdata, city records, and a pre-registered model.</p>'),
        ('>Ask a question about Washington, DC<',
         '>Ask a policy question about Washington, DC<'),
        ('placeholder="What would you like to know about DC?"',
         'placeholder="What policy should we simulate? e.g. $400 a month per child under 6"'),
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
    "main.ts": [
        ('aria-label="Juniper, explore the National Mall"',
         'aria-label="Policy Sim, explore Washington, DC"'),
        ('juniper<span class="brand-dot">.</span>',
         'policy sim<span class="brand-dot">.</span>'),
        ('Ask Juniper brings historical household, service, and health evidence into the map.',
         'Policy Sim brings household, service, and health evidence into the map, and simulates policy changes against it.'),
        ('simplified, and styled for Juniper.',
         'simplified, and styled for this atlas.'),
    ],
    "index.html": [
        ('<title>Juniper — Washington, DC</title>',
         '<title>Policy Sim — Washington, DC</title>'),
    ],}


def main(root: Path) -> int:
    src = root / "city" / "src" / "policy"
    if not src.is_dir():
        print(f"ERROR: {src} not found. Run dc_api/setup_frontend.sh first.")
        return 1
    for name, edits in EDITS.items():
        path = root / SUBDIR.get(name, "city/src/policy") / name
        text = path.read_text(encoding="utf-8")
        before = text
        for old, new in edits:
            if new in text:
                continue                      # already applied
            if old not in text:
                print(f"ERROR: {name} no longer contains the line this patch "
                      f"expects.\n"
                      f"  wanted: {old[:80]}...\n"
                      f"  The atlas is pinned in dc_api/setup_frontend.sh. If "
                      f"the pin moved, read the new upstream file before "
                      f"re-pointing this patch at it -- in particular check "
                      f"that chat.ts still POSTs to /api/v1/query, because an "
                      f"upstream branch exists that points it at a different "
                      f"service entirely.")
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
