"""One binding rule for every placement writer (2026-09-15, McD SEA billboards session:
`_apply_filmati_sync` rewrote 142 rows for 9/17 from the code after `_bind_supporto` had
bound them to the file)."""

import pytest

from business_logic.services import daily_programming_run as dp
from business_logic.services import finish_service as fs
from business_logic.services import playout_binding as pb


class FakeCursor:
    """Answers the two SELECTs the writers make and records every UPDATE."""

    def __init__(self, file_id="VD-BEAUTRYTYCOON24-0916A", live_id=None):
        self.file_id, self.live_id, self.updates, self._last = file_id, live_id, [], ""

    def execute(self, sql, params=None):
        self._last = sql
        if sql.lstrip().upper().startswith("UPDATE"):
            self.updates.append((" ".join(sql.split()), params))

    def fetchone(self):
        if "FS_FILMATI" in self._last:
            return (self.file_id,) if self.file_id is not None else None
        if "LIVE_ID" in self._last:
            return (self.live_id,)
        return None


def test_binding_is_prefix_plus_the_rules_file_id():
    cur = FakeCursor()
    assert pb.binding(cur, 148688) == pb.PREFIX + "VD-BEAUTRYTYCOON24-0916A"
    assert "PHYSICAL_SIZE > 0" in cur._last and "NOT LIKE '%%:%%'" in cur._last
    assert "LEGACY_MEDIAID = '0' THEN 0" in cur._last
    assert pb.binding(FakeCursor(file_id=None), 1) is None
    with pytest.raises(RuntimeError):
        pb.binding(FakeCursor(file_id="X" * 40), 1)


def test_filmati_sync_writes_the_file_binding_not_the_code():
    """The repro: asset code VD-BEAUTRYTYCOON24-0917A, file ...0916A. Before the fix the sync
    wrote '0ETX      VD-BEAUTRYTYCOON24-0917A' (verified live in a rolled-back transaction)."""
    cur = FakeCursor()
    dp._apply_filmati_sync(cur, 148688, [(15794670, "VD-BEAUTRYTYCOON24-0917A")])
    row_updates = [(s, p) for s, p in cur.updates if "TPALINSE" in s]
    assert len(row_updates) == 1
    sql, params = row_updates[0]
    assert "supporto=%s" in sql
    assert params[0] == pb.PREFIX + "VD-BEAUTRYTYCOON24-0916A"
    assert "0917A" not in str(params[0])


def test_filmati_sync_leaves_the_binding_alone_when_no_copy_is_sized_yet():
    cur = FakeCursor(file_id=None)
    dp._apply_filmati_sync(cur, 148688, [(15794670, "VD-BEAUTRYTYCOON24-0917A")])
    sql, params = [(s, p) for s, p in cur.updates if "TPALINSE" in s][0]
    assert "supporto" not in sql.lower()
    assert "SCHEDULE_CHECKSUM" in sql


def test_live_asset_rows_keep_the_sp_binding_and_only_freeze_the_checksum():
    cur = FakeCursor(live_id=2810)
    dp._apply_filmati_sync(cur, 2810, [(1, "SHOPLC")])
    assert not [s for s, _ in cur.updates if "FILMATI SET" in s and "TPALINSE" not in s]
    sql, _ = cur.updates[0]
    assert "supporto" not in sql.lower() and "SCHEDULE_CHECKSUM" in sql


def test_bind_supporto_and_finish_supporto_agree_with_binding():
    cur = FakeCursor()
    assert dp._bind_supporto(cur, 15794670, 148688) == pb.PREFIX + "VD-BEAUTRYTYCOON24-0916A"
    assert cur.updates[-1][1] == (pb.PREFIX + "VD-BEAUTRYTYCOON24-0916A", 15794670)
    assert fs._supporto(FakeCursor(), 148688) == pb.PREFIX + "VD-BEAUTRYTYCOON24-0916A"
    assert dp._bind_supporto(FakeCursor(file_id=None), 1, 1) is None
    with pytest.raises(RuntimeError):
        fs._supporto(FakeCursor(file_id=None), 1)
