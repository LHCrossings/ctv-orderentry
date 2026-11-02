"""The traffic-assign language filter renders each window as [start, end) on
TPALINSE.ORA, with an end written "23:59" meaning end of broadcast day (24:00).

Toyota 13938 CVC (Lee 2026-11-02): the Wednesday 23:59:10 bonus spot matched no
window because the clause was ``tp.ORA <= frames("23:59")`` = 23:59:00, so the
Cantonese cut was never assigned. Lee: "it should apply Cantonese through 23:59:59".
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "src", ROOT, ROOT / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from web.routes import orders  # noqa: E402

FPS = orders._FPS_GLOBAL


def _frames(h, m, s=0):
    return round((h * 3600 + m * 60 + s) * FPS)


def _clauses(lang):
    sql = orders._build_spot_filter({"languages": [lang], "markets": ["CVC"]})
    return re.findall(
        r"\(DATENAME\(weekday, tp\.DATA\) IN \(([^)]*)\) AND tp\.ORA >= (\d+) AND tp\.ORA < (\d+)\)",
        sql,
    )


def _matches(lang, weekday, frames):
    for days, lo, hi in _clauses(lang):
        if f"'{weekday}'" in days and int(lo) <= frames < int(hi):
            return True
    return False


def test_end_bound_is_exclusive_and_23_59_means_end_of_day():
    sql = orders._build_spot_filter({"languages": ["Cantonese"], "markets": ["CVC"]})
    assert "tp.ORA <=" not in sql
    assert f"tp.ORA >= {_frames(23, 30)} AND tp.ORA < {_frames(24, 0)}" in sql


def test_wednesday_23_59_10_is_cantonese():
    f = _frames(23, 59, 10)
    assert _matches("Cantonese", "Wednesday", f)
    assert not _matches("Mandarin", "Wednesday", f)


def test_23_59_59_is_still_inside_the_window():
    assert _matches("Cantonese", "Wednesday", _frames(23, 59, 59))
    assert _matches("Mandarin", "Saturday", _frames(23, 59, 59))


def test_shared_minute_belongs_to_one_language_only():
    f = _frames(20, 0, 0)
    assert _matches("Mandarin", "Monday", f)
    assert not _matches("Cantonese", "Monday", f)
    f = _frames(23, 30, 0)
    assert _matches("Cantonese", "Monday", f)
    assert not _matches("Mandarin", "Monday", f)


def test_lang_window_frames_shifts_the_post_midnight_tail():
    lo, hi = orders._lang_window_frames("00:00", "01:00")  # a DAL Mandarin block
    assert (lo, hi) == (_frames(24, 0), _frames(25, 0))
