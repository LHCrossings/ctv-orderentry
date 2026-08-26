"""
Tests for language-aware Admerasia colour matching (Chinese IOs).

A Chinese IO carries two ISCI legend blocks (Mandarin + Cantonese) that reuse the
IDENTICAL swatch colours, so a grid colour maps to two ISCIs and only the spot's own
weekday + airtime can pick the right one. These tests cover the two pure halves:

  • language_windows.classify_language  — the day-aware window lookup
  • admerasia_traffic_match             — colour + language -> ISCI, and its guardrails

The real-PDF legend read is covered by tests/integration/test_admerasia_legend_pdf.py
(needs real pdfplumber; conftest mocks it for the unit suite).
"""

import sys
from datetime import date
from pathlib import Path

import pytest

# Add browser_automation + repo root to path
_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.language_windows import (  # noqa: E402
    CTV_LANG_WINDOWS_BY_DAY,
    DAL_LANG_WINDOWS_BY_DAY,
    classify_language,
    classify_language_frames,
)
from browser_automation.parsers.admerasia_traffic_legend import normalize_isci  # noqa: E402
from browser_automation.parsers.admerasia_traffic_match import (  # noqa: E402
    _pick_isci,
    match_creatives,
)

_FPS = 29.97


def _hhmm(h, m=0):
    return h * 60 + m


# ── the day-aware window lookup ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "weekday,hh,mm,expect",
    [
        # Cantonese owns weekday 19:00-20:00; this is the "Variety Talk Show" slot.
        ("Monday", 19, 44, "Cantonese"),
        ("Friday", 19, 30, "Cantonese"),
        # ...and weekday 23:30-23:59, the tail Mandarin does NOT get on a weekday.
        ("Friday", 23, 40, "Cantonese"),
        # Mandarin owns weekday 20:00-23:30 — the "Mandarin Show" slot.
        ("Thursday", 22, 10, "Mandarin"),
        ("Monday", 23, 5, "Mandarin"),
        # THE CRITICAL EDGE: Cantonese has NO weekend window, so the same clock time that
        # is Cantonese on a weekday is Mandarin on Saturday. This is what makes the two
        # Saturday rows ("If You Are The One", "Bestie Time") resolvable.
        ("Saturday", 23, 40, "Mandarin"),
        ("Saturday", 22, 40, "Mandarin"),
        ("Saturday", 21, 20, "Mandarin"),
        # Other languages still resolve as before.
        ("Wednesday", 11, 0, "Vietnamese"),
        ("Tuesday", 9, 0, "Korean"),
        ("Thursday", 17, 0, "Filipino"),
    ],
)
def test_classify_ctv_unique(weekday, hh, mm, expect):
    assert classify_language(weekday, _hhmm(hh, mm)) == [expect]


def test_classify_returns_empty_outside_any_window():
    # 03:00 is inside no CTV window (all CTV programming starts at 06:00).
    assert classify_language("Monday", _hhmm(3)) == []


def test_classify_never_returns_aggregate_keys():
    """'Chinese'/'SouthAsian' are unions of their members — returning them would make
    every Mandarin spot look ambiguous."""
    for weekday in ("Monday", "Saturday"):
        for minute in range(6 * 60, 24 * 60, 10):
            assert not ({"Chinese", "SouthAsian"} & set(classify_language(weekday, minute)))


def test_classify_frames_matches_minutes():
    frames = round(_hhmm(22, 40) * 60 * _FPS)
    assert classify_language_frames("Saturday", frames) == ["Mandarin"]


def test_dal_market_uses_dal_table():
    """DAL (market 10) has entirely different windows — 17:00 is Cantonese there,
    Filipino on CTV."""
    assert classify_language("Monday", _hhmm(17, 30), market_id=10) == ["Cantonese"]
    assert classify_language("Monday", _hhmm(17, 30)) == ["Filipino"]


def test_ctv_mandarin_cantonese_never_overlap_on_the_same_weekday_minute():
    """The whole disambiguation rests on this: no (day, minute) is both."""
    for weekday in ("Monday", "Wednesday", "Friday", "Saturday", "Sunday"):
        for minute in range(6 * 60, 24 * 60):
            langs = set(classify_language(weekday, minute))
            assert not {"Mandarin", "Cantonese"} <= langs, (weekday, minute, langs)


def test_dal_mandarin_cantonese_never_overlap():
    """Same invariant on The Asian Channel, whose windows include a post-midnight tail
    (00:00-01:00 Mandarin / 01:00-02:00 Cantonese live at 24:00-26:00 broadcast)."""
    for weekday in ("Monday", "Friday", "Saturday", "Sunday"):
        for minute in range(6 * 60, 30 * 60):
            langs = set(classify_language(weekday, minute, market_id=10))
            assert not {"Mandarin", "Cantonese"} <= langs, (weekday, minute, langs)


def test_dal_post_midnight_resolves_on_the_shifted_clock():
    """DAL 00:00-01:00 is Mandarin and 01:00-02:00 Cantonese, stored at 24:00-26:00 on
    the SAME date — a spot there must classify, not fall through to []."""
    assert classify_language("Monday", 24 * 60 + 30, market_id=10) == ["Mandarin"]
    assert classify_language("Monday", 25 * 60 + 30, market_id=10) == ["Cantonese"]


def test_window_end_2359_includes_2359():
    """'23:59' is shorthand for end-of-broadcast-day, so 23:59 itself is inside."""
    assert classify_language("Saturday", 23 * 60 + 59) == ["Mandarin"]
    assert classify_language("Friday", 23 * 60 + 59) == ["Cantonese"]


def test_window_boundary_belongs_to_the_later_window():
    """20:00 exactly is Mandarin (start of 20:00-23:30), not Cantonese (end of 19:00-20:00)."""
    assert classify_language("Monday", 20 * 60) == ["Mandarin"]
    assert classify_language("Monday", 20 * 60 - 1) == ["Cantonese"]


def test_route_tables_are_the_shared_objects():
    """orders.py must alias these, not re-declare them (the mirror-drift lesson)."""
    sys.path.insert(0, str(_root / "src"))
    from web.routes import orders

    assert orders._CTV_LANG_WINDOWS is CTV_LANG_WINDOWS_BY_DAY
    assert orders._DAL_LANG_WINDOWS is DAL_LANG_WINDOWS_BY_DAY


# ── ISCI repair ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,fixed",
    [
        ("MCIMO46526VH", "MCIM046526VH"),  # Beverages Launch IO prints letter O for zero
        ("MCICO02326VH", "MCIC002326VH"),
        ("MCIM106526VH", "MCIM106526VH"),  # already clean — untouched
        ("MCIC010326VH", "MCIC010326VH"),
    ],
)
def test_normalize_isci(raw, fixed):
    assert normalize_isci(raw) == fixed


def test_normalize_isci_keeps_language_letter():
    """The letter at index 3 is the language and must never be digit-ised."""
    assert normalize_isci("MCIO046526VH").startswith("MCIO")


# ── colour + language -> ISCI ──────────────────────────────────────────────────

_SHARED = {"Mandarin": "MCIM014326VH", "Cantonese": "MCIC010326VH"}


def test_pick_isci_single_entry_is_language_agnostic():
    """Single-language IOs (and the vision fallback, which reports no language) must
    keep working with no language information at all."""
    assert _pick_isci({None: "MCIV107525VH"}, [])[0] == "MCIV107525VH"
    assert _pick_isci({"Vietnamese": "MCIV107525VH"}, [])[0] == "MCIV107525VH"


def test_pick_isci_uses_spot_language_on_a_shared_colour():
    assert _pick_isci(_SHARED, ["Cantonese"])[:2] == ("MCIC010326VH", "Cantonese")
    assert _pick_isci(_SHARED, ["Mandarin"])[:2] == ("MCIM014326VH", "Mandarin")


def test_pick_isci_refuses_when_airtime_has_no_window():
    isci, lang, why = _pick_isci(_SHARED, [])
    assert isci is None and lang is None
    assert "no programmed language window" in why


def test_pick_isci_refuses_when_language_absent_from_colour():
    isci, _, why = _pick_isci(_SHARED, ["Vietnamese"])
    assert isci is None
    assert "only has" in why


def test_pick_isci_refuses_ambiguous_airtime():
    isci, _, why = _pick_isci(_SHARED, ["Mandarin", "Cantonese"])
    assert isci is None
    assert "ambiguous" in why


def test_pick_isci_empty_map():
    assert _pick_isci({}, ["Mandarin"])[0] is None


# ── end-to-end through match_creatives ─────────────────────────────────────────


class _Cell:
    def __init__(self, row, col, day, count, cluster):
        self.row, self.col, self.day = row, col, day
        self.count, self.cluster = count, cluster


class _Grid:
    def __init__(self, cells, days):
        self.cells, self.calendar_days = cells, days
        self.palette = []


def _spot(sid, d, hh, mm, dur_sec, langs):
    return {
        "id": sid,
        "date": d,
        "ora": round((hh * 60 + mm) * 60 * _FPS),
        "duration": round(dur_sec * _FPS),
        "languages": langs,
    }


def test_match_creatives_splits_one_colour_across_two_languages():
    """The core bug: a single grid colour, two rows of the same duration on different
    dates — one Cantonese daypart, one Mandarin — must yield DIFFERENT ISCIs."""
    flight = date(2026, 8, 17)
    # row 0 = Cantonese programme (Wed 8/19), row 1 = Mandarin programme (Mon 8/24).
    grid = _Grid([_Cell(0, 2, 19, 1, 0), _Cell(1, 7, 24, 1, 0)], list(range(17, 32)))
    cluster_lang_isci = {0: dict(_SHARED)}
    filmati = {
        "MCIM014326VH": {"filmati_id": 144722, "duration": round(30 * _FPS)},
        "MCIC010326VH": {"filmati_id": 144721, "duration": round(30 * _FPS)},
    }
    spots = [
        _spot(1, date(2026, 8, 19), 19, 44, 30, ["Cantonese"]),
        _spot(2, date(2026, 8, 24), 23, 5, 30, ["Mandarin"]),
    ]
    res = match_creatives(grid, {0: 30, 1: 30}, {}, cluster_lang_isci, flight, spots, filmati)

    by_id = {a.tp_id: a for a in res.assignments}
    assert by_id[1].isci == "MCIC010326VH" and by_id[1].filmati_id == 144721
    assert by_id[2].isci == "MCIM014326VH" and by_id[2].filmati_id == 144722
    assert all(a.ok for a in res.assignments)


def test_match_creatives_reports_missing_filmati_with_the_language_resolved():
    """Joy Ride is not ingested: the spot must fail with the specific ISCI named, not
    with a vague row-match error — that requires the row duration to come from the
    legend rather than FILMATI."""
    flight = date(2026, 8, 17)
    grid = _Grid([_Cell(0, 3, 20, 1, 0)], list(range(17, 32)))
    res = match_creatives(
        grid,
        {0: 15},
        {},
        {0: {"Mandarin": "MCIM107526VH", "Cantonese": "MCIC088526VH"}},
        flight,
        [_spot(9, date(2026, 8, 20), 22, 10, 15, ["Mandarin"])],
        {},  # nothing ingested
    )
    a = res.assignments[0]
    assert not a.ok
    assert a.isci == "MCIM107526VH"
    assert a.language == "Mandarin"
    assert "no FILMATI" in a.reason


def test_match_creatives_warns_when_a_row_mixes_languages():
    """One grid row is one programme = one language. Two languages on a row means a
    spot is scheduled outside its programme's daypart."""
    flight = date(2026, 8, 17)
    grid = _Grid([_Cell(0, 2, 19, 1, 0), _Cell(0, 5, 22, 1, 0)], list(range(17, 32)))
    filmati = {
        "MCIM014326VH": {"filmati_id": 144722, "duration": round(30 * _FPS)},
        "MCIC010326VH": {"filmati_id": 144721, "duration": round(30 * _FPS)},
    }
    res = match_creatives(
        grid,
        {0: 30},
        {},
        {0: dict(_SHARED)},
        flight,
        [
            _spot(1, date(2026, 8, 19), 19, 44, 30, ["Cantonese"]),
            _spot(2, date(2026, 8, 22), 22, 40, 30, ["Mandarin"]),
        ],
        filmati,
    )
    assert any("multiple languages" in w for w in res.warnings)


def test_match_creatives_single_language_path_unchanged():
    """Regression guard for the 6 existing VT/FT orders: no language anywhere, one ISCI
    per colour, still assigns."""
    flight = date(2026, 1, 26)
    grid = _Grid([_Cell(0, 0, 26, 2, 0)], [26])
    filmati = {"MCIV106525VH": {"filmati_id": 500, "duration": round(15 * _FPS)}}
    res = match_creatives(
        grid,
        {0: 15},
        {},
        {0: {None: "MCIV106525VH"}},
        flight,
        [_spot(1, date(2026, 1, 26), 11, 0, 15, []), _spot(2, date(2026, 1, 26), 12, 0, 15, [])],
        filmati,
    )
    assert [a.filmati_id for a in res.assignments] == [500, 500]
    assert all(a.ok for a in res.assignments)
    assert not [w for w in res.warnings if "language" in w]
