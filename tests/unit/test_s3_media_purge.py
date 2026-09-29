"""S3 media purge — Etere's "Delete on -> AWS S3" in batches (Lee, 2026-09-29).

Scope guards are the whole safety story: program assets only, never short-form, never
HIATUS, never anything with a playlist row ahead, never an object whose size disagrees
with Etere. Only keys S3 confirms deleted leave the Media Library."""

import datetime as dt
import decimal
from pathlib import Path

import pytest

from business_logic.services import s3_media_purge as smp


def test_candidate_sql_carries_every_scope_guard():
    sql = smp.expired_candidates_sql(dt.date(2026, 9, 29))
    assert "m.ID_METADEVICE = 6" in sql, "only the S3 device"
    assert "f.NEWTYPE IN ('PGM', 'PGMX')" in sql, "program assets only"
    for w in ("BUMP", "OPEN", "CLOSE", "RELIG", "HIATUS"):
        assert f"f.COD_PROGRA NOT LIKE '%{w}%'" in sql
        assert f"f.DESCRIZIO NOT LIKE '%{w}%'" in sql
    assert "f.DATA_SCAD IS NOT NULL AND f.DATA_SCAD < '2026-09-30'" in sql, (
        "expired = DATA_SCAD on or before today"
    )
    assert "DATA >= '2026-09-29'" in sql and "future_rows" in sql
    assert "LIVELLO = 0" in sql


def _row(**kw):
    base = {
        "id_metafile": 1,
        "id_filmati": 10,
        "cod_progra": "DTV-X01",
        "descrizio": "x",
        "newtype": "PGM",
        "file_id": "DTV-X01",
        "file_name": "DTV-X01.mp4",
        "size": 100,
        "data_scad": dt.datetime(2024, 1, 1),
        "last_aired": None,
        "future_rows": 0,
    }
    base.update(kw)
    return base


def test_check_rows_keeps_only_size_matched_unbooked_objects():
    listing = {"DTV-X01.mp4": 100, "DTV-X02.mp4": 999, "DTV-X04.mp4": 100}
    rows = [
        _row(),
        _row(id_metafile=2, file_name="DTV-X02.mp4"),
        _row(id_metafile=3, file_name="DTV-X03.mp4"),
        _row(id_metafile=4, file_name="DTV-X04.mp4", future_rows=2),
        _row(id_metafile=5, file_name=None),
    ]
    ok, bad = smp.check_rows(rows, listing)
    assert [r["id_metafile"] for r in ok] == [1]
    problems = {r["id_metafile"]: r["problem"] for r in bad}
    assert "size differs" in problems[2]
    assert "missing in S3" in problems[3]
    assert "playlist row" in problems[4]
    assert "no FS_FILE" in problems[5]


def test_sql_literals():
    assert smp._lit(None) == "NULL"
    assert smp._lit(True) == "1" and smp._lit(False) == "0"
    assert smp._lit(decimal.Decimal("6544531218")) == "6544531218"
    assert smp._lit(dt.datetime(2026, 9, 9, 23, 6, 33, 270000)) == "'2026-09-09 23:06:33.270'"
    assert smp._lit("O'Brien") == "N'O''Brien'"
    assert smp._lit(b"\x01\xff") == "0x01ff"


def _ids(sql: str) -> list[int]:
    a = sql.index("IN (") + 4
    return [int(x) for x in sql[a : sql.index(")", a)].split(",")]


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self.description = None
        self._rows = []

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        if sql.startswith("DELETE FROM"):
            table = sql.split()[2]
            ids = _ids(sql)
            self.rowcount = sum(1 for i in ids if i in self.conn.fs[table])
            if self.conn.fail_on == table:
                raise RuntimeError("boom")
            self.conn.pending.append((table, ids))
        elif sql.startswith("UPDATE FILMATI SET DATA_SCAD"):
            ids = _ids(sql)
            hit = [i for i in ids if i in self.conn.unexpired]
            self.rowcount = len(hit)
            self.conn.pending.append(("__stamp__", hit))
        elif sql.startswith("SELECT ID_FILMATI, DATA_SCAD"):
            self._rows = [(i, None) for i in _ids(sql)]
        elif sql.startswith("SELECT COUNT(*) FROM FILMATI"):
            self._rows = [(sum(1 for i in _ids(sql) if i in self.conn.unexpired),)]
        elif sql.startswith("SELECT * FROM"):
            table = sql.split()[3]
            self.description = [("ID_METAFILE",), ("FILE_ID",)]
            self._rows = [(i, f"F{i}") for i in sorted(self.conn.fs[table])]
        elif sql.startswith("SELECT COUNT(*) FROM"):
            table = sql.split()[3]
            self._rows = [(sum(1 for i in _ids(sql) if i in self.conn.fs[table]),)]

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0]


class FakeConn:
    """Metafile ids ``ids``; their assets (100 + id) start NOT expired."""

    def __init__(self, ids, fail_on=None):
        self.fs = {t: set(ids) for t, _ in smp.FS_TABLES}
        self.unexpired = {100 + i for i in ids}
        self.sql: list[str] = []
        self.pending: list[tuple[str, list[int]]] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_on = fail_on

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        for table, ids in self.pending:
            if table == "__stamp__":
                self.unexpired -= set(ids)
            else:
                self.fs[table] -= set(ids)
        self.pending = []
        self.commits += 1

    def rollback(self):
        self.pending = []
        self.rollbacks += 1


class FakeS3:
    def __init__(self, keys, refuse=()):
        self.keys = set(keys)
        self.refuse = set(refuse)
        self.calls = 0

    def delete_objects(self, Bucket, Delete):
        self.calls += 1
        deleted, errors = [], []
        for o in Delete["Objects"]:
            if o["Key"] in self.refuse:
                errors.append({"Key": o["Key"], "Code": "AccessDenied", "Message": "no"})
            else:
                self.keys.discard(o["Key"])
                deleted.append({"Key": o["Key"]})
        return {"Deleted": deleted, "Errors": errors}

    def head_object(self, Bucket, Key):
        if Key in self.keys:
            return {"ContentLength": 10}
        exc = Exception("404")
        exc.response = {"Error": {"Code": "404"}}
        raise exc


def _rows(n):
    return [
        _row(id_metafile=i, id_filmati=100 + i, file_name=f"K{i}.mp4", size=10)
        for i in range(1, n + 1)
    ]


def test_dry_run_touches_nothing(tmp_path):
    conn, s3 = FakeConn([1, 2, 3]), FakeS3(["K1.mp4", "K2.mp4", "K3.mp4"])
    res = smp.purge(conn, s3, "b", _rows(3), apply=False, restore_dir=tmp_path)
    assert res["planned"] == 3 and res["bytes"] == 30
    assert s3.calls == 0 and conn.sql == [] and not list(tmp_path.iterdir())


def test_only_keys_s3_confirms_leave_the_db_and_restore_is_written_first(tmp_path):
    conn = FakeConn([1, 2, 3, 4, 5])
    s3 = FakeS3([f"K{i}.mp4" for i in range(1, 6)], refuse={"K3.mp4"})
    res = smp.purge(
        conn, s3, "b", _rows(5), apply=True, batch_size=2, restore_dir=tmp_path, log=lambda s: None
    )
    assert s3.calls == 3, "five keys in batches of two"
    assert res["deleted_s3"] == 4 and res["deleted_db"] == 4
    assert res["s3_errors"] == ["K3.mp4: AccessDenied no"]
    assert conn.fs["FS_METAFILE"] == {3}, "the refused key keeps its Media Library rows"
    assert conn.fs["smptemetadata"] == {3}
    assert conn.commits == 3 and conn.rollbacks == 0
    assert res["expired_stamped"] == 4 and conn.unexpired == {103}, (
        "every asset whose file left S3 is expired in the same transaction; the refused one is not"
    )
    restore = Path(res["restore"]).read_text()
    assert "UPDATE FILMATI SET DATA_SCAD = NULL WHERE ID_FILMATI = 101;" in restore, (
        "old expiry dates restorable"
    )
    assert "INSERT INTO FS_METAFILE (ID_METAFILE, FILE_ID) VALUES (3, N'F3');" in restore
    assert conn.sql.index(
        "SELECT * FROM FS_METAFILE WHERE ID_METAFILE IN (1,2,3,4,5)"
    ) < conn.sql.index("DELETE FROM smptemetadata WHERE id_metafile IN (1,2)")


def test_limit_and_db_failure_rolls_back_and_stops(tmp_path):
    conn = FakeConn([1, 2, 3, 4], fail_on="FS_FILE")
    s3 = FakeS3([f"K{i}.mp4" for i in range(1, 5)])
    with pytest.raises(RuntimeError, match="boom"):
        smp.purge(
            conn,
            s3,
            "b",
            _rows(4),
            apply=True,
            limit=3,
            batch_size=10,
            restore_dir=tmp_path,
            log=lambda s: None,
        )
    assert s3.calls == 1 and s3.keys == {"K4.mp4"}, "limit 3: the fourth key was never sent"
    assert conn.rollbacks == 1 and conn.commits == 0
    assert conn.fs["FS_METAFILE"] == {1, 2, 3, 4}, (
        "rolled back: records stay for the next dry run to report as dangling"
    )


def test_verify_reads_back_rows_objects_and_expiry():
    conn, s3 = FakeConn([1, 2]), FakeS3(["K2.mp4"])
    v = smp.verify(conn, s3, "b", _rows(2), workers=2)
    assert v["fs_rows_left"]["FS_METAFILE"] == 2 and v["objects_left"] == ["K2.mp4"] and not v["ok"]
    assert v["not_expired"] == 2
    conn.fs = {t: set() for t in conn.fs}
    s3.keys = set()
    assert not smp.verify(conn, s3, "b", _rows(2), workers=2)["ok"], (
        "an unexpired asset fails verify"
    )
    conn.unexpired = set()
    assert smp.verify(conn, s3, "b", _rows(2), workers=2)["ok"]


def test_mark_expired_and_remove_references_stamp_the_asset(tmp_path):
    conn = FakeConn([1, 2])
    rows = [_row(id_metafile=None, id_filmati=101, file_name=None, size=0, data_scad=None)]
    res = smp.mark_expired(conn, rows, apply=True, restore_dir=tmp_path, log=lambda s: None)
    assert res["expired_stamped"] == 1 and conn.unexpired == {102}
    assert (
        "UPDATE FILMATI SET DATA_SCAD = NULL WHERE ID_FILMATI = 101;"
        in Path(res["restore"]).read_text()
    )
    res = smp.remove_references(
        conn, _rows(2)[1:], apply=True, restore_dir=tmp_path, log=lambda s: None
    )
    assert res["deleted_db"] == 1 and res["expired_stamped"] == 1
    assert conn.fs["FS_METAFILE"] == {1} and conn.unexpired == set()


def test_unexpired_sql_and_categorize():
    sql = smp.unexpired_candidates_sql(dt.date(2026, 9, 29))
    assert "ISNULL(c.s3_copies, 0) = 0" in sql and "f.NEWTYPE IN ('PGM', 'PGMX')" in sql
    assert "f.DATA_SCAD >= '2026-09-30'" in sql, "not expired = no date or a future date"
    assert "f.CREATIONDATE < '2026-09-22'" in sql, "a week of grace for ingest in progress"
    assert "f.COD_PROGRA NOT LIKE '%HIATUS%'" in sql
    rows = [
        _row(id_metafile=None, id_filmati=7, other_copies=0),
        _row(id_metafile=None, id_filmati=8, other_copies=2, future_rows=1),
    ]
    cats = smp.categorize_unexpired(rows)
    assert [r["id_filmati"] for r in cats["unexpired"]] == [7] and [
        r["id_filmati"] for r in cats["booked"]
    ] == [8]


def test_short_form_types_never_eligible():
    assert set(smp.ELIGIBLE_TYPES) == {"PGM", "PGMX"}
    for t in ("COM", "PSA", "PER", "ID", "BAR", "BB", "INT", "AV", "PRO", "GEN", "FILM"):
        assert t not in smp.ELIGIBLE_TYPES


def test_probe_sizes_heads_each_key_once_and_drops_missing():
    s3 = FakeS3(["K1.mp4", "K2.mp4"])
    assert smp.probe_sizes(s3, "b", ["K1.mp4", "K1.mp4", "K2.mp4", "K9.mp4", None], workers=2) == {
        "K1.mp4": 10,
        "K2.mp4": 10,
    }
