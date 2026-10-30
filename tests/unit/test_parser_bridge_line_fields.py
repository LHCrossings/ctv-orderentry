"""Every registered parser's line object must normalize to real money and spots.

PG&E Wildfire Safety Q4-2026 (Imprenta, 2026-10-20): ImprentaLine stores
``rate_net`` / ``rate_gross`` / ``spots_by_week``, none of which the generic
``_normalize_line`` reads, so the manifest's IO detail carried three lines at
$0 / 0 spots and the one-click backwrite built a $0 Sales Confirmation against
a $7,528.96 run sheet. Lexus had the identical shape. This test pins the
dedicated normalizer AND sweeps every parser in the registry so the next line
class with its own field names fails here, not at the AE's reconcile popup.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
from dataclasses import dataclass, field
from datetime import date

import pytest

from web import parser_bridge as pb

RATE_NAMES = {"rate", "gross_rate", "net_rate", "rate_per_spot", "cost"}
SPOT_NAMES = {"weekly_spots", "spots_per_week", "total_spots", "spots", "num_spots"}


def _dedicated_keys() -> set[str]:
    src = inspect.getsource(pb.get_order_detail)
    return {k for k in pb._REGISTRY if f'order_type == "{k}"' in src}


@dataclass
class _Line:  # ImprentaLine / LexusLine field shape
    program: str
    days: str
    time: str
    duration: int
    rate_net: float
    rate_gross: float
    spots_by_week: list[int]
    week_date_ranges: list[tuple[date, date]]
    market: str
    is_bonus: bool = False
    language: str = ""


@dataclass
class _Order:
    lines: list[_Line]
    flight_start: date = date(2026, 9, 28)
    flight_end: date = date(2026, 12, 27)
    campaign: str = "Wildfire Safety Q4"
    client: str = "PG&E"
    market: str = "SFO"
    estimate: str = ""
    extra: dict = field(default_factory=dict)


def _order():
    weeks = [
        (
            date(2026, 9, 28) + __import__("datetime").timedelta(days=7 * i),
            date(2026, 10, 4) + __import__("datetime").timedelta(days=7 * i),
        )
        for i in range(4)
    ]
    return _Order(
        lines=[
            _Line(
                "Cantonese News/Talk",
                "M-F",
                "7p-8p; 11:30p-12a",
                30,
                40.0,
                47.06,
                [0, 4, 4, 4],
                weeks,
                "SFO",
            ),
            _Line("Mandarin (News)", "M-Su", "8p-9p", 30, 55.5, 65.29, [3, 3, 3, 0], weeks, "SFO"),
            _Line(
                "Chinese ROS Bonus",
                "M-Su",
                "6a-11:59p",
                30,
                0.0,
                0.0,
                [2, 2, 2, 2],
                weeks,
                "SFO",
                True,
            ),
        ]
    )


def test_imprenta_shape_normalizes_to_net_rates_and_spots():
    d = pb._normalize_net_weekly(_order(), agency="Imprenta")
    assert d["rates_are_net"] is True
    paid = [ln for ln in d["lines"] if not ln["is_bonus"]]
    assert [ln["rate"] for ln in paid] == [40.0, 55.5]  # NET, never the rounded gross
    assert [ln["total_spots"] for ln in d["lines"]] == [12, 9, 8]
    assert d["lines"][2]["rate"] == 0.0
    assert d["total_cost"] == round(40.0 * 12 + 55.5 * 9, 2)
    # A line's flight is the weeks that carry spots, not the sheet's full grid.
    assert d["lines"][0]["start_date"] == "2026-10-05"
    assert d["lines"][1]["end_date"] == "2026-10-18"
    assert d["client"] == "PG&E" and d["markets"] == ["SFO"]


@pytest.mark.parametrize("key", ["IMPRENTA", "LEXUS"])
def test_imprenta_and_lexus_route_to_the_dedicated_normalizer(key):
    assert key in _dedicated_keys()


def _line_classes(mod_name: str):
    try:
        mod = importlib.import_module(mod_name)
    except Exception:  # noqa: BLE001 - optional deps (pdfplumber shim etc.)
        return []
    out = []
    for name, cls in inspect.getmembers(mod, inspect.isclass):
        if dataclasses.is_dataclass(cls) and cls.__module__ == mod_name and "line" in name.lower():
            out.append(cls)
    return out


@pytest.mark.parametrize("key", sorted(pb._REGISTRY))
def test_every_parser_line_class_is_readable_by_its_normalizer(key):
    """A parser with its own field names needs its own normalizer; the generic
    one must find a rate AND a spot count by name, or the backwrite builds $0."""
    if key in _dedicated_keys():
        return
    mod_name, _fn = pb._REGISTRY[key]
    for cls in _line_classes(mod_name):
        names = {f.name for f in dataclasses.fields(cls)}
        names |= {n for n, v in inspect.getmembers(cls) if isinstance(v, property)}
        has_money_field = any("rate" in n or "cost" in n or "price" in n for n in names)
        if has_money_field:
            assert names & RATE_NAMES, (
                f"{key}: {cls.__name__} stores money under {sorted(n for n in names if 'rate' in n or 'cost' in n)} "
                "— the generic normalizer reads none of them (add a dedicated normalizer)"
            )
        if any("spot" in n or "week" in n for n in names):
            assert names & SPOT_NAMES, (
                f"{key}: {cls.__name__} stores spots under {sorted(n for n in names if 'spot' in n or 'week' in n)} "
                "— the generic normalizer reads none of them"
            )


def test_sweep_catches_the_imprenta_shape():
    """Self-test: the sweep's predicate rejects the exact field set that broke."""
    names = {f.name for f in dataclasses.fields(_Line)}
    assert not (names & RATE_NAMES) and not (names & SPOT_NAMES)


def test_harrahs_routes_to_the_dedicated_normalizer():
    """A monthly estimate with no dates on the sheet is dated from the gathered
    answers (contract 3231, 2026-10-30) — the generic normalizer stored one month."""
    assert "HARRAHS" in _dedicated_keys()
