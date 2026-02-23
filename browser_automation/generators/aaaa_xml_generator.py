"""
AAAA SpotTV XML Generator
=========================
Generates AAAA SpotTVCableProposal XML from Charmaine insertion order data.
This is the reverse of browser_automation/parsers/aaaa_xml_parser.py.

Namespace:  http://www.AAAA.org/schemas/spotTVCableProposal
Schema:     SpotTVCableProposal v0.3

The generated XML can be imported directly into agency traffic systems
(Strata, WideOrbit, FreeWheel) and also round-trips cleanly through
aaaa_xml_parser.py.

Usage (standalone):
    python3 browser_automation/generators/aaaa_xml_generator.py incoming/SCRF\\ 2026.pdf
    # → writes to outgoing/SCRF-2026.xml
"""

import io
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET


# ============================================================================
# NAMESPACE CONSTANTS  (mirror of aaaa_xml_parser.py)
# ============================================================================

ROOT_NS  = "http://www.AAAA.org/schemas/spotTVCableProposal"
TVBTP_NS = "http://www.AAAA.org/schemas/TVBGeneralTypes"

DAY_ELEMENTS = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]


def _r(tag: str) -> str:
    """Clark notation for root namespace."""
    return f"{{{ROOT_NS}}}{tag}"


def _tp(tag: str) -> str:
    """Clark notation for TVBGeneralTypes namespace."""
    return f"{{{TVBTP_NS}}}{tag}"


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class DayTimeSpec:
    start_time: str       # HH:MM 24h e.g. "19:00"
    end_time: str         # HH:MM 24h; "24:00" for midnight
    days: tuple           # 7 bools (Mon→Sun)
    program: str = ""     # Program name for this window (uses parent if blank)


@dataclass
class ProposalLine:
    program: str
    day_times: list          # list[DayTimeSpec] — multiple for split M-F/Sa-Su dayparts
    daypart_name: str        # "RT"
    spot_length_sec: int
    weekly_spots: list       # list[int] — one per week of flight
    rate: float


@dataclass
class ProposalSpec:
    estimate_number: str
    flight_start: str              # "YYYY-MM-DD"
    flight_end: str                # "YYYY-MM-DD"
    week_boundaries: list          # list[tuple] — [("2026-04-27","2026-05-03"), ...]
    client_name: str
    product_name: str
    contact_name: str
    contact_email: str
    buyer_name: str                # Agency or "Direct"
    seller_name: str               # "Crossings TV"
    call_letters: str              # "CRTV"
    market_description: str        # Goes into AvailList/Name element
    lines: list                    # list[ProposalLine]
    version: int = 1
    send_datetime: str = ""        # Auto-generated if blank


# ============================================================================
# HELPER FUNCTIONS  (reversals of aaaa_xml_parser.py helpers)
# ============================================================================

def _tcaa_time_to_24h(t: str) -> str:
    """
    Convert TCAA-style time to 24h XML format.
    Reverses _xml_times_to_tcaa_format() from the parser.

    Examples:
        "7p"  → "19:00"
        "12a" → "24:00"  (midnight = end of broadcast day)
        "11a" → "11:00"
        "1p"  → "13:00"
        "12p" → "12:00"
    """
    t = t.strip().lower()
    # Remove trailing 'm' (e.g. "7pm" → "7p")
    if len(t) > 1 and t.endswith("m") and t[-2] in ("a", "p"):
        t = t[:-1]

    m = re.match(r"^(\d{1,2})(?::(\d{2}))?([ap])$", t)
    if not m:
        return t  # Return as-is if unparseable

    hour   = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm   = m.group(3)

    if ampm == "a":
        if hour == 12:
            return "24:00"          # 12a = midnight = broadcast day end
        return f"{hour:02d}:{minute:02d}"
    else:  # pm
        if hour == 12:
            return f"12:{minute:02d}"
        return f"{hour + 12:02d}:{minute:02d}"


def _days_str_to_bools(days_str: str) -> tuple:
    """
    Convert day range string to 7-bool tuple (Mon→Sun).
    Reverses _parse_days_element() from the parser.

    "M-F"     → (T, T, T, T, T, F, F)
    "Sat-Sun" → (F, F, F, F, F, T, T)
    "M-Sun"   → (T, T, T, T, T, T, T)
    """
    s = days_str.strip().lower().replace(" ", "")

    if s in ("m-su", "m-sun", "mon-sun", "daily", "m-s", "7days"):
        return (True, True, True, True, True, True, True)
    if s in ("m-f", "mon-fri", "weekdays", "m-fri", "mon-f"):
        return (True, True, True, True, True, False, False)
    if s in ("sa-su", "sat-sun", "sat-su", "sa-sun", "weekend"):
        return (False, False, False, False, False, True, True)
    if s in ("m-sa", "mon-sat"):
        return (True, True, True, True, True, True, False)
    if s in ("sa", "sat", "saturday"):
        return (False, False, False, False, False, True, False)
    if s in ("su", "sun", "sunday"):
        return (False, False, False, False, False, False, True)

    # Default: all week
    return (True, True, True, True, True, True, True)


def _seconds_to_spot_length(seconds: int) -> str:
    """
    Convert seconds to XML HH:MM:SS spot length format.
    Reverses _spot_length_to_seconds() from the parser.
    15 → "00:00:15"
    """
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _mmddyyyy_to_iso(date_str: str) -> str:
    """
    Convert "04/27/2026" → "2026-04-27".
    Reverses _iso_to_mmddyyyy() from the parser.
    """
    if not date_str:
        return ""
    return datetime.strptime(date_str.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")


def _consolidate_periods(
    weekly_spots: list,
    week_boundaries: list,
    rate: float,
) -> list:
    """
    Collapse consecutive identical weeks into one DetailedPeriod.
    Reverses _parse_periods_to_weekly() from the parser.

    Returns list of dicts: {start_date, end_date, spots, rate}.
    Caller omits <SpotsPerWeek> when spots == 0 (schema convention).
    """
    periods = []
    if not weekly_spots or not week_boundaries:
        return periods

    i = 0
    while i < len(weekly_spots):
        spots      = weekly_spots[i]
        start_date = week_boundaries[i][0]
        end_date   = week_boundaries[i][1]

        # Extend run while next weeks have the same spot count
        j = i + 1
        while j < len(weekly_spots) and weekly_spots[j] == spots:
            end_date = week_boundaries[j][1]
            j += 1

        periods.append({
            "start_date": start_date,
            "end_date":   end_date,
            "spots":      spots,
            "rate":       rate,
        })
        i = j

    return periods


def parse_charmaine_daypart(daypart_str: str, program: str = "") -> list:
    """
    Parse a Charmaine daypart string into a list of DayTimeSpec objects.

    Handles:
        "M-F 7p-11p"                     → [DayTimeSpec weekday 19:00-23:00]
        "M-F 7p-11p; Sat-Sun 7p-12a"     → [weekday, weekend DayTimeSpec]
        "M-Sun 7p-12a"                   → [DayTimeSpec all-week]

    Rules:
        - Split on ";" for multiple windows
        - Last token in each window = time range "7p-11p"
        - Preceding tokens = day range "M-F" or "Sat-Sun"
    """
    results = []
    windows = [w.strip() for w in daypart_str.split(";") if w.strip()]

    for window in windows:
        tokens = window.strip().split()
        if len(tokens) < 2:
            continue

        time_range = tokens[-1]
        days_str   = " ".join(tokens[:-1])

        days = _days_str_to_bools(days_str)

        # Parse time range: "7p-11p" → ("19:00", "23:00")
        tr_match = re.match(
            r"^(.+?[ap]m?)-(.+[ap]m?)$", time_range, re.IGNORECASE
        )
        if tr_match:
            start_t = _tcaa_time_to_24h(tr_match.group(1))
            end_t   = _tcaa_time_to_24h(tr_match.group(2))
        elif "-" in time_range:
            parts   = time_range.split("-", 1)
            start_t = _tcaa_time_to_24h(parts[0])
            end_t   = _tcaa_time_to_24h(parts[1]) if len(parts) > 1 else "24:00"
        else:
            start_t = "19:00"
            end_t   = "24:00"

        results.append(DayTimeSpec(
            start_time=start_t,
            end_time=end_t,
            days=days,
            program=program,
        ))

    if not results:
        # Fallback: primetime all week
        results.append(DayTimeSpec(
            start_time="19:00",
            end_time="24:00",
            days=(True, True, True, True, True, True, True),
            program=program,
        ))

    return results


# ============================================================================
# XML ELEMENT BUILDERS
# ============================================================================

def _text_el(parent: ET.Element, tag: str, text: str) -> ET.Element:
    """Create a sub-element in ROOT_NS namespace with text content."""
    el = ET.SubElement(parent, _r(tag))
    el.text = text
    return el


def _build_days_element(parent: ET.Element, days: tuple) -> ET.Element:
    """
    Build a <Days> element with Y/N children for each day (Mon→Sun).
    Children are in the TVBGeneralTypes namespace (tvb-tp: prefix).
    """
    days_el = ET.SubElement(parent, _r("Days"))
    for i, day_name in enumerate(DAY_ELEMENTS):
        day_el = ET.SubElement(days_el, _tp(day_name))
        day_el.text = "Y" if (i < len(days) and days[i]) else "N"
    return days_el


def _build_avail_line(
    avail_list: ET.Element,
    line: ProposalLine,
    week_boundaries: list,
) -> ET.Element:
    """Build a complete <AvailLineWithDetailedPeriods> element."""
    avail_line = ET.SubElement(avail_list, _r("AvailLineWithDetailedPeriods"))

    # ── DayTimes ──
    day_times_el = ET.SubElement(avail_line, _r("DayTimes"))
    for dt in line.day_times:
        day_time = ET.SubElement(day_times_el, _r("DayTime"))
        _text_el(day_time, "StartTime", dt.start_time)
        _text_el(day_time, "EndTime",   dt.end_time)
        _build_days_element(day_time, dt.days)
        _text_el(day_time, "ProgramName", dt.program or line.program)

    # ── AvailName ──
    _text_el(avail_line, "AvailName", line.program)

    # ── SpotLength ──
    _text_el(avail_line, "SpotLength", _seconds_to_spot_length(line.spot_length_sec))

    # ── Periods ──
    periods_el  = ET.SubElement(avail_line, _r("Periods"))
    consolidated = _consolidate_periods(line.weekly_spots, week_boundaries, line.rate)

    for period in consolidated:
        period_el = ET.SubElement(periods_el, _r("DetailedPeriod"))
        period_el.set("startDate", period["start_date"])
        period_el.set("endDate",   period["end_date"])

        rate_el      = ET.SubElement(period_el, _r("Rate"))
        rate_el.text = f"{period['rate']:.2f}"

        # Omit <SpotsPerWeek> when 0 (schema convention for inactive periods)
        if period["spots"] != 0:
            spw_el      = ET.SubElement(period_el, _r("SpotsPerWeek"))
            spw_el.text = str(period["spots"])

    return avail_line


def _indent(elem: ET.Element, level: int = 0, space: str = "  ") -> None:
    """Add pretty-print indentation to XML tree in-place (Python 3.8 compatible)."""
    i = "\n" + level * space
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + space
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for child in elem:
            _indent(child, level + 1, space)
        # Last child tail closes the parent element
        if not child.tail or not child.tail.strip():
            child.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i
    if not level:
        elem.tail = "\n"


# ============================================================================
# MAIN GENERATOR
# ============================================================================

def generate_aaaa_xml(spec: ProposalSpec) -> str:
    """
    Generate an AAAA SpotTVCableProposal XML document from a ProposalSpec.
    Returns the XML as a UTF-8 string with declaration and indentation.
    """
    # Register namespace prefixes (affects ET serialisation globally)
    ET.register_namespace("",        ROOT_NS)
    ET.register_namespace("tvb-tp",  TVBTP_NS)

    send_dt = spec.send_datetime or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # ── Root ──
    root = ET.Element(_r("SpotCableData"))

    # ── Description ──
    desc = ET.SubElement(root, _r("Description"))
    _text_el(desc, "SchemaName",    "SpotTVCableProposal")
    _text_el(desc, "SchemaVersion", "0.3")
    _text_el(desc, "Media",         "Cable")
    _text_el(desc, "Action",        "Proposal")
    _text_el(desc, "SentBy",        spec.seller_name)
    _text_el(desc, "SentDate",      send_dt)

    # ── Proposal ──
    proposal = ET.SubElement(root, _r("Proposal"))
    proposal.set("uniqueIdentifier", spec.estimate_number)
    proposal.set("startDate",        spec.flight_start)
    proposal.set("endDate",          spec.flight_end)
    proposal.set("version",          str(spec.version))

    # Advertiser / Product
    adv_el = ET.SubElement(proposal, _r("Advertiser"))
    adv_el.set("name", spec.client_name)
    prod_el = ET.SubElement(adv_el, _r("Product"))
    prod_el.set("name", spec.product_name)

    # Buyer
    buyer_el = ET.SubElement(proposal, _r("Buyer"))
    buyer_el.set("buyingCompanyName", spec.buyer_name)

    # Seller
    seller_el = ET.SubElement(proposal, _r("Seller"))
    seller_el.set("sellingCompanyName", spec.seller_name)

    # Contact (optional reference info)
    if spec.contact_name or spec.contact_email:
        contact_el = ET.SubElement(proposal, _r("Contact"))
        if spec.contact_name:
            contact_el.set("name",  spec.contact_name)
        if spec.contact_email:
            contact_el.set("email", spec.contact_email)

    # ── AvailList ──
    avail_list = ET.SubElement(proposal, _r("AvailList"))
    avail_list.set("startDate", spec.flight_start)
    avail_list.set("endDate",   spec.flight_end)
    _text_el(avail_list, "Name", spec.market_description)

    # Outlets
    outlets_el = ET.SubElement(avail_list, _r("Outlets"))
    outlet_el  = ET.SubElement(outlets_el, _r("Outlet"))
    _text_el(outlet_el, "CallLetters", spec.call_letters)

    # AvailLines (one per ProposalLine)
    for line in spec.lines:
        _build_avail_line(avail_list, line, spec.week_boundaries)

    # ── Pretty-print and serialise ──
    _indent(root)

    buf  = io.BytesIO()
    tree = ET.ElementTree(root)
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue().decode("utf-8")


def write_aaaa_xml(spec: ProposalSpec, output_path: str) -> Path:
    """Write AAAA XML to file. Creates parent directories. Returns Path."""
    xml_str  = generate_aaaa_xml(spec)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(xml_str, encoding="utf-8")
    return out_path


# ============================================================================
# CHARMAINE ORDER → PROPOSAL SPEC CONVERSION
# ============================================================================

_MARKET_DESCRIPTIONS: dict = {
    "CVC": "Television Schedule - Sacramento-Central Valley Nielsen Live+3",
    "SFO": "Television Schedule - San Francisco Nielsen Live+3",
    "LAX": "Television Schedule - Los Angeles Nielsen Live+3",
    "SEA": "Television Schedule - Seattle-Tacoma Nielsen Live+3",
    "HOU": "Television Schedule - Houston Nielsen Live+3",
    "NYC": "Television Schedule - New York Nielsen Live+3",
    "WDC": "Television Schedule - Washington DC Nielsen Live+3",
    "CMP": "Television Schedule - Chicago-Minneapolis Nielsen Live+3",
    "DAL": "Television Schedule - Dallas Nielsen Live+3",
}


def _extract_email(email_str: str) -> str:
    """
    Extract bare email address from strings like 'Name <email@domain.com>'
    or 'email@domain.com'.
    """
    m = re.search(r"<([^>]+)>", email_str)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b[\w.+-]+@[\w.+-]+\.\w+\b", email_str)
    if m:
        return m.group()
    return email_str


def _find_bonus(bonus_by_lang: dict, paid_language: str):
    """
    Find a bonus line matching the paid language.
    Tries exact match, then prefix/substring match (handles "Chinese ( Mandarin)"
    vs "Chinese").
    """
    key = paid_language.lower().strip()

    # 1. Exact match
    if key in bonus_by_lang:
        return bonus_by_lang[key]

    # 2. One is prefix of the other (covers "Chinese" ↔ "Chinese ( Mandarin)")
    for bk, bv in bonus_by_lang.items():
        if key.startswith(bk) or bk.startswith(key):
            return bv

    return None


def charmaine_order_to_proposal_spec(
    order,                  # CharmaineOrder — duck-typed to avoid circular import
    estimate_number: str,
    buyer_name: str,
    call_letters: str,
) -> ProposalSpec:
    """
    Convert a CharmaineOrder to a ProposalSpec for XML generation.

    - Paid lines are included at their specified rate.
    - Bonus lines are included at $0.00 rate (agency sees full value).
    - Each paid line is paired with its matching bonus line (by language).
    - Week columns with empty start_date (e.g. phantom "Unit Value" columns
      that the parser picks up) are filtered out along with their spot counts.
    - Week boundaries are derived from the remaining valid week_columns:
        week[i] starts at week_columns[i].start_date
        week[i] ends at week_columns[i+1].start_date - 1 day
        last week ends at flight_end
    """
    flight_end_iso = _mmddyyyy_to_iso(order.flight_end)

    # ── Filter out phantom week columns (no valid start_date) ──
    valid_indices  = [i for i, wc in enumerate(order.week_columns) if wc.start_date]
    valid_wc       = [order.week_columns[i] for i in valid_indices]

    # ── Derive week boundaries from valid week_columns ──
    week_boundaries = []
    for j, wc in enumerate(valid_wc):
        week_start = _mmddyyyy_to_iso(wc.start_date)
        if j + 1 < len(valid_wc):
            next_start = datetime.strptime(valid_wc[j + 1].start_date, "%m/%d/%Y")
            week_end   = (next_start - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            week_end = flight_end_iso
        week_boundaries.append((week_start, week_end))

    # ── Separate paid and bonus lines ──
    paid_lines    = [l for l in order.lines if not l.is_bonus]
    bonus_by_lang = {
        l.language.lower().strip(): l
        for l in order.lines
        if l.is_bonus
    }

    # ── Build ProposalLines: paid line, then its bonus counterpart ──
    proposal_lines = []

    for paid in paid_lines:
        # Slice weekly_spots to valid columns only
        valid_weekly = [paid.weekly_spots[i] for i in valid_indices
                        if i < len(paid.weekly_spots)]

        day_times = parse_charmaine_daypart(paid.daypart, program=paid.language)

        proposal_lines.append(ProposalLine(
            program=paid.language,
            day_times=day_times,
            daypart_name="RT",
            spot_length_sec=order.duration_seconds,
            weekly_spots=valid_weekly,
            rate=paid.rate,
        ))

        # Match bonus by language with fuzzy fallback
        bonus = _find_bonus(bonus_by_lang, paid.language)
        if bonus:
            bonus_valid_weekly = [bonus.weekly_spots[i] for i in valid_indices
                                  if i < len(bonus.weekly_spots)]
            bonus_day_times = parse_charmaine_daypart(
                paid.daypart, program=bonus.language
            )
            proposal_lines.append(ProposalLine(
                program=bonus.language,
                day_times=bonus_day_times,
                daypart_name="RT",
                spot_length_sec=order.duration_seconds,
                weekly_spots=bonus_valid_weekly,
                rate=0.0,
            ))

    market_desc = _MARKET_DESCRIPTIONS.get(
        order.market,
        f"Television Schedule - {order.market} Nielsen Live+3",
    )

    return ProposalSpec(
        estimate_number=estimate_number,
        flight_start=_mmddyyyy_to_iso(order.flight_start),
        flight_end=_mmddyyyy_to_iso(order.flight_end),
        week_boundaries=week_boundaries,
        client_name=order.advertiser,
        product_name=order.campaign,
        contact_name=order.contact,
        contact_email=_extract_email(order.email),
        buyer_name=buyer_name,
        seller_name="Crossings TV",
        call_letters=call_letters,
        market_description=market_desc,
        lines=proposal_lines,
    )


# ============================================================================
# STANDALONE TEST / MAIN
# ============================================================================

if __name__ == "__main__":
    # Add project root to sys.path so we can import charmaine_parser
    _project_root = Path(__file__).parent.parent.parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

    from browser_automation.parsers.charmaine_parser import parse_charmaine_pdf

    # ── Determine input PDF ──
    if len(sys.argv) >= 2:
        pdf_path = sys.argv[1]
    else:
        pdf_path = str(_project_root / "incoming" / "SCRF 2026.pdf")

    print(f"[GEN] Parsing PDF: {pdf_path}")
    orders = parse_charmaine_pdf(pdf_path)

    if not orders:
        print("[GEN] ERROR: No orders parsed from PDF.")
        sys.exit(1)

    order = orders[0]
    print(f"[GEN] Order: {order.advertiser} — {order.campaign}")
    print(f"[GEN] Market: {order.market}  Duration: :{order.duration_seconds}s")
    print(f"[GEN] Flight: {order.flight_start} → {order.flight_end}")
    print(f"[GEN] Weeks:  {[wc.label for wc in order.week_columns]}")
    print(f"[GEN] Lines:  {len(order.lines)}")

    # ── Convert to ProposalSpec ──
    spec = charmaine_order_to_proposal_spec(
        order=order,
        estimate_number="SCRF-2026",
        buyer_name="Direct",
        call_letters="CRTV",
    )

    print(f"\n[GEN] ProposalSpec: {len(spec.lines)} proposal line(s)")
    for line in spec.lines:
        rate_tag = f"${line.rate:.2f}" if line.rate > 0 else "BONUS $0.00"
        print(
            f"[GEN]   {line.program:20s} {rate_tag:12s} "
            f"weekly={line.weekly_spots}  periods={len(line.day_times)} daypart(s)"
        )

    # ── Write XML ──
    output_path = _project_root / "outgoing" / "SCRF-2026.xml"
    out = write_aaaa_xml(spec, str(output_path))
    print(f"\n[GEN] ✓ Written: {out}")
    print(f"[GEN]   Size: {out.stat().st_size:,} bytes")
