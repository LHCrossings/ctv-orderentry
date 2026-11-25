"""Resorts World proposal: the Translations row is production, not a skipped row.

Contract 3204 (Oct-Dec 2026) entered without its $500 because the parser listed
'translation' among the footer rows to skip (Lee 2026-11-25). The row's Proposed
budget cell is the charge; airtime + production must equal the sheet's own
"Contract" figure. Fixtures: the real May-June 2026 proposal, PDF and xlsx.
"""

import inspect
import sys
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation import rwny_automation  # noqa: E402
from browser_automation.parsers import rwny_parser as rp  # noqa: E402

FIX = _root / "tests" / "fixtures" / "rwny"


@pytest.fixture(autouse=True)
def _pdf(real_pdfplumber, monkeypatch):
    monkeypatch.setitem(sys.modules, "pdfplumber", real_pdfplumber)


@pytest.mark.parametrize("name", ["rwny_may_june_2026.pdf", "rwny_may_june_2026.xlsx"])
def test_translations_row_is_production_and_the_sheet_foots(name):
    (order,) = rp.parse_rwny_pdf(str(FIX / name))
    assert len(order.lines) == 6  # the airtime lines are unchanged: 4 paid + 2 bonus
    assert sum(ln.total_spots for ln in order.lines) == 104
    assert order.airtime_total == 5500.0
    assert order.production == 500.0  # the discounted amount, not the $850 retail value
    assert order.contract_total == 6000.0


def test_money_cells_read_the_pdf_spacing():
    assert rp._money_cell("$ 5 00.00") == 500.0
    assert rp._money_cell("$ 2 ,400.00") == 2400.0
    assert rp._money_cell(500) == 500.0
    assert rp._money_cell("") is None and rp._money_cell(None) is None


def test_a_sheet_that_does_not_foot_refuses():
    (order,) = rp.parse_rwny_pdf(str(FIX / "rwny_may_june_2026.xlsx"))
    order.production = 0.0  # the old behaviour: the row skipped
    with pytest.raises(ValueError, match="does not foot"):
        rp._reconcile(order)


def test_translations_row_with_no_amount_refuses():
    with pytest.raises(ValueError, match="Translations row"):
        rp._production_from_row(["Translations ( 2 languages)", None, None], 2)


def test_a_sheet_with_no_contract_figure_still_parses():
    (order,) = rp.parse_rwny_pdf(str(FIX / "rwny_may_june_2026.xlsx"))
    order.contract_total = None
    assert rp._reconcile(order) is order


def test_automation_puts_production_in_the_production_box_and_verifies_it():
    src = inspect.getsource(rwny_automation._create_rwny_contract_direct)
    assert "production_cost=prod" in src
    assert "verify_production_charge" in src
    assert "production_pending" in src and "rolling back" in src
