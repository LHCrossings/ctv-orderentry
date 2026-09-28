"""Polaris parser tests — one order type, two readers.

Driven by the two real workbooks committed as fixtures: the April 2026 Prop C
positional insertion order and the September 2026 Affordable Santa Clara "AAPI
TV Schedule" (Charmaine's template, columns mapped by label). Each carries its
own totals, so it is its own oracle.

The negative tests TAMPER with a mutated workbook copy rather than asserting
guards exist — a blanked rate, a spot that disagrees with its week cell, a
renamed column, a wrong budget, a missing DMA row and a second week column
must each refuse, and a no-op mutation must still parse (or the negative
tests pass for the wrong reason).
"""

import sys
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

_root = Path(__file__).parent.parent.parent
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation import polaris_automation  # noqa: E402
from browser_automation.parsers import polaris_parser  # noqa: E402
from browser_automation.parsers.polaris_parser import (  # noqa: E402
    _parse_positional_rows,
    _schedule_header_index,
    parse_polaris_file,
    parse_polaris_xlsx,
)
from browser_automation.polaris_automation import _default_names, _learn_prefixes  # noqa: E402

FIX = _root / "tests" / "fixtures" / "polaris"
SEPT = str(FIX / "affordable-santa-clara-2026-09-29.xlsx")
APRIL = str(FIX / "prop-c-2026-04-21.xlsx")
LONG_NAME = (
    "Affordable Santa Clara Supporting Ferraris and Opposing Wantanabe and Kertes "
    "for Mayor 2026, Sponsored by Forty Niners Football Company LLC"
)


@pytest.fixture(scope="module")
def sept():
    return parse_polaris_xlsx(SEPT)


@pytest.fixture(scope="module")
def april():
    return parse_polaris_xlsx(APRIL)


# ── AAPI TV Schedule (Sept 2026) ─────────────────────────────────────────────


def test_sept_header(sept):
    assert sept.advertiser == LONG_NAME
    assert (sept.flight_start, sept.flight_end) == ("9/29/2026", "10/5/2026")  # year cell J9
    assert sept.gross_budget == Decimal("4276.00")
    assert sept.markets == ["SFO"]  # the Cantonese/LA block is all zeros


def test_sept_lines(sept):
    got = [
        (ln.days, ln.time_str, ln.program, ln.rate, ln.total_spots, ln.duration)
        for ln in sept.lines
    ]
    assert got == [
        ("M-F", "6a-7a", "Mandarin News", Decimal("120.00"), 5, 30),
        ("M-F", "7a-8a", "Mandarin Education", Decimal("120.00"), 3, 30),
        ("M-F", "8p-9p", "Mandarin News", Decimal("212.00"), 5, 30),
        ("M-F", "9p-10p", "Mandarin Drama", Decimal("212.00"), 5, 30),
        ("M-F", "10p-11:30p", "Mandarin Entertainment", Decimal("212.00"), 3, 30),
        ("Sa-Su", "8p-9p", "Mandarin News", Decimal("140.00"), 2, 30),
        ("Sa-Su", "9p-12a", "Mandarin Entertainment", Decimal("140.00"), 2, 30),
    ]
    assert sept.lines[0].get_description() == "M-F Mandarin News"
    assert not any(ln.is_bonus for ln in sept.lines)


def test_sept_reconciles_dollars_and_flags_the_sheets_spot_total(sept):
    assert sum(ln.rate * ln.total_spots for ln in sept.lines) == sept.gross_budget
    assert sept.total_spots == 25
    # Charmaine's Total row is `=SUM(J11:J17)` → 13 of 25; dollars are the oracle,
    # the disagreement is surfaced, never silently fixed or silently ignored.
    assert len(sept.warnings) == 1
    assert "13 spots" in sept.warnings[0] and "25" in sept.warnings[0]


# ── Positional insertion order (April 2026) stays byte-identical ─────────────


def test_april_routes_to_positional_reader(april):
    assert _schedule_header_index([]) is None
    wb = openpyxl.load_workbook(APRIL, data_only=True)
    rows = list(wb["Crossings TV"].iter_rows(values_only=True))
    wb.close()
    assert _schedule_header_index(rows) is None
    direct = _parse_positional_rows(rows, APRIL)
    assert direct == april
    assert len(april.lines) == 10 and april.total_spots == 26
    assert april.gross_budget == Decimal("4100.00")
    assert april.flight_start.startswith("4/21/") and april.flight_end.startswith("4/28/")
    assert [ln.duration for ln in april.lines] == [30] * 10  # default keeps the old shape
    assert april.warnings == []


def test_dispatcher_routes_pdf_to_pdf_reader(monkeypatch):
    seen = {}
    monkeypatch.setattr(polaris_parser, "parse_polaris_pdf", lambda p: seen.setdefault("p", p))
    parse_polaris_file("/some/order.pdf")
    assert seen["p"] == "/some/order.pdf"


def test_scanner_content_detection_routes_to_polaris():
    from domain.enums import OrderType
    from orchestration.order_scanner import _detect_xlsx_content

    assert _detect_xlsx_content(Path(SEPT)) == OrderType.POLARIS
    assert _detect_xlsx_content(Path(APRIL)) == OrderType.POLARIS


# ── Tampering ────────────────────────────────────────────────────────────────


def _mutated(tmp_path, mutate):
    """A values-only copy of the Sept sheet (formulas replaced by their cached
    values, since nothing outside Excel evaluates them), with one mutation."""
    cached = openpyxl.load_workbook(SEPT, data_only=True)["Crossings TV"]
    wb = openpyxl.load_workbook(SEPT)
    ws = wb["Crossings TV"]
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("="):
                c.value = cached[c.coordinate].value
    mutate(ws)
    out = tmp_path / "mutated.xlsx"
    wb.save(str(out))
    return str(out)


def _set(**cells):
    def mut(ws):
        for k, v in cells.items():
            ws[k] = v

    return mut


def test_noop_mutation_still_parses(tmp_path, sept):
    o = parse_polaris_xlsx(_mutated(tmp_path, lambda ws: None))
    assert o == sept


def test_blanked_rate_refuses(tmp_path):
    with pytest.raises(ValueError, match="unreadable GROSS Rate"):
        parse_polaris_xlsx(_mutated(tmp_path, _set(K15=None)))


def test_spot_disagreeing_with_week_cell_refuses(tmp_path):
    with pytest.raises(ValueError, match="week column says 4 but # Spot says 5"):
        parse_polaris_xlsx(_mutated(tmp_path, _set(I15=4)))


def test_cost_not_matching_rate_times_spots_refuses(tmp_path):
    with pytest.raises(ValueError, match="TOTAL GROSS COST reads \\$601.00"):
        parse_polaris_xlsx(_mutated(tmp_path, _set(M15=601)))


def test_renamed_column_refuses(tmp_path):
    with pytest.raises(ValueError, match="column header\\(s\\) not found: gross_rate"):
        parse_polaris_xlsx(_mutated(tmp_path, _set(K10="Rate")))


def test_wrong_budget_refuses(tmp_path):
    with pytest.raises(ValueError, match="TOTAL GROSS BUDGET is \\$4000.00"):
        parse_polaris_xlsx(_mutated(tmp_path, _set(D7=4000)))


def test_missing_dma_row_refuses(tmp_path):
    with pytest.raises(ValueError, match="no 'DMA:' cell"):
        parse_polaris_xlsx(_mutated(tmp_path, _set(C16=None)))


def test_second_week_column_refuses(tmp_path):
    with pytest.raises(ValueError, match="exactly one week column, found 2"):
        parse_polaris_xlsx(_mutated(tmp_path, _set(N10=6)))


def test_mixed_commission_refuses(tmp_path):
    with pytest.raises(ValueError, match="NET/GROSS ratio differs"):
        parse_polaris_xlsx(_mutated(tmp_path, _set(L15=120)))


def test_la_block_with_spots_gets_its_own_market(tmp_path):
    # Cantonese News under DMA: LA gets 2 spots; every total moves with it
    o = parse_polaris_xlsx(
        _mutated(tmp_path, _set(I11=2, J11=2, M11=424, D7=4700, I24=4700, J24=15))
    )
    assert o.markets == ["LAX", "SFO"]
    assert o.lines_for_market("LAX")[0].program == "Cantonese News"
    assert o.total_spots == 27 and o.gross_budget == Decimal("4700.00")
    assert len(o.warnings) == 1  # Total row still says 15, lines hold 27


# ── Gather naming defaults (Lee 2026-09-28) ──────────────────────────────────


def test_default_names_use_weekly_start_date():
    code, desc = _default_names(
        "Polaris ASC", "Affordable Santa Clara, Supporting Ferraris", "9/29/2026", "10/5/2026"
    )
    assert code == "Polaris ASC 260929"
    assert desc == "Affordable Santa Clara, Supporting Ferraris 260929-261005"
    assert len(code) <= 32 and len(desc) <= 80
    code_m, _ = _default_names("Polaris ASC", "x", "9/29/2026", "10/5/2026", "SF")
    assert code_m == "Polaris ASC SF 260929"


def test_learned_prefixes_round_trip_through_default_names():
    """What the operator typed for 3132 (9/28) must come back as the next default."""
    code_p, desc_p = _learn_prefixes("Polaris ASC 260929", "Affordable Santa Clara 260929-261005")
    assert (code_p, desc_p) == ("Polaris ASC", "Affordable Santa Clara")
    assert _default_names(code_p, desc_p, "10/6/2026", "10/12/2026") == (
        "Polaris ASC 261006",
        "Affordable Santa Clara 261006-261012",
    )
    # market suffix in the code is stripped too; a code with no tail is kept whole
    assert _learn_prefixes("POLARIS SF 260429", "Yes on Prop C 260429-260504", "SF")[0] == "POLARIS"
    assert _learn_prefixes("Polaris ASC", "Affordable Santa Clara") == (
        "Polaris ASC",
        "Affordable Santa Clara",
    )


def test_direct_entry_puts_the_full_committee_name_in_the_header_note(sept, monkeypatch):
    """Lee 9/28 (3132): description stays the short house name, the sheet's
    130-char committee name rides in CONTRATTITESTATA.NOTE."""
    from unittest.mock import MagicMock

    import browser_automation.etere_direct_client as edc

    client = MagicMock()
    client.create_contract_header.return_value = 3132
    monkeypatch.setattr(edc, "connect", lambda: MagicMock())
    monkeypatch.setattr(edc, "EtereDirectClient", lambda *a, **k: client)

    result = polaris_automation._create_polaris_contracts_direct(
        sept,
        {
            "customer_id": "482",
            "contracts": {"SFO": {"code": "Polaris ASC 260929",
                                  "description": "Affordable Santa Clara 260929-261005"}},
            "separation": (25, 0, 0),
        },
    )
    assert result == "3132"
    kw = client.create_contract_header.call_args.kwargs
    assert kw["code"] == "Polaris ASC 260929"
    assert kw["description"] == "Affordable Santa Clara 260929-261005"
    assert kw["note"] == LONG_NAME
    assert client.add_contract_line.call_count == len(sept.lines)


# ── Web bridge ───────────────────────────────────────────────────────────────


def test_bridge_header_carries_the_parsers_warning(sept):
    from web import parser_bridge as pb

    header = pb._normalize_order(sept)
    assert header["markets"] == ["SFO"] and header["total_spots"] == 25
    assert header["total_cost"] == 4276.0
    assert any("13 spots" in w for w in header["warnings"])
    assert pb._normalize_line(sept.lines[0], 0)["duration"] == "30"
