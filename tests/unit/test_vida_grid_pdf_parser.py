"""
The Vida Agency GROSS sheet printed to PDF — WA Health Plan Finder Open
Enrollment REV2 (11/2/2026), sent by Charmaine as a PDF AND as the workbook.

The PDF is read from its ruled grid into the same rows the workbook holds and
goes through the same sheet reader, so the oracle is the workbook: both files
must parse to the same order. REV2 also adds a PAID non-airtime row ("Interview
segment (Mandarin) 4x airtime") that both forms must read as a named charge.

Negative tests mutate the extracted WORDS (the layer that fails in the wild):
a dropped week digit, a digit slid one column, an edited net, a renamed header
and a missing REVISION date each refuse, while a no-op mutation parses the same.
"""

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.parsers.vida_parser import (  # noqa: E402
    GRID_X_TOL,
    GRID_Y_TOL,
    VidaParseError,
    is_vida_grid_text,
    is_vida_text,
    parse_vida,
    parse_vida_grid_layouts,
)

_FIX = _root / "tests" / "fixtures" / "vida"
PDF = str(_FIX / "vida_wahbe_oe_2611_rev2.pdf")
XLSX = str(_FIX / "vida_wahbe_oe_2611_rev2.xlsx")
PSE_PDF = str(_FIX / "vida_pse_flex_2610_revised.pdf")


@pytest.fixture(scope="module")
def layout(real_pdfplumber):
    """([(words, rules)], [text]) of the real PDF."""
    with real_pdfplumber.open(PDF) as pdf:
        layouts = [
            (
                p.extract_words(x_tolerance=GRID_X_TOL, y_tolerance=GRID_Y_TOL),
                list(p.rects) + list(p.lines),
            )
            for p in pdf.pages
        ]
        texts = [p.extract_text() or "" for p in pdf.pages]
    return layouts, texts


@pytest.fixture(scope="module")
def order(real_pdfplumber):
    return parse_vida(PDF)


def _shape(o):
    out = [
        (o.agency, o.project, o.market_code, o.contact, o.email, o.order_date, o.length_sec),
        (o.rates_are_net, o.agency_pct, tuple(o.notes)),
        [(c.description, c.amount, c.list_amount, c.net_amount) for c in o.charges],
    ]
    for fl in o.flights:
        out.append((fl.phase, fl.start, fl.end, tuple(fl.week_dates)))
        for ln in fl.lines:
            out.append(
                (
                    ln.program,
                    ln.days,
                    ln.time_text,
                    ln.rate,
                    ln.rate_card,
                    ln.net_rate,
                    tuple(ln.week_spots),
                    ln.is_bonus,
                )
            )
    return out


def _mutated(layout, fn):
    """Parse the page after fn(words) edited a deep copy of the word list."""
    layouts, texts = layout
    words = copy.deepcopy(layouts[0][0])
    fn(words)
    return parse_vida_grid_layouts([(words, layouts[0][1])], texts, source_path=PDF)


def _word(words, text, nth=0):
    return [w for w in words if w["text"] == text][nth]


# ── The real documents ───────────────────────────────────────────────────────


def test_pdf_parses_to_the_same_order_as_the_workbook(order):
    assert _shape(order) == _shape(parse_vida(XLSX))


def test_header_phases_and_money(order):
    assert order.project == "WA Health Plan Finder Open Enrollment"
    assert order.market_code == "SEA"
    assert order.order_date == date(2026, 11, 2)
    assert order.rates_are_net is False and order.agency_pct == 15.0
    assert [(fl.phase, fl.start, fl.end) for fl in order.flights] == [
        ("Phase 1", date(2026, 11, 16), date(2026, 12, 13)),
        ("Phase 2", date(2026, 12, 14), date(2027, 1, 15)),
    ]
    # week headers print with no year: January rolls into 2027
    assert order.flights[1].week_dates[-1] == date(2027, 1, 11)
    paid = [ln for fl in order.flights for ln in fl.paid_lines]
    bonus = [ln for fl in order.flights for ln in fl.bonus_lines]
    assert sum(ln.total_spots for ln in paid) == 228
    assert sum(ln.total_spots for ln in bonus) == 134
    assert order.entered_airtime == 11500.0 and order.net_airtime == 9775.0


def test_paid_interview_row_is_a_named_charge_beside_production(order):
    assert [(c.description, c.amount, c.net_amount) for c in order.charges] == [
        ("Production", 3176.47, 2700.0),
        ("Interview segment (Mandarin) 4x airtime", 2970.59, 2525.0),
    ]
    # sheet total: airtime + both charges
    assert round(order.entered_airtime + sum(c.amount for c in order.charges), 2) == 17647.06
    assert round(order.net_airtime + sum(c.billed for c in order.charges), 2) == 15000.0
    # the $0 Vietnamese interview stays a note, never a charge
    assert order.notes == [
        "Added value Interview segment (VIETNAMESE) 4x airtime (value $3,800, $0)"
    ]


def test_merged_footer_cells_land_in_their_row(layout):
    """PAID's Value / Proposed GROSS print half a row low in a merged cell; had they
    been read as the BONUSES row's, the PAID money check would not have run."""
    with pytest.raises(VidaParseError, match="PAID Proposed GROSS"):
        _mutated(layout, lambda ws: _word(ws, "11,500.00").update(text="11,600.00"))


def test_detection_and_routing(layout):
    _layouts, texts = layout
    assert is_vida_text(texts[0])
    assert is_vida_grid_text(texts[0])


def test_pse_net_pdf_still_takes_the_text_reader(real_pdfplumber):
    with real_pdfplumber.open(PSE_PDF) as pdf:
        assert not any(is_vida_grid_text(p.extract_text() or "") for p in pdf.pages)
    pse = parse_vida(PSE_PDF)
    assert pse.rates_are_net is True and len(pse.flights) == 3


# ── Tampered words refuse; a no-op parses identically ────────────────────────


def test_noop_mutation_parses_identically(layout, order):
    same = _mutated(layout, lambda ws: _word(ws, "partnership.").update(text="partnership!"))
    assert _shape(same) == _shape(order)


def test_dropped_week_digit_refuses(layout):
    def drop(ws):
        ws.remove(next(w for w in ws if w["text"] == "5" and 140 < w["top"] < 146))

    with pytest.raises(VidaParseError, match="Total Units"):
        _mutated(layout, drop)


def test_two_cells_swapped_between_weeks_refuse_at_the_paid_footer(layout):
    """Mandarin Drama 3 3 2 2 … with weeks 2 and 3 swapped still sums to its
    Total Units; only the per-week PAID row can see it."""

    def swap(ws):
        row = sorted(
            (w for w in ws if 150 < w["top"] < 157 and 306 < w["x0"] < 571),
            key=lambda w: w["x0"],
        )
        assert [w["text"] for w in row[:4]] == ["3", "3", "2", "2"]
        row[1]["text"], row[2]["text"] = row[2]["text"], row[1]["text"]

    with pytest.raises(VidaParseError, match="'PAID' row"):
        _mutated(layout, swap)


def test_digit_slid_onto_its_neighbour_refuses(layout):
    def slide(ws):
        w = next(w for w in ws if w["text"] == "3" and 150 < w["top"] < 157)
        w["x0"] += 29.4
        w["x1"] += 29.4

    with pytest.raises(VidaParseError, match="Total Units"):
        _mutated(layout, slide)


def test_edited_net_refuses_as_a_commission_disagreement(layout):
    with pytest.raises(VidaParseError, match="commission"):
        _mutated(layout, lambda ws: _word(ws, "2,010.25").update(text="2,000.25"))


def test_edited_interview_net_refuses(layout):
    with pytest.raises(VidaParseError, match="Interview segment"):
        _mutated(layout, lambda ws: _word(ws, ",525.00").update(text=",500.00"))


def test_renamed_header_refuses_by_name(layout):
    with pytest.raises(VidaParseError, match="Total Units"):
        _mutated(layout, lambda ws: _word(ws, "Units").update(text="Spots"))


def test_missing_revision_date_refuses(layout):
    def strip(ws):
        for w in [w for w in ws if w["text"] in ("REVISION", "11/2/2026")]:
            ws.remove(w)

    with pytest.raises(VidaParseError, match="year"):
        _mutated(layout, strip)


def test_page_without_cell_borders_refuses(layout):
    layouts, texts = layout
    with pytest.raises(VidaParseError, match="ruled grid"):
        parse_vida_grid_layouts([(layouts[0][0], [])], texts, source_path=PDF)


def test_two_grid_pages_refuse(layout):
    layouts, texts = layout
    with pytest.raises(VidaParseError, match="pages 1, 2"):
        parse_vida_grid_layouts(layouts * 2, texts * 2, source_path=PDF)


# ── Entry: the paid interview is its own hidden line, never Production ───────


def _flights_in(order, start="11/20/2026"):
    return [
        {"index": 0, "contract_code": "Vida WAHBE 2611", "start_date_override": start},
        {"index": 1, "contract_code": "Vida WAHBE 2612", "start_date_override": "12/14/2026"},
    ]


def test_interview_enters_as_a_hidden_line_on_flight_1_by_default(order):
    from browser_automation.vida_automation import default_service_lines, service_charges

    assert [c.description for c in service_charges(order)] == [
        "Interview segment (Mandarin) 4x airtime"
    ]
    assert default_service_lines(order, _flights_in(order)) == [
        {
            "description": "Interview segment (Mandarin) 4x airtime",
            "amount": 2970.59,  # the sheet's gross; the 15% header nets it to $2,525
            "date": "11/20/2026",  # the ANSWERED start of flight 1, not the sheet's 11/16
            "index": 0,
        }
    ]


def test_hidden_line_date_picks_the_phase_and_must_sit_in_a_flight(order):
    from browser_automation.vida_automation import _flight_for_date

    fi = _flights_in(order)
    assert _flight_for_date(order, fi, date(2026, 12, 13)) == 0
    assert _flight_for_date(order, fi, date(2026, 12, 14)) == 1
    assert _flight_for_date(order, fi, date(2027, 1, 15)) == 1
    assert _flight_for_date(order, fi, date(2026, 11, 19)) is None  # before the answered start
    assert _flight_for_date(order, fi, date(2027, 1, 16)) is None


def test_production_box_no_longer_takes_every_charge():
    """The box took sum(order.charges); with a second charge that would have
    labelled the interview 'Production' (Lee 11/19: it cannot be)."""
    src = (_root / "browser_automation" / "vida_automation.py").read_text()
    assert "sum(ch.amount for ch in order.charges)" not in src
    assert 'ch.description == "Production"' in src
    assert "hide_from_scheduler=True" in src
