"""Etere's station names reach the backwrite as they are spelled in Etere. Market 2 is
'CHI MSP' there (never renamed - Etere charges for it), and the IO side says CMP; the
two must meet in `_normalise_market` or the IO-vs-Etere check refuses the backwrite
(Lee, Illinois Lottery 2026-10-01)."""

import sys
from datetime import date
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in [str(_root), str(_root / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backwrite.eterebridge_runner import build_placement_csv_from_db  # noqa: E402,F401
from backwrite.transformer import SpotRow, _normalise_market, reconcile_io_vs_etere  # noqa: E402


@pytest.mark.parametrize(
    "raw,code",
    [
        ("CHI MSP", "CMP"),
        ("chi msp", "CMP"),
        ("CHI-MSP", "CMP"),
        ("Chicago", "CMP"),
        ("SAN FRANCISCO", "SFO"),
        ("Central Valley", "CVC"),
        ("WDC", "WDC"),
        ("MMT", "MMT"),
    ],
)
def test_every_station_name_the_runner_emits_normalises(raw, code):
    assert _normalise_market(raw) == code


def test_runner_station_names_all_resolve_to_codes():
    """The runner's COD_USER -> station-name table must round-trip through the
    normaliser; a new station spelling that does not is a refused backwrite."""
    import inspect
    import re

    src = inspect.getsource(build_placement_csv_from_db)
    names = re.findall(r'^\s*\d+:\s*"([^"]+)",', src, re.M)
    assert len(names) == 10, names
    for name in names:
        assert re.fullmatch(r"[A-Z]{3}", _normalise_market(name)), name


def _spot(market: str) -> SpotRow:
    return SpotRow(
        contract_code="Flowers ILLot 2510",
        client="Illinois Lottery",
        line_id="64352",
        priority="500",
        duration_s=15,
        flight_start=date(2026, 2, 2),
        time_from="19:00",
        time_to="21:00",
        gross_rate=33.0,
        days_pattern="M-F",
        market=market,
        air_date=date(2026, 2, 3),
        air_time="19:12:00",
        copy_code="ILLOT15M",
        row_description="M-F Cant and Mand PM News",
    )


def test_reconcile_accepts_chi_msp_for_a_cmp_order():
    spots = [_spot("CMP")]  # parse_csv has already normalised 'CHI MSP' to CMP
    io_detail = {"lines": [{"market": "CMP", "rate": 33.0, "total_spots": 1, "is_bonus": False}]}
    rec = reconcile_io_vs_etere(io_detail, spots, 15.0, False, True)
    assert rec["ok"], rec["messages"]
    assert not any("Missing market" in m for m in rec["messages"])


def test_reconcile_still_refuses_a_truly_missing_market():
    spots = [_spot("SFO")]
    io_detail = {"lines": [{"market": "CMP", "rate": 33.0, "total_spots": 1, "is_bonus": False}]}
    rec = reconcile_io_vs_etere(io_detail, spots, 15.0, False, True)
    assert not rec["ok"] and any("Missing market" in m for m in rec["messages"])
