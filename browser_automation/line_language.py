"""Per-line language verification at order entry (CTV_LineLanguage catalog).

The language of a contract line = what language the CUSTOMER bought. It can
often be guessed from the line description or the estimate code, but it can
NEVER be silently assumed — once written to the catalog nobody re-checks it.
Every gather flow that catalogs languages must run its lines through
`confirm_line_languages()` so a human vouches for each value.

Codes match the EtereBridge/backwrite language options.
"""

from __future__ import annotations

import re
from typing import Optional

# Valid codes (superset of EtereBridge config; 'M/C' = combined Chinese block)
LANGUAGE_CODES = ["E", "C", "M", "M/C", "V", "T", "K", "J", "SA", "Hm", "P", "H", "L"]

# Keyword → code guesses for IO line descriptions / language-block names.
# Order matters: more specific phrases first.
_KEYWORD_GUESSES = [
    (r'\bmandarin\b.*\bcantonese\b|\bcantonese\b.*\bmandarin\b|\bchinese\b', 'M/C'),
    (r'\bmandarin\b', 'M'),
    (r'\bcantonese\b', 'C'),
    (r'\bvietnamese\b', 'V'),
    (r'\bfilipino\b|\btagalog\b', 'T'),
    (r'\bkorean\b', 'K'),
    (r'\bjapanese\b', 'J'),
    (r'\bsouth\s*asian\b|\bhindi\b', 'SA'),
    (r'\bpunjabi\b', 'P'),
    (r'\bhmong\b', 'Hm'),
    (r'\benglish\b', 'E'),
]


def guess_language(text: str) -> Optional[str]:
    """Best-effort language guess from a line description / block name.

    Returns a code from LANGUAGE_CODES or None. A None guess is fine — the
    user types the code at the confirmation prompt.
    """
    t = (text or "").lower()
    for pattern, code in _KEYWORD_GUESSES:
        if re.search(pattern, t):
            return code
    return None


def confirm_line_languages(items: list[dict]) -> list[str]:
    """Interactive per-line language verification (gather-time, CLI).

    items: [{"label": str, "guess": str|None}, ...] — one per IO line, in the
    same order the automation will create contract lines.

    Returns the verified language code per line (same order). The user must
    actively confirm every value: Enter accepts the guess shown in brackets,
    typing a code overrides, and lines with no guess require a code. Supports
    'all <code>' at the first prompt to apply one code to every line.
    """
    codes = "/".join(LANGUAGE_CODES)
    print("\n[LANGUAGE] Verify the language the customer bought for each line")
    print(f"           Codes: {codes}   ('all <code>' applies to every line)")

    result: list[str] = []
    apply_all: Optional[str] = None

    for i, item in enumerate(items, 1):
        if apply_all:
            result.append(apply_all)
            print(f"  [{i}/{len(items)}] {item['label']}  → {apply_all} (applied to all)")
            continue

        guess = (item.get("guess") or "").strip()
        prompt = f"  [{i}/{len(items)}] {item['label']}  [{guess or '?'}]: "
        while True:
            raw = input(prompt).strip()
            if raw.lower().startswith("all "):
                cand = _normalize(raw[4:])
                if cand:
                    apply_all = cand
                    result.append(cand)
                    break
                print(f"    Unknown code {raw[4:]!r} — use one of: {codes}")
                continue
            if not raw:
                if guess:
                    result.append(guess)
                    break
                print("    No guess for this line — type a language code.")
                continue
            cand = _normalize(raw)
            if cand:
                result.append(cand)
                break
            print(f"    Unknown code {raw!r} — use one of: {codes}")

    return result


def _normalize(raw: str) -> Optional[str]:
    """Case-insensitive match against LANGUAGE_CODES ('hm' → 'Hm', 'm/c' → 'M/C')."""
    r = raw.strip().upper()
    for code in LANGUAGE_CODES:
        if code.upper() == r:
            return code
    return None
