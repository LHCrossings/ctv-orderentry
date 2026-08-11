"""Break-optimization spot ordering — the station ID closes the break.

Reported by Maija (2026-08-10): "whenever we optimize a break with a bookend, the
order gets changed and the Station ID is moved ahead of the bookend spot."

A bookend pair brackets the break's COMMERCIALS; the legal station ID always
airs last, after the closing bookend (Lee). `_bo_classify` ranks STATION ID 8 —
last among the ordinary spot types — but the closing bookend is forced to 999 so
it lands after every commercial, and 8 < 999 put the ID in front of it. Etere had
the break right; the optimizer flagged it "Out of Order" and would have moved the
ID on Apply Fix.

The order is computed server-side in `_bo_optimize` and the page posts
`brk.optimized` back verbatim, so this one function drives both what Maija sees
and what gets written to TPALINSE.XORDER.
"""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.web.routes.orders import _bo_classify, _bo_optimize  # noqa: E402

# The per-type ranks _bo_classify hands out, keyed by the label it returns.
PRIO = {"BOOKEND": 1, "BILLBOARD": 2, "COMPANION": 3, "PAYING": 4,
        "WORLDLINK": 5, "PI": 6, "PSA": 7, "STATION ID": 8}


def mk(*labels, contract="C1"):
    return [{"label": lb, "priority": PRIO[lb], "title": lb, "contract": contract}
            for lb in labels]


def order(*labels, **kw):
    return [s["label"] for s in _bo_optimize(mk(*labels, **kw))]


# ── The report ───────────────────────────────────────────────────────────────

def test_station_id_stays_after_the_closing_bookend():
    """Maija's Break 3, exactly as Etere had it — the optimizer must leave it be."""
    given = ("BOOKEND", "WORLDLINK", "PI", "BOOKEND", "STATION ID")
    assert order(*given) == list(given)


def test_a_scrambled_break_sorts_to_bookend_commercials_bookend_id():
    assert order("STATION ID", "PI", "BOOKEND", "WORLDLINK", "BOOKEND") == [
        "BOOKEND", "WORLDLINK", "PI", "BOOKEND", "STATION ID"]


def test_the_station_id_is_last_in_every_arrangement():
    """The invariant, independent of what else is in the break."""
    for spots in (
        ("STATION ID",),
        ("STATION ID", "PAYING"),
        ("BOOKEND", "BOOKEND", "STATION ID"),
        ("STATION ID", "BOOKEND", "PI", "PSA", "BOOKEND"),
        ("BILLBOARD", "COMPANION", "STATION ID", "BOOKEND", "WORLDLINK", "BOOKEND"),
    ):
        assert order(*spots)[-1] == "STATION ID", spots


# ── Everything else must be unchanged ────────────────────────────────────────

def test_bookend_pair_still_brackets_the_break_without_an_id():
    assert order("WORLDLINK", "BOOKEND", "PI", "BOOKEND") == [
        "BOOKEND", "WORLDLINK", "PI", "BOOKEND"]


def test_station_id_without_a_bookend_is_still_last():
    assert order("WORLDLINK", "PAYING", "PSA", "STATION ID", "PI") == [
        "PAYING", "WORLDLINK", "PI", "PSA", "STATION ID"]


def test_a_break_with_neither_bookend_nor_id_is_untouched():
    """Maija's Break 2 — reported '✓ OK', and must stay that way."""
    given = ("WORLDLINK", "WORLDLINK", "WORLDLINK", "WORLDLINK", "PSA")
    assert order(*given) == list(given)


def test_billboard_keeps_its_companion_adjacent():
    """A billboard and the :30 behind it move as one unit; the ID still closes."""
    got = order("BOOKEND", "PI", "BILLBOARD", "COMPANION", "PAYING", "BOOKEND",
                "STATION ID")
    assert got == ["BOOKEND", "BILLBOARD", "COMPANION", "PAYING", "PI", "BOOKEND",
                   "STATION ID"]
    assert got.index("COMPANION") == got.index("BILLBOARD") + 1


def test_optimize_never_adds_or_drops_a_spot():
    from collections import Counter
    for spots in (
        ("BOOKEND", "WORLDLINK", "PI", "BOOKEND", "STATION ID"),
        ("STATION ID", "BILLBOARD", "COMPANION", "PSA", "PAYING"),
        ("PI", "PI", "PI", "STATION ID"),
    ):
        assert Counter(order(*spots)) == Counter(spots), spots


# ── The classifier the ranks come from ───────────────────────────────────────

@pytest.mark.parametrize("newtype,capo,fine,is_wl,expected", [
    ("COM", 1, 1, False, "BOOKEND"),
    ("COM", 1, 0, False, "BILLBOARD"),
    ("COM", 0, 0, False, "PAYING"),
    ("COM", 0, 0, True,  "WORLDLINK"),
    ("PER", 0, 0, False, "PI"),
    ("PSA", 0, 0, False, "PSA"),
    ("ID",  0, 0, False, "STATION ID"),
])
def test_classify_labels(newtype, capo, fine, is_wl, expected):
    _prio, label = _bo_classify(newtype, capo, fine, is_wl, "")
    assert label == expected


def test_station_id_outranks_the_bottom_bookend():
    """Guards the relationship directly: whatever the numbers become, the ID must
    sort after the closing bookend."""
    from src.web.routes.orders import _BO_BOTTOM_BOOKEND, _BO_STATION_ID
    assert _BO_STATION_ID > _BO_BOTTOM_BOOKEND
