"""Fill & Finish window membership + overage remainder (NYC 9/4 08:00, 2026-09-04).

A paid spot that spills past the top of the hour but sits AHEAD of the next show's
F anchor in playlist order belongs to this window. Only when no F anchor exists at
`hi` (next show not yet placed) does a paid spot past `hi` mark the next hour.
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from business_logic.services.finish_plan import (  # noqa: E402
    FPS,
    Ev,
    mmss,
    packed_remainder,
    window_from_day,
)


def _row(i, ora_s, dur_s, newtype, event_type="T", desc="", line=None, xorder=None):
    # load_day tuple: ID, ORA(frames), DURATION(frames), NEWTYPE, EVENT_TYPE, ID_FILMATI, DESCRIZIO, line, XORDER
    return (
        i,
        int(ora_s * FPS),
        int(dur_s * FPS),
        newtype,
        event_type,
        1000 + i,
        desc,
        line,
        xorder or i * 10,
    )


H8, H9 = 8 * 3600.0, 9 * 3600.0


def _hour(with_anchor: bool):
    rows = [
        _row(1, H8, 14, "PGM", "F", "BUMP_OPEN"),
        _row(2, H8 + 14, 3540, "PGM", "T", "NEWSTODAY"),
        _row(3, H8 + 3554, 30, "COM", "T", "4IMPRINT30", line=82757),  # ends 08:59:44
        _row(4, H8 + 3584, 25, "ID", "T", "ID - NEW - GENERIC"),  # ends 09:00:09
        _row(5, H8 + 3609, 15, "COM", "T", "REDFIN15", line=83301),  # 09:00:09 — spilled paid
    ]
    if with_anchor:
        rows.append(_row(6, H9, 480, "PGM", "F", "KD-NEXT"))
    else:
        rows.append(_row(6, H9 + 60, 15, "COM", "T", "NEXT-HOUR-SPOT", line=90000))
    return rows


def test_spilled_paid_spot_belongs_to_window_when_anchor_exists():
    evs = window_from_day(_hour(True), H8, H9)
    assert [e.desc for e in evs] == [
        "BUMP_OPEN",
        "NEWSTODAY",
        "4IMPRINT30",
        "ID - NEW - GENERIC",
        "REDFIN15",
    ]


def test_paid_spot_past_hi_is_next_hour_when_no_anchor():
    evs = window_from_day(_hour(False), H8, H9)
    assert [e.desc for e in evs] == ["BUMP_OPEN", "NEWSTODAY", "4IMPRINT30", "ID - NEW - GENERIC"]


def test_program_past_hi_still_ends_window_with_anchor():
    rows = _hour(True)
    rows.insert(
        5, _row(7, H9 + 1, 600, "PGM", "T", "NEXT-PART")
    )  # next window's content before the F row
    evs = window_from_day(rows, H8, H9)
    assert evs[-1].desc == "REDFIN15"


def test_packed_remainder_counts_every_paid_spot_and_ignores_id():
    evs = window_from_day(_hour(True), H8, H9)
    # 14 + 3540 + 30 + 15 = 3599 of content; ID is empty time
    assert abs(packed_remainder(evs, H9) - 1.0) < 0.1
    # the same hour without the Redfin would have read +16
    assert abs(packed_remainder([e for e in evs if e.desc != "REDFIN15"], H9) - 16.0) < 0.1
    assert packed_remainder([], H9) == H9


def test_mmss_negative_is_readable():
    assert mmss(-17.74) == "-0:17.74"
    assert mmss(102.28) == "1:42.28"
    assert mmss(0) == "0:00.00"


def test_ev_is_fill_needs_no_contract_line():
    pi = Ev(1, 0, 30, "PER", "T", 5, "PI-505-030: Alien Power", None)
    paid_per = Ev(2, 0, 30, "PER", "T", 6, "PAID PER", 4242)
    assert pi.is_fill and not paid_per.is_fill


# ── plan_window state machine on synthetic hours (inventory patched, no DB) ──
from business_logic.services import finish_service as fs  # noqa: E402

# finish_service imports finish_plan under the `src.` prefix; use ITS Filler so isinstance holds
Filler = fs.Filler


def _plan(rows, inv, monkeypatch):
    monkeypatch.setattr(fs, "load_inventory", lambda cur, market, date: inv)
    return fs.plan_window(None, 1, "2026-09-04", H8, H9, rows=rows)


def _pi(i, ora_s, dur_s, code):
    return _row(i, ora_s, dur_s, "PER", "T", f"PI-{code}: Filler", line=None)


def test_overage_with_room_auto_refills(monkeypatch):
    # program 3500 + paid 60 + existing PIs 120 → -80 with fill, +40 without → strip + refill
    rows = [
        _row(1, H8, 3500, "PGM", "F", "SHOW"),
        _row(2, H8 + 3500, 60, "COM", "T", "PAID60", line=1),
        _pi(3, H8 + 3560, 60, "444-060"),
        _pi(4, H8 + 3620, 60, "467-060"),
        _row(9, H9, 600, "PGM", "F", "NEXT"),
    ]
    inv = [Filler(9001, "PI-900-030: New", 30.0, "PI", "PI-900", 0)]
    r = _plan(rows, inv, monkeypatch)
    assert r["state"] == "ready" and r["ok"]
    assert (
        r["notes"][0].startswith("overage -1:")
        and "2 existing PI/PSA/ID rows removed" in r["notes"][0]
    )
    assert r["n_delete"] == 2 and r["n_insert"] == 2  # :30 PI + ID
    assert not r["strip_only"]


def test_overrun_strips_all_fill_and_stays_writable(monkeypatch):
    # program 3560 + paid 60 = 3620 → program+paid alone spill 20s; two PIs present
    rows = [
        _row(1, H8, 3560, "PGM", "F", "SHOW"),
        _row(2, H8 + 3560, 60, "COM", "T", "PAID60", line=1),
        _pi(3, H8 + 3620, 60, "444-060"),
        _pi(4, H8 + 3680, 30, "505-030"),
        _row(9, H9, 600, "PGM", "F", "NEXT"),
    ]
    r = _plan(rows, [Filler(9001, "PI-900-030: New", 30.0, "PI", "PI-900", 0)], monkeypatch)
    assert r["state"] == "overrun" and r["ok"] and r["strip_only"]
    assert r["n_delete"] == 2 and r["n_insert"] == 0 and r["error"] is None
    assert abs(r["hard_remainder"] + 20.0) < 0.1
    assert any(n.startswith("overrun 0:") and "removing 2 PI/PSA/ID rows" in n for n in r["notes"])
    assert {e["op"] for e in r["edits"]} == {"delete"}


def test_overrun_with_nothing_left_to_strip_refuses(monkeypatch):
    rows = [
        _row(1, H8, 3560, "PGM", "F", "SHOW"),
        _row(2, H8 + 3560, 60, "COM", "T", "PAID60", line=1),
        _row(9, H9, 600, "PGM", "F", "NEXT"),
    ]
    r = _plan(rows, [], monkeypatch)
    assert r["state"] == "overrun" and not r["ok"] and not r["strip_only"]
    assert "no PI/PSA is left to remove" in r["error"]
