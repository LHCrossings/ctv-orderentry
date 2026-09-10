"""Fill & Finish window membership by BOOKED BLOCK (Maija 9/10/2026).

A playlist row's block is `trafficPalinse.offset` (Executive Editor's block column). It
decides which window owns the row — never the row's clock position or the presence of an
F anchor at the top of the hour:
  * DAL 17:30 HK Perspectives: DART :15 booked in the 17:30 block sat at 18:00:14 with no
    program placed at 18:00 and was cut as "the next hour's" → Finish seated the ID ahead
    of it and the spot aired after the ID.
  * DAL 20:00 The Founders: the PIs booked in the EMPTY 21:30 block sat right behind the
    show's last piece, were read as this window's fill, and were stripped → the 21:30 block
    vanished from EE.
Also: "programming placed" = every catalog piece letter on the playlist (To the Point runs
46:00 in a 60:00 slot and was "not placed" on the 5-minute rule), and an overrun with a
language filler swaps the filler for the longest same-pool filler that fits.
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import business_logic.services.finish_plan as fp  # noqa: E402
import business_logic.services.finish_service as fs  # noqa: E402
from business_logic.services.finish_plan import (  # noqa: E402
    FPS,
    Ev,
    filler_swaps,
    missing_pieces,
    window_from_day,
)


def _r(i, ora_s, dur_s, newtype, ev="T", desc="", line=None, blk_s=None, code=None, xorder=None):
    # load_day tuple: ID, ORA, DURATION, NEWTYPE, EVENT_TYPE, ID_FILMATI, DESCRIZIO, line,
    # XORDER, trafficPalinse.offset (booked block, frames), COD_PROGRA
    return (
        i,
        int(ora_s * FPS),
        int(dur_s * FPS),
        newtype,
        ev,
        1000 + i,
        desc,
        line,
        xorder or i * 10,
        None if blk_s is None else int(blk_s * FPS),
        code if code is not None else desc,
    )


H1730, H1800 = 17.5 * 3600.0, 18 * 3600.0
H2000, H2130, H2200 = 20 * 3600.0, 21.5 * 3600.0, 22 * 3600.0


def _dal_1730():
    """Maija's screenshot: HK Perspectives 17:30-18:00, nothing placed at 18:00."""
    return [
        _r(1, H1730, 386, "PGM", "F", "PH-HKPERSPECT144-091026A", line=0, blk_s=H1730),
        _r(2, H1730 + 386, 15, "COM", "T", "DART15M05", line=83610, blk_s=H1730 + 360),
        _r(3, H1730 + 401, 476, "PGM", "T", "PH-HKPERSPECT144-091026B", line=0, blk_s=H1730 + 420),
        _r(4, H1730 + 877, 718, "PGM", "T", "PH-HKPERSPECT144-091026C", line=0, blk_s=H1730 + 1020),
        _r(5, H1730 + 1595, 25, "ID", "T", "ID - TACDAL - GENERIC"),  # Finish's own, unbooked
        _r(
            6, H1800 + 14, 15, "COM", "T", "DART15M04", line=83610, blk_s=H1730 + 1680
        ),  # 17:58 break
        _r(
            7, H1800 + 29, 30, "PER", "T", "PI-505-030", line=-1, blk_s=H1730 + 1680
        ),  # hand PI, 17:30 block
        _r(8, H1800 + 59, 15, "COM", "T", "DART15M05 (18:00)", line=83612, blk_s=H1800 + 1200),
        _r(9, H1800 + 74, 60, "PER", "T", "PI-506-060", line=-1, blk_s=H1800 + 1200),
        _r(
            10, H1800 + 134, 25, "ID", "T", "ID - hand, 18:00 block"
        ),  # unbooked, behind the 18:00 rows
    ]


def test_spot_booked_in_this_block_past_the_top_is_ours_without_an_anchor():
    evs = window_from_day(_dal_1730(), H1730, H1800)
    assert [e.desc for e in evs] == [
        "PH-HKPERSPECT144-091026A",
        "DART15M05",
        "PH-HKPERSPECT144-091026B",
        "PH-HKPERSPECT144-091026C",
        "ID - TACDAL - GENERIC",
        "DART15M04",
        "PI-505-030",
    ]


def test_next_blocks_booked_rows_and_the_hand_fill_behind_them_are_not_ours():
    evs = window_from_day(_dal_1730(), H1730, H1800)
    descs = {e.desc for e in evs}
    assert "DART15M05 (18:00)" not in descs and "PI-506-060" not in descs
    assert "ID - hand, 18:00 block" not in descs


def test_next_block_pis_behind_the_last_piece_are_not_this_windows_fill():
    """DAL 20:00-21:30 The Founders; the 21:30 block has no program, only hand PIs."""
    rows = [
        _r(1, H2000, 3000, "PGM", "F", "THEFOUNDERS11-091026A", line=0, blk_s=H2000),
        _r(2, H2000 + 3000, 60, "COM", "T", "PAID", line=5000, blk_s=H2000 + 2400),
        _r(3, H2000 + 3060, 389, "PGM", "T", "CHINESEFILLER25-008", line=0, blk_s=H2000 + 3000),
        _r(4, H2000 + 3449, 60, "PER", "T", "PI-503-060", None),  # Finish's own fill, unbooked
        _r(5, H2000 + 3509, 25, "ID", "T", "ID - TACDAL - GENERIC"),
        _r(6, H2130 + 17, 60, "PER", "T", "PI-485-060", line=-1, blk_s=H2130 + 420),  # 21:30 block
        _r(7, H2130 + 77, 60, "PER", "T", "PI-504-060", line=-1, blk_s=H2130 + 420),
        _r(8, H2130 + 137, 25, "ID", "T", "ID - hand 21:30"),  # unbooked, behind the 21:30 rows
        _r(9, H2130 + 162, 1500, "NOOP", "T", ""),
        _r(10, H2200, 700, "PGM", "F", "KD-THEFIRSTMAN129-0910A", line=0, blk_s=H2200),
    ]
    evs = window_from_day(rows, H2000, H2130)
    assert [e.desc for e in evs] == [
        "THEFOUNDERS11-091026A",
        "PAID",
        "CHINESEFILLER25-008",
        "PI-503-060",
        "ID - TACDAL - GENERIC",
    ]
    # the planner sees no overage, so nothing of the 21:30 block is stripped
    assert fp.packed_remainder(evs, H2130) > 0


def test_fallback_without_an_f_anchor_still_owns_booked_rows_past_the_top():
    rows = [
        _r(1, H1730 + 15, 663, "PGM", "T", "V-PastPresCult36-091026A", line=0, blk_s=H1730),
        _r(2, H1730 + 678, 420, "PGM", "T", "V-PastPresCult36-091026B", line=0, blk_s=H1730 + 600),
        _r(3, H1800 + 3, 30, "COM", "T", "LATE-BUT-OURS", line=77, blk_s=H1730 + 1680),
        _r(4, H1800 + 33, 30, "COM", "T", "NEXT-BLOCK", line=78, blk_s=H1800 + 600),
    ]
    evs = window_from_day(rows, H1730, H1800)
    assert [e.desc for e in evs] == [
        "V-PastPresCult36-091026A",
        "V-PastPresCult36-091026B",
        "LATE-BUT-OURS",
    ]


class _CatalogCur:
    def __init__(self, codes):
        self.codes = codes
        self.patterns = []

    def execute(self, sql, params):
        self.patterns.append(params[0])

    def fetchall(self):
        return [(c,) for c in self.codes]


def _pgm(i, code, desc=None):
    return Ev(i, 0, 600, "PGM", "T", 5000 + i, desc or code, 0, None, code)


def test_missing_pieces_reads_the_catalog_not_the_remainder():
    evs = [
        _pgm(1, "THEPOINT090926A"),
        _pgm(2, "THEPOINT090926B"),
        _pgm(4, "THEPOINT090926D"),
        _pgm(5, "K-FILLER25-003"),
        _pgm(6, "BUMP", "BUMP_OPEN"),
    ]
    cur = _CatalogCur(["THEPOINT090926A", "THEPOINT090926B", "THEPOINT090926C", "THEPOINT090926D"])
    assert missing_pieces(cur, evs) == {"THEPOINT090926": ["C"]}
    assert cur.patterns == ["THEPOINT090926_"]  # one query per show base, fillers/bumpers skipped
    cur = _CatalogCur(["THEPOINT090926A", "THEPOINT090926B", "THEPOINT090926D"])
    assert missing_pieces(cur, evs) == {}


def _korean_hour(paid_s):
    lo = 9 * 3600.0
    return [
        Ev(
            1,
            lo,
            1770,
            "PGM",
            "F",
            1,
            "KD-THEFIRSTMAN129-0910A",
            0,
            None,
            "KD-THEFIRSTMAN129-0910A",
        ),
        Ev(2, lo + 1770, paid_s, "COM", "T", 2, "PAID", 4242, None, "PAID"),
        Ev(3, lo + 1770 + paid_s, 894, "PGM", "T", 3, "K-FILLER25-041", 0, None, "K-FILLER25-041"),
    ]


def test_filler_swap_picks_the_longest_same_pool_filler_that_fits(monkeypatch):
    pool = [
        {"fid": 11, "code": "K-FILLER25-003", "frames": int(266 * FPS)},
        {"fid": 12, "code": "K-FILLER25-010", "frames": int(590 * FPS)},
        {"fid": 13, "code": "K-FILLER25-020", "frames": int(600 * FPS)},
        {"fid": 14, "code": "K-FILLER25-041", "frames": int(500 * FPS)},  # already in the show
    ]
    monkeypatch.setattr(fp, "active_pool", lambda cur, patterns: pool)
    # program 1770 + paid 1230 + filler 894 = 3894 → 4:54 over; budget = 894 - 294 - 5 = 595
    swaps = filler_swaps(None, _korean_hour(1230), 10 * 3600.0)
    assert [(old.code, new["code"]) for old, new in swaps] == [("K-FILLER25-041", "K-FILLER25-010")]
    # no overrun → no swap, the pool is never consulted
    monkeypatch.setattr(
        fp, "active_pool", lambda cur, patterns: (_ for _ in ()).throw(AssertionError)
    )
    assert filler_swaps(None, _korean_hour(540), 10 * 3600.0) == []


def test_filler_swap_gives_up_when_even_the_shortest_filler_is_too_long(monkeypatch):
    monkeypatch.setattr(
        fp,
        "active_pool",
        lambda cur, patterns: [{"fid": 11, "code": "K-FILLER25-003", "frames": int(700 * FPS)}],
    )
    assert filler_swaps(None, _korean_hour(1230), 10 * 3600.0) == []


class _SeatCur:
    def __init__(self, xorders):
        self.xo = dict(xorders)
        self.writes = 0
        self._res = None

    def execute(self, sql, params):
        if sql.startswith("SELECT XORDER"):
            self._res = (self.xo[params[0]],)
        elif sql.startswith("SELECT MIN(XORDER)"):
            _m, _d, after, skip = params
            c = [v for k, v in self.xo.items() if v > after and k != skip]
            self._res = (min(c) if c else None,)
        else:
            self.writes += 1
            if sql.startswith("UPDATE TPALINSE SET XORDER="):
                self.xo[params[1]] = params[0]

    def fetchone(self):
        return self._res


def test_seat_is_a_no_op_for_a_row_already_behind_its_predecessor():
    cur = _SeatCur({1: 1000, 99: 1500, 2: 2000})
    assert fs._seat(cur, 1, "2026-09-10", 99, 1) == 1500 and cur.writes == 0
    cur = _SeatCur({1: 1000, 2: 2000, 99: 2500})  # behind row 2 → must move between 1 and 2
    assert 1000 < fs._seat(cur, 1, "2026-09-10", 99, 1) < 2000 and cur.writes == 1
