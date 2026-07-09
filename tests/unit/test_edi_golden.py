"""
Golden tests for TVB EDI .txt generation.

Fixtures under tests/fixtures/edi_golden/ were captured 2026-07-09 from three
real June-2026 invoice pairs (BVK UC Davis, Davis Elen WA McD, TCAA) via the
production scan → generate flow. Each *_input.json freezes the exact
(template, inv, spots) inputs; the paired .txt is the byte-exact output.

The EDI billing redesign (tasks/edi-billing-redesign.md) requires generated
output to remain byte-identical for identical inputs through every refactor
phase. If this test fails, the output format changed — that is a bug unless
the format change was explicitly requested; re-capturing fixtures to make it
pass is not allowed without sign-off.
"""
import json
from pathlib import Path

import pytest

from business_logic.services.edi_billing import generate_edi as _generate_edi

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "edi_golden"
CASES = sorted(FIXTURE_DIR.glob("*_input.json"))


def test_fixtures_exist():
    assert len(CASES) >= 3, f"expected >=3 golden fixtures in {FIXTURE_DIR}"


@pytest.mark.parametrize("fixture", CASES, ids=[c.stem for c in CASES])
def test_edi_output_byte_identical(fixture):
    data = json.loads(fixture.read_text())
    expected = (FIXTURE_DIR / data["expected"]).read_text()
    actual = _generate_edi(data["template"], data["inv"], data["spots"])
    assert actual == expected, (
        f"EDI output for {data['expected']} is no longer byte-identical "
        "(see tests/unit/test_edi_golden.py docstring)"
    )
