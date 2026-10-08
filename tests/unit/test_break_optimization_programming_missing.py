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

PRIO = {
    "BOOKEND": 1,
    "BILLBOARD": 2,
    "COMPANION": 3,
    "PAYING": 4,
    "WORLDLINK": 5,
    "PI": 6,
    "PSA": 7,
    "STATION ID": 8,
}


def F(h, m=0, s=0):
    return round((h * 3600 + m * 60 + s) * _BO_FPS)


def spot(sid, ora, label="PAYING", intended=None, title=None, dur=900):
    newtype = {"PI": "PER", "PSA": "PSA", "STATION ID": "ID"}.get(label, "COM")
    return {
        "id": sid,
        "ora": ora,
        "time": _bo_frames_to_time(ora),
        "title": title or f"SPOT{sid}",
        "cod_progra": "",
        "newtype": newtype,
        "label": label,
        "priority": PRIO[label],
        "duration": dur,
        "contract": "C1",
        "is_fixed": False,
        "intended_ora": intended,
        "intended_time": _bo_frames_to_time(intended) if intended is not None else None,
    }


def fixed(sid, ora, newtype="PGM"):
    return {
        "id": sid,
        "ora": ora,
        "time": _bo_frames_to_time(ora),
        "title": "SHOW PART",
        "cod_progra": "SHOW",
        "newtype": newtype,
        "label": newtype,
        "priority": 0,
        "duration": 0,
        "contract": "",
        "is_fixed": True,
        "intended_ora": None,
        "intended_time": None,
    }


TO = F(8)  # window 7:00–8:00


# ── Normal windows stay exactly as before ────────────────────────────────────


def test_programmed_window_is_not_flagged_and_still_optimizes():
    rows = [
        fixed(1, F(7)),
        spot(10, F(7, 12), "STATION ID"),  # out of order → must still be caught
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
    rows = [
        spot(10, F(7, 5), intended=F(7, 5)),
        spot(11, F(7, 5, 30), "PI"),
        spot(12, F(7, 6), intended=F(7, 20)),
    ]
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
    rows = [
        spot(10, F(7, 5), "PI", title="PI-504-030: A"),
        spot(11, F(7, 5, 30), "PI", title="PI-504-060: B"),
        spot(12, F(7, 6), "BOOKEND"),
    ]
    breaks, _ = _bo_build_breaks(rows, TO)
    brk = breaks[0]
    assert brk["programming_missing"] is True
    assert brk["violation"] is False
    assert brk["bookend_warning"] is False


# ── Signal 2: absorbed spots from later programming ──────────────────────────


def test_break_with_foreign_offsets_is_flagged_absorbed_and_itemized():
    """Jenna's Break 3: the show's own spots plus later spots the EE scrunch
    pulled up because the next show's programming isn't placed. Since 10/8
    (show-by-show Finish, Lee) the break is SPLIT: our spots are ordered, the
    absorbed tail is itemized and never moves."""
    rows = [
        fixed(1, F(7)),
        spot(10, F(7, 30), intended=F(7, 30)),  # earlier, closed break
        fixed(2, F(7, 31)),
        spot(11, F(7, 57), "PI"),  # native (no offset)
        spot(12, F(7, 58), intended=F(7, 58)),  # native
        spot(13, F(7, 59), intended=F(8, 10), title="REDFIN"),  # absorbed
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
    assert last["own_count"] == 2
    # our PAYING spot goes ahead of our PI; the absorbed tail keeps order AND times
    assert [s["id"] for s in last["optimized"]] == [12, 11, 13, 14]
    assert last["changed"] is True and last["ordering_violation"] is True
    tail = last["optimized"][2:]
    assert all(s["foreign"] for s in tail)
    assert [s["new_ora"] for s in tail] == [F(7, 59), F(7, 59, 30)]
    assert not any(s.get("foreign") for s in last["optimized"][:2])


# ── Show-by-show Finish: order our spots, leave the next show's tail (Lee 10/8) ──


def _maija_break_3():
    """SFO 10/7 18:00 Magandang Buhay, Break 3 as Ashe saw it (Maija's screenshot):
    two PIs ahead of this show's WorldLink :15, then PSA + ID, then three spots
    booked for the 18:30 show (18:38 break) that EE pulled up behind."""
    return [
        fixed(1, F(18)),
        spot(1, F(18, 27, 51), "PI", title="PI-504-030: Legal Help Center", dur=899),
        spot(2, F(18, 28, 21), "PI", title="PI-497-060: eZwell", dur=1798),
        spot(3, F(18, 29, 20), "WORLDLINK", intended=F(18, 27), title="PHOLICIOUS15E05", dur=450),
        spot(4, F(18, 29, 36), "PSA", title="PSA-105-015", dur=450),
        spot(5, F(18, 29, 51), "STATION ID", title="ID - NEW - SFO ONLY", dur=750),
        spot(6, F(18, 30, 16), "WORLDLINK", intended=F(18, 38), title="REDFIN15E06", dur=450),
        spot(7, F(18, 30, 31), "PAYING", intended=F(18, 38), title="SRC30E58", dur=899),
        spot(8, F(18, 31, 1), "WORLDLINK", intended=F(18, 38), title="AHA2ME18", dur=1798),
    ]


def test_maija_break_3_orders_this_shows_spots_and_leaves_the_tail():
    breaks, _ = _bo_build_breaks(_maija_break_3(), F(18, 30))
    brk = breaks[-1]
    assert brk["pm_reason"] == "absorbed" and brk["own_count"] == 5
    assert [s["id"] for s in brk["optimized"]] == [3, 1, 2, 4, 5, 6, 7, 8]
    assert brk["changed"] is True  # Ashe had to do this by hand before
    # our group is re-timed from the break start and keeps its total length,
    # so the first absorbed spot still starts exactly where it did
    own = brk["optimized"][:5]
    assert own[0]["new_ora"] == F(18, 27, 51)
    assert abs(own[-1]["new_ora"] + own[-1]["duration"] - F(18, 30, 16)) <= 1  # frame rounding
    assert [(s["new_ora"], s.get("foreign")) for s in brk["optimized"][5:]] == [
        (F(18, 30, 16), True),
        (F(18, 30, 31), True),
        (F(18, 31, 1), True),
    ]
    assert [f["id"] for f in brk["foreign_spots"]] == [6, 7, 8]


def test_absorbed_spot_interleaved_among_ours_keeps_the_whole_break_frozen():
    """Ordering ours would have to move the foreign spot — so nothing moves (old rule)."""
    rows = [
        fixed(1, F(7)),
        spot(11, F(7, 57), "PI"),
        spot(13, F(7, 57, 30), intended=F(8, 10), title="REDFIN"),  # foreign, in the middle
        spot(12, F(7, 58), intended=F(7, 58)),
    ]
    breaks, _ = _bo_build_breaks(rows, TO)
    brk = breaks[-1]
    assert brk["programming_missing"] is True and brk["own_count"] == 0
    assert brk["changed"] is False
    assert [s["id"] for s in brk["optimized"]] == [11, 13, 12]


def test_split_break_already_in_order_is_unchanged():
    rows = [
        fixed(1, F(7)),
        spot(12, F(7, 57), intended=F(7, 57)),
        spot(11, F(7, 57, 30), "PI"),
        spot(13, F(7, 58), intended=F(8, 10), title="REDFIN"),
    ]
    breaks, _ = _bo_build_breaks(rows, TO)
    brk = breaks[-1]
    assert brk["own_count"] == 2 and brk["changed"] is False and brk["violation"] is False


def test_apply_writes_only_our_spots_of_a_split_break(monkeypatch):
    """bo_apply_market must never UPDATE a foreign tail row, even on a changed break."""
    import src.web.routes.orders as orders

    breaks, _ = _bo_build_breaks(_maija_break_3(), F(18, 30))
    monkeypatch.setattr(orders, "_bo_process_market", lambda *a, **k: (breaks, True))
    monkeypatch.setattr(orders, "_bo_apply_pi_replacement", lambda *a, **k: None)
    written = []

    class Cur:
        def __init__(self):
            self.rows = []

        def execute(self, sql, params=()):
            if sql.startswith("SELECT ID_TPALINSE, XORDER"):
                self.rows = [{"ID_TPALINSE": i, "XORDER": 1000 + i} for i in params]
            elif sql.startswith("UPDATE TPALINSE"):
                written.append(params[-1])

        def fetchall(self):
            return self.rows

    class Conn:
        def cursor(self, as_dict=False):
            return Cur()

    out = orders.bo_apply_market(Conn(), 4, "2026-10-07", F(18), F(18, 30))
    assert out["breaks_changed"] == 1 and out["spots_updated"] == 5 and out["breaks_waiting"] == 1
    assert sorted(written) == [1, 2, 3, 4, 5]  # 6, 7, 8 untouched


def test_next_show_placed_keeps_the_terminal_break_normal():
    """A closing PGM row in the 3-min buffer (the next show IS placed) means
    the terminal break is real and stays optimizable."""
    rows = [
        fixed(1, F(7)),
        spot(10, F(7, 57), intended=F(7, 57)),
        spot(11, F(7, 58), intended=F(7, 57)),
        fixed(2, F(8)),
    ]
    breaks, has_pgm = _bo_build_breaks(rows, TO)
    assert has_pgm is True
    assert breaks[-1]["programming_missing"] is False


def test_buffer_only_block_never_starts_a_break():
    rows = [fixed(1, F(7)), spot(10, F(7, 30)), fixed(2, F(8)), spot(11, F(8, 1))]
    breaks, _ = _bo_build_breaks(rows, TO)
    assert [s["id"] for b in breaks for s in b["current"]] == [10]


def test_separation_ignores_pairs_with_the_absorbed_tail():
    """An own spot and an absorbed spot of the same customer sit minutes apart only
    because EE pulled the tail up; the real gap returns when the next show is placed."""
    from src.web.routes.orders import _bo_check_separation

    breaks, _ = _bo_build_breaks(_maija_break_3(), F(18, 30))
    brk = breaks[-1]

    def sep(sid, ora, cust, cust_sep=15):
        return {
            "ID_TPALINSE": sid,
            "ORA": ora,
            "TITLE": f"S{sid}",
            "COMMITTENTE": cust,
            "contract_id": 100 + cust,
            "cust_sep": round(cust_sep * 60 * _BO_FPS),
            "order_sep": 0,
            "capofila": 0,
            "finefila": 0,
            "line_time_from": None,
            "line_time_to": None,
        }

    # PhoLicious (own, id 3) and REDFIN (absorbed, id 6) share customer 7, 56 s apart;
    # SRC (absorbed, id 7) and AHA (absorbed, id 8) share customer 9, 30 s apart.
    ctx = [
        sep(3, F(18, 29, 20), 7),
        sep(6, F(18, 30, 16), 7),
        sep(7, F(18, 30, 31), 9),
        sep(8, F(18, 31, 1), 9),
        sep(99, F(18, 37), 7),  # unplaced 18:30 show, past the window end: artifact position
    ]
    _bo_check_separation(breaks, ctx, F(18, 30))
    assert brk["sep_violations"] == [] and brk["violation"] is True  # ordering only
    # the same two own spots 56 s apart with no tail involved IS a violation
    rows = [
        fixed(1, F(18)),
        spot(3, F(18, 29, 20), "WORLDLINK", intended=F(18, 27)),
        spot(6, F(18, 30, 16), "WORLDLINK", intended=F(18, 27)),
        fixed(2, F(18, 31)),
    ]
    b2, _ = _bo_build_breaks(rows, F(18, 31))
    _bo_check_separation(b2, [sep(3, F(18, 29, 20), 7), sep(6, F(18, 30, 16), 7)])
    assert len(b2[-1]["sep_violations"]) == 1
