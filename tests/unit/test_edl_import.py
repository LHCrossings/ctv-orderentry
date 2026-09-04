"""EDIUS marker CSV → Etere EDL: splits, EOM and GAP (omit) pairs.

Oracles: NEWSTODAY040926 (filmati 135045) hand-marked in Etere with 3 splits + 1 omit,
and today's MBC.csv (2026-09-04) which marks the omit as two adjacent GAP point markers.
"""

import pytest

from business_logic.services.edl_import import (
    _omitted_frames,
    _tc_to_frames,
    apply_edl_from_csv,
    expected_parts,
    parse_edius_csv,
)

HEADER = (
    "# EDIUS Marker list\r\n# Format Version 3\r\n# Created Date : Fri Sep  4 11:43:55 2026\r\n"
    "#\r\n# No, Anchor, Position, Duration, Comment\r\n"
)
MBC_ROWS = [
    ('1,"ON","00:11:03;07", ,""', 19877, False),
    ('2,"ON","00:20:26;10", ,""', 36754, False),
    ('3,"ON","00:31:33;19", ,""', 56753, False),
    ('4,"ON","00:39:58;20", ,"GAP"', 71888, True),
    ('5,"ON","00:43:05;16", ,"GAP"', 77488, True),
    ('6,"ON","00:52:31;23", ,""', 94459, False),
]
MBC_CSV = HEADER + "\r\n".join(r[0] for r in MBC_ROWS) + "\r\n"


def test_drop_frame_timecodes_match_hand_conversion():
    for row, frame, _ in MBC_ROWS:
        assert _tc_to_frames(row.split(",")[2].strip('"')) == frame


def test_parse_mbc_csv_pairs_gap_markers():
    splits, eom, omits = parse_edius_csv(MBC_CSV)
    assert splits == [19877, 36754, 56753]
    assert eom == 94459
    assert omits == [(71888, 77488)]
    assert expected_parts(splits, eom, omits) == [
        (0, 19877),
        (19878, 36754),
        (36755, 56753),
        (56754, 71888),
        (77489, 94459),
    ]


def test_expected_parts_reproduces_etere_explode_of_oracle():
    # NEWSTODAY040926: dbo.ExplodeEdl(135045, 0, 'eeAutomatic', 4, ...) live 2026-09-04
    splits, eom, omits = [20792, 38923, 60147], 96228, [(80819, 87107)]
    assert expected_parts(splits, eom, omits) == [
        (0, 20792),
        (20793, 38923),
        (38924, 60147),
        (60148, 80819),
        (87108, 96228),
    ]
    # Etere stored DURATION 89940 = EOM+1 − inclusive omit length
    assert eom + 1 - _omitted_frames(omits) == 89940


def test_no_gap_csv_is_unchanged():
    csv = HEADER + "\r\n".join(r[0] for r in MBC_ROWS if not r[2]) + "\r\n"
    splits, eom, omits = parse_edius_csv(csv)
    assert (splits, eom, omits) == ([19877, 36754, 56753], 94459, [])
    assert expected_parts(splits, eom) == expected_parts(splits, eom, [])
    assert expected_parts(splits, eom)[-1] == (56754, 94459)


def test_gap_comment_is_case_and_space_insensitive():
    csv = MBC_CSV.replace('"GAP"', '" gap "')
    assert parse_edius_csv(csv)[2] == [(71888, 77488)]


@pytest.mark.parametrize(
    "drop_row, why",
    [
        (4, "lone GAP marker (out without in)"),
        (3, "lone GAP marker (in without out)"),
    ],
)
def test_unpaired_gap_refuses(drop_row, why):
    rows = [r[0] for i, r in enumerate(MBC_ROWS) if i != drop_row]
    with pytest.raises(ValueError, match="no adjacent GAP partner"):
        parse_edius_csv(HEADER + "\r\n".join(rows) + "\r\n")


def test_split_between_gap_markers_refuses():
    rows = [r[0] for r in MBC_ROWS]
    rows.insert(4, '9,"ON","00:41:00;00", ,""')  # a plain split inside the gap pair
    with pytest.raises(ValueError, match="no adjacent GAP partner"):
        parse_edius_csv(HEADER + "\r\n".join(rows) + "\r\n")


def test_gap_as_last_marker_refuses():
    rows = [r[0] for r in MBC_ROWS]
    rows[-1] = rows[-1].replace('""', '"GAP"')
    with pytest.raises(ValueError, match="GAP"):
        parse_edius_csv(HEADER + "\r\n".join(rows) + "\r\n")


class _Cur:
    def __init__(self, durs, plan):
        self.durs, self.plan, self.sql = durs, plan, []
        self._rows = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))
        if "FROM FEDLDESCRIPTION" in sql:
            self._rows = list(self.durs.items())
        elif "ExplodeEdl" in sql:
            self._rows = list(self.plan)
        else:
            self._rows = []

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, cur):
        self.cur, self.committed, self.rolled_back = cur, 0, 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def _inserts(cur):
    return [p for s, p in cur.sql if s.startswith("INSERT INTO FINTERRUZIONI")]


def test_apply_writes_omit_row_and_nets_duration():
    splits, eom, omits = [19877, 36754, 56753], 94459, [(71888, 77488)]
    exp = expected_parts(splits, eom, omits)
    cur = _Cur({0: 121074, 3000000: 121195}, exp)
    conn = _Conn(cur)
    res = apply_edl_from_csv(conn, 148268, splits, eom, 4, omits=omits)
    assert res["ok"], res
    assert conn.committed == 1 and conn.rolled_back == 0
    assert "1 gap(s)" in res["message"] and "5 parts" in res["message"]

    # FILMATI: POS_FIN = EOM, DURATA = EOM+1 − inclusive omit length
    filmati_upd = [p for s, p in cur.sql if s.startswith("UPDATE FILMATI")]
    assert filmati_upd == [(94459, 94459 + 1 - 5601, 148268)]

    # VERSION 0 header nets the same frames
    hdr = {p[3]: p for s, p in cur.sql if s.startswith("UPDATE FEDLDESCRIPTION")}
    assert hdr[0][:2] == (94459, 94459 + 1 - 5601)
    r = 121195 / 121074
    v_omit = (round(71888 * r), round(77488 * r))
    assert hdr[3000000][:2] == (
        round(94459 * r),
        round(94459 * r) + 1 - (v_omit[1] - v_omit[0] + 1),
    )

    # One omit row per VERSION: MARKIN<MARKOUT, BULK_VIDEO=1, INSERTION_POINT=0, FLAG='', MARKORDER=MARKIN
    ins = _inserts(cur)
    assert len(ins) == 2 * (3 + 1)
    omit_rows = [p for p in ins if p[3] == 1]
    assert [(p[1], p[2], p[4], p[5], p[6], p[7]) for p in omit_rows if p[6] == 0] == [
        (71888, 77488, 0, "", 0, 71888)
    ]
    split_rows = [p for p in ins if p[3] == 0 and p[6] == 0]
    assert all(p[1] == p[2] and p[4] == 1 and p[5] == "P" for p in split_rows)


def test_apply_rolls_back_when_explode_disagrees():
    splits, eom, omits = [19877], 94459, [(71888, 77488)]
    wrong_plan = expected_parts(
        splits + [71888, 77488], eom
    )  # what a splits-only write would produce
    cur = _Cur({0: 121074}, wrong_plan)
    conn = _Conn(cur)
    res = apply_edl_from_csv(conn, 148268, splits, eom, 4, omits=omits)
    assert not res["ok"]
    assert conn.rolled_back == 1 and conn.committed == 0
    assert res["expected"] == expected_parts(splits, eom, omits)


def test_apply_refuses_gap_outside_program():
    conn = _Conn(_Cur({0: 10}, []))
    assert not apply_edl_from_csv(conn, 1, [], 1000, None, omits=[(900, 1200)])["ok"]
    assert not apply_edl_from_csv(conn, 1, [], 1000, None, omits=[(500, 500)])["ok"]
    assert conn.cur.sql == []  # refused before touching the DB
