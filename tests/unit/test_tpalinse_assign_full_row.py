"""A creative on a placed playlist row is seven columns, not one (WorldLink room,
2026-10-30: `UPDATE TPALINSE SET ID_FILMATI = …` alone left 78 CVC rows reading COMS
with an empty binding — "would air black"). Every writer goes through
`tpalinse_assign.assign_creative`, whose statement names every column the traffic
assign route writes."""

import re
from pathlib import Path

from business_logic.services import playout_binding as pb
from business_logic.services import tpalinse_assign as ta

ROUTES = Path(__file__).resolve().parents[2] / "src" / "web" / "routes" / "orders.py"


def _set_columns(sql: str) -> set[str]:
    up = sql.upper()
    body = sql[up.index("SET") + 3 : up.index("WHERE") if "WHERE" in up else len(sql)]
    return {m.group(1).upper() for m in re.finditer(r"(\w+)\s*=", body)}


def test_assign_sql_names_every_column_the_traffic_assign_writes():
    src = ROUTES.read_text()
    route_stmt = re.search(
        r'"UPDATE TPALINSE SET COD_PROGRA = %s, TITLE = %s, ID_FILMATI = %d,"\s*'
        r'" NEWTYPE = %s, SUPPORTO = %s, ASPECT = %s, DURATION_P = %d"',
        src,
    )
    assert route_stmt, "traffic assign statement not found in orders.py"
    route_cols = _set_columns(route_stmt.group(0).replace('"', ""))
    assert _set_columns(ta.ASSIGN_SQL) == route_cols == set(ta.ASSIGN_COLUMNS)


def test_no_writer_sets_id_filmati_alone():
    src = ROUTES.read_text()
    bare = re.findall(r"UPDATE\s+TPALINSE\s+SET\s+ID_FILMATI\s*=\s*%\w\s+WHERE", src, re.I)
    assert not bare, bare


def test_reapply_filmati_uses_the_shared_writer():
    src = ROUTES.read_text()
    start = src.index("async def worldlink_room_reapply_filmati(")
    body = src[start : src.index("@router.", start)]
    assert "assign_creative(" in body
    assert "UPDATE TPALINSE" not in body


class FakeCursor:
    def __init__(self, file_id="REDFIN15E07", code="BNS"):
        self.file_id, self.code, self.updates, self._last = file_id, code, [], ""

    def execute(self, sql, params=None):
        self._last = sql
        if sql.lstrip().upper().startswith("UPDATE"):
            self.updates.append((" ".join(sql.split()), params))

    def fetchone(self):
        q = self._last
        if "VIDEOSTANDARD" in q:
            return ("D", 450)
        if "FILE_ID" in q and "FS_FILMATI" in q:
            return (self.file_id,) if self.file_id else None
        if "FROM FILMATI" in q:
            return ("RNOANOV115H", "REDFIN15E07: OW Animation")
        if "trf_bookingcode" in q:
            return (self.code,)
        return None


def test_assign_creative_writes_the_full_row_with_the_one_binding_rule():
    cur = FakeCursor()
    assert ta.assign_creative(cur, 16346162, 146733) is True
    sql, params = cur.updates[0]
    assert sql == " ".join(ta.ASSIGN_SQL.split())
    assert params == (
        "RNOANOV115H",
        "REDFIN15E07: OW Animation",
        146733,
        "BNS",
        pb.PREFIX + "REDFIN15E07",
        "H",
        450,
        16346162,
    )


def test_assign_creative_writes_nothing_when_the_asset_has_no_copy():
    cur = FakeCursor(file_id=None)
    assert ta.assign_creative(cur, 1, 2) is False
    assert cur.updates == []
