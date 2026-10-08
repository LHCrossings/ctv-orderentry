"""Daily Programming zone buttons (Maija 10/8): the team splits a day East/Central vs
Pacific (+DAL), so one click per zone must select exactly those markets' COD_USERs."""

import re
from pathlib import Path

TEMPLATE = (
    Path(__file__).resolve().parents[2] / "src/web/templates/master_control/daily_programming.html"
)
MARKET_IDS = {
    "NYC": 1,
    "CMP": 2,
    "HOU": 3,
    "SFO": 4,
    "SEA": 5,
    "LAX": 6,
    "CVC": 7,
    "WDC": 8,
    "MMT": 9,
    "DAL": 10,
}
ZONES = {
    "East/Central": ["NYC", "WDC", "CMP", "HOU", "MMT"],
    "Pacific": ["SFO", "SEA", "LAX", "CVC"],
}


def _zones():
    html = TEMPLATE.read_text()
    found = {}
    for m in re.finditer(
        r'class="mkt-pill zone-pill" data-zone="([^"]+)" data-codusers="([^"]+)"', html
    ):
        found[m.group(1)] = sorted(int(x) for x in m.group(2).split(","))
    return html, found


def test_zone_buttons_select_exactly_the_team_split():
    _, found = _zones()
    assert found == {z: sorted(MARKET_IDS[c] for c in codes) for z, codes in ZONES.items()}


def test_zones_cover_every_crossings_market_once_and_never_dal():
    _, found = _zones()
    all_ids = sorted(cu for ids in found.values() for cu in ids)
    assert all_ids == sorted(v for k, v in MARKET_IDS.items() if k != "DAL")


def test_market_pill_handlers_exclude_zone_pills():
    """A zone pill carries no data-coduser; a selector that catches it would register NaN."""
    html, _ = _zones()
    js = html[html.index("<script") :]
    for m in re.finditer(r"querySelectorAll\('(\.mkt-pill[^']*)'\)", js):
        assert m.group(1) == ".mkt-pill:not(.zone-pill)", m.group(0)
    assert "querySelectorAll('.zone-pill')" in js
