"""Separation columns are literal: `separation_order` IS the order interval and
`separation_event` IS the event interval, and the tuple handed around is
(customer, order, event) — what `add_contract_line(separation_intervals=)`, the
orchestrator's confirm prompt and the Customers page all use.

Lee, 2026-10-06: the (customer, event, order) tuple that most gathers used was a
relic of the old Etere web line form, which had Order and Event swapped (Etere
fixed it). We bypass the form now, so a swapped reader/writer silently stores the
order interval in the event column (Covered California 386 on 10/6)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.domain.entities import Customer  # noqa: E402

AUTOMATIONS = sorted((ROOT / "browser_automation").glob("*_automation.py"))

# writer: tuple index 1 must land in separation_order, index 2 in separation_event
_SWAPPED_WRITER = re.compile(
    r"separation_event\s*=\s*(?:int\()?\w+\[1\]|separation_order\s*=\s*(?:int\()?\w+\[2\]"
)
# reader: an attribute/subscript read of the event column must never precede the
# order column in a tuple (plain column-name lists in SQL text are not reads)
_SWAPPED_READER = re.compile(
    r"""(?:\w+\.|\w*\[['"])separation_event['"\]]*\s*(?:or 0)?,\s*(?:\w+\.|\w*\[['"])separation_order\b"""
)
_SWAPPED_LOCAL = re.compile(r"\(\s*sep_c\s*,\s*sep_e\s*,\s*sep_o\s*\)")


def test_no_automation_writes_or_reads_the_columns_swapped():
    bad = []
    for path in AUTOMATIONS:
        text = path.read_text()
        for rx in (_SWAPPED_WRITER, _SWAPPED_READER, _SWAPPED_LOCAL):
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                bad.append(f"{path.name}:{line}: {m.group(0)!r}")
    assert not bad, "swapped separation column access:\n  " + "\n  ".join(bad)


def test_structural_regexes_catch_the_old_shape():
    assert _SWAPPED_WRITER.search("separation_event=separation[1],")
    assert _SWAPPED_READER.search("(s0, cust.separation_event, cust.separation_order)")
    assert _SWAPPED_READER.search("customer.separation_event,\n    customer.separation_order,")
    assert _SWAPPED_READER.search("row['separation_event']    or 0,\n row['separation_order']")
    assert not _SWAPPED_READER.search("(separation_customer, separation_event, separation_order)")


def test_customer_entity_tuple_is_customer_order_event():
    c = Customer(
        customer_id="1",
        customer_name="x",
        order_type=None,
        separation_customer=10,
        separation_order=15,
        separation_event=0,
    )
    assert c.get_separation_intervals() == (10, 15, 0)
