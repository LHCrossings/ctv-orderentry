"""H&L traffic instructions — a row's metadata can sit on EITHER side of its ISCI.

HL prints these tables two ways, and the August 2026 order (#13935 R1) is the
first one we've seen use the second:

  A  ISCI-first — `TYRN41271H <title> :30 ACM TV (Cantonese) :30 100% 8/4 8/11`
     (or the ":30 100% dates" on the following line)

  B  wrapped — the title is long enough to take the whole line, so the dates
     print ABOVE the ISCI and the ISCI keeps only its dialect:
         2026 August National Sales Event … :30 100% 8/12/26 8/31/26 @ 12P
         TYRN43021H (Cantonese)
     …and when even the dialect won't fit, the ISCI sits alone on its line:
         TYRN43031H
         (Mandarin)

Layout B produced three defects at once, reported by Maija as "Hindi doesn't show
the date, and Cantonese 8/12–8/31 is missing":

  1. `TYRN43031H` (Mandarin) vanished — a bare ISCI line didn't start a block, so
     it was swallowed as body text of the ISCI above it.
  2. `TYRN43021H` was tagged **Mandarin** — its block had swollen to include the
     next row's "(Mandarin)", and the dialect was the LAST "(Word)" in the block.
     That is the dangerous one: the Cantonese cut would have aired in Mandarin
     programming on 34 spots.
  3. `TYRN43051H` (Hindi) got no dates — its block ran past the end of the table
     into "Link to spots:", and its own dates were on the line above.
"""

import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

FIXTURES = _root / "tests" / "fixtures" / "hl_traffic"
WRAPPED = FIXTURES / "toyota_13935_r1_wrapped.pdf"  # layout B (the bug)
ISCI_FIRST = FIXTURES / "toyota_13933_isci_first.pdf"  # layout A (regression)


@pytest.fixture(scope="module", autouse=True)
def _use_real_pdfplumber(real_pdfplumber):
    return real_pdfplumber


def _parse(path):
    from browser_automation.parsers.hl_traffic_parser import parse_hl_traffic_pdf

    return parse_hl_traffic_pdf(path.read_bytes())


@pytest.fixture(scope="module")
def wrapped():
    return _parse(WRAPPED)


# ── Layout B: the reported bug ───────────────────────────────────────────────


def test_both_flights_are_parsed_for_every_dialect(wrapped):
    """4 dialects × 2 flights = 8 spots. The parse returned 7."""
    assert len(wrapped.spots) == 8
    by_isci = {s.isci: s for s in wrapped.spots}
    assert sorted(by_isci) == [
        "TYRN41271H",
        "TYRN41281H",
        "TYRN41291H",
        "TYRN41301H",
        "TYRN43021H",
        "TYRN43031H",
        "TYRN43041H",
        "TYRN43051H",
    ]


def test_a_bare_isci_line_still_starts_a_spot(wrapped):
    """TYRN43031H prints alone on its line, with "(Mandarin)" underneath."""
    m = {s.isci: s for s in wrapped.spots}["TYRN43031H"]
    assert m.dialect == "Mandarin"
    assert (m.date_from_sql, m.date_to_sql) == ("2026-08-12", "2026-08-31")


def test_each_isci_keeps_its_own_dialect(wrapped):
    """The whole point: a creative must never be tagged with its neighbour's
    language, or the wrong-language cut airs."""
    got = {s.isci: s.dialect for s in wrapped.spots}
    assert got == {
        "TYRN41271H": "Cantonese",
        "TYRN41281H": "Mandarin",
        "TYRN41291H": "Tagalog",
        "TYRN41301H": "Hindi",
        "TYRN43021H": "Cantonese",
        "TYRN43031H": "Mandarin",
        "TYRN43041H": "Tagalog",
        "TYRN43051H": "Hindi",
    }


def test_the_last_row_of_a_table_gets_its_dates(wrapped):
    """TYRN43051H is followed by "Link to spots:" — an end-of-table marker the
    old pattern missed (it only knew "Link to new spots")."""
    h = {s.isci: s for s in wrapped.spots}["TYRN43051H"]
    assert (h.date_from_sql, h.date_to_sql) == ("2026-08-12", "2026-08-31")


def test_dialects_normalise_to_the_language_window_names(wrapped):
    got = {s.isci: s.system_dialect for s in wrapped.spots}
    assert got["TYRN43041H"] == "Filipino"  # Tagalog →
    assert got["TYRN43051H"] == "SouthAsian"  # Hindi →
    assert got["TYRN43021H"] == "Cantonese"


def test_each_flight_has_its_own_window_never_the_headers(wrapped):
    """Per-spot dates drive assignment; the header's 8/4–8/31 spans both flights
    and must never leak onto a spot."""
    windows = {(s.date_from_sql, s.date_to_sql) for s in wrapped.spots}
    assert windows == {("2026-08-04", "2026-08-11"), ("2026-08-12", "2026-08-31")}
    assert (wrapped.start_date, wrapped.end_date) == ("8/4/26", "8/31/26")


def test_titles_are_recovered_from_the_line_above(wrapped):
    """In layout B the printed title is above the ISCI, so line 1 is just
    "(Cantonese)" — which used to be stored as the title."""
    for s in wrapped.spots:
        assert not s.title.startswith("("), f"{s.isci} title is a dialect: {s.title!r}"
    t = {s.isci: s.title for s in wrapped.spots}
    assert t["TYRN43021H"].startswith("2026 August National Sales Event")
    assert t["TYRN41271H"].startswith("2026 Hybrid Leadership")


def test_header_fields(wrapped):
    assert wrapped.estimate == "13935"
    assert wrapped.advertiser == "NorCal Toyota Dealers"
    assert all(s.duration_sec == 30 for s in wrapped.spots)
    assert all(s.rotation_pct == 100.0 for s in wrapped.spots)


# ── Layout A: unchanged ──────────────────────────────────────────────────────


def test_isci_first_layout_is_unaffected():
    """The June #13933 instruction must parse exactly as before the fix."""
    o = _parse(ISCI_FIRST)
    assert o.estimate == "13933"
    assert len(o.spots) == 4
    assert {s.isci: s.dialect for s in o.spots} == {
        "TYRN39271H": "Cantonese",
        "TYRN39281H": "Mandarin",
        "TYRN39291H": "Hindi",
        "TYRN39301H": "Tagalog",
    }
    assert {(s.date_from_sql, s.date_to_sql) for s in o.spots} == {("2026-06-02", "2026-06-21")}


# ── Unit-level guards on the helpers ─────────────────────────────────────────


def test_a_parenthesised_non_language_is_never_read_as_the_dialect():
    """Titles carry parentheses too. Only a known dialect may win, else a title
    like "(Non Offer)" becomes the spot's language."""
    from browser_automation.parsers.hl_traffic_parser import _resolve_dialect

    block = {"idx": 5, "rest": "(Non Offer) Spring Update", "after": [(6, "(Cantonese)")]}
    assert _resolve_dialect(block, []) == "Cantonese"


def test_dialect_search_never_runs_backwards_into_another_row():
    """Only the ISCI's own line and the lines below it — the reverse scan is what
    mislabelled the Cantonese cut."""
    from browser_automation.parsers.hl_traffic_parser import _resolve_dialect

    block = {"idx": 9, "rest": "(Cantonese)", "after": []}
    assert _resolve_dialect(block, ["(Mandarin)"] * 9) == "Cantonese"


def test_a_metadata_line_is_claimed_by_only_one_isci():
    """Layout B resolves every ISCI from the line ABOVE it. If a line could be
    claimed twice, a PDF whose flights differ per dialect would silently give
    each spot its neighbour's window."""
    from browser_automation.parsers.hl_traffic_parser import _resolve_dates

    lines = [
        "Title A :30 100% 8/12/26 8/31/26",
        "TYRN01A (Cantonese)",
        "Title B :30 100% 9/01/26 9/30/26",
        "TYRN02B (Mandarin)",
    ]
    claimed = set()
    a = _resolve_dates({"idx": 1, "rest": "(Cantonese)", "after": [(2, lines[2])]}, lines, claimed)
    b = _resolve_dates({"idx": 3, "rest": "(Mandarin)", "after": []}, lines, claimed)
    assert a == ("8/12/26", "8/31/26"), "row A must take the line above it"
    assert b == ("9/01/26", "9/30/26"), "row B must not inherit row A's window"


def test_a_trailing_time_annotation_does_not_shift_the_window():
    from browser_automation.parsers.hl_traffic_parser import _date_pair

    assert _date_pair(":30 100% 8/12/26 8/31/26 @ 12P") == ("8/12/26", "8/31/26")
    assert _date_pair(":30 100% 8/12/26") is None
