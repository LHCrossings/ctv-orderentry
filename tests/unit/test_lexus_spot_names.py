"""Lexus (IW Group) spot-name proposals — Lee's serial rule from the delivered file names.

Lee 11/6: the November 2026 batch (18 .mov files) had to be numbered and named by hand
each month; serial = model + title + month + agency tag, the :15/:30 and language cuts
share it, and a market suffix (-SE/-SF) appears only when one serial has a different ISCI
per market for the same cut. Existing assets are never renamed.
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
for p in [str(_root), str(_root / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_automation.parsers.lexus_spot_names import (  # noqa: E402
    next_lexus_serial,
    parse_delivery_name,
    propose_names,
    title_words,
)

NOV_FILES = [
    "TYIW26TL877H_MY27-ES_TV_CHILDS-EYES_ENG_CY26-NOV_23-07_LEA-4.mov",
    "TYIW26TL878H_MY27-ES_TV_CHILDS-EYES_ENG_CY26-NOV_08-07_LEA-4.mov",
    "TYIW26TL885H_MY26-NX_TV_CHILDS-EYES_ENG_CY26-NOV_23_07_LEA-4.mov",
    "TYIW26TL886H_MY26-NX_TV_CHILDS-EYES_ENG_CY26-NOV_08_07_LEA-4.mov",
    "TYIW26TL887H_MY26-NX_TV_CHILDS-EYES_CH_CY26-NOV_23-07_LWA-7.mov",
    "TYIW26TL888H_MY26-NX_TV_CHILDS-EYES_CH_CY26-NOV_08-07_LWA-7.mov",
    "TYIW26TL891H_MY26-NX_TV_CHILDS-EYES_VT_CY26-NOV_23-07_LWA-7.mov",
    "TYIW26TL892H_MY26-NX_TV_CHILDS-EYES_VT_CY26-NOV_08-07_LWA-7.mov",
    "TYIW26TL893H_MY26-NX_TV_CHILDS-EYES_ENG_CY26-NOV_23_07_LWA-6.mov",
    "TYIW26TL894H_MY26-NX_TV_CHILDS-EYES_ENG_CY26-NOV_08_07_LWA-6.mov",
    "TYIW26TL929H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-NOV_23_07_LEA-4.mov",
    "TYIW26TL930H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-NOV_08_07_LEA-4.mov",
    "TYIW26TL931H_MY26-RX_TV_OVER-THE-YEARS_CH_CY26-NOV_23-07_LWA-7.mov",
    "TYIW26TL932H_MY26-RX_TV_OVER-THE-YEARS_CH_CY26-NOV_08-07_LWA-7.mov",
    "TYIW26TL935H_MY26-RX_TV_OVER-THE-YEARS_VT_CY26-NOV_23-07_LWA-7.mov",
    "TYIW26TL936H_MY26-RX_TV_OVER-THE-YEARS_VT_CY26-NOV_08-07_LWA-7.mov",
    "TYIW26TL937H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-NOV_23_07_LWA-6.mov",
    "TYIW26TL938H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-NOV_08_07_LWA-6.mov",
]


def _nov():
    return [parse_delivery_name(n) for n in NOV_FILES]


def test_delivery_name_fields():
    d = parse_delivery_name(NOV_FILES[10])
    assert (
        d.isci,
        d.model_year,
        d.model,
        d.title,
        d.month,
        d.duration_sec,
        d.tag,
        d.lang_code,
    ) == ("TYIW26TL929H", "26", "RX", "Over the Years", "Nov", 30, "LEA-4", "E")
    assert parse_delivery_name(NOV_FILES[11]).duration_sec == 15  # 08_07 = :15
    assert parse_delivery_name(NOV_FILES[4]).lang_code == "M"  # CH → Mandarin by default
    assert parse_delivery_name(NOV_FILES[4], sheet_language="Cantonese").lang_code == "C"
    assert parse_delivery_name(NOV_FILES[6]).lang_code == "V"
    assert parse_delivery_name("random_clip.mov") is None
    # a sheet row: file-name cell without the ISCI prefix, ISCI prepended by the caller
    d = parse_delivery_name(
        "TYIW26TL931H_MY26-RX_TV_OVER-THE-YEARS_CH_CY26-NOV_23-07_LWA-7", "Mandarin"
    )
    assert (d.isci, d.lang_code) == ("TYIW26TL931H", "M")


def test_title_words():
    assert title_words("OVER-THE-YEARS") == "Over the Years"
    assert title_words("CHILDS-EYES") == "Childs Eyes"
    assert title_words("Game-Time") == "Game Time"
    assert title_words("Invite_PS1") == "Invite PS1"


def test_november_batch_numbers_by_title_and_tag_with_no_market_suffix():
    rows = propose_names(_nov(), {}, 312)
    desc = {r.isci: r.description for r in rows}
    assert len(rows) == 18 and all(not r.existing for r in rows)
    # one serial per (model, title, month, tag); :15/:30 and M/V cuts share it
    assert desc["TYIW26TL929H"] == "LEXUS30E314: MY26 RX Over the Years Nov LEA-4"
    assert desc["TYIW26TL930H"] == "LEXUS15E314: MY26 RX Over the Years Nov LEA-4"
    assert desc["TYIW26TL937H"] == "LEXUS30E316: MY26 RX Over the Years Nov LWA-6"
    assert desc["TYIW26TL931H"] == "LEXUS30M318: MY26 RX Over the Years Nov LWA-7"
    assert desc["TYIW26TL935H"] == "LEXUS30V318: MY26 RX Over the Years Nov LWA-7"
    assert desc["TYIW26TL936H"] == "LEXUS15V318: MY26 RX Over the Years Nov LWA-7"
    assert desc["TYIW26TL877H"] == "LEXUS30E312: MY27 ES Childs Eyes Nov LEA-4"
    assert desc["TYIW26TL878H"] == "LEXUS15E312: MY27 ES Childs Eyes Nov LEA-4"
    assert sorted({r.serial for r in rows}) == [312, 313, 314, 315, 316, 317, 318]
    # SEA and SFO share every western file, so no name needs a market suffix (Lee 11/6)
    assert not any(
        "-SE" in r.description or "-SF" in r.description or "-NY" in r.description for r in rows
    )
    assert all(r.code == r.isci for r in rows)


def test_order_of_input_does_not_change_the_numbering():
    a = propose_names(_nov(), {}, 312)
    b = propose_names(list(reversed(_nov())), {}, 312)
    assert {r.isci: r.description for r in a} == {r.isci: r.description for r in b}


def test_existing_isci_is_never_renamed_and_pins_its_groups_serial():
    existing = {"TYIW26TL929H": "LEXUS30E312-NY: MY26 RX Over the Years Nov LEA-4"}
    rows = propose_names(_nov(), existing, 320)
    by = {r.isci: r for r in rows}
    assert (
        by["TYIW26TL929H"].existing and by["TYIW26TL929H"].description == existing["TYIW26TL929H"]
    )
    assert by["TYIW26TL930H"].description == "LEXUS15E312: MY26 RX Over the Years Nov LEA-4"
    new_serials = sorted({r.serial for r in rows if not r.existing and r.serial != 312})
    assert new_serials[0] == 320 and len(new_serials) == 6


def test_december_rows_number_after_november():
    dec = [
        parse_delivery_name("TYIW27TL025H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-DEC_23_07_LEA-4"),
        parse_delivery_name("TYIW26TL981H_MY26-NX_TV_CHILDS-EYES_ENG_CY26-DEC_23_07_LEA-4"),
    ]
    rows = propose_names(dec + _nov(), {}, 312)
    by = {r.isci: r for r in rows}
    assert by["TYIW26TL929H"].serial == 314  # November unchanged
    assert by["TYIW27TL025H"].serial > max(r.serial for r in rows if "Nov" in r.description)
    assert by["TYIW27TL025H"].description.endswith("MY26 RX Over the Years Dec LEA-4")


def test_duplicate_rows_collapse_to_one_isci():
    rows = propose_names(_nov() + _nov(), {}, 312)
    assert len(rows) == 18


def test_next_lexus_serial():
    assert (
        next_lexus_serial(["LEXUS15E311-NY: x", "LEXUS30V307-SF: y", "PACO30E12: z", None]) == 312
    )
    assert next_lexus_serial([]) == 1


def test_one_serial_with_a_cut_per_market_gets_se_and_sf():
    """Lee 11/6: 'if spot number 277 has exactly the same title but there is a different
    ISCI for SEA and one for SFO, we keep 277 and add suffix SE or SF'."""
    sea = parse_delivery_name(
        "TYIW26TL937H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-NOV_23_07_LWA-6", "Hinglish", market="SEA"
    )
    sfo = parse_delivery_name(
        "TYIW26TL940H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-NOV_23_07_LWA-6", "Hinglish", market="SFO"
    )
    sea15 = parse_delivery_name(
        "TYIW26TL938H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-NOV_08_07_LWA-6", "Hinglish", market="SEA"
    )
    rows = {r.isci: r for r in propose_names([sea, sfo, sea15], {}, 277)}
    assert rows["TYIW26TL937H"].description == "LEXUS30E277-SE: MY26 RX Over the Years Nov LWA-6"
    assert rows["TYIW26TL940H"].description == "LEXUS30E277-SF: MY26 RX Over the Years Nov LWA-6"
    # the :15 has one ISCI, so it shares the serial with no suffix
    assert rows["TYIW26TL938H"].description == "LEXUS15E277: MY26 RX Over the Years Nov LWA-6"
    # the same file on both sheets is one spot, not two versions
    both = propose_names([sea, parse_delivery_name(sea.source, "Hinglish", market="SFO")], {}, 277)
    assert [r.description for r in both] == ["LEXUS30E277: MY26 RX Over the Years Nov LWA-6"]
    # from .mov names alone the market is unknown: same serial, flagged for Lee
    a = parse_delivery_name("TYIW26TL937H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-NOV_23_07_LWA-6.mov")
    b = parse_delivery_name("TYIW26TL940H_MY26-RX_TV_OVER-THE-YEARS_ENG_CY26-NOV_23_07_LWA-6.mov")
    flagged = propose_names([a, b], {}, 277)
    assert {r.serial for r in flagged} == {277}
    assert all("add the market suffix" in r.note for r in flagged)
