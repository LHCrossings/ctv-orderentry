"""As-Run Report: the Language Block column (Charmaine 11/20)."""
import io
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
for _p in (str(_root), str(_root / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from web.routes.reports import _asrun_block_name, _build_asrun_excel  # noqa: E402

F = 107892  # frames in one hour at 29.97
DAY = [(19 * F, 20 * F, "C - Cantonese Primetime News"), (20 * F, 23 * F + F // 2, "M - Primetime News REV")]


def test_booked_break_decides_the_block():
    # booked in the 19:00 block, aired 14 s into the next hour
    assert _asrun_block_name(DAY, 19 * F + 59 * 1798, 20 * F + 420) == "C - Cantonese Primetime News"


def test_block_end_is_exclusive():
    assert _asrun_block_name(DAY, 20 * F, None) == "M - Primetime News REV"


def test_air_time_is_the_fallback_and_no_grid_is_blank():
    assert _asrun_block_name(DAY, None, 19 * F + 5) == "C - Cantonese Primetime News"
    assert _asrun_block_name(DAY, 5 * F, 20 * F + 5) == "M - Primetime News REV"
    assert _asrun_block_name([], 19 * F, 19 * F) == ""
    assert _asrun_block_name(DAY, 6 * F, 6 * F) == ""


def test_excel_carries_the_column_and_keeps_rate_last():
    import openpyxl

    payload = {
        "contract_code": "X 1", "contract_description": "d", "date_from": "2026-11-02", "date_to": "2026-11-08",
        "total": 1,
        "markets": [{"market": "CVC", "count": 1, "airings": [{
            "date": "2026-11-03", "time": "19:13:50", "block": "C - Cantonese Primetime News",
            "spot_code": "A", "spot_title": "t", "type": "COM", "rate": 65.0,
        }]}],
    }
    ws = openpyxl.load_workbook(io.BytesIO(_build_asrun_excel(payload))).active
    head = [c.value for c in ws[4]]
    assert head == ["Market", "Date", "Time", "Language Block", "Spot Code", "Title", "Type", "Rate"]
    assert ws.cell(5, 4).value == "C - Cantonese Primetime News"
    assert ws.cell(5, 8).value == 65 and ws.cell(5, 8).number_format == "$#,##0.00"
    assert ws.cell(6, 8).value == 65
