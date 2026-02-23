"""
Universal AAAA SpotTV XML Parser
=================================
Parses AAAA/TVB SpotTV XML orders exported from any agency traffic
system (Strata, Freewheel, WideOrbit, Matrix, etc.) and converts
them into TCAAEstimate / TCAALine objects for handoff to the
existing TCAA automation pipeline.

Namespace:  http://www.AAAA.org/schemas/spotTVCableProposal
Schema:     SpotTVCableProposal / SpotTVCableOrder (v0.3.x)

This parser is AGENCY-AGNOSTIC. Any agency whose traffic system
can export AAAA SpotTV XML uses this same parser. The output
is identical to what parse_tcaa_pdf() produces, so process_tcaa_order()
requires zero changes.

Usage:
    estimates = parse_aaaa_xml("path/to/order.xml")
    # → same List[TCAAEstimate] as parse_tcaa_pdf()
    # → feed directly into process_tcaa_order(driver, xml_path)
    # → OR call process_xml_order(driver, xml_path) directly

Architecture note:
    This lives at:  browser_automation/parsers/aaaa_xml_parser.py
    It imports from: parsers/tcaa_parser.py  (TCAAEstimate, TCAALine)
    It is called by: browser_automation/xml_automation.py
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import sys

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from parsers.tcaa_parser import TCAAEstimate, TCAALine


# ============================================================================
# XML NAMESPACE CONSTANTS
# ============================================================================

# AAAA/TVB namespace URIs. Any compliant traffic system uses these.
# ElementTree requires Clark notation {uri}localname for element lookups.
# Use the helper functions _r() and _tp() to build tag strings cleanly.

ROOT_NS  = "http://www.AAAA.org/schemas/spotTVCableProposal"
TVBTP_NS = "http://www.AAAA.org/schemas/TVBGeneralTypes"


def _r(tag: str) -> str:
    """Build a root-namespace Clark tag: {ROOT_NS}tag"""
    return f"{{{ROOT_NS}}}{tag}"


def _tp(tag: str) -> str:
    """Build a TVBGeneralTypes Clark tag: {TVBTP_NS}tag"""
    return f"{{{TVBTP_NS}}}{tag}"


# Day-of-week element names (in TVBGeneralTypes namespace)
DAY_ELEMENTS = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]

# Map active-day boolean tuples to EtereClient day pattern strings
_DAY_PATTERN_MAP: dict[tuple, str] = {
    (True,  True,  True,  True,  True,  True,  True):  "M-Su",
    (True,  True,  True,  True,  True,  True,  False): "M-Sa",
    (True,  True,  True,  True,  True,  False, False): "M-F",
    (False, False, False, False, False, True,  True):  "Sa-Su",
    (False, False, False, False, False, True,  False): "Sa",
    (False, False, False, False, False, False, True):  "Su",
}


# ============================================================================
# PUBLIC API
# ============================================================================

def parse_aaaa_xml(xml_path: str) -> list[TCAAEstimate]:
    """
    Parse an AAAA SpotTV XML file and return a list of TCAAEstimate objects.

    One XML file typically contains one Proposal element = one TCAAEstimate.
    Multiple Proposal elements (rare) each become their own TCAAEstimate.

    Args:
        xml_path: Path to the AAAA SpotTV XML file

    Returns:
        List of TCAAEstimate objects ready for process_tcaa_order()

    Raises:
        ValueError: If the file is not a valid AAAA SpotTV XML
        FileNotFoundError: If the file does not exist
    """
    path = Path(xml_path)
    if not path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    print(f"[XML] Parsing: {path.name}")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Validate this is an AAAA SpotTV file
    schema_el = root.find(f".//{_r('SchemaName')}")
    if schema_el is None:
        raise ValueError("Cannot find SchemaName — is this an AAAA SpotTV XML file?")

    schema_name = schema_el.text or ""
    if "SpotTV" not in schema_name and "spotTV" not in schema_name.lower():
        raise ValueError(
            f"Schema '{schema_name}' is not a SpotTV schema. "
            f"This parser handles SpotTVCableProposal / SpotTVCableOrder only."
        )

    media  = _text(root, _r("Media"))  or "Unknown"
    action = _text(root, _r("Action")) or "Unknown"
    print(f"[XML] Schema: {schema_name}, Media: {media}, Action: {action}")

    # Find all Proposal elements
    proposals = root.findall(_r("Proposal"))
    if not proposals:
        raise ValueError("No <Proposal> elements found in XML file.")

    estimates = []
    for proposal in proposals:
        estimate = _parse_proposal(proposal)
        if estimate is not None:
            estimates.append(estimate)
            print(
                f"[XML] ✓ Estimate {estimate.estimate_number}: "
                f"{estimate.client} — {len(estimate.lines)} line(s)"
            )

    if not estimates:
        raise ValueError("XML parsed but no valid estimates could be extracted.")

    return estimates


# ============================================================================
# PROPOSAL PARSING
# ============================================================================

def _parse_proposal(proposal: ET.Element) -> Optional[TCAAEstimate]:
    """
    Parse a single <Proposal> element into a TCAAEstimate.

    Field mapping:
        uniqueIdentifier attr  → estimate_number
        SellerReference text   → estimate_number (fallback)
        Advertiser/@name       → client
        Advertiser/Product/@name → product (used in description)
        Buyer/@buyingCompanyName → buyer
        startDate / endDate    → flight_start / flight_end (converted to MM/DD/YYYY)
        AvailList/@startDate   → flight_start fallback
        Outlets                → market (detected from DMA description)
        AvailLineWithDetailedPeriods → lines
    """
    # ── Estimate number ──
    estimate_number = (
        proposal.get("uniqueIdentifier")
        or _text(proposal, _r("SellerReference"))
        or "UNKNOWN"
    )

    # ── Advertiser / client ──
    advertiser = proposal.find(_r("Advertiser"))
    client  = advertiser.get("name", "Unknown") if advertiser is not None else "Unknown"
    product = ""
    if advertiser is not None:
        prod_el = advertiser.find(_r("Product"))
        if prod_el is not None:
            product = prod_el.get("name", "")

    description = f"{product} XML Order".strip() if product else "XML Order"

    # ── Buyer ──
    buyer_el = proposal.find(_r("Buyer"))
    buyer    = buyer_el.get("buyingCompanyName", "") if buyer_el is not None else ""
    if buyer == "N/A":
        buyer = ""

    # ── Flight dates (ISO → MM/DD/YYYY) ──
    raw_start = proposal.get("startDate")
    raw_end   = proposal.get("endDate")

    if not raw_start or not raw_end:
        avail_list = proposal.find(_r("AvailList"))
        if avail_list is not None:
            raw_start = raw_start or avail_list.get("startDate")
            raw_end   = raw_end   or avail_list.get("endDate")

    if not raw_start or not raw_end:
        print(f"[XML] ✗ No flight dates in proposal {estimate_number}")
        return None

    flight_start = _iso_to_mmddyyyy(raw_start)
    flight_end   = _iso_to_mmddyyyy(raw_end)

    # ── Market (from AvailList DMA description) ──
    market = _detect_market(proposal)

    # ── Lines ──
    avail_list = proposal.find(_r("AvailList"))
    lines: list[TCAALine] = []

    if avail_list is not None:
        for avail_line in avail_list.findall(_r("AvailLineWithDetailedPeriods")):
            line = _parse_avail_line(avail_line, flight_start, flight_end)
            if line is not None:
                lines.append(line)

    if not lines:
        print(f"[XML] ⚠ No lines parsed from proposal {estimate_number}")

    return TCAAEstimate(
        estimate_number=estimate_number,
        description=description,
        flight_start=flight_start,
        flight_end=flight_end,
        client=client,
        buyer=buyer,
        market=market,
        lines=lines,
    )


# ============================================================================
# AVAIL LINE PARSING
# ============================================================================

def _parse_avail_line(
    avail_line: ET.Element,
    flight_start: str,
    flight_end: str,
) -> Optional[TCAALine]:
    """
    Parse a single <AvailLineWithDetailedPeriods> into a TCAALine.

    XML structure:
        <AvailLineWithDetailedPeriods>
            <DayTimes>
                <DayTime>
                    <StartTime>06:00</StartTime>
                    <EndTime>07:00</EndTime>
                    <Days>
                        <tvb-tp:Monday>Y</tvb-tp:Monday> ...
                    </Days>
                    <ProgramName>mandarin news</ProgramName>
                </DayTime>
            </DayTimes>
            <AvailName>mandarin news</AvailName>
            <SpotLength>00:00:30</SpotLength>
            <Periods>
                <DetailedPeriod startDate="..." endDate="...">
                    <Rate>25.00</Rate>
                    <SpotsPerWeek>14</SpotsPerWeek>   ← absent = 0 spots
                </DetailedPeriod>
                ...
            </Periods>
        </AvailLineWithDetailedPeriods>
    """
    # ── DayTime block ──
    day_time_el = avail_line.find(f".//{_r('DayTime')}")
    if day_time_el is None:
        print("[XML] ⚠ AvailLine missing DayTime element, skipping")
        return None

    # Times
    start_time_raw = _text(day_time_el, _r("StartTime")) or "06:00"
    end_time_raw   = _text(day_time_el, _r("EndTime"))   or "23:59"
    time_str = _xml_times_to_tcaa_format(start_time_raw, end_time_raw)

    # Days
    days_el  = day_time_el.find(_r("Days"))
    days_str = _parse_days_element(days_el)

    # Program name (DayTime/ProgramName preferred; AvailName fallback)
    program = (
        _text(day_time_el, _r("ProgramName"))
        or _text(avail_line, _r("AvailName"))
        or "Unknown"
    )

    # ── Spot length → seconds ──
    spot_length_raw = _text(avail_line, _r("SpotLength")) or "00:00:30"
    duration = _spot_length_to_seconds(spot_length_raw)

    # ── Periods → weekly_spots ──
    periods_el  = avail_line.find(_r("Periods"))
    weekly_spots, rate = _parse_periods_to_weekly(periods_el, flight_start, flight_end)

    total_spots = sum(weekly_spots)
    total_cost  = round(total_spots * rate, 2)

    print(
        f"[XML]   {program!r:25s}  {time_str:18s}  {days_str}  "
        f"{duration}s  ${rate:.2f}  weekly={weekly_spots}  total={total_spots}"
    )

    return TCAALine(
        station="CRTV-Cable",
        days=days_str,
        daypart="RT",
        time=time_str,
        program=program,
        duration=duration,
        weekly_spots=weekly_spots,
        rate=rate,
        total_spots=total_spots,
        total_cost=total_cost,
    )


# ============================================================================
# PERIOD → WEEKLY SPOTS EXPANSION
# ============================================================================

def _parse_periods_to_weekly(
    periods_el: Optional[ET.Element],
    flight_start: str,
    flight_end: str,
) -> tuple[list[int], float]:
    """
    Convert <DetailedPeriod> elements into a per-week spots list.

    The XML gives date-ranged periods, each with optional SpotsPerWeek.
    We expand these into a flat list [0, 14, 14, 14] — one entry per
    calendar week of the flight — matching TCAALine.weekly_spots.

    Rules:
    - Missing SpotsPerWeek element → 0 spots (partial/empty week)
    - Weeks are anchored to flight_start (week 0 = flight_start week)
    - Period date ranges map onto week indices by overlap detection
    - Rate is taken from the last period that has a <Rate> element
    """
    if periods_el is None:
        return [0], 0.0

    flight_start_dt = datetime.strptime(flight_start, "%m/%d/%Y")
    flight_end_dt   = datetime.strptime(flight_end,   "%m/%d/%Y")

    flight_days = (flight_end_dt - flight_start_dt).days + 1
    total_weeks = (flight_days + 6) // 7  # ceiling division

    weekly_spots = [0] * total_weeks
    rate = 0.0

    for period in periods_el.findall(_r("DetailedPeriod")):
        period_start_raw = period.get("startDate")
        period_end_raw   = period.get("endDate")

        if not period_start_raw or not period_end_raw:
            continue

        # Rate (last one wins)
        rate_text = _text(period, _r("Rate"))
        if rate_text:
            try:
                rate = float(rate_text)
            except ValueError:
                pass

        # SpotsPerWeek (absent = 0 — e.g. partial first week)
        spw_text = _text(period, _r("SpotsPerWeek"))
        spots_per_week = int(spw_text) if spw_text is not None else 0

        period_start_dt = datetime.strptime(period_start_raw, "%Y-%m-%d")
        period_end_dt   = datetime.strptime(period_end_raw,   "%Y-%m-%d")

        # Map period onto overlapping weeks
        for week_idx in range(total_weeks):
            week_start = flight_start_dt + timedelta(days=week_idx * 7)
            week_end   = week_start + timedelta(days=6)

            if max(period_start_dt, week_start) <= min(period_end_dt, week_end):
                weekly_spots[week_idx] = spots_per_week

    return weekly_spots, rate


# ============================================================================
# MARKET DETECTION
# ============================================================================

def _detect_market(proposal: ET.Element) -> str:
    """
    Detect market code from the AvailList description (DMA name).

    The XML does not carry a market code directly. The AvailList <n>
    element contains the DMA description e.g.:
        "Television Schedule - Seattle-Tacoma Dec25 DMA Nielsen Live+3"

    Returns market code ("SEA", "LAX", etc.) or "UNKNOWN" if not found.
    The xml_automation.py will prompt the user if UNKNOWN is returned.
    """
    avail_list = proposal.find(_r("AvailList"))
    if avail_list is not None:
        avail_desc = (_text(avail_list, _r("n")) or "").lower()

        dma_market_map = {
            "seattle":        "SEA",
            "los angeles":    "LAX",
            "san francisco":  "SFO",
            "houston":        "HOU",
            "chicago":        "CMP",
            "minneapolis":    "CMP",
            "washington":     "WDC",
            "new york":       "NYC",
            "sacramento":     "CVC",
            "central valley": "CVC",
            "dallas":         "DAL",
        }

        for keyword, code in dma_market_map.items():
            if keyword in avail_desc:
                print(f"[XML] ✓ Market detected from DMA: {code} ('{avail_desc[:50]}')")
                return code

    print("[XML] ⚠ Market not detected from XML — user will be prompted")
    return "UNKNOWN"


# ============================================================================
# DAYS PARSING
# ============================================================================

def _parse_days_element(days_el: Optional[ET.Element]) -> str:
    """
    Parse a <Days> element into an EtereClient day pattern string.

    Input:
        <Days>
            <tvb-tp:Monday>Y</tvb-tp:Monday>
            <tvb-tp:Tuesday>Y</tvb-tp:Tuesday>
            ...
        </Days>

    Output: "M-Su", "M-F", "Sa-Su", etc.
    """
    if days_el is None:
        return "M-Su"

    active = []
    for day_name in DAY_ELEMENTS:
        el = days_el.find(_tp(day_name))
        active.append(
            el is not None
            and el.text is not None
            and el.text.strip().upper() == "Y"
        )

    pattern = _DAY_PATTERN_MAP.get(tuple(active))
    if pattern:
        return pattern

    # Fallback for unusual patterns not in the map
    day_abbrevs = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    active_indices = [i for i, v in enumerate(active) if v]

    if not active_indices:
        return "M-Su"

    # Express as range if contiguous
    if active_indices == list(range(active_indices[0], active_indices[-1] + 1)):
        return f"{day_abbrevs[active_indices[0]]}-{day_abbrevs[active_indices[-1]]}"

    return ",".join(day_abbrevs[i] for i in active_indices)


# ============================================================================
# TIME FORMAT CONVERSION
# ============================================================================

def _xml_times_to_tcaa_format(start_time: str, end_time: str) -> str:
    """
    Convert XML 24-hour HH:MM times to TCAA-style "6:00a-7:00a".

    XML uses:  "06:00", "19:00", "24:00" (midnight as end-of-broadcast-day)
    TCAA uses: "6:00a-7:00a", "4:00p-7:00p", "7:00p-12:00a"

    EtereClient.parse_time_range() consumes the TCAA format.

    Special cases:
        24:00 (end time) → "12:00a"  (midnight = end of broadcast day)
        00:00 (end time) → "12:00a"  (same)
    """
    def _conv(t: str, is_end: bool) -> str:
        t = t.strip()
        if t in ("24:00", "00:00") and is_end:
            return "12:00a"
        parts = t.split(":")
        if len(parts) != 2:
            return t
        hour   = int(parts[0])
        minute = parts[1]
        if hour == 0:
            return f"12:{minute}a"
        elif hour < 12:
            return f"{hour}:{minute}a"
        elif hour == 12:
            return f"12:{minute}p"
        else:
            return f"{hour - 12}:{minute}p"

    return f"{_conv(start_time, False)}-{_conv(end_time, True)}"


# ============================================================================
# SPOT LENGTH CONVERSION
# ============================================================================

def _spot_length_to_seconds(spot_length: str) -> int:
    """
    Convert XML spot length "00:00:30" → 30 seconds.
    Handles HH:MM:SS and MM:SS formats.
    """
    parts = spot_length.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        pass
    return 30  # Default


# ============================================================================
# DATE FORMAT CONVERSION
# ============================================================================

def _iso_to_mmddyyyy(iso_date: str) -> str:
    """Convert ISO date "2026-03-30" → "03/30/2026" (TCAAEstimate format)."""
    return datetime.strptime(iso_date.strip(), "%Y-%m-%d").strftime("%m/%d/%Y")


# ============================================================================
# ELEMENT TEXT HELPER
# ============================================================================

def _text(element: ET.Element, tag: str) -> Optional[str]:
    """
    Find a child element by Clark-notation tag and return its stripped text.
    Returns None if the element is not found or has no text.
    """
    found = element.find(tag)
    if found is not None and found.text:
        return found.text.strip()
    return None


# ============================================================================
# DIAGNOSTIC SUMMARY
# ============================================================================

def print_parse_summary(estimates: list[TCAAEstimate]) -> None:
    """Print a human-readable summary of parsed estimates."""
    print(f"\n{'='*70}")
    print("XML PARSE SUMMARY")
    print(f"{'='*70}")
    print(f"Found {len(estimates)} estimate(s):\n")

    for est in estimates:
        print(f"  Estimate:  {est.estimate_number}")
        print(f"  Client:    {est.client}")
        print(f"  Market:    {est.market}")
        print(f"  Flight:    {est.flight_start} – {est.flight_end}")
        print(f"  Lines:     {len(est.lines)}")
        for i, line in enumerate(est.lines, 1):
            bonus_tag = " [BONUS]" if line.is_bonus() else ""
            print(
                f"    {i:2d}. {line.days:6s}  {line.time:18s}  "
                f"{line.program:25s}  {line.duration}s  ${line.rate:.2f}"
                f"  total={line.total_spots}{bonus_tag}"
            )
            print(f"         weekly: {line.weekly_spots}")
        print()


# ============================================================================
# STANDALONE TEST
# ============================================================================

if __name__ == "__main__":
    import sys as _sys

    xml_file = _sys.argv[1] if len(_sys.argv) > 1 else "CRTV-TV_XML.xml"

    print(f"Testing AAAA XML Parser with: {xml_file}\n")

    try:
        estimates = parse_aaaa_xml(xml_file)
        print_parse_summary(estimates)

        print("[TEST] Verifying TCAAEstimate compatibility...")
        for est in estimates:
            assert est.estimate_number, "Missing estimate_number"
            assert est.flight_start,    "Missing flight_start"
            assert est.flight_end,      "Missing flight_end"
            assert est.lines,           "No lines parsed"
            for line in est.lines:
                assert line.weekly_spots, "Missing weekly_spots"
                assert line.duration > 0, "Invalid duration"
        print("[TEST] ✓ All compatibility checks passed\n")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
