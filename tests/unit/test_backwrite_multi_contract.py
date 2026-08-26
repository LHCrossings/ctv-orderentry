"""Multi-estimate manifests must backwrite EVERY contract, each against its own
IO estimate.

Regression for Sac County Voters (and HL ACM Q4 Toyota 3026-3028): a manifest
with N contracts always backwrote contracts[0], reconciled "green" (the
whole-PDF io_detail has empty top-level lines, so the IO check silently
no-oped), and archived the whole manifest — the other contracts never
backwrote.
"""

import sys
from pathlib import Path

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from src.web.routes.orders import _io_detail_for_contract  # noqa: E402


def _manifest(codes, subs, top=None):
    detail = dict(top or {})
    detail.setdefault("lines", [])
    if subs is not None:
        detail["sub_orders"] = subs
    return {
        "contracts": [{"code": c} for c in codes],
        "io_detail": detail,
    }


def _sub(est, tag):
    return {"estimate_number": est, "lines": [{"description": tag}]}


class TestIoDetailForContract:
    def test_matches_by_unique_estimate_number_in_code(self):
        m = _manifest(
            ["HL Toyota 13937 CV", "HL Toyota 13938 CV", "HL Toyota 13939 CV"],
            [_sub("13937", "oct"), _sub("13938", "nov"), _sub("13939", "dec")],
        )
        for i, tag in enumerate(["oct", "nov", "dec"]):
            assert _io_detail_for_contract(m, i)["lines"][0]["description"] == tag

    def test_old_est_suffix_codes_fall_back_to_index(self):
        # Pre-fix codes contain TWO estimate numbers ('… 13937 CV Est 13938') —
        # containment matching is ambiguous, index matching is correct (entry
        # walked the same parsed list that produced the sub_orders).
        m = _manifest(
            [
                "HL Toyota 13937 CV Est 13937",
                "HL Toyota 13937 CV Est 13938",
                "HL Toyota 13937 CV Est 13939",
            ],
            [_sub("13937", "oct"), _sub("13938", "nov"), _sub("13939", "dec")],
        )
        assert _io_detail_for_contract(m, 1)["lines"][0]["description"] == "nov"
        assert _io_detail_for_contract(m, 2)["lines"][0]["description"] == "dec"

    def test_index_match_when_estimates_missing(self):
        m = _manifest(
            ["A", "B"],
            [_sub("", "one"), _sub("", "two")],
        )
        assert _io_detail_for_contract(m, 0)["lines"][0]["description"] == "one"
        assert _io_detail_for_contract(m, 1)["lines"][0]["description"] == "two"

    def test_single_contract_single_sub_order(self):
        m = _manifest(["X"], [_sub("100", "only")])
        assert _io_detail_for_contract(m, 0)["lines"][0]["description"] == "only"

    def test_no_sub_orders_returns_whole_detail(self):
        m = _manifest(["X"], None, top={"lines": [{"description": "flat"}]})
        assert _io_detail_for_contract(m, 0)["lines"][0]["description"] == "flat"

    def test_count_mismatch_without_estimates_returns_none(self):
        m = _manifest(["A", "B", "C"], [_sub("", "one"), _sub("", "two")])
        assert _io_detail_for_contract(m, 2) is None

    def test_parse_error_returns_none(self):
        m = {"contracts": [{"code": "X"}], "io_detail": {"error": "boom"}}
        assert _io_detail_for_contract(m, 0) is None


def test_multi_order_rollup_carries_rates_are_net():
    """parser_bridge's multi-order roll-up must expose rates_are_net so the
    manifest's top-level flag (which gates gross-up) is right for net-rate
    multi-estimate PDFs."""
    from dataclasses import dataclass, field
    from types import SimpleNamespace
    from unittest.mock import patch

    from src.web.parser_bridge import get_order_detail

    @dataclass
    class FakeOrder:
        client: str = "C"
        rates_are_net: bool = False
        lines: list = field(default_factory=list)

    fake = [FakeOrder(), FakeOrder(rates_are_net=True)]
    fake_module = SimpleNamespace(parse_hl_pdf=lambda p: fake)
    with patch("src.web.parser_bridge.importlib.import_module", return_value=fake_module):
        detail = get_order_detail(Path("fake.pdf"), "HL")
    assert detail.get("sub_orders") and len(detail["sub_orders"]) == 2
    assert detail["rates_are_net"] is True
