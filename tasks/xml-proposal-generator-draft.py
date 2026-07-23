import sys
import uuid

sys.path.insert(0, '/home/scrib/dev/ctv-orderentry')
import xml.etree.ElementTree as ET
from datetime import datetime

from browser_automation.generators.aaaa_xml_generator import charmaine_order_to_proposal_spec
from browser_automation.parsers.charmaine_parser import parse_charmaine_pdf

ROOT = "http://www.AAAA.org/schemas/spotTVCableProposal"
TVB = "http://www.AAAA.org/schemas/spotTV"
TP = "http://www.AAAA.org/schemas/TVBGeneralTypes"


def r(t):
    return f"{{{ROOT}}}{t}"


def vb(t):
    return f"{{{TVB}}}{t}"


def tp(t):
    return f"{{{TP}}}{t}"


def sub(p, t, text=None, **a):
    e = ET.SubElement(p, t)
    for k, v in a.items():
        e.set(k, str(v))
    if text is not None:
        e.text = str(text)
    return e


DAYNAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def periods(weekly, boundaries):
    out = []
    i = 0
    n = len(weekly)
    while i < n:
        c = weekly[i]
        if not c or c <= 0:
            i += 1
            continue
        j = i
        while j + 1 < n and weekly[j + 1] == c:
            j += 1
        out.append((boundaries[i][0], boundaries[j][1], int(c)))
        i = j + 1
    return out


def build(spec, H):
    ET.register_namespace("", ROOT)
    ET.register_namespace("tvb", TVB)
    ET.register_namespace("tvb-tp", TP)
    root = ET.Element(r("AAAA-Message"))
    av = sub(root, r("AAAA-Values"))
    sub(av, r("SchemaName"), "SpotTVCableProposal")
    sub(av, r("SchemaVersion"), "0.3.0.5A")
    sub(av, r("Media"), "SpotTV")
    sub(av, r("BusinessObject"), "Proposal")
    sub(av, r("Action"), "New")
    sub(av, r("UniqueMessageID"), str(uuid.UUID(int=0)))
    p = sub(root, r("Proposal"), uniqueIdentifier=H["uid"], version="1",
            sendDateTime=datetime(2026, 7, 21, 9, 0, 0).isoformat(), weekStartDay="Mo",
            startDate=spec.flight_start, endDate=spec.flight_end)
    s = sub(p, r("Seller"), companyName=H["seller"])
    sub(s, r("OfficeName"), H["seller_office"])
    sp = sub(s, r("Salesperson"), name=H["salesperson"])
    sub(sp, r("Phone"), H["seller_phone"], type="voice", location="work")
    if H.get("seller_fax"):
        sub(sp, r("Phone"), H["seller_fax"], type="fax")
    sub(sp, r("Email"), H["seller_email"], type="primary")
    b = sub(p, r("Buyer"), buyingCompanyName=H["buyer"])
    sub(b, r("OfficeName"), H["buyer_office"])
    sub(b, r("BuyerName"), H["buyer_name"])
    adv = sub(p, r("Advertiser"), name=spec.client_name)
    sub(adv, r("Product"), name=H["product"])
    sub(p, r("Name"), H["name"])
    sub(p, r("SellerReference"), H["uid"])
    outs = sub(p, r("Outlets"))
    sub(outs, r("TelevisionStation"), callLetters=H["call"], parentPlus="N", outletId="OUT0")
    al = sub(p, r("AvailList"), identifier="AL001", startDate=spec.flight_start,
             endDate=spec.flight_end, isPackage="N")
    sub(al, r("Name"), spec.market_description)
    ors = sub(al, r("OutletReferences"))
    sub(ors, r("OutletReference"), outletFromProposalRef="OUT0", outletForListId="OUL0")
    dcs = sub(al, r("DemoCategories"))
    dc = sub(dcs, r("DemoCategory"), DemoId="DM0")
    sub(dc, vb("DemoType"), "Rating")
    sub(dc, vb("Group"), "Adults")
    sub(dc, vb("AgeFrom"), "25")
    sub(dc, vb("AgeTo"), "54")
    sub(al, r("TargetDemo"), demoRef="DM0")
    if H.get("charge_note"):
        _c = sub(al, r("Comment"))
        sub(_c, tp("CommentLine"), H["charge_note"])
    for ln in spec.lines:
        awl = sub(al, r("AvailLineWithDetailedPeriods"))
        sub(awl, r("OutletReference"), outletFromListRef="OUL0")
        dts = sub(awl, r("DayTimes"))
        for dt in ln.day_times:
            d = sub(dts, r("DayTime"))
            sub(d, r("StartTime"), dt.start_time)
            sub(d, r("EndTime"), dt.end_time)
            days = sub(d, r("Days"))
            for i, nm in enumerate(DAYNAMES):
                sub(days, tp(nm), "Y" if dt.days[i] else "N")
        sub(awl, r("DaypartName"), ln.daypart_name or "ROS")
        sub(awl, r("AvailName"), ln.program)
        sub(awl, r("SpotLength"), f"00:00:{int(ln.spot_length_sec):02d}")
        per = sub(awl, r("Periods"))
        for (st, en, cnt) in periods(ln.weekly_spots, spec.week_boundaries):
            dp = sub(per, r("DetailedPeriod"), startDate=st, endDate=en)
            sub(dp, r("Rate"), f"{ln.rate:.2f}")
            sub(dp, r("SpotsPerWeek"), cnt)
    return root


o = parse_charmaine_pdf("incoming/BAAQMD-2026.pdf")[0]
o.lines = [ln for ln in o.lines if not ln.language.strip().lower().startswith("total")]
o.advertiser = "Bay Area Air Quality Management District"
# Lee 2026-07-21: Vietnamese window is 10a-1p (parsed as 11a-1p); line dc is frozen
import dataclasses as _dc

o.lines = [_dc.replace(_ln, daypart=_ln.daypart.replace("11a-1p", "10a-1p"))
           if "11a-1p" in (_ln.daypart or "") else _ln for _ln in o.lines]
spec = charmaine_order_to_proposal_spec(o, estimate_number="BAAQMD-2026-REV1",
                                        buyer_name="Crispin", call_letters="CRTV")
# DISCOUNTED rate (col E) + per-line spot length (col F) from the proposal Excel
import openpyxl

_wb = openpyxl.load_workbook(
    "/mnt/c/Work Temp/!New/!Orders/Crossings TV Media Proposal_BAAQMD_2026_REV1.xlsm", data_only=True)
_xmap = {}
for _r in _wb["BAAQMD"].iter_rows(values_only=True):
    _lang, _disc, _len = _r[2], _r[5], _r[6]
    if (isinstance(_lang, str) and isinstance(_disc, (int, float))
            and isinstance(_len, str) and _len.strip().rstrip("s").lstrip(":").isdigit()):
        _xmap[_lang.strip().lower()] = (float(_disc), int(_len.strip().rstrip("s").lstrip(":")))
for _ln in spec.lines:
    if _ln.program.strip().lower() in _xmap:
        _ln.rate, _ln.spot_length_sec = _xmap[_ln.program.strip().lower()]
H = dict(uid="BAAQMD-2026-REV1", name="BAAQMD 2026",
         seller="Crossings TV", seller_office="Sacramento", salesperson="Charmaine Lane",
         seller_phone="(888)901-5288 x106", seller_fax="(888)878-8936",
         seller_email="charmaine.lane@crossingstv.com",
         buyer="Crispin", buyer_office="TBD", buyer_name="Alexander Boyle",
         product="Bay Area AQMD", call="Crossings TV",
         charge_note="Production & Translation: $2,080.00 (one-time charge, not airtime)")
root = build(spec, H)
xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
open("outgoing/BAAQMD-2026.xml", "wb").write(xml)
print("wrote outgoing/BAAQMD-2026.xml", len(xml), "bytes; lines:", len(spec.lines))

from lxml import etree

schema = etree.XMLSchema(etree.parse(".claude/documents/tvb_xml_schemas/spotTVCableProposal-0.3.0.5A.xsd"))
doc = etree.parse("outgoing/BAAQMD-2026.xml")
ok = schema.validate(doc)
print("VALID" if ok else "INVALID")
if not ok:
    for e in schema.error_log[:12]:
        print("  -", e.message[:150])
