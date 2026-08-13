"""Break optimization vs the day-of Executive Editor compaction (Jenna, 2026-08-12).

When a window's programming has not been inserted yet, EE's day-of refresh
removes the gap and pulls LATER shows' spots up into the window. The optimizer
then sees one phantom mega-break, flags separation/out-of-order violations that
are pure artifacts, and — the real hazard — Apply Fix would physically repack
spots that belong to other programming (the 2026-07-10 Korean News corruption).

`_bo_build_breaks` flags such breaks `programming_missing` on two positive
signals: the window has no live PGM row at all, or the break contains spots
whose intended break position (trafficPalinse.offset — survives both BO packing
and the EE scrunch, verified live 2026-08-12) lies at/after the window end.
Flagged breaks must come back inert: optimized == current, changed False, no
violations, and the absorbed spots itemized for display.
"""

import sys
from pathlib import Path

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.web.routes.orders import _BO_FPS, _bo_build_breaks, _bo_frames_to_time  # noqa: E402

PRIO = {"BOOKEND": 1, "BILLBOARD": 2, "COMPANION": 3, "PAYING": 4,
        "WORLDLINK": 5, "PI": 6, "PSA": 7, "STATION ID": 8}


def F(h, m=0, s=0):
    return round((h * 3600 + m * 60 + s) * _BO_FPS)


def spot(sid, ora, label="PAYING", intended=None, title=None, dur=900):
    newtype = {"PI": "PER", "PSA": "PSA", "STATION ID": "ID"}.get(label, "COM")
    return {
        "id": sid, "ora": ora, "time": _bo_frames_to_time(ora),
        "title": title or f"SPOT{sid}", "cod_progra": "", "newtype": newtype,
        "label": label, "priority": PRIO[label], "duration": dur,
        "contract": "C1", "is_fixed": False,
        "intended_ora": intended,
        "intended_time": _bo_frames_to_time(intended) if intended is not None else None,
    }


def fixed(sid, ora, newtype="PGM"):
    return {
        "id": sid, "ora": ora, "time": _bo_frames_to_time(ora),
        "title": "SHOW PART", "cod_progra": "SHOW", "newtype": newtype,
        "label": newtype, "priority": 0, "duration": 0, "contract": "",
        "is_fixed": True, "intended_ora": None, "intended_time": None,
    }


TO = F(8)  # window 7:00–8:00


# ── Normal windows stay exactly as before ────────────────────────────────────

def test_programmed_window_is_not_flagged_and_still_optimizes():
    rows = [
        fixed(1, F(7)),
        spot(10, F(7, 12), "STATION ID"),   # out of order → must still be caught
        spot(11, F(7, 12, 30), "PAYING"),
        fixed(2, F(7, 30)),
        spot(12, F(7, 57), "PAYING", intended=F(7, 57)),
    ]
    breaks, has_pgm = _bo_build_breaks(rows, TO)
    assert has_pgm is True
    assert [b["programming_missing"] for b in breaks] == [False, False]
    first = breaks[0]
    assert first["changed"] and first["ordering_violation"]
    assert [s["id"] for s in first["optimized"]] == [11, 10]


def test_optimized_times_chain_from_the_break_start():
    rows = [fixed(1, F(7)), spot(10, F(7, 10), dur=900), spot(11, F(7, 10, 30), dur=450)]
    breaks, _ = _bo_build_breaks(rows, TO)
    opt = breaks[0]["optimized"]
    assert opt[0]["new_ora"] == F(7, 10)
    assert opt[1]["new_ora"] == F(7, 10) + 900


def test_a_noop_is_transparent_inside_a_programmed_window():
    rows = [fixed(1, F(7)), spot(10, F(7, 10)), fixed(99, F(7, 11), "NOOP"), spot(11, F(7, 12))]
    breaks, _ = _bo_build_breaks(rows, TO)
    assert len(breaks) == 1
    assert [s["id"] for s in breaks[0]["current"]] == [10, 11]
    assert breaks[0]["programming_missing"] is False


def test_native_spots_without_offsets_do_not_flag():
    """PIs/station IDs have no trafficPalinse row (intended_ora None) — that is
    not evidence of absorption."""
    rows = [fixed(1, F(7)), spot(10, F(7, 10), "PI"), spot(11, F(7, 11), "STATION ID")]
    breaks, _ = _bo_build_breaks(rows, TO)
    assert breaks[0]["programming_missing"] is False


# ── Signal 1: the window has no programming at all ───────────────────────────

def test_window_without_pgm_rows_is_flagged_and_inert():
    rows = [spot(10, F(7, 5), intended=F(7, 5)),
            spot(11, F(7, 5, 30), "PI"),
            spot(12, F(7, 6), intended=F(7, 20))]
    breaks, has_pgm = _bo_build_breaks(rows, TO)
    assert has_pgm is False
    assert len(breaks) == 1
    brk = breaks[0]
    assert brk["programming_missing"] is True and brk["pm_reason"] == "window"
    # Inert: identity optimization, nothing for /apply or /bulk-apply to write
    assert brk["changed"] is False and brk["violation"] is False
    assert [s["id"] for s in brk["optimized"]] == [s["id"] for s in brk["current"]]
    assert all(o["new_ora"] == c["ora"] for o, c in zip(brk["optimized"], brk["current"]))


def test_a_noop_does_not_count_as_programming():
    """Etere drops a NOOP into an UNFILLED program hole — it is the marker of
    missing programming, never proof of its presence."""
    rows = [fixed(99, F(7), "NOOP"), spot(10, F(7, 5), intended=F(7, 5))]
    breaks, has_pgm = _bo_build_breaks(rows, TO)
    assert has_pgm is False
    assert breaks[0]["pm_reason"] == "window"


def test_flagged_break_reports_no_phantom_artifacts():
    """Duplicate PI products and an odd bookend count inside a scrunched
    mega-break are artifacts of the missing programming, not real problems."""
    rows = [spot(10, F(7, 5), "PI", title="PI-504-030: A"),
            spot(11, F(7, 5, 30), "PI", title="PI-504-060: B"),
            spot(12, F(7, 6), "BOOKEND")]
    breaks, _ = _bo_build_breaks(rows, TO)
    brk = breaks[0]
    assert brk["programming_missing"] is True
    assert brk["violation"] is False
    assert brk["bookend_warning"] is False


# ── Signal 2: absorbed spots from later programming ──────────────────────────

def test_break_with_foreign_offsets_is_flagged_absorbed_and_itemized():
    """Jenna's Break 3: the show's own spots plus later spots the EE scrunch
    pulled up because the next show's programming isn't placed."""
    rows = [
        fixed(1, F(7)),
        spot(10, F(7, 30), intended=F(7, 30)),                    # earlier, closed break
        fixed(2, F(7, 31)),
        spot(11, F(7, 57), "PI"),                                  # native (no offset)
        spot(12, F(7, 58), intended=F(7, 58)),                     # native
        spot(13, F(7, 59), intended=F(8, 10), title="REDFIN"),     # absorbed
        spot(14, F(7, 59, 30), intended=F(8, 30), title="4IMPRINT"),  # absorbed
    ]
    breaks, has_pgm = _bo_build_breaks(rows, TO)
    assert has_pgm is True
    assert len(breaks) == 2
    assert breaks[0]["programming_missing"] is False
    last = breaks[1]
    assert last["programming_missing"] is True and last["pm_reason"] == "absorbed"
    assert [f["id"] for f in last["foreign_spots"]] == [13, 14]
    assert last["foreign_spots"][0]["intended_time"] == _bo_frames_to_time(F(8, 10))
    assert last["changed"] is False
    assert [s["id"] for s in last["optimized"]] == [11, 12, 13, 14]


def test_next_show_placed_keeps_the_terminal_break_normal():
    """A closing PGM row in the 3-min buffer (the next show IS placed) means
    the terminal break is real and stays optimizable."""
    rows = [fixed(1, F(7)),
            spot(10, F(7, 57), intended=F(7, 57)),
            spot(11, F(7, 58), intended=F(7, 57)),
            fixed(2, F(8))]
    breaks, has_pgm = _bo_build_breaks(rows, TO)
    assert has_pgm is True
    assert breaks[-1]["programming_missing"] is False


def test_buffer_only_block_never_starts_a_break():
    rows = [fixed(1, F(7)), spot(10, F(7, 30)), fixed(2, F(8)), spot(11, F(8, 1))]
    breaks, _ = _bo_build_breaks(rows, TO)
    assert [s["id"] for b in breaks for s in b["current"]] == [10]
