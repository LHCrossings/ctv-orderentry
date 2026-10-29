"""Ingest / Datamover stall rules (2026-10-29).

Oracle: five days of wf_log before the watchdog was written. Every Initial Import step other
than the Wait completed within a minute, except on the 10/28 crash morning (Mediainfo step
assigned to EtereMM9 at 06:36:58, finished 07:41:50 = 65 min). The 06:00 restore burst queued
S3 downloads for up to 13 min. The looping zombies sit at start_cnt 86-750.
"""

from business_logic.services.ingest_watchdog import (
    INGEST_STALL_MIN,
    LOOP_RESTARTS,
    RESTORE_STALL_MIN,
    classify,
    format_report,
    worker_for,
)

MM_DM = "mm-dm-84536704-a5c6-40d2-a24e-008030258e"  # Mediainfo on set File (Datamover)
MM_S3 = "mm-s3-3daa4c5d-40a4-42fa-bfd1-6bf7f930b7"  # WIP -> AWS S3 upload (Datamover)
MM_S3_RESTORE = "mm-s3-8a769474-daa5-43eb-946a-4daa70"  # AWS S3 -> CIB1 download
DLAY = "dlay--612b7054-e7f0-4363-b605-c1560a99ab"  # Wait [01:00:00]
SCME = "scme-4a1708f0-2998-4282-9069-2aa30e3bc47"  # Search and set File (SQL)
FSDEL = "fsdel--287e3746-fa48-4ed5-b9ec-5752abf1a"  # FileDelete on WIP (EtereRC)


def _job(ucode, minutes, cls=12, start_cnt=1, code="NEWSTODAY102226"):
    return {
        "id_instance": 1601142,
        "id_instanceclass": cls,
        "start_cnt": start_cnt,
        "action_ucode": ucode,
        "step": ucode[:8],
        "minutes": minutes,
        "code": code,
        "workflow": "1.0 Initial Import" if cls == 12 else "Restore to CIB1",
        "since": "2026-10-21T19:37:00",
        "message": "",
    }


def test_healthy_ingest_in_its_hour_wait_is_not_a_stall():
    stalls, looping = classify([_job(DLAY, 58.0)])
    assert stalls == [] and looping == []


def test_datamover_step_open_past_the_threshold_is_a_stall():
    """10/21: NEWSTODAY102226's Mediainfo step sat from 19:37 until 04:48."""
    stalls, _ = classify([_job(MM_DM, INGEST_STALL_MIN)])
    assert len(stalls) == 1
    assert stalls[0]["worker"] == "Datamover (EtereMM9)"
    assert stalls[0]["threshold"] == INGEST_STALL_MIN


def test_ingest_step_just_under_the_threshold_is_quiet():
    stalls, _ = classify([_job(MM_S3, INGEST_STALL_MIN - 0.5)])
    assert stalls == []


def test_every_non_wait_ingest_step_counts_not_only_the_datamovers():
    """10/23: 95 ingests cleared only when EtereRC was restarted; a SQL step that never
    completes is just as stuck."""
    stalls, _ = classify([_job(FSDEL, 30), _job(SCME, 30)])
    assert [s["worker"] for s in stalls] == ["Etere RC (EtereRC)", "SQL workflow engine"]


def test_restore_download_tolerates_the_0600_burst_but_not_a_dead_datamover():
    burst = _job(MM_S3_RESTORE, 13.2, cls=18, code="THEONEFANFAV003-102026F")
    dead = _job(MM_S3_RESTORE, RESTORE_STALL_MIN, cls=18, code="THEONEFANFAV003-102026F")
    assert classify([burst])[0] == []
    stalls, _ = classify([dead])
    assert len(stalls) == 1 and stalls[0]["threshold"] == RESTORE_STALL_MIN


def test_restore_non_datamover_steps_and_deletes_are_ignored():
    # "Pick File on CIB1" (g.wiz) sits in state 1 for a looping restore; deletes wait a day.
    jobs = [
        _job("g.wiz.scm-619f8c6a", 600, cls=18, start_cnt=1),
        _job("mm-dl-aaaa", 600, cls=22, start_cnt=1),  # Delete on CIB1
        _job("sch-schedule-xyz", 6000, cls=17, start_cnt=1),
    ]
    assert classify(jobs) == ([], [])


def test_looping_instances_are_counted_never_alarmed():
    """V-TOPNEWS071725C (563 restarts) and HMONG-REPORT031625F/G (712) sit in 'Search and
    set File' / 'Video file not found' forever; the SQL Agent restarts them every 20 h."""
    zombie = _job(SCME, 800, start_cnt=563, code="V-TOPNEWS071725C")
    fresh = _job(MM_DM, 40, start_cnt=2)  # restarted once by hand, then stuck again
    stalls, looping = classify([zombie, fresh])
    assert [s["code"] for s in stalls] == ["NEWSTODAY102226"]
    assert [j["code"] for j in looping] == ["V-TOPNEWS071725C"]
    assert classify([_job(MM_DM, 40, start_cnt=LOOP_RESTARTS - 1)])[0], "4 restarts is fresh"


def test_stalls_sorted_oldest_first():
    stalls, _ = classify([_job(MM_DM, 25, code="B"), _job(MM_DM, 90, code="A")])
    assert [s["code"] for s in stalls] == ["A", "B"]


def test_worker_labels():
    assert worker_for(MM_S3) == "Datamover (EtereMM9)"
    assert worker_for("a.wks") == "Active Sync (EtereActivesync)"
    assert worker_for("af.wiz.5.smptch") == "SQL workflow engine"


def test_report_names_the_runbook_and_the_loopers():
    stalls, looping = classify([_job(MM_DM, 65), _job(SCME, 800, start_cnt=712, code="HMONG")])
    result = {
        "count": 1,
        "oldest_min": 65,
        "workers": ["Datamover (EtereMM9)"],
        "stalls": stalls,
        "looping": {"count": 1, "codes": ["HMONG"]},
        "open_jobs": 2,
        "thresholds": {"ingest_min": 20, "restore_min": 45},
    }
    text = format_report(result)
    assert "NEWSTODAY102226" in text and "sign out" in text and "HMONG" in text
    assert "not alarming" in text
