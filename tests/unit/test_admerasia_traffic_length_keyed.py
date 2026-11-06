"""Admerasia colour-blind legend: the McDonald's APA Next IOs (11/5/2026) print the ISCI
legend with no fill and group the grid rows under ':15s <title>' / ':30s <title>'. Every
swatch and cell is white, so colour says nothing; the row's Length column names the
creative (one per length per language). The traffic page reported "two Vietnamese
creatives share grid colour (255, 255, 255) — cannot tell them apart" and assigned the
:30 spots only.
"""

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers.admerasia_traffic_match import (  # noqa: E402
    length_keyed_legend,
    match_creatives,
)

_FPS = 29.97


def _leg(isci, secs, lang):
    return SimpleNamespace(isci_code=isci, duration_sec=secs, language=lang)


class _Cell:
    def __init__(self, row, col, day, count, cluster=0):
        self.row, self.col, self.day = row, col, day
        self.count, self.cluster = count, cluster


class _Grid:
    def __init__(self, cells, days):
        self.cells, self.calendar_days = cells, days
        self.palette = [(255, 255, 255)]


def _spot(sid, d, hh, mm, dur_sec, langs):
    return {
        "id": sid,
        "date": d,
        "ora": round((hh * 60 + mm) * 60 * _FPS),
        "duration": round(dur_sec * _FPS),
        "languages": langs,
    }


SEA_LEGEND = [_leg("MCIV139526VH", 15, "Vietnamese"), _leg("MCIV012326VH", 30, "Vietnamese")]


def test_length_keyed_legend_maps_rows_by_their_length_column():
    rows = {0: 15, 1: 15, 2: 15, 3: 30, 4: 30, 5: 30}
    out = length_keyed_legend(SEA_LEGEND, rows)
    assert out == {
        0: {"Vietnamese": "MCIV139526VH"},
        1: {"Vietnamese": "MCIV139526VH"},
        2: {"Vietnamese": "MCIV139526VH"},
        3: {"Vietnamese": "MCIV012326VH"},
        4: {"Vietnamese": "MCIV012326VH"},
        5: {"Vietnamese": "MCIV012326VH"},
    }


def test_length_keyed_legend_keeps_both_languages_on_a_chinese_io():
    legend = [
        _leg("MCIM141526VH", 15, "Mandarin"),
        _leg("MCIM016326VH", 30, "Mandarin"),
        _leg("MCIC141526VH", 15, "Cantonese"),
        _leg("MCIC016326VH", 30, "Cantonese"),
    ]
    out = length_keyed_legend(legend, {0: 15, 1: 30})
    assert out == {
        0: {"Mandarin": "MCIM141526VH", "Cantonese": "MCIC141526VH"},
        1: {"Mandarin": "MCIM016326VH", "Cantonese": "MCIC016326VH"},
    }


def test_length_keyed_legend_refuses_two_creatives_of_one_length():
    # A real rotation (two :30s at 50/50) can only be split by colour — never by length.
    legend = SEA_LEGEND + [_leg("MCIV999926VH", 30, "Vietnamese")]
    assert length_keyed_legend(legend, {0: 15, 1: 30}) is None


def test_length_keyed_legend_refuses_a_legend_without_durations():
    assert length_keyed_legend([_leg("MCIV139526VH", None, "Vietnamese")], {0: 15}) is None


def test_length_keyed_legend_skips_rows_whose_length_no_creative_has():
    assert length_keyed_legend(SEA_LEGEND, {0: 60}) is None
    assert length_keyed_legend(SEA_LEGEND, {0: 60, 1: 15}) == {1: {"Vietnamese": "MCIV139526VH"}}


def test_match_creatives_uses_the_row_key_when_colour_is_blind():
    """Seattle 24-MD10-2611VT shape: one white colour, a :15 row and a :30 row with
    a spot each on the same day; without the row key the :15 spot matched 0 rows."""
    flight = date(2026, 11, 15)
    grid = _Grid([_Cell(0, 1, 16, 1), _Cell(1, 1, 16, 1)], list(range(15, 31)))
    row_lang_isci = length_keyed_legend(SEA_LEGEND, {0: 15, 1: 30})
    filmati = {
        "MCIV139526VH": {"filmati_id": 153052, "duration": round(15 * _FPS)},
        "MCIV012326VH": {"filmati_id": 153053, "duration": round(30 * _FPS)},
    }
    spots = [
        _spot(1, date(2026, 11, 16), 11, 40, 15, ["Vietnamese"]),
        _spot(2, date(2026, 11, 16), 12, 20, 30, ["Vietnamese"]),
    ]
    # cluster map is what the colour path produced: the LAST creative won the colour.
    cluster_lang_isci = {0: {"Vietnamese": "MCIV012326VH"}}
    res = match_creatives(
        grid,
        {0: 15, 1: 30},
        {},
        cluster_lang_isci,
        flight,
        spots,
        filmati,
        row_lang_isci=row_lang_isci,
    )
    by_id = {a.tp_id: a for a in res.assignments}
    assert by_id[1].ok and by_id[1].isci == "MCIV139526VH" and by_id[1].filmati_id == 153052
    assert by_id[2].ok and by_id[2].isci == "MCIV012326VH" and by_id[2].filmati_id == 153053
    assert not [w for w in res.warnings if "grid shows" in w]


def test_match_creatives_colour_path_is_unchanged_without_a_row_key():
    flight = date(2026, 11, 15)
    grid = _Grid([_Cell(0, 1, 16, 1), _Cell(1, 1, 16, 1)], list(range(15, 31)))
    filmati = {"MCIV012326VH": {"filmati_id": 153053, "duration": round(30 * _FPS)}}
    spots = [_spot(1, date(2026, 11, 16), 11, 40, 15, ["Vietnamese"])]
    res = match_creatives(
        grid, {0: 30, 1: 30}, {}, {0: {"Vietnamese": "MCIV012326VH"}}, flight, spots, filmati
    )
    assert not res.assignments[0].ok and "matched 0 grid rows" in res.assignments[0].reason
