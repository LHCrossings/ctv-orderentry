"""Broadcast Health event log (2026-09-09: 'did the dot turn yellow and we missed it?')."""

import datetime as dt

from business_logic.services.health_events import EventLog, diff_events

AT = "2026-09-08T08:03:30-07:00"


def _snap(offair=(), media=(), unreachable=False, error=""):
    return {
        "unreachable": unreachable,
        "error": error,
        "offair": [
            {"stationId": sid, "stationName": name, "titles": ["Video freeze"], "since": AT}
            for sid, name in offair
        ],
        "media": [
            {"id": i, "code": c, "kind": "truncated", "first": "NYC 09-08 11:00"} for i, c in media
        ],
    }


def _kinds(events):
    return [e["kind"] for e in events]


def test_first_snapshot_logs_start_and_active_conditions_only():
    ev = diff_events(None, _snap(offair=[("1", "NYC")], media=[(148469, "TOPNEWS090826A")]), AT)
    assert _kinds(ev) == ["start", "offair", "media"]
    assert ev[1]["station"] == "NYC" and ev[1]["titles"] == ["Video freeze"]
    assert ev[2]["code"] == "TOPNEWS090826A"


def test_quiet_poll_writes_nothing():
    s = _snap(offair=[("1", "NYC")])
    assert diff_events(s, s, AT) == []


def test_station_going_off_then_back_on():
    clean = _snap()
    off = _snap(offair=[("1", "NYC"), ("8", "WDC")])
    ev = diff_events(clean, off, AT)
    assert _kinds(ev) == ["offair", "offair"]
    assert {e["station"] for e in ev} == {"NYC", "WDC"}
    back = diff_events(off, _snap(offair=[("8", "WDC")]), AT)
    assert _kinds(back) == ["onair"] and back[0]["station"] == "NYC"


def test_media_finding_appearing_and_clearing():
    ev = diff_events(_snap(), _snap(media=[(148469, "TOPNEWS090826A")]), AT)
    assert _kinds(ev) == ["media"] and ev[0]["id"] == 148469
    ev = diff_events(_snap(media=[(148469, "TOPNEWS090826A")]), _snap(), AT)
    assert _kinds(ev) == ["media_clear"] and ev[0]["code"] == "TOPNEWS090826A"


def test_feed_loss_is_not_a_recovery():
    off = _snap(offair=[("1", "NYC")])
    lost = _snap(unreachable=True, error="timed out")
    ev = diff_events(off, lost, AT)
    assert _kinds(ev) == ["feed_lost"] and ev[0]["error"] == "timed out"
    back = diff_events(lost, off, AT)
    # The dot went red → grey → red: NYC's off-air state is logged again with the feed.
    assert _kinds(back) == ["feed_back", "offair"] and back[1]["station"] == "NYC"


def test_events_carry_the_timestamp_given():
    ev = diff_events(None, _snap(), AT)
    assert ev == [{"at": AT, "kind": "start"}]


def test_event_log_round_trip_newest_first_and_window(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    now = dt.datetime(2026, 9, 9, 0, 30, tzinfo=dt.timezone(dt.timedelta(hours=-7)))
    old = (now - dt.timedelta(days=10)).isoformat(timespec="seconds")
    recent = (now - dt.timedelta(hours=16)).isoformat(timespec="seconds")
    log.append([{"at": old, "kind": "start"}])
    log.append(
        [
            {"at": recent, "kind": "offair", "station": "NYC"},
            {"at": recent, "kind": "onair", "station": "NYC"},
        ]
    )
    (tmp_path / "events.jsonl").open("a").write("not json\n")
    got = log.read(days=7, now=now)
    assert [e["kind"] for e in got] == ["onair", "offair"]
    assert len(log.read(days=30, now=now)) == 3
    assert log.read(days=7, now=now + dt.timedelta(days=30)) == []


def test_event_log_stays_bounded(tmp_path, monkeypatch):
    import business_logic.services.health_events as he

    monkeypatch.setattr(he, "MAX_LINES", 10)
    monkeypatch.setattr(he, "KEEP_LINES", 5)
    log = EventLog(tmp_path / "events.jsonl")
    log.append([{"at": AT, "kind": "start"} for _ in range(12)])
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 5
