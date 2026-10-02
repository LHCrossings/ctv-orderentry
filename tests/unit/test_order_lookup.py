"""Plain-English order summary for /reports/order-lookup (pure function)."""

from datetime import date

from business_logic.services.order_lookup import language_codes_for, summarize

TODAY = date(2026, 10, 2)


def _hdr(**kw):
    base = {
        "id": 3131,
        "code": "Admerasia McD 6SF 2610",
        "description": "McDonald's Est 6 SFO 2610-2611",
        "customer": "McDonald's",
        "agency": "Admerasia Inc.",
        "ae": "Charmaine Lane",
        "date_start": "10/10/2026",
        "date_end": "11/01/2026",
        "first_air": "10/10/2026",
        "last_air": "11/01/2026",
        "first_unassigned": None,
    }
    base.update(kw)
    return base


def _line(ordered, scheduled, with_creative, aired=0, desc="S-U 11:00a-12:00p", bonus=False):
    return {
        "description": desc,
        "market": "SFO",
        "language": "V",
        "ordered": ordered,
        "scheduled": scheduled,
        "with_creative": with_creative,
        "aired": aired,
        "is_bonus": bonus,
    }


def test_fully_entered_scheduled_and_assigned():
    lines = [_line(8, 8, 8), _line(3, 3, 3, desc="M,W,F 12:00p-1:00p")]
    creatives = [{"isci": "MCIV011326VH"}, {"isci": "MCIV122526VH"}]
    s = summarize(_hdr(), lines, creatives, today=TODAY)
    text = " ".join(s["sentences"])
    assert text.startswith(
        "Order Admerasia McD 6SF 2610 (McDonald's Est 6 SFO 2610-2611) for McDonald's via Admerasia Inc. is entered in Etere."
    )
    assert "It runs 10/10/26 to 11/1/26 in SFO (Vietnamese), sold by Charmaine Lane." in text
    assert "All 11 ordered spots are scheduled on the log." in text
    assert "Every scheduled spot has a creative assigned; the 2 creatives are listed below." in text
    assert "Nothing has aired yet; the first spot is scheduled for Sat 10/10/26." in text
    assert s["scheduled_state"] == "full" and s["creative_state"] == "full"
    assert s["languages"] == ["Vietnamese"]


def test_partially_scheduled_names_the_short_lines():
    lines = [_line(8, 8, 8), _line(6, 2, 2, desc="M-F 10a-11a Vietnamese")]
    s = summarize(_hdr(), lines, [{"isci": "X"}], today=TODAY)
    text = " ".join(s["sentences"])
    assert (
        "10 of 14 ordered spots are scheduled on the log; 4 are not placed yet (lines: M-F 10a-11a Vietnamese)."
        in text
    )
    assert s["scheduled_state"] == "partial"


def test_nothing_scheduled_yet():
    s = summarize(_hdr(), [_line(8, 0, 0)], [], today=TODAY)
    text = " ".join(s["sentences"])
    assert "None of the 8 ordered spots are scheduled on the log yet." in text
    assert s["scheduled_state"] == "none" and s["creative_state"] == "na"
    assert "creative" not in text.lower()
    assert "aired" not in text.lower()


def test_creatives_partially_assigned_with_first_unassigned_day():
    lines = [_line(12, 12, 12), _line(18, 18, 2, desc="Filipino 4p-6p [SC1115]")]
    s = summarize(
        _hdr(first_unassigned="10/12/2026"), lines, [{"isci": "A"}, {"isci": "B"}], today=TODAY
    )
    text = " ".join(s["sentences"])
    assert (
        "14 of 30 scheduled spots have a creative assigned; 16 still need one (first unassigned spot Mon 10/12/26)."
        in text
    )
    assert s["creative_state"] == "partial"


def test_no_creative_assigned_at_all():
    s = summarize(_hdr(), [_line(8, 8, 0)], [], today=TODAY)
    assert "No creative is assigned to the scheduled spots yet." in " ".join(s["sentences"])
    assert s["creative_state"] == "none"


def test_bonus_counted_and_named():
    lines = [_line(8, 8, 8), _line(4, 4, 4, desc="BNS ROS", bonus=True)]
    text = " ".join(summarize(_hdr(), lines, [{"isci": "A"}], today=TODAY)["sentences"])
    assert "All 12 ordered spots (including 4 bonus) are scheduled on the log." in text


def test_finished_flight_reports_aired():
    hdr = _hdr(
        date_start="10/06/2025",
        date_end="11/02/2025",
        first_air="10/06/2025",
        last_air="11/02/2025",
    )
    s = summarize(hdr, [_line(23, 23, 23, aired=23)], [{"isci": "A"}], today=TODAY)
    assert (
        "The flight has ended; 23 of 23 scheduled spots aired (last on Sun 11/2/25)."
        in " ".join(s["sentences"])
    )


def test_mid_flight_reports_aired_so_far():
    hdr = _hdr(date_start="09/28/2026", first_air="09/28/2026", last_air="10/25/2026")
    s = summarize(hdr, [_line(20, 20, 20, aired=5)], [{"isci": "A"}], today=TODAY)
    assert "5 spots have aired so far; the last is scheduled for Sun 10/25/26." in " ".join(
        s["sentences"]
    )


def test_single_creative_wording():
    text = " ".join(summarize(_hdr(), [_line(4, 4, 4)], [{"isci": "A"}], today=TODAY)["sentences"])
    assert "the creative is listed below" in text


def test_language_search_words():
    assert language_codes_for("vietnamese") == {"V"}
    assert language_codes_for("viet") == {"V"}
    assert language_codes_for("chinese") == {"M", "C", "M/C"}
    assert language_codes_for("mandarin") == {"M", "M/C"}
    assert language_codes_for("hmong") == {"Hm"}
    assert language_codes_for("filipino") == {"T"} and language_codes_for("tagalog") == {"T"}
    assert language_codes_for("mcd") == set()  # an advertiser word is not a language
    assert language_codes_for("sf") == set()  # too short
