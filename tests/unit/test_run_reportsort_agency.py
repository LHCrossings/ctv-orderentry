"""Agency mode of scripts/run_reportsort.py (Aki's agency post logs, 2026-09-07):
calendar-month chunking and CSV-safe concatenation of the per-chunk reports."""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

_root = Path(__file__).resolve().parents[2]


def _load():
    # the script imports requests + the Etere client at module level; neither is needed here
    sys.modules.setdefault("requests", MagicMock())
    spec = importlib.util.spec_from_file_location(
        "run_reportsort", _root / "scripts" / "run_reportsort.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rr = _load()


def test_month_chunks_clip_first_and_last():
    assert rr.month_chunks(date(2026, 1, 15), date(2026, 3, 10)) == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 10)),
    ]
    assert rr.month_chunks(date(2026, 6, 3), date(2026, 6, 3)) == [
        (date(2026, 6, 3), date(2026, 6, 3))
    ]
    assert rr.month_chunks(date(2025, 12, 20), date(2026, 1, 5)) == [
        (date(2025, 12, 20), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 5)),
    ]


def _chunk(rows):
    head = "﻿Textbox180,COD_CONTRATTO\r\nWorldlink,All\r\n\r\nCOD_CONTRATTO1,committente,x\r\n"
    return (head + "".join(r + "\r\n" for r in rows)).encode("utf-8")


def test_concat_keeps_one_header_block_and_every_data_row():
    a = _chunk(['WL A 1,"Rue Gilt (Icon, Inc)",1', "Textbox97,Textbox361,1"])
    b = _chunk(['3Fold LRCC 2611,"City\r\nof Sac",2', "Textbox97,Textbox361,2"])
    out = rr.concat_report_csvs([a, b]).decode("utf-8")
    import csv  # noqa: E401
    import io

    rows = list(csv.reader(io.StringIO(out, newline="")))
    assert rows[3] == ["COD_CONTRATTO1", "committente", "x"]
    assert sum(1 for r in rows if r and r[0] == "COD_CONTRATTO1") == 1  # header once
    assert ["WL A 1", "Rue Gilt (Icon, Inc)", "1"] in rows
    assert ["3Fold LRCC 2611", "City\r\nof Sac", "2"] in rows  # quoted newline survives
    assert (
        sum(1 for r in rows if r and r[0] == "Textbox97") == 2
    )  # footers ride along, filtered later
    assert not out.startswith("﻿")  # ReportSort reads utf-8-sig, plain utf-8 is fine


def test_concat_skips_empty_months_and_empty_input():
    empty = _chunk([])
    a = _chunk(["WL A 1,c,1"])
    rows = rr.concat_report_csvs([empty, a, empty]).decode("utf-8").splitlines()
    assert len(rows) == 5 and rows[-1] == "WL A 1,c,1"
    assert rr.concat_report_csvs([empty, empty]) == b""
