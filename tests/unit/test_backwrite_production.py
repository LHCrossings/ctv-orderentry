"""Production backwrite (Lee 2026-11-25): one PRD billing line per production charge.

Oracle: Lee's hand-built ``RWNY 2611 PRODUCTION.xlsx`` for contract 3204 ($500
translation VO, non-agency, Calendar, NYC), with his corrections: 1 x the amount,
the real weekday, ``Non-Agency`` (never "Direct"), language left blank.
"""

import io
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
import pytest

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backwrite.production import (  # noqa: E402
    ProductionCharge,
    bill_date,
    build_workbook,
    output_name,
)

RWNY = {
    "contract_id": 3204,
    "code": "RWNY 2610",
    "description": "Resorts World NY Oct-Dec 2026",
    "billing_type": "Calendar",
    "agency_flag": "Non-Agency",
    "agency_fee": 0.0,
    "agency": "",
    "client": "Resorts World New York",
    "sales_person": "Charmaine Lane",
    "contact": {"email_1": "joe.wong@rwnewyork.com", "phone": "718-215-2994"},
}
AGENCY = dict(
    RWNY,
    contract_id=3254,
    code="Vida WAHBE 2611",
    billing_type="Broadcast",
    agency_flag="Agency",
    agency_fee=0.15,
    agency="The Vida Agency",
    client="WA Health Benefit Exchange",
)
VO = ProductionCharge(date(2026, 11, 20), "Vietnamese/Tagalog Translation VO", 500.0, "NYC")


def _book(contract, charges, **kw):
    rec: dict = {}
    wb = openpyxl.load_workbook(
        io.BytesIO(build_workbook(contract, charges, validation_out=rec, **kw))
    )
    return wb, rec


def _run_rows(wb):
    ws = wb["Run Sheet"]
    head = [c.value for c in ws[1]]
    return [
        dict(zip(head, [c.value for c in r]))
        for r in ws.iter_rows(min_row=2)
        if r[0].value is not None
    ]


def test_bill_date_is_the_20th_of_the_month():
    assert bill_date(date(2026, 11, 2)) == date(2026, 11, 20)
    assert bill_date(date(2026, 12, 31)) == date(2026, 12, 20)


def test_output_name_takes_the_billing_month():
    assert output_name("RWNY 2610", date(2026, 11, 20)) == "RWNY 2611 PRODUCTION.xlsx"
    assert output_name("BVK UCDHS 4828", date(2026, 11, 20)) == "BVK UCDHS 4828 PRODUCTION.xlsx"
    assert output_name("opAD NYDOH 3180", date(2026, 11, 20)) == "opAD NYDOH 3180 PRODUCTION.xlsx"


def test_rwny_3204_matches_the_hand_built_workbook():
    wb, rec = _book(RWNY, [VO])
    assert rec["ok"] and rec["gross"]["run_sheet"] == 500.0 and rec["net"]["run_sheet"] == 500.0

    (row,) = _run_rows(wb)
    assert row["Bill Code"] == "Resorts World New York PRODUCTION"
    assert row["Air Date"] == datetime(2026, 11, 20)
    assert row["Day"] == "Friday"  # the hand-built file said Monday
    assert row["Time In"] == row["Time out"] == row["Length"] == timedelta(0)
    assert row["Media"] == "Vietnamese/Tagalog Translation VO"
    assert row["Program"] == "BILLING LINE"
    assert row["Lang."] is None
    assert row["#"] == 1
    assert row["Type"] == "PRD"
    assert row["Gross Rate"] == 500.0
    assert row["Broker Fees"] == 0
    assert row["Month"] == datetime(2026, 11, 20)  # Calendar: the row's own date
    assert row["Sales Person"] == "Charmaine Lane"
    assert row["Revenue Type"] == "Branded Content"
    assert row["Billing Type"] == "Calendar"
    assert row["Agency?"] == "Non-Agency"
    assert str(row["Contract"]) == "3204"
    assert row["Market"] == "NYC"

    sc = wb["Sales Confirmation"]
    assert sc["L4"].value == "PRODUCTION CHARGES"
    assert sc["F16"].value == 1 and sc["O16"].value == 500.0
    assert sc["H16"].value == "Vietnamese/Tagalog Translation VO"
    assert sc["J16"].value == "PRD"

    pivot = [[c.value for c in r] for r in wb["Sheet1"].iter_rows(min_row=4) if r[0].value]
    assert ["November 2026", 500, 500] in [[a, int(b or 0), int(c or 0)] for a, b, c in pivot if b]


def test_agency_contract_must_say_gross_or_net():
    charge = ProductionCharge(date(2026, 11, 20), "Production", 3176.47, "SEA")
    with pytest.raises(ValueError, match="gross"):
        build_workbook(AGENCY, [charge])

    wb, rec = _book(AGENCY, [charge], amounts_are_gross=True)
    (row,) = _run_rows(wb)
    assert row["Agency?"] == "Agency"
    assert row["Bill Code"] == "The Vida Agency:WA Health Benefit Exchange PRODUCTION"
    assert row["Broker Fees"] == "=P2*0.15"
    assert rec["net"]["run_sheet"] == round(3176.47 * 0.85, 2)
    # Broadcast: 11/20/2026 is in broadcast November.
    assert row["Month"] == datetime(2026, 11, 1)

    wb, rec = _book(AGENCY, [charge], amounts_are_gross=False)
    (row,) = _run_rows(wb)
    assert row["Agency?"] == "Agency"  # an agency is attached, commission or not
    assert row["Broker Fees"] == 0
    assert rec["net"]["run_sheet"] == 3176.47


def test_two_charges_are_two_lines_and_two_rows():
    charges = [
        ProductionCharge(date(2026, 10, 20), "Production", 1220.0, "CVC"),
        ProductionCharge(date(2026, 11, 20), "Production", 1220.0, "CVC"),
    ]
    wb, rec = _book(RWNY, charges)
    rows = _run_rows(wb)
    assert [r["Air Date"] for r in rows] == [datetime(2026, 10, 20), datetime(2026, 11, 20)]
    assert [r["Day"] for r in rows] == ["Tuesday", "Friday"]
    assert rec["ok"] and rec["gross"]["monthly"] == 2440.0


@pytest.mark.parametrize(
    "charge, word",
    [
        (ProductionCharge(date(2026, 11, 20), "", 500.0, "NYC"), "description"),
        (ProductionCharge(date(2026, 11, 20), "VO", 0.0, "NYC"), "amount"),
        (ProductionCharge(date(2026, 11, 20), "VO", 500.0, ""), "market"),
    ],
)
def test_an_incomplete_charge_refuses(charge, word):
    with pytest.raises(ValueError, match=word):
        build_workbook(RWNY, [charge])


def test_no_charges_refuses():
    with pytest.raises(ValueError, match="No production charges"):
        build_workbook(RWNY, [])
