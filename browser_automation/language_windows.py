"""
Crossings TV language airing windows + order validation.

Each in-language block airs only in specific dayparts on Crossings TV. This
module validates that a PAID line's ordered daypart actually matches the language
it was booked for — catching messy IOs where, e.g., a Filipino spot is ordered in
the 7p-12a Chinese slot. ROS/bonus lines are exempt: they run across the whole
language window, so their daypart is not meaningful here.

The windows MIRROR ``_CTV_LANG_WINDOWS`` in ``src/web/routes/orders.py`` (the
traffic-assignment source of truth). Keep the two in sync — if programming
windows change there, update them here too. Times are broadcast-day 24h "HH:MM".
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def _m(hhmm: str) -> int:
    h, mnt = hhmm.split(":")
    return int(h) * 60 + int(mnt)


# language -> allowed airing interval(s) on Crossings TV (CTV markets).
# "Chinese" = the union of Mandarin + Cantonese windows.
_WINDOWS_HHMM: dict[str, List[Tuple[str, str]]] = {
    "Chinese":     [("06:00", "08:00"), ("19:00", "23:59")],
    "Mandarin":    [("06:00", "08:00"), ("20:00", "23:59")],
    "Cantonese":   [("19:00", "20:00"), ("23:30", "23:59")],
    "Korean":      [("08:00", "10:00")],
    "Vietnamese":  [("10:00", "13:00")],
    "South Asian": [("13:00", "16:00")],
    "Hindi":       [("13:00", "16:00")],
    "Punjabi":     [("14:00", "16:00")],
    "Filipino":    [("16:00", "19:00")],
    "Hmong":       [("18:00", "20:00")],
}

CTV_LANG_WINDOWS: dict[str, List[Tuple[int, int]]] = {
    lang: [(_m(a), _m(b)) for a, b in ivs] for lang, ivs in _WINDOWS_HHMM.items()
}

_TOL_MIN = 1  # allow a 1-minute slop (e.g. 23:59 vs 24:00 rounding)


def check_language_window(language: str, time_from: str, time_to: str) -> Optional[str]:
    """Validate a paid line's daypart against its language's airing window(s).

    Returns None if the ordered [time_from, time_to] window fits inside one of the
    language's allowed intervals (or the language has no window on file and can't
    be validated). Returns a human-readable mismatch message otherwise.

    Args & window strings are broadcast-day 24h "HH:MM".
    """
    intervals = CTV_LANG_WINDOWS.get(language)
    if not intervals:
        return None  # unmapped language (e.g. Japanese) — nothing to check against
    lo, hi = _m(time_from), _m(time_to)
    for a, b in intervals:
        if lo >= a - _TOL_MIN and hi <= b + _TOL_MIN:
            return None
    allowed = ", ".join(f"{a}-{b}" for a, b in _WINDOWS_HHMM[language])
    return (f"{language} airs {allowed}, but this line is ordered "
            f"{time_from}-{time_to}")
