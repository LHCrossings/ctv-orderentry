"""Spot name proposer: Lee's Woof list of 11/20 and the six Holiday spots delivered that day."""
import sys
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
for _p in (str(_root), str(_root / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from business_logic.services.spot_names import (
    SpotNameError,
    creative_key,
    parse_delivery,
    parse_prior,
    propose,
)

PRIOR = (Path(__file__).parents[1] / "fixtures/spot_names/woof_prior_20261120.txt").read_text()

# The portal's rows as cells: date, advertiser, ISCI, badges, title, length, destination, status.
PORTAL = "\n".join(
    f"11/19/2026 3:35 PM\tWoof\t{isci}\nHD\nBVS\n1080p\t{title}\t:{dur}\tCrossings TV\t\tReady For Download"
    for isci, title, dur in [
        ("WFHAWVV115H", "HC_HolidayAug26_WrappingVCouch_16×9_015_V2", 15),
        ("WFHADHV115H", "PUP_HolidayAug26_DogHolidayChaos_16×9_015_V2_1", 15),
        ("WFHAHZV115H", "WKA_HolidayAug26_HolidayZoomies_16×9_015_V2", 15),
        ("WFHAWVV130H", "HC_HolidayAug26_WrappingVCouch_16×9_030_V2_1", 30),
        ("WFHAHZV130H", "WKA_HolidayAug26_HolidayZoomies_16×9_030_V2_1", 30),
        ("WFHADHV130H", "PUP_HolidayAug26_DogHolidayChaos_16×9_030_V2_1", 30),
    ]
)

EXPECTED = [
    "WOOF15E20: HC_HolidayAug26_WrappingVCouch_16x9_015_V2 (WFHAWVV115H)",
    "WOOF30E20: HC_HolidayAug26_WrappingVCouch_16x9_030_V2_1 (WFHAWVV130H)",
    "WOOF15E21: PUP_HolidayAug26_DogHolidayChaos_16x9_015_V2_1 (WFHADHV115H)",
    "WOOF30E21: PUP_HolidayAug26_DogHolidayChaos_16x9_030_V2_1 (WFHADHV130H)",
    "WOOF15E22: WKA_HolidayAug26_HolidayZoomies_16x9_015_V2 (WFHAHZV115H)",
    "WOOF30E22: WKA_HolidayAug26_HolidayZoomies_16x9_030_V2_1 (WFHAHZV130H)",
]


def _lines(delivery, prior=PRIOR, **kw):
    spots, bad = parse_delivery(delivery)
    assert not bad
    kw.setdefault("order", "pasted")
    return [p.line for p in propose(spots, parse_prior(prior)[0], **kw) if p.is_new]


def test_prior_list_reads_every_line():
    spots, bad = parse_prior(PRIOR)
    assert not bad and len(spots) == 21
    assert max(s.serial for s in spots) == 19
    assert spots[2].code == "WOOF15E03" and spots[2].isci == "WFWPREV115H"


def test_holiday_delivery_pairs_share_a_serial():
    assert _lines(PORTAL) == EXPECTED


def test_one_spot_per_line_forms_read_the_same():
    plain = "\n".join(ln.split(': ')[1] for ln in EXPECTED)          # title (ISCI)
    assert _lines(plain) == EXPECTED
    isci_first = "\n".join(
        f"{ln.rsplit('(', 1)[1][:-1]} {ln.split(': ')[1].rsplit(' (', 1)[0]}" for ln in EXPECTED
    )
    assert _lines(isci_first) == EXPECTED


def test_two_different_creatives_with_one_isci_stem_do_not_pair():
    # E12 (Alt2 EndCardB :30) and E18 (Baby EndCardA :15) share the stem WFWZBEV1..H.
    assert creative_key("Woof_Zoomies2_30s_Alt2_16x9_EndCardB_R4") != creative_key(
        "Woof_Zoomies2_15s_Baby_16x9_EndCardA_R4"
    )


def test_later_cut_of_a_listed_creative_takes_its_serial():
    out = propose(
        parse_delivery("WFWZBEV130X Woof_Zoomies2_30s_Baby_16x9_EndCardA_R5 :30")[0], parse_prior(PRIOR)[0]
    )
    assert out[0].code == "WOOF30E18" and "WOOF15E18" in out[0].note


def test_isci_already_on_the_list_is_reported_not_renamed():
    out = propose(parse_delivery("WFZ1SSV115H Zoomies 15 :15\n" + PORTAL)[0], parse_prior(PRIOR)[0], order="pasted")
    assert [p.line for p in out if p.is_new] == EXPECTED
    assert [(p.code, p.is_new) for p in out if not p.is_new] == [("WOOF15E01", False)]


def test_same_title_and_length_twice_gets_two_serials():
    out = _lines("AAAA1115H Spot_One_015_V1 :15\nAAAB1115H Spot_One_015_V2 :15")
    assert [ln.split(":")[0] for ln in out] == ["WOOF15E20", "WOOF15E21"]


def test_no_prior_list_needs_client_and_language():
    spots = parse_delivery("AAAA1115H Spot_One_015_V1 :15")[0]
    with pytest.raises(SpotNameError):
        propose(spots, [])
    assert propose(spots, [], client="acme", lang="e")[0].code == "ACME15E01"


def test_unreadable_text_is_returned_not_dropped():
    assert parse_delivery("hello there")[1] == ["hello there"]
    assert parse_prior("WOOF15E01 Zoomies")[1] == ["WOOF15E01 Zoomies"]


# ── McDonald's: one serial per creative across LANGUAGES (Lee 11/20) ─────────

_FIX = Path(__file__).parents[1] / "fixtures/spot_names"
MCD_PRIOR = (_FIX / "mcd_prior_20261120.txt").read_text()
MCD_ER = (_FIX / "mcd_extreme_reach_20261119.txt").read_text()  # Extreme Reach rows, copied as-is

MCD_EXPECTED = [
    "MD30C439: Home Alone McDs Alone US CAN (MCIC016326VH)",
    "MD15C440: Home Alone Storytelling US CAN (MCIC109526VH)",
    "MD15C441: Home Alone Shaker Fry US CAN (MCIC110526VH)",
    "MD15C442: Home Alone Sock US CAN (MCIC111526VH)",
    "MD30M439: Home Alone McDs Alone US MAN (MCIM022326VH)",
    "MD15M440: Home Alone Storytelling US MAN (MCIM147526VH)",
    "MD15M441: Home Alone Shaker Fry US MAN (MCIM148526VH)",
    "MD15M442: Home Alone Sock US MAN (MCIM149526VH)",
    "MD30V439: Home Alone McDs Alone US VIE (MCIV018326VH)",
    "MD15V440: Home Alone Storytelling US VIE (MCIV145526VH)",
    "MD15V441: Home Alone Shaker Fry US VIE (MCIV146526VH)",
    "MD15V442: Home Alone Sock US VIE (MCIV147526VH)",
]


def test_mcd_prior_list_skips_the_separator_line():
    spots, bad = parse_prior(MCD_PRIOR)
    assert not bad and len(spots) == 25 and {s.lang for s in spots} == {"C", "M", "V"}


def test_extreme_reach_copy_reads_twelve_spots():
    spots, bad = parse_delivery(MCD_ER)
    assert not bad and len(spots) == 12
    assert (spots[0].isci, spots[0].duration) == ("MCIM022326VH", 30)
    assert spots[0].title == "2026_HomeAlone_USMcDsAlone_ACM_MAN_TVC-OLV_16x9_30"


def test_mcd_serial_is_shared_across_languages_in_isci_order_with_short_titles():
    out = propose(parse_delivery(MCD_ER)[0], parse_prior(MCD_PRIOR)[0], simplify=True)
    assert [p.line for p in out] == MCD_EXPECTED
    assert not any(p.note for p in out)


def test_mcd_titles_as_delivered_keep_the_file_name():
    out = propose(parse_delivery(MCD_ER)[0], parse_prior(MCD_PRIOR)[0])
    assert out[0].line == "MD30C439: 2026_HomeAlone_USMcDsAlone_ACM_CAN_TVC-OLV_16x9_30 (MCIC016326VH)"


def test_new_language_of_a_listed_creative_takes_its_serial():
    out = propose(parse_delivery("MCIC015326VH The_Kitchen_US_CAN_30 :30")[0], parse_prior(MCD_PRIOR)[0])
    assert out[0].code == "MD30C434"          # the Cantonese cut the list does not have yet


def test_title_without_a_language_word_is_flagged_on_a_multi_language_list():
    out = propose(parse_delivery("MCIX999526VH Mystery_Spot_15 :15")[0], parse_prior(MCD_PRIOR)[0])
    assert "no language word" in out[0].note


def test_single_language_list_does_not_read_a_language_out_of_the_title():
    # Woof's list is all E: a title word such as CAN or MAN is not a language there
    out = _lines("WFXXXXV115H Old_Man_Can_Run_015 :15")
    assert out[0].startswith("WOOF15E20:")


def test_route_groups_languages_with_a_blank_line(client):
    r = client.post(
        "/api/scripts/spot-names/propose", json={"prior": MCD_PRIOR, "delivery": MCD_ER, "simplify": True}
    )
    lines = r.json()["lines"]
    assert [x for x in lines if x] == MCD_EXPECTED and lines[4] == "" and lines[9] == ""


# ── the Control Room route ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from orchestration.config import ApplicationConfig
    from web.routes.orders import build_router

    d = tmp_path_factory.mktemp("cfg")
    cfg = ApplicationConfig(
        incoming_dir=d / "in", processed_dir=d / "out", error_dir=d / "err", customer_db_path=d / "c.db"
    )
    templates = Path(__file__).parents[2] / "src/web/templates"
    app = FastAPI()
    app.include_router(build_router(cfg, Jinja2Templates(directory=str(templates))))
    return TestClient(app)


def test_route_returns_the_holiday_names(client):
    r = client.post("/api/scripts/spot-names/propose", json={"prior": PRIOR, "delivery": PORTAL, "order": "pasted"})
    assert r.status_code == 200
    assert r.json()["lines"] == EXPECTED


def test_route_refuses_an_empty_delivery_and_a_missing_client(client):
    assert client.post("/api/scripts/spot-names/propose", json={"prior": PRIOR, "delivery": ""}).status_code == 400
    r = client.post("/api/scripts/spot-names/propose", json={"prior": "", "delivery": "AAAA1115H Spot_One_015 :15"})
    assert r.status_code == 400 and "client code" in r.json()["detail"]


def test_page_renders(client):
    assert "Propose Names" in client.get("/scripts/spot-names").text
