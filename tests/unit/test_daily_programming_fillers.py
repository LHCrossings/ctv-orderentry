"""Daily Programming "Add filler" — pool parameterization + anchor hardening.

Covers the 2026-08 setup-flow filler feature: language→pool resolution,
pattern-parameterized pool SQL, duration-targeted draws from a non-K pool, and
the _is_placed filler exclusion (a surplus filler stacked near a window's end
must not mark the NEXT window "already placed").
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.business_logic.services import filler_rotation as fr  # noqa: E402
from src.business_logic.services.daily_programming_run import (  # noqa: E402
    FPS,
    _group_anchors,
    _is_placed,
)


class StubCursor:
    """Captures execute() calls; serves canned rows to fetchall()."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows


# ── pool_for_language ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("language", "pool"),
    [
        ("korean", "korean"),
        ("Korean", "korean"),
        ("chinese", "chinese"),
        ("Mandarin", "chinese"),
        ("CANTONESE", "chinese"),
        ("filipino", "filipino"),
        ("Tagalog", "filipino"),
        ("punjabi", "filipino"),
        ("  Filipino  ", "filipino"),
    ],
)
def test_pool_for_language_mapped(language, pool):
    assert fr.pool_for_language(language) == pool


@pytest.mark.parametrize("language", ["vietnamese", "hmong", "japanese", "", None])
def test_pool_for_language_unmapped_is_none(language):
    assert fr.pool_for_language(language) is None


def test_pool_patterns_cover_every_language_target():
    assert set(fr._LANGUAGE_POOL.values()) <= set(fr.POOL_PATTERNS)


# ── active_pool ──────────────────────────────────────────────────────────────


def test_active_pool_default_is_k_pool():
    cur = StubCursor([(1, "K-FILLER25-001 ", 500)])
    pool = fr.active_pool(cur)
    sql, params = cur.calls[0]
    assert params == ("K-FILLER[0-9][0-9]-%",)
    assert sql.count("COD_PROGRA LIKE %s") == 1
    assert pool == [{"fid": 1, "code": "K-FILLER25-001", "durata": 500}]


def test_active_pool_multi_pattern_ors_one_like_per_pattern():
    cur = StubCursor()
    fr.active_pool(cur, fr.POOL_PATTERNS["chinese"])
    sql, params = cur.calls[0]
    assert params == ("CHINESEFILLER%", "UNIAM%")
    assert sql.count("COD_PROGRA LIKE %s") == 2
    assert "COD_PROGRA LIKE %s OR COD_PROGRA LIKE %s" in sql


def test_active_pool_keeps_usability_filters():
    cur = StubCursor()
    fr.active_pool(cur, fr.POOL_PATTERNS["filipino"])
    sql, _ = cur.calls[0]
    for must in ("NEWTYPE = 'PGM'", "DO NOT USE", "HIATUS", "DATA_SCAD"):
        assert must in sql


# ── draw_until with a parameterized pool ─────────────────────────────────────


def _pool_rows(*durs):
    return [(i + 1, f"UNIAE{1600 + i}", d) for i, d in enumerate(durs)]


def test_draw_until_forwards_patterns():
    cur = StubCursor(_pool_rows(1000))
    fr.draw_until(cur, 500, patterns=fr.POOL_PATTERNS["filipino"])
    _, params = cur.calls[0]
    assert params == ("UNIAE%",)


def test_draw_until_reaches_target_with_bounded_overshoot():
    # Finishers exist for every gap → total lands in [target, target + cap].
    cur = StubCursor(_pool_rows(3000, 4000, 5000, 6000, 7000))
    picks = fr.draw_until(cur, 9000)
    total = sum(p["durata"] for p in picks)
    assert 9000 <= total <= 9000 + fr._OVERSHOOT_CAP_FRAMES


def test_draw_until_nonpositive_target_returns_empty():
    assert fr.draw_until(StubCursor(_pool_rows(1000)), 0) == []
    assert fr.draw_until(StubCursor(_pool_rows(1000)), -5) == []


def test_draw_until_respects_exclude():
    cur = StubCursor(_pool_rows(4000, 5000))
    picks = fr.draw_until(cur, 3000, exclude_codes=["UNIAE1600"])
    assert [p["code"] for p in picks] == ["UNIAE1601"]


# ── anchor grouping / _is_placed hardening ───────────────────────────────────


def _fr(hhmm):
    h, m = hhmm.split(":")
    return int((int(h) * 3600 + int(m) * 60) * FPS)


class PlacedCursor(StubCursor):
    """Serves TPALINSE (ORA, COD_PROGRA) rows to _is_placed's SELECT."""


def test_filler_near_window_end_does_not_place_next_window():
    lo, hi = _fr("12:00"), _fr("13:00")
    # 11:00 show fully placed; its surplus filler drifted to 11:59 — inside the
    # 12:00 window's [lo - TOL, hi - TOL) anchor band. Before the filler
    # exclusion this marked 12:00 "already placed" and skipped its show.
    rows = [
        (_fr("11:00"), "THEPOINTA"),
        (_fr("11:20"), "THEPOINTB"),
        (_fr("11:59"), "UNIAE1642"),
    ]
    assert _is_placed(PlacedCursor(rows), 1, "2026-08-24", lo, hi) is False


def test_show_pieces_still_anchor_their_own_window():
    lo, hi = _fr("12:00"), _fr("13:00")
    rows = [
        (_fr("12:00"), "ANGPANDDAYA"),
        (_fr("12:30"), "ANGPANDDAYB"),
        (_fr("12:58"), "CHINESEFILLER25-004"),
    ]
    assert _is_placed(PlacedCursor(rows), 1, "2026-08-24", lo, hi) is True


def test_fillers_do_not_split_a_shows_piece_group():
    # A letterless filler between piece groups is its own base — the show's
    # ascending A→B→C chain stays ONE group (one anchor at piece A).
    rows = [
        (_fr("12:00"), "SHOWXA"),
        (_fr("12:22"), "UNIAM1660"),
        (_fr("12:25"), "SHOWXB"),
        (_fr("12:50"), "SHOWXC"),
    ]
    anchors = _group_anchors(rows)
    assert anchors == [_fr("12:00"), _fr("12:22")]  # show anchor + filler's own


def test_letter_reset_still_splits_repeat_airings():
    rows = [
        (_fr("10:00"), "VD-SCENTOFGRASS15-0721A"),
        (_fr("10:30"), "VD-SCENTOFGRASS15-0721B"),
        (_fr("12:00"), "VD-SCENTOFGRASS15-0721A"),  # repeat airing restarts at A
        (_fr("12:30"), "VD-SCENTOFGRASS15-0721B"),
    ]
    assert _group_anchors(rows) == [_fr("10:00"), _fr("12:00")]
