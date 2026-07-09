"""
Phase 3 service tests: broadcast month range, reconcile status (incl. the
fractional-cent rounding rule), and the TVB EDI field validators.
"""
from datetime import date

from business_logic.services.edi_billing import (
    broadcast_month_range,
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
    assert end == date(2025, 8, 31)   # Sep 1 is a Monday → Aug ends 8/31


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
    "call_letters": "CRTV", "edi_code": "9912591",
    "agency_name": "Media Solutions", "representative": "Charmaine Lane",
    "salesperson": "Kelly Wheeler", "advertiser_name": "OCHCA",
    "product_name": "Crisis Crossings LA",
    "agency_address": ["707 Commons Drive", "Ste 201", "Sacramento CA", "95825"],
    "payee_address": ["Accounts Receivable", "901 H Street Ste 120 PMB 91",
                      "Sacramento CA", "95814"],
    "agency_ad_code": "X", "agency_prod_code": "Y",
}
GOOD_INV = {
    "invoice_number": "2606-042", "invoice_date": "260630",
    "broadcast_month": "2606", "bcast_start": "260601", "bcast_end": "260628",
    "estimate_code": "4759", "order_number": "2763",
}
GOOD_SPOTS = [{"run_date": "260601", "time_hhmm": "0810", "duration": 30,
               "copy_id": "ABC123", "rate_cents": 11765}]


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
