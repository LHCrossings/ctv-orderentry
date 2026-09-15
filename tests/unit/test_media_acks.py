"""Media Check dismissals (2026-09-15: McD SEA billboards, complete files under the size floor)."""

import json

from business_logic.services.health_events import diff_events
from business_logic.services.media_acks import MediaAcks, finding_size


def _finding(fid=149053, code="MD07BBV418", size=2252180, kind="truncated"):
    return {
        "id_filmati": fid,
        "code": code,
        "kind": kind,
        "newtype": "COM",
        "durata": 211,
        "detail": "2,252,180 bytes for 00:00:07 = 10,674 B/frame",
        "copies": [
            {"device": "AWS S3 Bucket", "size": size, "codec": "MP4"},
            {"device": "CIB3", "size": size, "codec": "MP4"},
            {"device": "CIB1", "size": 0, "codec": "MP4"},  # not yet stamped — ignored
        ],
        "airings": [],
        "siblings": [],
    }


def test_finding_size_is_the_largest_sized_copy():
    f = _finding()
    f["copies"][1]["size"] = 2252190
    assert finding_size(f) == 2252190
    assert finding_size({"copies": []}) == 0


def test_dismiss_moves_a_finding_from_active_to_dismissed(tmp_path):
    store = MediaAcks(tmp_path / "acks.json")
    f = _finding()
    active, dismissed = store.apply([f])
    assert [x["id_filmati"] for x in active] == [149053] and dismissed == []
    assert active[0]["ack"] is None

    rec = store.ack(f, note="static billboard, viewed by Lee")
    assert rec["size"] == 2252180 and rec["note"] == "static billboard, viewed by Lee"
    active, dismissed = store.apply([f])
    assert active == []
    assert dismissed[0]["ack"]["code"] == "MD07BBV418"
    assert store.get(f)["at"] == rec["at"]


def test_a_reingested_file_alerts_again(tmp_path):
    """The dismissal is pinned to the copy size; a new file under the same asset is a new question."""
    store = MediaAcks(tmp_path / "acks.json")
    store.ack(_finding())
    bigger = _finding(size=7_500_000)
    active, dismissed = store.apply([bigger])
    assert [x["id_filmati"] for x in active] == [149053] and dismissed == []
    assert store.get(bigger) is None


def test_undo_restores_the_alarm(tmp_path):
    store = MediaAcks(tmp_path / "acks.json")
    f = _finding()
    store.ack(f)
    assert store.unack(149053)["code"] == "MD07BBV418"
    assert store.unack(149053) is None  # already gone; not an error
    active, dismissed = store.apply([f])
    assert len(active) == 1 and dismissed == []


def test_only_the_dismissed_asset_is_affected(tmp_path):
    store = MediaAcks(tmp_path / "acks.json")
    a, b = _finding(), _finding(fid=149052, code="MD06BBM418", size=2119898)
    store.ack(a)
    active, dismissed = store.apply([a, b])
    assert [x["code"] for x in active] == ["MD06BBM418"]
    assert [x["code"] for x in dismissed] == ["MD07BBV418"]


def test_old_dismissals_are_pruned_and_bad_files_are_tolerated(tmp_path):
    p = tmp_path / "acks.json"
    p.write_text(
        json.dumps(
            {
                "1": {"code": "OLD", "size": 5, "at": "2026-01-01T00:00:00-08:00"},
                "2": {"code": "NOAT", "size": 5},
            }
        )
    )
    store = MediaAcks(p)
    store.ack(_finding())  # any write prunes
    keys = set(json.loads(p.read_text()))
    assert keys == {"149053"}

    p.write_text("{not json")
    assert MediaAcks(p).load() == {}
    assert MediaAcks(tmp_path / "missing.json").apply([_finding()])[0][0]["ack"] is None


def test_dismissal_is_a_logged_transition_not_a_silent_clear():
    """The header snapshot is built from ACTIVE findings, so a dismissal reads to the event
    differ as the finding clearing; the route logs an explicit media_ack beside it."""
    prev = {
        "unreachable": False,
        "offair": [],
        "media": [{"id": 149053, "code": "MD07BBV418", "kind": "truncated"}],
    }
    cur = {"unreachable": False, "offair": [], "media": []}
    assert [e["kind"] for e in diff_events(prev, cur, "2026-09-15T10:00:00-07:00")] == [
        "media_clear"
    ]
