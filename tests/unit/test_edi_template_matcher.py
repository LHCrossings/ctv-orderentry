"""
Template matcher tests (EDI billing redesign Phase 2).

Includes regression tests for the three confirmed misdetections from
tasks/edi-billing-redesign.md §1:
- McDonald's invoices pulled the Davis Elen SoCal Toyota template
- Thunder Valley invoices pulled RPM Muckleshoot
- any filename containing "media" pulled Ocean Media BetMGM
"""

from business_logic.services.edi_billing import (
    TemplateMatch,
    match_template,
    normalize_market,
    resolve_market,
)

# Minimal stand-ins for the real template JSONs (real names + real Etere IDs
# verified live 2026-07-09).
T_DAVIS_WA_MCD = {
    "name": "Davis Elen WA McD", "agency_name": "Daviselen",
    "advertiser_match": "Western WA Operator Association (McD)",
    "market_match": "SEA", "etere_customer_ids": [122],
}
T_DAVIS_SOCAL_TOYOTA = {
    "name": "Davis Elen SoCal Toyota", "agency_name": "Daviselen",
    "advertiser_match": "Southern CA Toyota Dealers Association",
    "market_match": "LAX", "etere_customer_ids": [362],
}
T_RPM_MUCKLESHOOT = {
    "name": "RPM Muckleshoot", "agency_name": "RPM Advertising",
    "advertiser_match": "Muckleshoot Casino", "market_match": "SEA",
    "etere_customer_ids": [129],
}
T_RPM_TVC_CVC = {
    "name": "RPM TVC (CVC)", "agency_name": "RPM Advertising",
    "advertiser_match": "Thunder Valley Casino", "market_match": "CVC",
    "etere_customer_ids": [68],
}
T_RPM_TVC_SFO = {
    "name": "RPM TVC (SFO)", "agency_name": "RPM Advertising",
    "advertiser_match": "Thunder Valley Casino", "market_match": "SFO",
    "etere_customer_ids": [68],
}
T_OCEAN_BETMGM = {
    "name": "Ocean Media BetMGM", "agency_name": "Ocean Media, LLC",
    "advertiser_match": "", "market_match": "",
    "etere_customer_ids": [999],
}
T_BVK = {
    "name": "BVK UC Davis Health", "agency_name": "BVK",
    "advertiser_match": "", "market_match": "",
    "etere_customer_ids": [93],
}

ALL = [
    # alphabetical-ish, Muckleshoot before TVC — order must NOT matter for ID matches
    T_BVK, T_DAVIS_SOCAL_TOYOTA, T_DAVIS_WA_MCD, T_OCEAN_BETMGM,
    T_RPM_MUCKLESHOOT, T_RPM_TVC_CVC, T_RPM_TVC_SFO,
]


# ── regression: the three confirmed misdetections ──────────────────────────

def test_mcd_never_matches_toyota():
    m = match_template(ALL, customer_id=122, market="SEA",
                       filename="2606-016 Davis Elen_2685_postlog.csv")
    assert m.name == "Davis Elen WA McD"
    assert m.confidence == "customer-id"


def test_thunder_valley_never_matches_muckleshoot():
    m = match_template(ALL, customer_id=68, market="CVC",
                       filename="2606-047 RPM Advertising Inc_2833_postlog.csv")
    assert m.name == "RPM TVC (CVC)"
    assert m.confidence == "customer-id"


def test_generic_word_media_never_pulls_betmgm():
    # non-Ocean customer, filename contains "Media" — must not match BetMGM
    m = match_template(ALL, customer_id=None,
                       filename="2606-042 Media Solutions_2763_postlog.csv")
    assert m.name != "Ocean Media BetMGM"
    assert m.confidence == "none"


# ── customer-ID pass ────────────────────────────────────────────────────────

def test_market_tie_break_sfo():
    m = match_template(ALL, customer_id=68, market="SFO")
    assert m.name == "RPM TVC (SFO)"


def test_ambiguous_does_not_guess():
    m = match_template(ALL, customer_id=68, market="")   # no market to break the tie
    assert m.name == ""
    assert m.confidence == "ambiguous"
    assert set(m.candidates) == {"RPM TVC (CVC)", "RPM TVC (SFO)"}


def test_unknown_market_keeps_ambiguity():
    # customer has CVC+SFO templates; invoice says SEA — narrowing must not
    # silently drop to zero and pick arbitrarily
    m = match_template(ALL, customer_id=68, market="SEA")
    assert m.confidence == "ambiguous"


def test_agency_id_tie_break():
    a = dict(T_RPM_TVC_CVC, etere_agency_id=67)
    b = dict(T_RPM_TVC_SFO, etere_agency_id=999)
    m = match_template([a, b], customer_id=68, agency_id=67)
    assert m.name == "RPM TVC (CVC)"
    assert m.confidence == "customer-id"


def test_id_match_beats_advertiser_text():
    # advertiser text says Toyota but the contract's customer is McD — ID wins
    m = match_template(ALL, customer_id=122, market="SEA",
                       advertiser="Southern CA Toyota Dealers Association")
    assert m.name == "Davis Elen WA McD"


# ── legacy fuzzy fallback (no customer ID available) ────────────────────────

def test_advertiser_text_fallback_is_flagged_fuzzy():
    m = match_template(ALL, customer_id=None, market="SEA",
                       advertiser="Muckleshoot Casino")
    assert m.name == "RPM Muckleshoot"
    assert m.confidence == "fuzzy"


def test_distinctive_agency_word_in_filename_is_fuzzy():
    m = match_template(ALL, customer_id=None,
                       filename="2606-009 BVK_2738_postlog.csv")
    assert m.name == "BVK UC Davis Health"
    assert m.confidence == "fuzzy"


def test_no_match_returns_none_not_first_template():
    m = match_template(ALL, customer_id=None,
                       filename="2606-099 Totally Unknown Agency_9999_postlog.csv")
    assert m.name == ""
    assert m.confidence == "none"


def test_empty_template_list():
    m = match_template([], customer_id=68)
    assert m == TemplateMatch("", "none", [], m.detail)


# ── market resolution (June 2026 batch findings) ────────────────────────────

def test_normalize_full_market_names():
    assert normalize_market("SAN FRANCISCO") == "SFO"
    assert normalize_market("Central Valley") == "CVC"
    assert normalize_market("CVC") == "CVC"


def test_csv_spot_market_outranks_affidavit_header():
    # contract 2590: affidavit header said SEA, every spot aired CVC
    assert resolve_market("CVC", "SEA") == "CVC"


def test_blank_affidavit_market_falls_back_to_csv():
    # contract 2736: affidavit Market field blank (regex used to grab "Fax")
    assert resolve_market("CVC", "") == "CVC"
    assert resolve_market("CVC", "Fax") == "CVC"


def test_pdf_market_used_when_csv_unknown():
    assert resolve_market("", "SFO") == "SFO"
    assert resolve_market("garbage", "SFO") == "SFO"
