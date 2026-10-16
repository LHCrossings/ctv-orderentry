"""The Make Goods query reads the line window the Etere form shows.

CONTRATTIRIGHE carries two time pairs. ORA_INIZIOF/ORA_FINEF are what the line
form displays and loadBlock() uses; ORA_FINE sometimes holds the START again
(~5% of lines, e.g. 3179 lines 86100/86104/86105 on 2026-10-16), so a page that
reads raw ORA_FINE shows "19:00 - 19:00" and prefills the Apply form with a
zero-width window. Every consumer must COALESCE the F pair first.
"""

import re
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[2] / "src" / "web" / "routes" / "orders.py"


def _make_goods_query() -> str:
    src = ROUTES.read_text()
    start = src.index("async def get_make_goods(")
    end = src.index("async def apply_make_good(")
    return src[start:end]


def test_make_goods_reads_the_form_time_columns():
    body = _make_goods_query()
    assert "COALESCE(cr.ORA_INIZIOF, cr.ORA_INIZIO)" in body
    assert re.search(r"COALESCE\(cr\.ORA_FINEF,\s*cr\.ORA_FINE\)", body)


def test_make_goods_never_selects_raw_ora_fine():
    body = _make_goods_query()
    select = body[body.index("SELECT") : body.index("FROM Traffic_ScheduleList")]
    for line in select.splitlines():
        stripped = line.strip().rstrip(",")
        assert stripped not in ("cr.ORA_FINE", "cr.ORA_INIZIO"), line
