"""Ghost-spot scan summary (2026-09-09: 783 4imprint :30 rows with no contract aired double)."""

from business_logic.services.ghost_spots import GHOST_WHERE, format_report, hms, summarize


def _row(title, market, date, time, tid=1, code="AH2-233XXA6LH"):
    return {
        "id_tpalinse": tid,
        "market": market,
        "date": date,
        "time": time,
        "code": code,
        "title": title,
        "dur_s": 30,
        "status": "I",
    }


def test_summarize_groups_by_creative_most_first_with_first_airing():
    rows = [
        _row("4IMPRINT30E59: Pants V3 30", "NYC", "2026-09-10", "08:11:05", 1),
        _row("4IMPRINT30E59: Pants V3 30", "WDC", "2026-09-10", "07:59:13", 2),
        _row("4IMPRINT30E59: Pants V3 30", "NYC", "2026-09-11", "19:37:19", 3),
        _row("REDFIN15E07: OW Animation", "CVC", "2026-09-09", "12:00:00", 4),
    ]
    out = summarize(rows)
    assert [g["title"] for g in out] == ["4IMPRINT30E59: Pants V3 30", "REDFIN15E07: OW Animation"]
    assert out[0]["count"] == 3
    assert out[0]["markets"] == "NYC 2, WDC 1"
    assert out[0]["first"] == {"market": "WDC", "date": "2026-09-10", "time": "07:59:13"}


def test_summarize_empty():
    assert summarize([]) == []


def test_ghost_where_excludes_fillers_and_soft_deleted_rows():
    # PER/PSA fillers legitimately have no trafficPalinse; only COM rows are ghosts.
    assert "NEWTYPE = 'COM'" in GHOST_WHERE
    assert "LIVELLO = 0" in GHOST_WHERE
    assert "tp.id_trafficPalinse IS NULL" in GHOST_WHERE


def test_hms_is_broadcast_frames():
    assert hms(0) == "00:00:00"
    assert hms(89910) == "00:50:00"  # 3000 s × 29.97 exactly
    assert hms(89910 + 30) == "00:50:01"


def test_report_clean_and_alert():
    assert "clean" in format_report({"count": 0, "by_title": [], "rows": []})
    rows = [_row("4IMPRINT30E59: Pants V3 30", "NYC", "2026-09-10", "08:11:05", 15575366)]
    rep = format_report({"count": 1, "by_title": summarize(rows), "rows": rows})
    assert "1 future ghost spot" in rep and "id_tpalinse=15575366" in rep and "UNBILLED" in rep
