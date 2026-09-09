"""Media integrity rules (2026-09-07 TheOne090726B freeze).

The bad piece B was 36,962,304 bytes for 55,176 frames (670 B/frame) on all six playout
copies; healthy house MP4s sit at ~38,500. Proxy copies never reach `classify` (excluded
by device in `scan`), so a legitimate H264 proxy must not be simulated here as a playout copy.
"""

from business_logic.services.media_integrity import (
    MIN_BYTES_PER_FRAME,
    SIBLING_RATIO,
    classify,
    format_report,
    hms,
    sibling_key,
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


# TOPNEWS090826A (2026-09-08 freeze): 241,696,768 bytes for 10,792 frames = 22,396 B/frame;
# its batch siblings B and C ran 38,490 and 38,535. The absolute floor passed it.
TOPNEWS_A_FRAMES = 10_792
TOPNEWS_A_BYTES = 241_696_768
TOPNEWS_SIBLINGS = [38_490.0, 38_535.0]


def test_partial_truncation_clears_the_absolute_floor_alone():
    # Why the 9/8 scan said 0 findings: 22,396 > 12,000. Kept as the record of the miss.
    assert 22_396 > MIN_BYTES_PER_FRAME
    assert classify(TOPNEWS_A_FRAMES, _copies(TOPNEWS_A_BYTES, devs=["AWS S3 Bucket"])) == []


def test_partial_truncation_is_caught_against_its_siblings():
    found = classify(
        TOPNEWS_A_FRAMES, _copies(TOPNEWS_A_BYTES, devs=["AWS S3 Bucket"]), TOPNEWS_SIBLINGS
    )
    assert [k for k, _ in found] == ["truncated"]
    assert "58%" in found[0][1] and "2 sibling" in found[0][1]


def test_full_size_piece_is_clean_against_its_siblings():
    # V-TOPNEWS090826r1A, the re-export: 415,785,882 bytes = 38,527 B/frame.
    assert classify(TOPNEWS_A_FRAMES, _copies(415_785_882), TOPNEWS_SIBLINGS) == []


def test_sibling_rule_uses_the_best_copy_not_a_partial_cib_transfer():
    # A half-copied CIB file is an 'inconsistent' finding, not a truncation of the export.
    copies = _copies(415_785_882) + [{"device": "CIB6", "size": 200_000_000, "codec": "MP4"}]
    found = classify(TOPNEWS_A_FRAMES, copies, TOPNEWS_SIBLINGS)
    assert [k for k, _ in found] == ["inconsistent"]


def test_sibling_rule_reports_once_when_the_floor_would_also_fire():
    found = classify(55176, _copies(36_962_304), [38_530.0, 38_520.0, 38_540.0])
    assert [k for k, _ in found] == ["truncated"]
    assert "sibling" in found[0][1]


def test_sibling_ratio_is_below_every_legit_ratio_seen():
    # Backtest 7/1–9/8: every healthy piece sat at 1.00 of its batch median; the three
    # truncations sat at 0.02, 0.05 and 0.58.
    assert 0.58 < SIBLING_RATIO < 1.0


def test_sibling_key_strips_piece_letter_and_reexport_tag():
    assert sibling_key("TOPNEWS090826A") == "TOPNEWS090826"
    assert sibling_key("V-TOPNEWS090826R1A") == "V-TOPNEWS090826"
    assert sibling_key("THEONE090726r1B") == "THEONE090726"
    assert sibling_key("PH-NEWSEXPRESSP1-090826C") == "PH-NEWSEXPRESSP1-090826"
    assert sibling_key("THEMUSICPROJECT229-091526F") == "THEMUSICPROJECT229-091526"


def test_sibling_key_is_none_for_codes_without_a_piece_letter():
    # Commercials, PSAs and IDs have no batch — only the absolute floor applies.
    assert sibling_key("SCV15K28") is None
    assert sibling_key("LACARE30K13") is None
    assert sibling_key("ID - NEW - GENERIC") is None
    assert sibling_key("") is None


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
