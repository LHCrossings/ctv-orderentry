"""
Agency XML Export — Crossings TV proposal workbook → TVB SpotTV Cable Proposal XML.

Charmaine's Excel proposal (the "Crossings TV Media Proposal_<client>.xlsm"
template) becomes the 4A's/TVB `SpotTVCableProposal 0.3.0.5A` message that an
agency's buying system ingests to build its insertion order.

The SHAPE of the output is the file the agency actually ingested —
BAAQMD 2026 R1C (Kurt + Charmaine, 2026-07-23, kept as
`tests/fixtures/crispin/baaqmd_2026_r1c_agency_ingested.xml`):

  * no Seller phone / email / office, no Buyer office — names only
  * no DemoCategories / TargetDemo / DemoValues, no Comment
  * one AvailList per market banner, one TelevisionStation outlet
  * one AvailLineWithDetailedPeriods per airtime row, IN SHEET ORDER
    (paid rows, then the ROS bonus rows, exactly as the sheet lists them)
  * DayTime carries ProgramName = the row's Language cell; AvailName the same
  * DaypartName = the language FAMILY in caps (CHINESE / FILIPINO / VIETNAMESE …)
  * ROS = 06:00–24:00, all seven days
  * ONE DetailedPeriod per line spanning the line's weeks, with <Rate> only —
    the discounted rate (0.00 for bonus). The agency did not need SpotsPerWeek;
    `include_spots_per_week=True` adds them (schema-valid) if a buyer wants them.
  * a production / translation charge (Lee 9/25: "I don't see any money for
    production") rides as ONE MORE line, the way the agency's own IO carried it
    (TRANSLATION COST, 1 unit, first flight week, :15, ROS): AvailName = the
    sheet's label, DaypartName PRODUCTION, Rate = the GROSS discounted amount,
    one DetailedPeriod over the first week with SpotsPerWeek 1 so the unit count
    is explicit. The schema has no charge element; this is the only shape that
    lands the money on the IO.

The workbook is read by `crispin_parser.parse_crispin_xlsx` (header labels, not
column indexes; spot and money totals reconciled against the sheet's own
footer). Header values the sheet does not carry (buyer company, call letters,
proposal id, product) are defaulted from the sheet and confirmed on the page.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from browser_automation.parsers.crispin_parser import CrispinLine, CrispinOrder

NS_PROPOSAL = "http://www.AAAA.org/schemas/spotTVCableProposal"
NS_TVB = "http://www.AAAA.org/schemas/spotTV"
NS_TP = "http://www.AAAA.org/schemas/TVBGeneralTypes"
SCHEMA_VERSION = "0.3.0.5A"

XSD_PATH = (
    Path(__file__).resolve().parents[2]
    / ".claude"
    / "documents"
    / "tvb_xml_schemas"
    / f"spotTVCableProposal-{SCHEMA_VERSION}.xsd"
)

# The sheet's Agency cell decides WHO the buyer is (Lee 9/25: BAAQMD moved from Allison
# to Crispin — the sheet said CRISPIN, the older hand-made XML still said Allison). What
# the sheet cannot state is remembered PER AGENCY after a successful export: the buyer
# company's proper spelling ("Crispin LLC" for a cell reading "CRISPIN"), our station
# code in that agency's system (3131CA), and the salesperson.
HEADER_MEMORY_PATH = Path(__file__).resolve().parents[2] / "data" / "agency_xml_headers.json"
_REMEMBERED_FIELDS = ("buyer_company", "call_letters", "salesperson")
_LEGAL_SUFFIXES = {
    "llc",
    "l.l.c",
    "l.l.c.",
    "inc",
    "inc.",
    "ltd",
    "ltd.",
    "co",
    "co.",
    "corp",
    "corp.",
}

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# DaypartName = language family, the way the agency's ingested file labelled it.
_LANGUAGE_FAMILY = (
    ("cantonese", "CHINESE"),
    ("mandarin", "CHINESE"),
    ("chinese", "CHINESE"),
    ("filipino", "FILIPINO"),
    ("tagalog", "FILIPINO"),
    ("vietnamese", "VIETNAMESE"),
    ("korean", "KOREAN"),
    ("hmong", "HMONG"),
    ("japanese", "JAPANESE"),
    ("punjabi", "SOUTH ASIAN"),
    ("hindi", "SOUTH ASIAN"),
    ("south asian", "SOUTH ASIAN"),
)

_DAY_PATTERNS = {
    ("m-su", "m-sun", "mon-sun", "m-s", "daily", "7days", "sun-sat"): (1, 1, 1, 1, 1, 1, 1),
    ("m-f", "mon-fri", "m-fri", "mon-f", "weekdays"): (1, 1, 1, 1, 1, 0, 0),
    ("m-sa", "m-sat", "mon-sat"): (1, 1, 1, 1, 1, 1, 0),
    ("sa-su", "sat-sun", "sa-sun", "sat-su", "weekend", "weekends"): (0, 0, 0, 0, 0, 1, 1),
    ("sa", "sat", "saturday"): (0, 0, 0, 0, 0, 1, 0),
    ("su", "sun", "sunday"): (0, 0, 0, 0, 0, 0, 1),
    ("f-su", "f-sun", "fri-sun"): (0, 0, 0, 0, 1, 1, 1),
}


class AgencyXmlError(ValueError):
    """A value the XML needs could not be read or would be a guess."""


@dataclass
class ExportHeader:
    """Everything the XML needs that the workbook does not state (or states loosely)."""

    proposal_id: str  # Proposal@uniqueIdentifier + SellerReference, e.g. BAAQMD-2026-R1C
    proposal_name: str  # <Name>, e.g. "Crossings TV - BAAQMD 2026"
    advertiser: str  # Advertiser@name
    product: str  # Product@name, e.g. "BAAQMD 2026"
    buyer_company: str  # Buyer@buyingCompanyName (the agency)
    buyer_name: str  # <BuyerName> (the agency contact)
    call_letters: str  # TelevisionStation@callLetters — the buyer's station code
    salesperson: str = "Charmaine Lane"
    seller: str = "Crossings TV"
    version: int = 1
    send_date: Optional[date] = None
    include_spots_per_week: bool = False

    def require(self) -> None:
        missing = [
            k
            for k in (
                "proposal_id",
                "proposal_name",
                "advertiser",
                "product",
                "buyer_company",
                "buyer_name",
                "call_letters",
                "salesperson",
            )
            if not str(getattr(self, k) or "").strip()
        ]
        if missing:
            raise AgencyXmlError("Missing header field(s): " + ", ".join(missing))


@dataclass
class DayTime:
    start: str  # "19:00"
    end: str  # "24:00"
    days: tuple  # seven ints Mon..Sun


@dataclass
class ExportLine:
    program: str
    daypart_name: str
    day_times: List[DayTime]
    length_sec: int
    rate: float
    start: date
    end: date
    weekly_spots: List[int]
    week_dates: List[date]
    is_charge: bool = False


@dataclass
class ExportPreview:
    header: ExportHeader
    lines: List[ExportLine]
    flight_start: date
    flight_end: date
    market_label: str
    notes: List[str] = field(default_factory=list)


# ─── Header defaults ──────────────────────────────────────────────────────────


def _short_client(order: CrispinOrder) -> str:
    """'Crossings TV:  BAAQMD' → 'BAAQMD'; else the advertiser's initials."""
    m = re.match(r"crossings\s+tv\s*:\s*(.+)$", order.title or "", re.IGNORECASE)
    if m and m.group(1).strip():
        return " ".join(m.group(1).split())
    words = [w for w in re.split(r"[^A-Za-z0-9]+", order.advertiser) if w]
    return "".join(w[0] for w in words).upper() if words else "PROPOSAL"


def agency_key(agency: str) -> str:
    """'CRISPIN' and 'Crispin LLC' are one agency: lowercase, legal suffixes dropped."""
    words = [w for w in re.split(r"[\s,]+", agency.lower()) if w and w not in _LEGAL_SUFFIXES]
    return " ".join(words)


def remembered_header(agency: str, path: Path = HEADER_MEMORY_PATH) -> dict:
    key = agency_key(agency)
    if not key:
        return {}
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entry = store.get(key) or {}
    return {k: str(entry.get(k, "")).strip() for k in _REMEMBERED_FIELDS if entry.get(k)}


def remember_header(header: ExportHeader, path: Path = HEADER_MEMORY_PATH) -> None:
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(store, dict):
            store = {}
    except (OSError, ValueError):
        store = {}
    key = agency_key(header.buyer_company)
    if not key:
        return
    store[key] = {
        **{k: getattr(header, k) for k in _REMEMBERED_FIELDS},
        "updated": date.today().isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")


def default_header(order: CrispinOrder, memory_path: Path = HEADER_MEMORY_PATH) -> ExportHeader:
    short = _short_client(order)
    year = order.week_dates[0].year if order.week_dates else date.today().year
    variant = " ".join(w.capitalize() for w in (order.subtitle or "").split())
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{short} {year} {variant}").strip("-").upper()
    known = remembered_header(order.agency, memory_path)
    return ExportHeader(
        proposal_id=slug,
        proposal_name=f"Crossings TV - {short} {year}" + (f" {variant}" if variant else ""),
        advertiser=order.advertiser,
        product=f"{short} {year}",
        buyer_company=known.get("buyer_company") or order.agency,
        buyer_name=order.contact,
        call_letters=known.get("call_letters", ""),
        salesperson=known.get("salesperson") or "Charmaine Lane",
        send_date=date.today(),
    )


# ─── Daypart → DayTime ────────────────────────────────────────────────────────


def _time_24h(tok: str, period_hint: str) -> str:
    t = tok.strip().lower().replace(" ", "")
    t = {
        "12m": "12a",
        "12mid": "12a",
        "mid": "12a",
        "midnight": "12a",
        "12n": "12p",
        "noon": "12p",
    }.get(t, t)
    if t.endswith("m") and len(t) > 1 and t[-2] in "ap":
        t = t[:-1]
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?([ap])?", t)
    if not m:
        raise AgencyXmlError(f"cannot read time '{tok}'")
    hour, minute, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3) or period_hint
    if not ap or not (1 <= hour <= 12) or minute > 59:
        raise AgencyXmlError(f"cannot read time '{tok}'")
    if ap == "a":
        return "24:00" if hour == 12 else f"{hour:02d}:{minute:02d}"
    return f"{12 if hour == 12 else hour + 12:02d}:{minute:02d}"


def _days_bits(days_str: str) -> tuple:
    key = days_str.strip().lower().replace(" ", "").replace("–", "-")
    for aliases, bits in _DAY_PATTERNS.items():
        if key in aliases:
            return bits
    raise AgencyXmlError(f"cannot read day pattern '{days_str}'")


def parse_daypart(daypart: str) -> List[DayTime]:
    """'M-F 7p-8p' → [DayTime 19:00–20:00 Mon–Fri]; 'ROS' → 06:00–24:00 all week.

    Multiple windows split on ';'. Anything unreadable raises — a window that
    decides where spots air is never defaulted.
    """
    text = " ".join((daypart or "").split())
    if not text:
        raise AgencyXmlError("empty daypart")
    if text.upper() == "ROS":
        return [DayTime("06:00", "24:00", (1, 1, 1, 1, 1, 1, 1))]
    out: List[DayTime] = []
    for window in (w.strip() for w in text.split(";") if w.strip()):
        tokens = window.split()
        if len(tokens) < 2:
            raise AgencyXmlError(f"cannot read daypart '{daypart}'")
        time_range = tokens[-1]
        parts = re.split(r"[-–]", time_range)
        if len(parts) != 2:
            raise AgencyXmlError(f"cannot read time range '{time_range}'")
        end_hint = (re.search(r"[ap]", parts[1].lower()) or [""])[0]
        start_hint = (re.search(r"[ap]", parts[0].lower()) or [""])[0]
        start = _time_24h(parts[0], start_hint or end_hint)
        end = _time_24h(parts[1], end_hint or start_hint)
        if start_hint == "" and end_hint and start > end:
            # "11-1p" → 11am–1pm: the start crossed noon, flip its period
            start = _time_24h(parts[0], "a" if end_hint == "p" else "p")
        out.append(DayTime(start, end, _days_bits(" ".join(tokens[:-1]))))
    return out


def language_family(language: str) -> str:
    low = language.lower()
    for needle, family in _LANGUAGE_FAMILY:
        if needle in low:
            return family
    return language.split()[0].upper() if language.split() else "ROS"


# ─── Workbook → preview ───────────────────────────────────────────────────────


def _line_span(ln: CrispinLine) -> tuple:
    active = [d for d, n in zip(ln.week_dates, ln.week_spots) if n > 0]
    if not active:
        raise AgencyXmlError(f"line '{ln.language_block}' has no spots in any week")
    return active[0], active[-1] + timedelta(days=6)


def build_preview(order: CrispinOrder, header: Optional[ExportHeader] = None) -> ExportPreview:
    header = header or default_header(order)
    if not order.lines:
        raise AgencyXmlError("the workbook has no airtime lines")
    lines: List[ExportLine] = []
    for ln in order.lines:
        start, end = _line_span(ln)
        lines.append(
            ExportLine(
                program=" ".join(ln.language_block.split()),
                daypart_name=language_family(ln.language_block),
                day_times=parse_daypart(ln.daypart),
                length_sec=int(ln.length_sec),
                rate=float(ln.rate),
                start=start,
                end=end,
                weekly_spots=list(ln.week_spots),
                week_dates=list(ln.week_dates),
            )
        )
    flight_start = min(ln.start for ln in lines)
    flight_end = max(ln.end for ln in lines)
    notes: List[str] = []
    for ch in order.charges:
        notes.append(
            f"{ch.description}: ${ch.amount:,.2f} gross, carried as a one-unit line in the "
            f"first flight week (the shape the agency's IO uses for production money)"
        )
        lines.append(
            ExportLine(
                program=ch.description,
                daypart_name="PRODUCTION",
                day_times=parse_daypart("ROS"),
                length_sec=15,
                rate=float(ch.amount),
                start=flight_start,
                end=flight_start + timedelta(days=6),
                weekly_spots=[1],
                week_dates=[flight_start],
                is_charge=True,
            )
        )
    return ExportPreview(
        header=header,
        lines=lines,
        flight_start=flight_start,
        flight_end=flight_end,
        market_label=" ".join((order.market_label or "").split()),
        notes=notes,
    )


# ─── XML ──────────────────────────────────────────────────────────────────────


def _q(tag: str) -> str:
    return f"{{{NS_PROPOSAL}}}{tag}"


def _sub(parent: ET.Element, tag: str, text=None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag)
    for k, v in attrs.items():
        el.set(k, str(v))
    if text is not None:
        el.text = str(text)
    return el


def _periods(line: ExportLine) -> List[tuple]:
    """Consolidate weekly counts into (start, end, spots) runs, dropping zero weeks."""
    out: List[tuple] = []
    i, n = 0, len(line.weekly_spots)
    while i < n:
        c = line.weekly_spots[i]
        if c <= 0:
            i += 1
            continue
        j = i
        while j + 1 < n and line.weekly_spots[j + 1] == c:
            j += 1
        out.append((line.week_dates[i], line.week_dates[j] + timedelta(days=6), c))
        i = j + 1
    return out


def build_proposal_xml(preview: ExportPreview) -> bytes:
    h = preview.header
    h.require()
    ET.register_namespace("tvb", NS_TVB)
    ET.register_namespace("tvb-tp", NS_TP)
    ET.register_namespace("", NS_PROPOSAL)
    send = h.send_date or date.today()

    root = ET.Element(_q("AAAA-Message"))
    # ElementTree drops an unused prefix; the ingested file declared tvb, keep it.
    root.set("xmlns:tvb", NS_TVB)
    vals = _sub(root, _q("AAAA-Values"))
    _sub(vals, _q("SchemaName"), "SpotTVCableProposal")
    _sub(vals, _q("SchemaVersion"), SCHEMA_VERSION)
    _sub(vals, _q("Media"), "SpotTV")
    _sub(vals, _q("BusinessObject"), "Proposal")
    _sub(vals, _q("Action"), "New")
    _sub(vals, _q("UniqueMessageID"), f"CROSSINGS-{h.proposal_id}")

    prop = _sub(
        root,
        _q("Proposal"),
        uniqueIdentifier=h.proposal_id,
        version=str(h.version),
        sendDateTime=datetime(send.year, send.month, send.day).isoformat(),
        weekStartDay="Mo",
        startDate=preview.flight_start.isoformat(),
        endDate=preview.flight_end.isoformat(),
    )
    seller = _sub(prop, _q("Seller"), companyName=h.seller)
    _sub(seller, _q("Salesperson"), name=h.salesperson)
    buyer = _sub(prop, _q("Buyer"), buyingCompanyName=h.buyer_company)
    _sub(buyer, _q("BuyerName"), h.buyer_name)
    adv = _sub(prop, _q("Advertiser"), name=h.advertiser)
    _sub(adv, _q("Product"), name=h.product)
    _sub(prop, _q("Name"), h.proposal_name)
    _sub(prop, _q("SellerReference"), h.proposal_id)
    outlets = _sub(prop, _q("Outlets"))
    _sub(
        outlets,
        _q("TelevisionStation"),
        callLetters=h.call_letters,
        parentPlus="N",
        outletId="OUT0",
    )

    al = _sub(
        prop,
        _q("AvailList"),
        startDate=preview.flight_start.isoformat(),
        endDate=preview.flight_end.isoformat(),
        identifier="AL001",
        isPackage="N",
    )
    _sub(al, _q("Name"), preview.market_label or h.proposal_name)
    refs = _sub(al, _q("OutletReferences"))
    _sub(refs, _q("OutletReference"), outletFromProposalRef="OUT0", outletForListId="OUL0")

    for line in preview.lines:
        awl = _sub(al, _q("AvailLineWithDetailedPeriods"))
        _sub(awl, _q("OutletReference"), outletFromListRef="OUL0")
        dts = _sub(awl, _q("DayTimes"))
        for dt in line.day_times:
            d = _sub(dts, _q("DayTime"))
            _sub(d, _q("StartTime"), dt.start)
            _sub(d, _q("EndTime"), dt.end)
            days = _sub(d, _q("Days"))
            for name, on in zip(DAY_NAMES, dt.days):
                _sub(days, f"{{{NS_TP}}}{name}", "Y" if on else "N")
            _sub(d, _q("ProgramName"), line.program)
        _sub(awl, _q("DaypartName"), line.daypart_name)
        _sub(awl, _q("AvailName"), line.program)
        _sub(awl, _q("SpotLength"), f"00:{line.length_sec // 60:02d}:{line.length_sec % 60:02d}")
        periods = _sub(awl, _q("Periods"))
        if h.include_spots_per_week or line.is_charge:
            for start, end, spots in _periods(line):
                dp = _sub(
                    periods,
                    _q("DetailedPeriod"),
                    startDate=start.isoformat(),
                    endDate=end.isoformat(),
                )
                _sub(dp, _q("Rate"), f"{line.rate:.2f}")
                _sub(dp, _q("SpotsPerWeek"), spots)
        else:
            dp = _sub(
                periods,
                _q("DetailedPeriod"),
                startDate=line.start.isoformat(),
                endDate=line.end.isoformat(),
            )
            _sub(dp, _q("Rate"), f"{line.rate:.2f}")

    ET.indent(root, space="\t")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8") + b"\n"


def validate_proposal_xml(xml_bytes: bytes) -> List[str]:
    """Schema errors (empty list = valid). Raises if the local XSD is missing."""
    from lxml import etree  # runtime dependency (pyproject)

    if not XSD_PATH.exists():
        raise AgencyXmlError(f"schema not found: {XSD_PATH}")
    schema = etree.XMLSchema(etree.parse(str(XSD_PATH)))
    doc = etree.fromstring(xml_bytes)
    schema.validate(doc)
    return [e.message for e in schema.error_log]


def export_proposal(order: CrispinOrder, header: ExportHeader) -> bytes:
    """Workbook order + confirmed header → validated XML bytes (raises on any schema error)."""
    xml_bytes = build_proposal_xml(build_preview(order, header))
    errors = validate_proposal_xml(xml_bytes)
    if errors:
        raise AgencyXmlError("XML failed schema validation: " + "; ".join(errors[:5]))
    return xml_bytes


def suggested_filename(header: ExportHeader) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", header.proposal_id).strip("_") or "proposal"
    return f"{stem}.xml"
