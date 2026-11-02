"""The traffic-log viewer's time formatter must not use glibc-only strftime
directives: the web server runs on Windows, where ``%-H`` raises
``ValueError: Invalid format string``. SFO Log 11/2 Monday carried two program
rows in ``h:mm:ss`` (openpyxl → ``datetime.time``) among ``[h]:mm:ss`` rows
(→ ``timedelta``), and the page showed
``Unexpected token 'I', "Internal S"... is not valid JSON`` (Lee 2026-11-02).
"""

import re
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.web.routes.orders import _fmt_log_time  # noqa: E402


@pytest.mark.parametrize(
    "val, out",
    [
        (None, ""),
        (timedelta(hours=13, minutes=30), "13:30:00"),
        (timedelta(seconds=30), "0:00:30"),
        (timedelta(hours=25, minutes=5, seconds=7), "25:05:07"),  # post-midnight [h]
        (time(13, 30), "13:30:00"),  # an h:mm:ss cell → datetime.time
        (time(0, 30), "0:30:00"),
        (datetime(1899, 12, 30, 14, 0, 5), "14:00:05"),
        ("1:00:00", "1:00:00"),
    ],
)
def test_every_cell_shape_renders_without_strftime(val, out):
    assert _fmt_log_time(val) == out


_GLIBC_ONLY = re.compile(r"""strftime\(\s*['"][^'"]*%-[a-zA-Z]""")


def test_no_glibc_only_strftime_directive_anywhere():
    """``%-d`` / ``%-H`` / ... are Linux-only; the server and the entry tools run on Windows."""
    hits = []
    for folder in ("src", "browser_automation", "scripts"):
        for path in (_root / folder).rglob("*.py"):
            for n, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if _GLIBC_ONLY.search(line):
                    hits.append(f"{path.relative_to(_root)}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_the_guard_sees_the_old_shape():
    assert _GLIBC_ONLY.search('return val.strftime("%-H:%M:%S")')
    assert not _GLIBC_ONLY.search('return val.strftime("%H:%M:%S")')
