"""
Crossings TV / The Asian Channel language airing windows.

Each in-language block airs only in specific dayparts. This module is the single
source of truth for those windows and serves two distinct jobs:

1. **Day-aware tables** (``CTV_LANG_WINDOWS_BY_DAY`` / ``DAL_LANG_WINDOWS_BY_DAY``)
   plus :func:`classify_language` — given a spot's weekday and airtime, say which
   language is airing. Used by traffic assignment (spot filters) and by the
   Admerasia Chinese colour matcher, where Mandarin and Cantonese share grid
   colours and only the day+time can tell them apart. ``src/web/routes/orders.py``
   imports these as ``_CTV_LANG_WINDOWS`` / ``_DAL_LANG_WINDOWS``.

2. **Day-less validation** (:func:`check_language_window`) — validates that a PAID
   line's ordered daypart matches the language it was booked for, catching messy
   IOs where e.g. a Filipino spot is ordered in the 7p-12a Chinese slot. ROS/bonus
   lines are exempt: they run across the whole window, so their daypart is not
   meaningful here. Always pass the line's `market`: The Asian Channel programs
   entirely different dayparts from Crossings TV (DAL Cantonese airs 17:00-18:00
   where CTV Cantonese airs 19:00-20:00), so validating a DAL order against the CTV
   windows flags every correct line on it.

The day-less envelopes (``CTV_LANG_WINDOWS`` / ``DAL_LANG_WINDOWS``) are DERIVED
from the day-aware tables by :func:`_envelope` — they used to be a second
hand-written table that could drift. Being a per-language union across all days
they are strictly coarser than their source, and their intervals may OVERLAP
(CTV Mandarin 20:00-23:59 contains Cantonese 23:30-23:59), so an envelope can
validate a whole ordered daypart but can NEVER identify a language. Only the
day-aware table is unambiguous, because Cantonese is weekday-only while Mandarin's
23:30-23:59 slice is weekend-only.

Times are broadcast-day 24h "HH:MM" (see the 06:00->30:00 broadcast-day rule in
``tasks/lessons.md`` — post-midnight is 24:00-29:59 on the same date).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

_WD: Set[str] = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
_WE: Set[str] = {"Saturday", "Sunday"}
_ALL: Set[str] = _WD | _WE
_MSA: Set[str] = _WD | {"Saturday"}

# language -> [(days_set, from HH:MM, to HH:MM)] — end EXCLUSIVE.
CTV_LANG_WINDOWS_BY_DAY: Dict[str, List[Tuple[Set[str], str, str]]] = {
    "Mandarin": [
        (_MSA, "06:00", "07:00"),
        (_ALL, "07:00", "08:00"),
        (_WD,  "20:00", "23:30"),
        (_WE,  "20:00", "23:59"),
    ],
    "Cantonese": [
        (_WD, "19:00", "20:00"),
        (_WD, "23:30", "23:59"),
    ],
    "Korean":     [(_ALL, "08:00", "10:00")],
    "Vietnamese": [(_ALL, "10:00", "13:00")],
    "Hindi": [
        (_WD, "13:00", "14:00"),
        (_WE, "13:00", "16:00"),
    ],
    "Punjabi":  [(_WD, "14:00", "16:00")],
    "Filipino": [
        (_WD, "16:00", "19:00"),
        (_WE, "16:00", "18:00"),
    ],
    "Hmong": [(_WE, "18:00", "20:00")],
}
CTV_LANG_WINDOWS_BY_DAY["Chinese"] = (
    CTV_LANG_WINDOWS_BY_DAY["Mandarin"] + CTV_LANG_WINDOWS_BY_DAY["Cantonese"])
CTV_LANG_WINDOWS_BY_DAY["SouthAsian"] = (
    CTV_LANG_WINDOWS_BY_DAY["Hindi"] + CTV_LANG_WINDOWS_BY_DAY["Punjabi"])

# The Asian Channel (DAL) — broadcast day runs 0600-0559 (wraps past midnight)
DAL_LANG_WINDOWS_BY_DAY: Dict[str, List[Tuple[Set[str], str, str]]] = {
    "Mandarin": [
        (_WD,  "06:00", "09:30"),
        (_WE,  "06:00", "10:00"),
        (_ALL, "13:00", "17:00"),
        (_ALL, "18:00", "22:00"),
        (_ALL, "00:00", "01:00"),
        (_WD,  "02:00", "05:30"),
        (_WE,  "02:00", "05:59"),
    ],
    "Cantonese": [
        (_WD,  "09:30", "10:00"),
        (_ALL, "17:00", "18:00"),
        (_ALL, "01:00", "02:00"),
        (_WD,  "05:30", "05:59"),
    ],
    "Vietnamese": [(_ALL, "10:00", "11:00")],
    "Korean": [
        (_WD,  "11:00", "12:00"),
        (_WE,  "11:00", "13:00"),
        (_WD,  "22:00", "23:00"),
        (_WE,  "22:00", "23:59"),
    ],
}
DAL_LANG_WINDOWS_BY_DAY["Chinese"] = (
    DAL_LANG_WINDOWS_BY_DAY["Mandarin"] + DAL_LANG_WINDOWS_BY_DAY["Cantonese"])

# Aggregate keys are unions of their member languages, so they always match
# alongside the specific language. classify_language() must never return them.
_AGGREGATE_LANGS = frozenset({"Chinese", "SouthAsian"})

_DAL_MARKET_ID = 10


# Spellings a parser/IO may use for a language, mapped to the table's own key.
_LANG_ALIASES = {"South Asian": "SouthAsian", "Tagalog": "Filipino"}

_TOL_MIN = 1  # allow a 1-minute slop (e.g. 23:59 vs 24:00 rounding)

_FPS = 29.97


def _m(hhmm: str) -> int:
    h, mnt = hhmm.split(":")
    return int(h) * 60 + int(mnt)


def is_dal(market: object) -> bool:
    """True if `market` names The Asian Channel — a COD_USER id (10) or a code."""
    if market is None:
        return False
    if isinstance(market, int):
        return market == _DAL_MARKET_ID
    return str(market).strip().upper() in {"DAL", "TAC"}


def windows_for_market(market: object) -> Dict[str, List[Tuple[Set[str], str, str]]]:
    """Day-aware window table for a market (COD_USER id 10 or code "DAL" = DAL)."""
    return DAL_LANG_WINDOWS_BY_DAY if is_dal(market) else CTV_LANG_WINDOWS_BY_DAY


def _win_bounds(a: str, b: str) -> Tuple[int, int]:
    """A window's [start, end) in broadcast-day minutes (06:00 -> 30:00).

    Three rules, all from the broadcast-day lesson in ``tasks/lessons.md``:
      • an hour < 06:00 is the post-midnight tail — shift it +24h (DAL's 00:00-01:00
        Mandarin block lives at 24:00-25:00, never at 0-60);
      • an end written "23:59" MEANS end-of-broadcast-day 24:00, so 23:59 itself is
        inside the window;
      • if the end still lands at/before the start, the window wraps the day end — add
        a further 24h.

    Boundaries are half-open with NO tolerance: adjacent windows must not both claim
    their shared minute, or 20:00 Monday would be Cantonese *and* Mandarin and the
    Chinese disambiguation collapses.
    """
    lo = _m(a)
    if lo < 6 * 60:
        lo += 24 * 60
    hi = 24 * 60 if b == "23:59" else _m(b)
    if b != "23:59" and hi < 6 * 60:
        hi += 24 * 60
    if hi <= lo:
        hi += 24 * 60
    return lo, hi


def classify_language(weekday: str, minute_of_day: int,
                      market_id: Optional[int] = None) -> List[str]:
    """Which language(s) air at `minute_of_day` on `weekday` in this market.

    `weekday` is a full English day name ("Saturday"). `minute_of_day` is
    broadcast-day minutes from 00:00; a post-midnight spot may exceed 1440 and is
    compared against the +24h-shifted windows (see :func:`_win_bounds`).

    Returns the concrete languages only — aggregate keys ("Chinese", "SouthAsian")
    are suppressed since they are unions and would always double-report. A
    single-element list is an unambiguous identification; [] means the time falls
    in no programmed window; >1 element means genuinely overlapping windows and the
    caller must not guess.
    """
    out: List[str] = []
    for lang, wins in windows_for_market(market_id).items():
        if lang in _AGGREGATE_LANGS:
            continue
        for days, a, b in wins:
            lo, hi = _win_bounds(a, b)
            if weekday in days and lo <= minute_of_day < hi:
                out.append(lang)
                break
    return out


def classify_language_frames(weekday: str, frames: int, market_id: Optional[int] = None,
                            fps: float = _FPS) -> List[str]:
    """`classify_language` for a TPALINSE.ORA frame-of-broadcast-day value."""
    return classify_language(weekday, round(frames / fps / 60), market_id)


def _merge(intervals: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Union of [lo, hi) minute intervals — overlapping and abutting ones merged."""
    out: List[Tuple[int, int]] = []
    for lo, hi in sorted(intervals):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _envelope(table: Dict[str, List[Tuple[Set[str], str, str]]]) -> Dict[str, List[Tuple[int, int]]]:
    """Day-less validation envelope DERIVED from a day-aware table: per language,
    the union of its windows across every day, in broadcast-day minutes.

    Deriving it is the whole point — the envelope used to be a second hand-written
    table and the two could drift silently. It is coarser than its source by
    construction (a weekday-only window becomes all-week) and so can validate a
    whole ordered daypart but can NEVER identify a language; only
    :func:`classify_language` may do that.
    """
    return {
        lang: _merge([_win_bounds(a, b) for _, a, b in wins])
        for lang, wins in table.items()
    }


CTV_LANG_WINDOWS: Dict[str, List[Tuple[int, int]]] = _envelope(CTV_LANG_WINDOWS_BY_DAY)
DAL_LANG_WINDOWS: Dict[str, List[Tuple[int, int]]] = _envelope(DAL_LANG_WINDOWS_BY_DAY)


def envelope_for_market(market: object) -> Dict[str, List[Tuple[int, int]]]:
    """Day-less validation envelope for a market (id 10 or code "DAL" = DAL)."""
    return DAL_LANG_WINDOWS if is_dal(market) else CTV_LANG_WINDOWS


def _fmt(minute: int, is_end: bool = False) -> str:
    """Broadcast-day minute back to "HH:MM" for a message. The post-midnight tail
    unwraps (25:00 -> "01:00"). An END at exactly 24:00 is written "23:59", the way
    an order writes end-of-broadcast-day — but a START at 24:00 is plain midnight,
    so the rule must not be applied to both ends of an interval."""
    if is_end and minute == 24 * 60:
        return "23:59"
    m = minute % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def check_language_window(language: str, time_from: str, time_to: str,
                          market: object = None) -> Optional[str]:
    """Validate a paid line's daypart against its language's airing window(s).

    Returns None if the ordered [time_from, time_to] window fits inside one of the
    language's allowed intervals (or the language has no window on file and can't
    be validated). Returns a human-readable mismatch message otherwise.

    `market` selects the schedule to validate against: The Asian Channel (COD_USER
    10 / "DAL") programs completely different dayparts from Crossings TV, so
    validating a DAL order against the CTV windows flags every correct line — e.g.
    DAL Cantonese airs 17:00-18:00 where CTV Cantonese airs 19:00-20:00. Default
    (None) is CTV.

    Args & window strings are broadcast-day 24h "HH:MM".
    """
    envelope = envelope_for_market(market)
    key = _LANG_ALIASES.get(language, language)
    intervals = envelope.get(key)
    if not intervals:
        return None  # unmapped language (e.g. Japanese) — nothing to check against
    lo, hi = _win_bounds(time_from, time_to)
    for a, b in intervals:
        if lo >= a - _TOL_MIN and hi <= b + _TOL_MIN:
            return None
    allowed = ", ".join(f"{_fmt(a)}-{_fmt(b, is_end=True)}" for a, b in intervals)
    channel = "The Asian Channel" if is_dal(market) else "Crossings TV"
    return (f"{language} airs {allowed} on {channel}, but this line is ordered "
            f"{time_from}-{time_to}")
