"""Read the weekly programming grids (K: drive Excel) for the Daily Programming tool.

The grids are the manual source of truth: one .xlsx per M-Su week, named by the
Monday's date, under K:\\Programming\\! <Network>\\<year>\\<MM yyyy>\\.
This reads a single day's program lineup out of the "Local Channels" sheet.

See tasks/daily_programming_discovery.md for the grid layout details.
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path

import openpyxl

# Override for non-Windows/dev (e.g. PROGRAMMING_GRID_ROOT=/mnt/k/Programming).
GRID_ROOT = os.environ.get("PROGRAMMING_GRID_ROOT", r"K:\Programming")

NETWORK_DIR = {"CTV": "! Crossings TV", "TAC": "! The Asian Channel"}
NETWORK_FILE = {"CTV": "Crossings TV", "TAC": "The Asian Channel"}

# Footer / non-program markers that signal the end of the day's lineup.
_FOOTER_RE = re.compile(r"local channels|xfinity|spectrum|\bch\.|^\s*\d{1,2}/\d{1,2}/\d{2,4}\s*$", re.I)


def _week_monday(d: datetime.date) -> datetime.date:
    return d - datetime.timedelta(days=d.weekday())


def find_grid_file(network: str, d: datetime.date) -> Path | None:
    """Locate the weekly grid .xlsx for the week containing ``d``.

    Checks only the specific month folders (fast on the network drive) — the
    week's Monday and Sunday months, each with and without the in-progress
    ``!!!`` prefix (e.g. "06 2026" or "!!!08 2026"). Globs a single small
    folder for the Monday-stamped file rather than walking the whole tree.
    """
    netdir = NETWORK_DIR.get(network)
    if not netdir:
        return None
    mon = _week_monday(d)
    stamp = mon.strftime("%Y%m%d")
    seen: set = set()
    for dd in (mon, mon + datetime.timedelta(days=6)):  # broadcast month may follow the week's end
        for prefix in ("", "!!!"):
            month_dir = Path(GRID_ROOT) / netdir / dd.strftime("%Y") / f"{prefix}{dd.month:02d} {dd.year}"
            if month_dir in seen:
                continue
            seen.add(month_dir)
            if month_dir.exists():
                matches = list(month_dir.glob(f"*{stamp}*.xlsx"))
                if matches:
                    return matches[0]
    return None


def _time_label(ws, row: int) -> str | None:
    v = ws.cell(row, 1).value
    if isinstance(v, datetime.time):
        return v.strftime("%H:%M")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def _covering_range(ws, row: int, col: int):
    for mr in ws.merged_cells.ranges:
        if mr.min_row <= row <= mr.max_row and mr.min_col <= col <= mr.max_col:
            return mr
    return None


def _parse_title(raw: str) -> dict:
    """Split a grid cell into title + language + kind from the trailing (Lang Kind)."""
    text = " ".join(str(raw).split())
    language = kind = None
    m = re.search(r"\(([^()]*)\)\s*$", text)
    if m:
        inside = m.group(1).strip()
        text_wo = text[: m.start()].strip()
        parts = inside.split()
        if parts:
            language = parts[0]
            kind = " ".join(parts[1:]) or None
        title = text_wo or text
    else:
        title = text
    return {"title": title, "language": language, "kind": kind, "raw": text}


def get_day_programs(network: str, d: datetime.date) -> dict:
    """Return the program lineup for one network/day from the K: grid.

    Result: {"found": bool, "file": str|None, "date": iso, "programs": [
        {start, end, title, language, kind, raw}, ...]}.
    """
    path = find_grid_file(network, d)
    if not path:
        return {"found": False, "file": None, "date": d.isoformat(), "programs": [],
                "error": f"No grid file found for {network} week of {_week_monday(d):%Y-%m-%d}"}

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Local Channels"]

    # Day column: row 3 holds the per-column dates (Mon..Sun in cols 2..8).
    daycol = None
    for c in range(2, 9):
        v = ws.cell(3, c).value
        if isinstance(v, datetime.datetime) and v.date() == d:
            daycol = c
            break
    if daycol is None:
        return {"found": False, "file": str(path), "date": d.isoformat(), "programs": [],
                "error": f"{d:%Y-%m-%d} not found as a day column in {path.name}"}

    programs: list[dict] = []
    seen: set = set()
    r = 4
    while r <= ws.max_row:
        mr = _covering_range(ws, r, daycol)
        if mr and mr.min_col <= daycol <= mr.max_col:
            key = (mr.min_row, mr.min_col)
            top = mr.min_row
            val = ws.cell(top, mr.min_col).value
            advance = mr.max_row + 1
            start, end = _time_label(ws, top), _time_label(ws, mr.max_row + 1)
            r = advance
            if key in seen:
                continue
            seen.add(key)
        else:
            val = ws.cell(r, daycol).value
            start, end = _time_label(ws, r), _time_label(ws, r + 1)
            r += 1

        if not val:
            continue
        raw = " ".join(str(val).split())
        if _FOOTER_RE.search(raw):
            break  # reached the channel-listing footer
        prog = _parse_title(raw)
        prog["start"], prog["end"] = start, end
        programs.append(prog)

    return {"found": True, "file": str(path), "date": d.isoformat(), "programs": programs}
