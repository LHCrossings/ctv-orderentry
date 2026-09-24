"""
Phase 3 service tests: broadcast month range, reconcile status (incl. the
fractional-cent rounding rule), and the TVB EDI field validators.
"""

from datetime import date

import pytest

from business_logic.services.edi_billing import (
    AffidavitData,
    broadcast_month_range,
    estimate_from_customer_ref,
    generate_edi,
    invoice_info,
    production_invoice,
    reconcile_status,
    validate_invoice,
)

# ── broadcast month ─────────────────────────────────────────────────────────


def test_broadcast_june_2026():
    # matches the R31 period on the validated June invoices: 6/1–6/28
    assert broadcast_month_range(26, 6) == (date(2026, 6, 1), date(2026, 6, 28))


def test_broadcast_august_2025():
    # Aug 1 2025 is a Friday → broadcast August starts Monday July 28
    start, end = broadcast_month_range(25, 8)
    assert start == date(2025, 7, 28)
    assert end == date(2025, 8, 31)  # Sep 1 is a Monday → Aug ends 8/31


def test_broadcast_december_year_rollover():
    start, end = broadcast_month_range(26, 12)
    assert start.weekday() == 0
    assert (end + __import__("datetime").timedelta(days=1)).weekday() == 0
    assert end.year == 2027 or end.month == 12  # ends the day before bcast Jan


# ── reconcile ───────────────────────────────────────────────────────────────


def test_reconcile_exact_match():
    assert reconcile_status(239, 4475.0, 239, 4475.0)["status"] == "match"


def test_reconcile_rounding_2606_042():
    # the real June case: 112 spots, $6,588.24 vs $6,588.40 (56 × $117.647…)
    r = reconcile_status(112, 6588.24, 112, 6588.40)
    assert r["status"] == "rounding"


def test_reconcile_rounding_bound_scales_with_spots():
    # 2 spots: same $0.16 difference cannot be rounding (max 2 × $0.005 = $0.01)
    assert reconcile_status(2, 100.16, 2, 100.00)["status"] == "mismatch"


def test_reconcile_spot_count_mismatch():
    r = reconcile_status(100, 500.0, 99, 500.0)
    assert r["status"] == "mismatch"
    assert "spots" in r["detail"]


def test_reconcile_missing_side():
    assert reconcile_status(None, None, 10, 100.0)["status"] == "missing"


# ── validators ──────────────────────────────────────────────────────────────

GOOD_TEMPLATE = {
    "call_letters": "CRTV",
    "edi_code": "9912591",
    "agency_name": "Media Solutions",
    "representative": "Charmaine Lane",
    "salesperson": "Kelly Wheeler",
    "advertiser_name": "OCHCA",
    "product_name": "Crisis Crossings LA",
    "agency_address": ["707 Commons Drive", "Ste 201", "Sacramento CA", "95825"],
    "payee_address": [
        "Accounts Receivable",
        "901 H Street Ste 120 PMB 91",
        "Sacramento CA",
        "95814",
    ],
    "agency_ad_code": "X",
    "agency_prod_code": "Y",
}
GOOD_INV = {
    "invoice_number": "2606-042",
    "invoice_date": "260630",
    "broadcast_month": "2606",
    "bcast_start": "260601",
    "bcast_end": "260628",
    "estimate_code": "4759",
    "order_number": "2763",
}
GOOD_SPOTS = [
    {
        "run_date": "260601",
        "time_hhmm": "0810",
        "duration": 30,
        "copy_id": "ABC123",
        "rate_cents": 11765,
    }
]


def _errors(issues):
    return [i for i in issues if i["level"] == "error"]


def test_valid_invoice_has_no_errors():
    assert _errors(validate_invoice(GOOD_TEMPLATE, GOOD_INV, GOOD_SPOTS)) == []


def test_advertiser_over_25_chars_is_error():
    inv = dict(GOOD_INV, advertiser_name="A" * 26)
    errs = _errors(validate_invoice(GOOD_TEMPLATE, inv, GOOD_SPOTS))
    assert any(e["field"] == "advertiser_name" for e in errs)


def test_call_letters_must_be_4_chars():
    t = dict(GOOD_TEMPLATE, call_letters="CRT")
    errs = _errors(validate_invoice(t, GOOD_INV, GOOD_SPOTS))
    assert any(e["field"] == "call_letters" for e in errs)


def test_bad_invoice_date_is_error():
    inv = dict(GOOD_INV, invoice_date="6/30/26")
    errs = _errors(validate_invoice(GOOD_TEMPLATE, inv, GOOD_SPOTS))
    assert any(e["field"] == "invoice_date" for e in errs)


def test_no_spots_is_error():
    errs = _errors(validate_invoice(GOOD_TEMPLATE, GOOD_INV, []))
    assert any(e["field"] == "spots" for e in errs)


def test_five_address_lines_is_error():
    # the BVK June 8 template-edit incident: 5th line (zip) silently dropped
    t = dict(GOOD_TEMPLATE, agency_address=["a", "b", "c", "d", "e"])
    errs = _errors(validate_invoice(t, GOOD_INV, GOOD_SPOTS))
    assert any(e["field"] == "agency_address" for e in errs)


def test_empty_ad_codes_warn_not_error():
    t = dict(GOOD_TEMPLATE, agency_ad_code="", agency_prod_code="")
    issues = validate_invoice(t, GOOD_INV, GOOD_SPOTS)
    assert _errors(issues) == []
    assert any(i["field"] == "agency_ad_code" and i["level"] == "warn" for i in issues)


def test_comment_over_130_is_error():
    inv = dict(GOOD_INV, comment_top="x" * 131)
    errs = _errors(validate_invoice(GOOD_TEMPLATE, inv, GOOD_SPOTS))
    assert any(e["field"] == "comment_top" for e in errs)


# ── R34 commission from affidavit net (Lee, 2026-09-02) ─────────────────────
# TVInvoices carries 2-decimal rates, so the EDI gross drifts from our
# affidavit gross; the commission (a dollar field) absorbs the drift so the
# EDI net equals our invoice to the penny.

from pathlib import Path

from business_logic.services.edi_billing import (
    commission_plan,
    parse_affidavit,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edi"


def test_commission_plan_2606_042_absorbs_rate_drift():
    # EDI gross 56 × $117.65 = $6,588.40; our net $5,600.00
    plan = commission_plan(658840, 112, 15.0, 560000)
    assert plan["mode"] == "net"
    assert plan["commission"] == 98840  # not the 15% figure 98826
    assert plan["pct_commission"] == 98826
    assert plan["net"] == 560000
    assert plan["delta_cents"] == 14


def test_commission_plan_2605_054():
    plan = commission_plan(329420, 56, 15.0, 280000)
    assert plan["mode"] == "net"
    assert (plan["commission"], plan["net"]) == (49420, 280000)


def test_commission_plan_without_net_is_plain_percentage():
    plan = commission_plan(447500, 239, 15.0)
    assert plan["mode"] == "pct"
    assert (plan["commission"], plan["net"]) == (67125, 380375)


def test_commission_plan_beyond_rounding_falls_back():
    # 2 spots: a $1.00 gap cannot be rate rounding (max 2 × $0.005)
    plan = commission_plan(20000, 2, 15.0, 16900)
    assert plan["mode"] == "pct-fallback"
    assert plan["commission"] == 3000  # percentage, not 3100
    assert plan["net"] == 17000


def test_r34_uses_affidavit_net():
    spots = [dict(GOOD_SPOTS[0]) for _ in range(56)]
    inv = {**GOOD_INV, "gross_cents": 658840, "spot_count": 112, "net_cents": 560000}
    r34 = next(
        ln for ln in generate_edi(GOOD_TEMPLATE, inv, spots).splitlines() if ln.startswith("34;")
    )
    assert r34 == "34;;658840;98840;560000;;;;;;;;112;;;;"


def test_r34_without_net_unchanged():
    inv = {**GOOD_INV, "gross_cents": 447500, "spot_count": 239}
    r34 = next(
        ln
        for ln in generate_edi(GOOD_TEMPLATE, inv, GOOD_SPOTS).splitlines()
        if ln.startswith("34;")
    )
    assert r34 == "34;;447500;67125;380375;;;;;;;;239;;;;"


def test_validate_warns_when_net_override_out_of_tolerance():
    inv = {**GOOD_INV, "gross_cents": 11765, "spot_count": 1, "net_cents": 9000}
    issues = validate_invoice(GOOD_TEMPLATE, inv, GOOD_SPOTS)
    assert [i for i in issues if i["field"] == "commission" and i["level"] == "warn"]
    assert not _errors(issues)


def test_validate_silent_when_net_override_is_rounding_sized():
    inv = {**GOOD_INV, "gross_cents": 11765, "spot_count": 1, "net_cents": 10000}
    issues = validate_invoice(GOOD_TEMPLATE, inv, GOOD_SPOTS)
    assert not [i for i in issues if i["field"] == "commission"]


def test_net_due_regex_survives_split_digits():
    # Real June affidavits: the renderer drops a space into the figure.
    from business_logic.services.edi_billing import _NET_DUE_RE, _money

    for line, want in [
        ("Net Amount Due $ 3 ,803.75", 3803.75),
        ("Net Amount Due $ 9 99.94", 999.94),
        ("Net Amount Due $ 8 ,478.75", 8478.75),
        ("Net Amount Due $ 5,600.00", 5600.00),
    ]:
        assert _money(_NET_DUE_RE.search(line).group(1)) == want


def test_parse_affidavit_reads_commission_and_net(real_pdfplumber):
    a = parse_affidavit((_FIXTURES / "2606-042_affidavit.pdf").read_bytes(), source="fixture")
    assert a.invoice_id == "2606-042"
    assert a.total_spots == 112
    assert a.gross_amount == 6588.24
    assert a.commission_amount == 988.24
    assert a.net_amount == 5600.00


def test_parse_affidavit_crispin_mmi_2608_019(real_pdfplumber):
    """Crispin/MMI affidavit: Estimate field reads 'Order 212735, Est 0001' — the
    order number must reach rep_order_number without a Davis-Elen 'Order #:' box."""
    a = parse_affidavit((_FIXTURES / "2608-019_affidavit.pdf").read_bytes(), source="fixture")
    assert a.invoice_id == "2608-019"
    assert a.contract_no == "3012"
    assert a.market == "SFO"
    assert a.advertiser == "Bay Area Air Quality Management District (Crispin)"
    assert a.total_spots == 96
    assert a.gross_amount == 6211.92
    assert a.commission_amount == 931.79
    assert a.net_amount == 5280.13
    assert a.rep_order_number == "212735"
    assert a.comment_bottom == "BAAQ 2026 TV SUMMERCAMPAIGN"


def test_parse_affidavit_production_only_2608_020(real_pdfplumber):
    """A production-only affidavit has no COPY LIST: zero spots, gross from the
    plain Subtotals line, flagged is_production."""
    a = parse_affidavit((_FIXTURES / "2608-020_affidavit.pdf").read_bytes(), source="fixture")
    assert a.is_production is True
    assert a.invoice_id == "2608-020"
    assert a.contract_no == "3012"
    assert a.total_spots == 0
    assert a.gross_amount == 2447.06
    assert a.commission_amount == 367.06
    assert a.net_amount == 2080.00


def test_airtime_affidavit_is_not_production(real_pdfplumber):
    a = parse_affidavit((_FIXTURES / "2608-019_affidavit.pdf").read_bytes(), source="fixture")
    assert a.is_production is False


def test_estimate_from_customer_ref():
    assert estimate_from_customer_ref("Order 212735, Est 0001") == "0001"
    assert estimate_from_customer_ref("Est. 4807") == "4807"
    assert estimate_from_customer_ref("") == ""
    assert estimate_from_customer_ref("PO 12345") == ""


def _strip_pad(line: str) -> str:
    return line.rstrip(";")


def test_production_invoice_reproduces_bvk_2605_011():
    """Oracle: Lee's hand-built 2605-011_BVK_PROD.txt (May 2026 TVInvoices upload).
    Every populated field of records 31/32/51/33/34/12 must match; only the
    trailing empty-field padding differs (the generator pads to the spec width)."""
    a = AffidavitData(
        invoice_id="2605-011",
        market="CVC",
        gross_amount=3176.47,
        commission_amount=476.47,
        net_amount=2700.00,
        is_production=True,
        comment_bottom="The above charges are for production only. Airtime",
    )
    inv = invoice_info("2605-011.csv")
    inv.update(order_number="2738", agency_ad_code="UCD", agency_prod_code="UCD")
    inv, spots = production_invoice(inv, a, "4807")
    assert inv["estimate_code"] == "4807PRD"
    assert spots == [
        {
            "run_date": "260515",
            "time_hhmm": "0611",
            "duration": 30,
            "copy_id": "PRODUCTION",
            "rate_cents": 317647,
            "market": "CVC",
        }
    ]
    template = {
        "representative": "Charmaine Lane",
        "salesperson": "Jennifer Murphy",
        "advertiser_name": "UC Davis Health",
        "product_name": "UC Davis Health",
        "commission_pct": 15,
    }
    got = [
        _strip_pad(ln)
        for ln in generate_edi(template, inv, spots).splitlines()
        if ln[:2] in ("31", "32", "51", "33", "34", "12")
    ]
    assert got == [
        "31;Charmaine Lane;Jennifer Murphy;UC Davis Health;UC Davis Health;260531;;4807PRD;"
        "2605-011;2605;260427;260531;260427;260531;260427;260531;;;Y;;;;2738;;UCD;;UCD",
        "32;EST 4807 PRODUCTION",
        "51;Y;260515;;0611;30;PRODUCTION;317647",
        "33;PRODUCTION CHARGES",
        "34;;317647;47647;270000;;;;;;;;1",
        "12;1;317647",
    ]
    assert validate_invoice(template | {"call_letters": "CRSE"}, inv, spots) == []


def test_production_invoice_keeps_operator_estimate_and_needs_gross():
    a = AffidavitData(
        invoice_id="2608-020",
        market="SFO",
        gross_amount=2447.06,
        net_amount=2080.0,
        is_production=True,
    )
    inv = invoice_info("2608-020.csv")
    inv["estimate_code"] = "0001PRD"  # already set on the page → not overwritten
    inv, spots = production_invoice(inv, a, "9999")
    assert inv["estimate_code"] == "0001PRD"
    assert inv["comment_top"] == "EST 9999 PRODUCTION"
    assert inv["net_cents"] == 208000 and inv["gross_cents"] == 244706
    assert (inv["bcast_start"], inv["bcast_end"]) == ("260727", "260830")
    with pytest.raises(ValueError):
        production_invoice(
            invoice_info("2608-021.csv"),
            AffidavitData(invoice_id="2608-021", is_production=True),
            "",
        )
