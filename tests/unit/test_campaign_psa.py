"""Campaign PSA fill rule (Lee 2026-10-21): one per show through Finish's fill path,
language from the show, market-day cap, preferred spacing, inert outside the flight."""

import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.business_logic.services import campaign_psa as cp  # noqa: E402
from src.business_logic.services.finish_plan import (  # noqa: E402
    FPS,
    Ev,
    Filler,
    plan,
)

H20 = 20 * 3600.0
H21 = 21 * 3600.0
EN, ZH, VI = 9001, 9002, 9003
CAMP = cp.Campaign(
    name="T",
    start=date(2026, 10, 30),
    end=date(2027, 10, 30),
    creatives={"english": ("PSA-EN",), "chinese": ("PSA-ZH",), "vietnamese": ("PSA-VI",)},
)
ASSETS = {
    "PSA-EN": (EN, "PSA-EN", "PSA-900-030: Campaign EN", 899),
    "PSA-ZH": (ZH, "PSA-ZH", "PSA-901-030: Campaign ZH", 899),
    "PSA-VI": (VI, "PSA-VI", "PSA-902-030: Campaign VI", 899),
}


class FakeCur:
    """Answers the three queries the rule makes from canned rows."""

    def __init__(self, placed=(), last=None):
        self.placed = list(placed)  # (fid, ora_seconds)
        self.last = last or {}
        self._rows = []

    def execute(self, sql, params=()):
        if "FROM FILMATI" in sql:
            code = params[0]
            self._rows = [ASSETS[code]] if code in ASSETS else []
        elif "MAX(CAST(DATA AS float)" in sql:
            self._rows = [(f, v) for f, v in self.last.items()]
        elif "FROM TPALINSE" in sql:
            self._rows = [(f, int(o * FPS)) for f, o in self.placed]
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return self._rows


@pytest.fixture(autouse=True)
def _campaign(monkeypatch):
    monkeypatch.setattr(cp, "CAMPAIGNS", (CAMP,))


def _pgm(i, ora, dur, desc="SHOW"):
    return Ev(i, ora, dur, "PGM", "T" if i else "F", 5000 + i, desc, None)


def _paid(i, ora, dur=30.0):
    return Ev(i, ora, dur, "COM", "T", 6000 + i, f"PAID{i}", 70000 + i)


def _show():
    # two story pieces with an interior break, then a final break, 20:00-21:00
    return [
        _pgm(1, H20, 1500.0, "PIECE A"),
        _paid(2, H20 + 1500),
        _pgm(3, H20 + 1530, 1500.0, "PIECE B"),
        _paid(4, H20 + 3030),
        _paid(5, H20 + 3060),
    ]


# ── creative_language ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "word,want",
    [
        ("Mandarin", "chinese"),
        ("Chinese", "chinese"),
        ("Cantonese", "english"),  # simplified text does not suit a traditional-text audience
        ("Vietnamese", "vietnamese"),
        ("Korean", "english"),
        ("Filipino", "english"),
        ("Hmong", "english"),
    ],
)
def test_creative_language_from_the_grid_word(word, want):
    assert cp.creative_language(word, 4, "2026-11-02", H20) == want


def test_creative_language_falls_back_to_the_language_windows():
    # SFO Monday 20:30 is Mandarin; DAL (10) Monday 17:30 is Cantonese; SFO Monday 11:00 Vietnamese
    assert cp.creative_language("", 4, "2026-11-02", H20 + 1800) == "chinese"
    assert cp.creative_language("", 10, "2026-11-02", 17.5 * 3600) == "english"
    assert cp.creative_language("", 4, "2026-11-02", 11 * 3600.0) == "vietnamese"


# ── campaign_psa_for ──────────────────────────────────────────────────────────


def test_picks_the_language_creative_and_counts_the_day():
    f, note = cp.campaign_psa_for(FakeCur(), 4, "2026-11-02", H20, H21, "Mandarin", _show())
    assert f is not None and f.filmati == ZH and f.kind == "PSA" and abs(f.dur - 30.0) < 0.1
    assert f.campaign == "CAMPAIGN T"
    assert note == "campaign PSA: chinese creative PSA-ZH (1 of 8 today)"


def test_inert_outside_the_flight():
    assert cp.campaign_psa_for(FakeCur(), 4, "2026-10-29", H20, H21, "Mandarin", _show()) == (
        None,
        "",
    )
    assert cp.campaign_psa_for(FakeCur(), 4, "2027-10-31", H20, H21, "Mandarin", _show()) == (
        None,
        "",
    )


def test_no_creatives_yet_says_so(monkeypatch):
    empty = cp.Campaign("E", date(2026, 10, 30), date(2027, 10, 30), {"english": ()})
    monkeypatch.setattr(cp, "CAMPAIGNS", (empty,))
    f, note = cp.campaign_psa_for(FakeCur(), 4, "2026-11-02", H20, H21, "Korean", _show())
    assert f is None and "no creatives ingested yet" in note


def test_one_per_show():
    evs = _show() + [Ev(9, H20 + 1530, 30.0, "PSA", "T", ZH, "PSA-901-030: Campaign ZH", None)]
    f, note = cp.campaign_psa_for(FakeCur(), 4, "2026-11-02", H20, H21, "Mandarin", evs)
    assert f is None and note == "campaign PSA already in this show"


def test_market_day_cap_counts_every_creative_outside_the_window():
    placed = [(EN, 7 * 3600.0 + i * 1800) for i in range(8)]  # eight English ones earlier today
    f, note = cp.campaign_psa_for(FakeCur(placed), 4, "2026-11-02", H20, H21, "Mandarin", _show())
    assert f is None and "cap 8 reached" in note
    # a row inside THIS window (a refill re-plan) is not counted against the cap
    inside = placed[:7] + [(ZH, H20 + 1530)]
    f, _ = cp.campaign_psa_for(FakeCur(inside), 4, "2026-11-02", H20, H21, "Mandarin", _show())
    assert f is not None


def test_spacing_is_preferred_not_required():
    f, note = cp.campaign_psa_for(
        FakeCur([(EN, H20 - 3600)]), 4, "2026-11-02", H20, H21, "Mandarin", _show()
    )
    assert f is None and "spacing" in note
    f, _ = cp.campaign_psa_for(
        FakeCur([(EN, H20 - 7300)]), 4, "2026-11-02", H20, H21, "Mandarin", _show()
    )
    assert f is not None  # more than 2 h ago: fine


def test_least_recently_aired_creative_wins_when_a_language_has_two(monkeypatch):
    two = cp.Campaign(
        "T2", date(2026, 10, 30), date(2027, 10, 30), {"english": ("PSA-EN", "PSA-VI")}
    )
    monkeypatch.setattr(cp, "CAMPAIGNS", (two,))
    f, _ = cp.campaign_psa_for(
        FakeCur(last={EN: 5.0, VI: 1.0}), 4, "2026-11-02", H20, H21, "Korean", _show()
    )
    assert f.filmati == VI


# ── the planner seats it ahead of the PI fill, inside the show ────────────────


def _inv():
    pis = [
        Filler(100 + i, f"PI-{i:03d}-060: PI {i}", 60.0, "PI", f"PI-{i:03d}", float(i))
        for i in range(10)
    ] + [
        Filler(150 + i, f"PI-{i + 50:03d}-030: PI {i}", 30.0, "PI", f"PI-{i + 50:03d}", float(i))
        for i in range(4)
    ]
    psas = [
        Filler(200 + i, f"PSA-{i:03d}-015: p", 15.0, "PSA", f"PSA-{i:03d}", 0.0) for i in range(8)
    ] + [
        Filler(250 + i, f"PSA-{i + 50:03d}-010: p", 10.0, "PSA", f"PSA-{i + 50:03d}", 0.0)
        for i in range(4)
    ]
    return pis + psas


def test_planner_seats_the_campaign_in_the_shortest_interior_break():
    camp = Filler(ZH, "PSA-901-030: Campaign ZH", 30.0, "PSA", "CAMPAIGN T", 0.0)
    breaks, notes = plan(_show(), _inv(), H21, 4, campaign=camp)
    interior = [b for b in breaks if b is not breaks[-1]]
    assert any(any(x is camp for x in b.items) for b in interior)
    assert not any(x is camp for x in breaks[-1].items)
    assert any(n.startswith("campaign PSA PSA-901-030") for n in notes)
    # the PIs still fill the rest and the ID lands
    assert any(isinstance(x, Filler) and x.kind == "ID" for x in breaks[-1].items)


def test_planner_skips_the_campaign_when_the_show_has_no_room():
    tight = [
        _pgm(1, H20, 3540.0, "LONG SHOW"),
        _paid(2, H20 + 3540, 30.0),  # ends 20:59:30 → 30 s left, ID needs 5
    ]
    camp = Filler(EN, "PSA-900-030: Campaign EN", 30.0, "PSA", "CAMPAIGN T", 0.0)
    breaks, notes = plan(tight, _inv(), H21, 4, campaign=camp)
    assert not any(x is camp for b in breaks for x in b.items)
    assert any(n.startswith("campaign PSA skipped") for n in notes)


def test_fix_phase_removes_other_fill_before_the_campaign_row():
    # Over-full hour: a PI and a campaign row sit in the final break; the deficit needs one
    # of them out. With the campaign protected, the PI goes first.
    evs = [
        _pgm(1, H20, 3480.0, "SHOW"),
        Ev(2, H20 + 3480, 30.0, "PSA", "T", ZH, "PSA-901-030: Campaign ZH", None),
        Ev(3, H20 + 3510, 60.0, "PER", "T", 777, "PI-777-060: Some PI", None),
        _paid(4, H20 + 3570, 30.0),  # ends 21:00:00 → R = 0 < 5
    ]
    breaks, notes = plan(evs, _inv(), H21, 4, protected={ZH})
    final = breaks[-1]
    assert any(isinstance(x, Ev) and x.filmati == ZH for x in final.items)
    assert not any(isinstance(x, Ev) and x.filmati == 777 for x in final.items)
    assert any(n.startswith("remove PI-777-060") for n in notes)


def test_finished_hour_stays_finished_with_a_campaign_offered():
    # An hour Finish already packed (pre-ID gap inside [5, 10]) gets no campaign insert and
    # no other new fill — re-running Finish on the flight must not churn finished shows.
    packed = [
        _pgm(1, H20, 3300.0, "SHOW"),
        Ev(2, H20 + 3300, 60.0, "PER", "T", 700, "PI-700-060: PI", None),
        Ev(3, H20 + 3360, 60.0, "PER", "T", 701, "PI-701-060: PI", None),
        Ev(4, H20 + 3420, 60.0, "PER", "T", 702, "PI-702-060: PI", None),
        Ev(5, H20 + 3480, 60.0, "PER", "T", 703, "PI-703-060: PI", None),
        Ev(6, H20 + 3540, 15.0, "PSA", "T", 800, "PSA-800-015: p", None),
        Ev(8, H20 + 3555, 15.0, "PSA", "T", 801, "PSA-801-015: p", None),
        Ev(9, H20 + 3570, 15.0, "PSA", "T", 802, "PSA-802-015: p", None),
        Ev(10, H20 + 3585, 10.0, "PSA", "T", 803, "PSA-803-010: p", None),
        Ev(7, H20 + 3595, 25.0, "ID", "T", 67910, "ID - NEW - GENERIC", None),  # gap 5
    ]
    camp = Filler(EN, "PSA-900-030: Campaign EN", 30.0, "PSA", "CAMPAIGN T", 0.0)
    breaks, notes = plan(packed, _inv(), H21, 4, campaign=camp)
    assert not any(isinstance(x, Filler) and x.kind != "ID" for b in breaks for x in b.items)
    assert any(n.startswith("campaign PSA skipped") for n in notes)
    assert any(n.startswith("edits: 0 delete, 0 move, 1 insert") for n in notes)
