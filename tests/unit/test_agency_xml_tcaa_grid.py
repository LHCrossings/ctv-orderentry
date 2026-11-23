"""Agency XML Export: the TCAA annual grid (Program / TIME / Gross, three weeks a month).

Fixture: Charmaine's "Crossings TV _TCAA_2027.xlsx" as received 2026-11-23, saved
values-only with the three September week cells moved from Tuesday to Monday
(9/6, 9/13, 9/20); the as-received Tuesday shape is a refusal test below.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from browser_automation.generators import agency_xml_export as ax
from browser_automation.parsers.crispin_parser import parse_crispin_xlsx
from browser_automation.parsers.tcaa_grid_parser import TcaaGridError, parse_tcaa_grid_xlsx

FIX = Path(__file__).resolve().parents[1] / "fixtures"
GRID = FIX / "tcaa_grid" / "tcaa_2027_september_mondays.xlsx"
STANDARD = FIX / "crispin" / "proposal_baaqmd_2026_rev1.xlsm"
NS = {"p": ax.NS_PROPOSAL}


@pytest.fixture(scope="module")
def order():
    return ax.read_proposal(str(GRID))


def _header(order, **over):
    h = ax.default_header(order, memory_path=Path("/nonexistent/memory.json"))
    h.buyer_name, h.call_letters, h.spot_length_sec = "Test Buyer", "CRTV", 30
    h.include_spots_per_week = True
    h.send_date = date(2026, 11, 23)
    for k, v in over.items():
        setattr(h, k, v)
    return h


def _tampered(tmp_path, edits):
    wb = openpyxl.load_workbook(GRID)
    ws = wb.worksheets[0]
    for cell, value in edits.items():
        ws[cell] = value
    out = tmp_path / "t.xlsx"
    wb.save(out)
    return str(out)


def test_grid_reads_the_sheet(order):
    assert (order.agency, order.advertiser) == ("TCAA", "Western Washington Toyota")
    assert order.market_label == "Seattle - CRTV-Cable"
    assert len(order.lines) == 11 and len(order.week_dates) == 36
    assert [(ln.language_block, ln.daypart, ln.rate, ln.total_spots) for ln in order.lines[:2]] == [
        ("Mandarin News", "M-Sat 6a-7a", 25.0, 432),
        ("Chinese Evening", "M-Sun 7p-12a", 25.0, 1260),
    ]
    assert [ln.language_block for ln in order.bonus_lines] == [
        "Chinese ROS",
        "Korean ROS",
        "Vietnamese ROS",
        "South Asian",
        "Filipino ROS",
    ]
    assert all(ln.daypart == "M-Su 6a-12a" for ln in order.bonus_lines)
    assert sum(ln.total_spots for ln in order.lines) == 6552
    assert sum(ln.total_spots * ln.rate for ln in order.lines) == 118800


def test_dispatcher_routes_each_layout():
    assert ax.proposal_tabs(str(GRID)) == ["Regular Advertising "]
    assert ax.read_proposal(str(STANDARD)).lines == parse_crispin_xlsx(str(STANDARD)).lines
    assert ax.needs_spot_length(ax.read_proposal(str(GRID)))
    assert not ax.needs_spot_length(ax.read_proposal(str(STANDARD)))


def test_xml_carries_every_spot_and_never_covers_a_skipped_week(order):
    xml = ax.export_proposal(order, _header(order))
    root = ET.fromstring(xml)
    listed = set(order.week_dates)
    spots = money = 0
    lines = root.findall(".//p:AvailLineWithDetailedPeriods", NS)
    assert len(lines) == 11
    for line in lines:
        periods = line.findall(".//p:DetailedPeriod", NS)
        assert len(periods) == 12  # one per month: three listed weeks, then a gap
        assert line.find("p:SpotLength", NS).text == "00:00:30"
        for dp in periods:
            start = date.fromisoformat(dp.get("startDate"))
            end = date.fromisoformat(dp.get("endDate"))
            weeks = ((end - start).days + 1) // 7
            assert all(date.fromordinal(start.toordinal() + 7 * k) in listed for k in range(weeks))
            n = int(dp.find("p:SpotsPerWeek", NS).text) * weeks
            spots += n
            money += n * float(dp.find("p:Rate", NS).text)
    assert (spots, money) == (6552, 118800.0)
    first = lines[0]
    assert first.find(".//p:StartTime", NS).text == "06:00"
    assert [d.text for d in first.find(".//p:Days", NS)] == ["Y"] * 6 + ["N"]
    assert first.find("p:DaypartName", NS).text == "CHINESE"


def test_rate_only_export_still_skips_the_unlisted_weeks(order):
    xml = ax.export_proposal(order, _header(order, include_spots_per_week=False))
    line = ET.fromstring(xml).find(".//p:AvailLineWithDetailedPeriods", NS)
    periods = line.findall(".//p:DetailedPeriod", NS)
    assert len(periods) == 12 and periods[0].get("endDate") == "2027-01-31"
    assert line.find(".//p:SpotsPerWeek", NS) is None


def test_export_refuses_without_a_spot_length(order):
    with pytest.raises(ax.AgencyXmlError, match="spot length"):
        ax.export_proposal(order, _header(order, spot_length_sec=None))
    assert ax.build_preview(order, allow_unknown_length=True).lines[0].length_sec == 0


def test_untouched_copy_still_parses(tmp_path):
    assert len(parse_tcaa_grid_xlsx(_tampered(tmp_path, {})).lines) == 11


@pytest.mark.parametrize(
    "edits, message",
    [
        ({"AC8": datetime(2027, 9, 7)}, "AC8 is 9/7/2027, a Tuesday"),  # as received 11/23
        ({"E8": datetime(2026, 1, 12)}, "the sheet says 2027"),
        ({"F8": datetime(2027, 1, 4)}, "not after the column before"),
        ({"F9": 11}, "TOTAL says 432"),
        ({"D10": 30}, "Gross"),
        ({"D9": None}, "no rate"),
        ({"D9": ":15s"}, "should hold a number"),
        ({"C9": None}, "no time window"),
        ({"B9": "Mandarin News"}, "does not start with a day pattern"),
        ({"B23": "Net"}, "'Gross' was not found"),
        ({"E20": 131}, "Total Paid"),
    ],
)
def test_tampered_sheet_refuses(tmp_path, edits, message):
    with pytest.raises(TcaaGridError, match=message):
        parse_tcaa_grid_xlsx(_tampered(tmp_path, edits))


def test_page_asks_for_the_spot_length_and_downloads(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from web.routes import agency_xml as route

    memory = tmp_path / "headers.json"
    monkeypatch.setattr(route, "remember_header", lambda h: ax.remember_header(h, memory))
    monkeypatch.setattr(route, "default_header", lambda o: ax.default_header(o, memory))
    app = FastAPI()
    app.include_router(
        route.build_agency_xml_router(Jinja2Templates(directory="src/web/templates"))
    )
    client = TestClient(app)

    def post(url, **form):
        with GRID.open("rb") as fh:
            return client.post(url, files={"workbook": (GRID.name, fh)}, data=form)

    pv = post("/orders/agency-xml/preview").json()
    assert pv["shared"]["needs_spot_length"] is True
    assert pv["shared"]["buyer_company"] == "TCAA" and pv["shared"]["call_letters"] == ""
    sheet = pv["sheets"][0]
    assert (sheet["proposal_id"], sheet["paid_total"]) == ("TCAA-2027", 118800.0)
    assert sheet["lines"][0]["length_sec"] == 0

    import json

    form = dict(
        sheets=json.dumps(
            [{k: sheet[k] for k in ("sheet", "proposal_id", "proposal_name", "product")}]
        ),
        advertiser="Western Washington Toyota",
        buyer_company="TCAA",
        buyer_name="Test Buyer",
        call_letters="CRTV",
        include_spots_per_week="1",
    )
    refused = post("/orders/agency-xml/generate", **form)
    assert refused.status_code == 400 and "spot length" in refused.json()["detail"]
    ok = post("/orders/agency-xml/generate", spot_length="30", **form)
    assert ok.status_code == 200 and ok.content.count(b"<SpotsPerWeek>") == 11 * 12
