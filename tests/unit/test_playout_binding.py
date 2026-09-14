"""Playout binding rule (2026-09-14: 258 renamed-show rows for 9/15-16 pointed at the code,
not the file, and both guards were blind to anything not yet restored to a CIB)."""

import ast
import datetime as dt
from pathlib import Path

from business_logic.services import playout_binding as pb

ROOT = Path(__file__).resolve().parents[2]


def test_file_id_rule_is_sized_colon_free_and_s3_first():
    sql = pb.FILE_ID_APPLY
    assert "PHYSICAL_SIZE > 0" in sql, "a size-0 stale CIB record must never name the file"
    assert "NOT LIKE '%:%'" in sql, (
        "a colon never belongs in a binding (PI-LF-0011: Ellipse Deluxe)"
    )
    assert "LEGACY_MEDIAID = '0' THEN 0" in sql, "the S3 master names the file every CIB restores"
    assert "ID_METADEVICE <> 6" not in sql, (
        "requiring a CIB copy hides every row more than a day out"
    )


def test_mismatch_where_scopes_unaired_non_live_rows_in_window():
    w = pb.mismatch_where(dt.date(2026, 9, 14), dt.date(2026, 9, 16), asset_id=127964)
    assert "BETWEEN '2026-09-14' AND '2026-09-16'" in w
    assert "t.STATUS IN ('I', 'E')" in w and "f.LIVE_ID IS NULL" in w and "t.LIVELLO = 0" in w
    assert "t.ID_FILMATI = 127964" in w
    assert f"<> '{pb.PREFIX}' + RTRIM(fs.FILE_ID)" in w


def _row(code, file, market, date, time, tid):
    return {
        "id_tpalinse": tid,
        "market": market,
        "date": date,
        "time": time,
        "status": "I",
        "code": code,
        "bound": code,
        "file": file,
        "id_filmati": 1,
    }


def test_summarize_groups_by_code_and_file_most_rows_first():
    rows = [
        _row(
            "CD-TERESATENG12-091526A", "CD-TeresaTeng12-0130A", "NYC", "2026-09-15", "21:00:00", 1
        ),
        _row(
            "CD-TERESATENG12-091526A", "CD-TeresaTeng12-0130A", "DAL", "2026-09-15", "19:00:00", 2
        ),
        _row(
            "CD-TERESATENG12-091526A", "CD-TeresaTeng12-0130A", "DAL", "2026-09-15", "26:00:00", 3
        ),
        _row("LFEL- 1737H", "PI-LF-0011", "DAL", "2026-09-15", "12:30:00", 4),
    ]
    out = pb.summarize(rows)
    assert [g["code"] for g in out] == ["CD-TERESATENG12-091526A", "LFEL- 1737H"]
    assert out[0]["count"] == 3 and out[0]["markets"] == "DAL 2, NYC 1"
    assert out[0]["first"] == {"market": "DAL", "date": "2026-09-15", "time": "19:00:00"}
    assert out[1]["file"] == "PI-LF-0011"


def test_restore_sql_writes_back_the_previous_binding_and_status():
    r = _row(
        "CD-TERESATENG12-091526A",
        "CD-TeresaTeng12-0130A",
        "NYC",
        "2026-09-15",
        "21:00:00",
        15757635,
    )
    r["status"] = "E"
    assert pb.restore_sql([r]) == (
        "UPDATE TPALINSE SET SUPPORTO='0ETX      CD-TERESATENG12-091526A', STATUS='E'"
        " WHERE ID_TPALINSE=15757635;\n"
    )


def _route_source(name: str) -> str:
    src = (ROOT / "src/web/routes/orders.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} not found")


def test_rename_programming_rebinds_through_the_shared_rule():
    body = _route_source("rename_programming_apply")
    assert 'rebind_asset(cursor, p["asset_id"])' in body
    assert "ID_METADEVICE <> 6" not in body, "the rename tool must not require a CIB copy"


def test_no_consumer_keeps_the_old_non_s3_rule():
    for rel in (
        "scripts/check_bindings.py",
        "src/web/routes/orders.py",
        "src/web/routes/broadcast_health.py",
    ):
        assert "ID_METADEVICE <> 6" not in (ROOT / rel).read_text(), rel
