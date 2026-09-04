"""Admerasia is the AGENCY; the advertiser is the IO's `Ref:` line.

Seoul Medical Group (SMG, ANAGRAF 478) arrived 2026-09-04 on the exact McDonald's IO
layout. Every Admerasia IO — SMG's included — quotes McDonald's boilerplate in its
notes ("Kindly do NOT provide any BONUS ads for McDonald's"), so the client must be
read from the Ref: line and nowhere else, or SMG books as McDonald's 42.
"""

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

_root = Path(__file__).parent.parent.parent
for _p in (_root, _root / "src", _root / "browser_automation"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browser_automation.parsers.admerasia_parser import (  # noqa: E402
    AdmerasiaOrder,
    get_customer_id_from_client,
    get_default_notes,
    get_default_order_code,
    get_default_order_description,
    get_default_separation_intervals,
    resolve_admerasia_client,
)
from business_logic.services.order_detection_service import (  # noqa: E402
    OrderDetectionService,
)
from domain.enums import OrderType  # noqa: E402
from web.parser_bridge import _normalize_admerasia  # noqa: E402

SMG_HEADER = (
    "Ref: Seoul Medical Group\nCampaign: SMG WA 2026\n"
    "Campaign Period: 10/15/2026 - 12/7/2026\nDMA: Seattle\nNo religious shows"
)
MCD_HEADER = (
    "Ref: McDonald's\nCampaign: Hot Honey - 2026\n"
    "Campaign Period: 2/2/2026 - 3/1/2026\nDMA: Seattle\nNo religious shows"
)

# First-page text of 01-SMG-2610VT with the McDonald's boilerplate the agency
# prints in the notes of EVERY IO, whoever the client is.
SMG_PAGE_TEXT = """Ref: Seoul Medical Group
Campaign: SMG WA 2026
Campaign Period: 10/15/2026 - 12/7/2026
DMA: Seattle
No religious shows
Admerasia, Inc.
520 W 27th St Crossings TV
Order Number: 01-SMG-2610VT
Order Date: 9/3/2026
Version: Original
Broadcast Order
(M-F) CBN NEWS / Featured News PST11:00a-11:30a $ 51.00 1 1 1 38 $ 1 ,938.00
5) Kindly do NOT provide any BONUS ads for McDonald's
7) Kindly prioritize premium placement for MD spots
"""


def _order(header, number, markets=("Seattle",), start=date(2026, 10, 15)):
    return AdmerasiaOrder(
        order_number=number,
        order_date=date(2026, 9, 3),
        header_text=header,
        markets=list(markets),
        language="Vietnamese",
        week_start_dates=[start],
    )


# ── client resolution ───────────────────────────────────────────────────────


def test_smg_resolves_from_the_ref_line_despite_mcdonalds_in_the_notes():
    text = SMG_HEADER + "\n5) Kindly do NOT provide any BONUS ads for McDonald's"
    client = resolve_admerasia_client(text)
    assert client.name == "Seoul Medical Group"
    assert client.customer_id == 478
    assert client.abbreviation == "SMG"
    assert client.separation == (15, 0, 0)


def test_mcdonalds_profile_unchanged():
    client = resolve_admerasia_client(MCD_HEADER)
    assert (client.customer_id, client.abbreviation, client.separation) == (42, "McD", (3, 5, 0))
    assert get_customer_id_from_client(MCD_HEADER) == 42
    assert get_default_separation_intervals(MCD_HEADER) == (3, 5, 0)


def test_unknown_client_gets_no_customer_id_so_the_gather_prompts():
    client = resolve_admerasia_client("Ref: Golden Bridge Dental\nDMA: Houston")
    assert client.customer_id is None
    assert client.name == "Golden Bridge Dental"
    assert client.abbreviation == "GBD"
    assert client.separation == (15, 0, 0)
    assert get_customer_id_from_client("Ref: Golden Bridge Dental") is None


def test_no_ref_line_is_unknown_not_mcdonalds():
    """The McDonald's boilerplate alone must never identify the client."""
    client = resolve_admerasia_client("5) Kindly do NOT provide any BONUS ads for McDonald's")
    assert client.customer_id is None


# ── contract code / description ─────────────────────────────────────────────


def test_smg_code_and_description():
    o = _order(SMG_HEADER, "01-SMG-2610VT")
    assert get_default_order_code(o) == "Admerasia SMG 1SE 2610"
    assert get_default_order_description(o) == "Seoul Medical Group Est 1 SEA 2610-2612"
    assert o.client_name == "Seoul Medical Group"


def test_smg_second_estimate_and_month_follow_the_io():
    header = SMG_HEADER.replace("10/15/2026 - 12/7/2026", "9/7/2026 - 9/27/2026")
    o = _order(header, "07-SMG-2609CT", start=date(2026, 9, 7))
    assert get_default_order_code(o) == "Admerasia SMG 7SE 2609"
    # single calendar month → no range
    assert get_default_order_description(o) == "Seoul Medical Group Est 7 SEA 2609"


def test_mcdonalds_code_and_description_unchanged():
    o = _order(MCD_HEADER, "11-MD10-2602CT", start=date(2026, 2, 2))
    assert get_default_order_code(o) == "Admerasia McD 11SE 2602"
    # 2/2–3/1 touches February and March on the calendar → range in the description only
    assert get_default_order_description(o) == "McDonald's Est 11 SEA 2602-2603"


def test_description_range_falls_back_to_flight_dates_without_a_campaign_period():
    o = _order("Ref: Seoul Medical Group\nDMA: Seattle", "01-SMG-2610VT")
    o.week_start_dates = [date(2026, 10, 15), date(2026, 11, 26)]  # flight end = 12/2
    assert get_default_order_description(o) == "Seoul Medical Group Est 1 SEA 2610-2612"


def test_notes_fallback_names_the_real_client():
    o = _order("", "01-SMG-2610VT")
    assert get_default_notes(o) == "Unknown Order 01-SMG-2610VT"
    assert get_default_notes(_order(SMG_HEADER, "01-SMG-2610VT")) == SMG_HEADER


# ── detection ───────────────────────────────────────────────────────────────


@pytest.fixture
def service():
    return OrderDetectionService()


def test_detect_smg_io_as_admerasia(service):
    assert service.detect_from_text(SMG_PAGE_TEXT) == OrderType.ADMERASIA


def test_detect_smg_io_without_any_mcdonalds_mention(service):
    text = "\n".join(
        ln for ln in SMG_PAGE_TEXT.splitlines() if "McDonald" not in ln and "MD spots" not in ln
    )
    assert "McDonald" not in text
    assert service.detect_from_text(text) == OrderType.ADMERASIA


def test_admerasia_name_alone_is_still_not_enough(service):
    assert service.detect_from_text("Admerasia, Inc.\nSome other client\n") != OrderType.ADMERASIA


def test_client_name_comes_from_the_ref_line(service):
    assert (
        service.extract_client_name(SMG_PAGE_TEXT, None, OrderType.ADMERASIA)
        == "Seoul Medical Group"
    )


# ── web preview ─────────────────────────────────────────────────────────────


def test_bridge_preview_reports_the_real_client():
    o = _order(SMG_HEADER, "01-SMG-2610VT")
    assert _normalize_admerasia(o)["client"] == "Seoul Medical Group"


def test_bridge_preview_keeps_mcdonalds_default_for_legacy_objects():
    legacy = SimpleNamespace(order_number="11-MD10-2602CT", lines=[], language="Chinese")
    assert _normalize_admerasia(legacy)["client"] == "McDonald's"
