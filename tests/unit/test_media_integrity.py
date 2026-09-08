"""Media integrity rules (2026-09-07 TheOne090726B freeze).

The bad piece B was 36,962,304 bytes for 55,176 frames (670 B/frame) on all six playout
copies; healthy house MP4s sit at ~38,500. Proxy copies never reach `classify` (excluded
by device in `scan`), so a legitimate H264 proxy must not be simulated here as a playout copy.
"""

from business_logic.services.media_integrity import (
    MIN_BYTES_PER_FRAME,
    classify,
    format_report,
    hms,
)

DEVS = ["AWS S3 Bucket", "CIB1", "CIB3", "CIB4", "CIB5", "CIB6"]


def _copies(size, codec="MP4", devs=DEVS):
    return [{"device": d, "size": size, "codec": codec} for d in devs]


def test_theone_piece_b_is_truncated():
    found = classify(55176, _copies(36_962_304))
    assert [k for k, _ in found] == ["truncated"]
    assert "670 B/frame" in found[0][1]
    assert "CIB1" in found[0][1]


def test_healthy_house_file_is_clean():
    # TheOne090726A: 1,938,824,636 bytes / 50,321 frames = 38,529 B/frame
    assert classify(50321, _copies(1_938_824_636)) == []


def test_lowest_legitimate_file_seen_is_clean():
    # TOPNEWS090826A ran at 22,396 B/frame on 2026-09-07 and aired fine.
    assert classify(360 * 30, _copies(22_396 * 360 * 30)) == []
    assert 22_396 > MIN_BYTES_PER_FRAME


def test_partial_copy_on_one_cib_is_inconsistent():
    copies = _copies(1_650_677_904)
    copies[2]["size"] = 900_000_000  # CIB3 got half the file
    found = classify(42842, copies)
    assert [k for k, _ in found] == ["inconsistent"]
    assert "CIB3" in found[0][1] and "900,000,000" in found[0][1]


def test_unsized_copies_are_not_evidence():
    copies = _copies(1_650_677_904)
    copies[1]["size"] = 0  # CIB1 has not stamped the file yet
    assert classify(42842, copies) == []
    assert classify(42842, _copies(0)) == []


def test_different_codecs_are_compared_separately():
    copies = _copies(1_650_677_904) + [{"device": "CIB6", "size": 700_000_000, "codec": "MPEG-PS"}]
    assert classify(42842, copies) == []


def test_zero_duration_is_skipped():
    assert classify(0, _copies(10)) == []


def test_report_names_file_rule_and_first_airing():
    result = {
        "from": "2026-09-08",
        "to": "2026-09-10",
        "assets": 570,
        "checked_at": "2026-09-08T03:00:00",
        "findings": [
            {
                "id_filmati": 148403,
                "code": "THEONE090726B",
                "newtype": "PGM",
                "durata": 55176,
                "kind": "truncated",
                "detail": "36,962,304 bytes ...",
                "copies": [],
                "airings": [
                    {"market": "DAL", "date": "2026-09-08", "time": "08:29", "status": "I"}
                ],
            }
        ],
    }
    text = format_report(result)
    assert "TRUNCATED" in text and "THEONE090726B" in text and "airs DAL 2026-09-08 08:29" in text
    assert hms(55176) == "00:30:41"
