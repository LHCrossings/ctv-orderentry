"""
day_utils.py — Universal Day Code Parser

Recognizes any day abbreviation variant by tokenizing greedily
(longest match first) rather than storing every possible combination.

Canonical internal codes (Etere convention):
    M = Monday      T = Tuesday     W = Wednesday
    R = Thursday    F = Friday      S = Saturday    U = Sunday

Display aliases: S → Sa, U → Su (everywhere else stays the same)
"""

from __future__ import annotations

# ── Week ordering ─────────────────────────────────────────────────────────────
WEEK_ORDER: list[str] = ['M', 'T', 'W', 'R', 'F', 'S', 'U']

# Etere day_ids indices (matches contractLineBlocks* element order in UI)
CODE_TO_IDX: dict[str, int] = {'U': 0, 'M': 1, 'T': 2, 'W': 3, 'R': 4, 'F': 5, 'S': 6}

# ── Alias table ───────────────────────────────────────────────────────────────
# Every known variant → canonical single-letter code
# Sorted longest-first so the tokenizer always tries the longest match first
_ALIAS_MAP: dict[str, str] = {
    # Monday
    'Monday': 'M', 'Mon': 'M', 'Mo': 'M', 'M': 'M',
    # Tuesday
    'Tuesday': 'T', 'Tues': 'T', 'Tue': 'T', 'Tu': 'T', 'T': 'T',
    # Wednesday
    'Wednesday': 'W', 'Wed': 'W', 'We': 'W', 'W': 'W',
    # Thursday  →  R  (Etere uses R to avoid ambiguity with Tuesday's T)
    'Thursday': 'R', 'Thurs': 'R', 'Thur': 'R', 'Thu': 'R', 'Th': 'R', 'R': 'R',
    # Friday
    'Friday': 'F', 'Fri': 'F', 'Fr': 'F', 'F': 'F',
    # Saturday  →  S  (displayed as Sa)
    'Saturday': 'S', 'Sat': 'S', 'SAT': 'S', 'Sa': 'S', 'S': 'S',
    # Sunday    →  U  (displayed as Su)
    'Sunday': 'U', 'Sun': 'U', 'SUN': 'U', 'Su': 'U', 'U': 'U',
}

# Pre-sorted longest-first for greedy matching
_TOKENS: list[str] = sorted(_ALIAS_MAP, key=len, reverse=True)

# Human-readable display for range/comma output
_DISPLAY: dict[str, str] = {'S': 'Sa', 'U': 'Su'}


def _disp(code: str) -> str:
    return _DISPLAY.get(code, code)


# ── Core tokenizer ────────────────────────────────────────────────────────────

def tokenize(s: str) -> list[str]:
    """
    Parse any day string into an ordered list of canonical Etere codes.

    Handles all formats in any combination:
      Concatenated:     "MTuWThF"      → ['M','T','W','R','F']
      Mixed-case H&L:   "WThFSaSu"     → ['W','R','F','S','U']
      Comma-separated:  "M,Tu,Th,F"    → ['M','T','R','F']
      Space-separated:  "M T W R F"    → ['M','T','W','R','F']
      Range notation:   "M-F"          → ['M','T','W','R','F']
      Single token:     "Th" or "Thu"  → ['R']
    """
    s = s.strip()
    result: list[str] = []
    i = 0

    while i < len(s):
        ch = s[i]

        # Skip delimiters
        if ch in (' ', ','):
            i += 1
            continue

        # Hyphen after a recognized day → try to expand as a range
        if ch == '-' and result:
            j = i + 1
            for token in _TOKENS:
                if s[j:].startswith(token):
                    end_code = _ALIAS_MAP[token]
                    start_code = result[-1]
                    if start_code in WEEK_ORDER and end_code in WEEK_ORDER:
                        si = WEEK_ORDER.index(start_code)
                        ei = WEEK_ORDER.index(end_code)
                        if si <= ei:
                            result.pop()
                            result.extend(WEEK_ORDER[si:ei + 1])
                            i = j + len(token)
                            break
            else:
                i += 1  # unrecognised hyphen — skip
            continue

        # Greedy token match
        for token in _TOKENS:
            if s[i:].startswith(token):
                result.append(_ALIAS_MAP[token])
                i += len(token)
                break
        else:
            i += 1  # unrecognised character — skip

    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for code in result:
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


# ── Output formatters ─────────────────────────────────────────────────────────

def to_etere(s: str) -> str:
    """
    Convert any day string to a clean Etere-compatible display string.

    Contiguous sequences become range notation; non-contiguous become
    comma-separated canonical codes.

    Examples:
        "MTuWThF"    → "M-F"
        "WThF"       → "W-F"
        "SaSu"       → "Sa-Su"
        "WThFSaSu"   → "W-Su"
        "Th"         → "R"
        "M,W,F"      → "M,W,F"
    """
    codes = tokenize(s)
    if not codes:
        return s  # unrecognised input — pass through unchanged

    if len(codes) == 1:
        return _disp(codes[0])

    indices = [WEEK_ORDER.index(c) for c in codes]
    if indices == list(range(min(indices), max(indices) + 1)):
        # Contiguous block → range notation
        return f"{_disp(WEEK_ORDER[min(indices)])}-{_disp(WEEK_ORDER[max(indices)])}"

    # Non-contiguous → comma-separated
    return ','.join(_disp(c) for c in codes)


def to_indices(s: str) -> list[int]:
    """
    Convert any day string to a sorted list of Etere day_ids indices.

    Index mapping (matches contractLineBlocks* element order):
        0=Sunday, 1=Monday, 2=Tuesday, 3=Wednesday,
        4=Thursday, 5=Friday, 6=Saturday
    """
    codes = tokenize(s)
    return sorted(CODE_TO_IDX[c] for c in codes if c in CODE_TO_IDX)
