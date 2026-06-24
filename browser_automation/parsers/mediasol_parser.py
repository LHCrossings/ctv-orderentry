"""
Media Solutions / Pulsar Advertising c/o Mediasol Order Parser

Parses Strata IO PDFs from Media Solutions (also filed as "Pulsar Advertising c/o Mediasol").
Format is the same family as H&L Partners (Strata IO system) with these differences:
  - Station header is "Crossings TV-TV" (not "CRTV-TV")
  - No line number prefix on data lines
  - Two rate columns: Gross and STN Net — we parse STN Net (rates_are_net=True)
  - No trailing GRP/rating float — last number on the line is total spots
  - A language tag line (VIETNAMESE, BONUS, etc.) appears after the program name
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pdfplumber

# Import shared utilities from hl_parser to avoid duplication


@dataclass
class MediasolLine:
    """A single line item from a Media Solutions order."""
    station: str
    days: str
    daypart: str
    time: str
    program: str
    duration: int
    weekly_spots: List[int]
    rate: float       # STN Net rate as parsed from PDF
    total_spots: int
    total_cost: float
    line_number: Optional[int] = None

    def is_bonus(self) -> bool:
        return self.rate == 0.0


@dataclass
class MediasolEstimate:
    """A single estimate (contract) from a Media Solutions order."""
    estimate_number: str
    description: str
    flight_start: str
    flight_end: str
    client: str
    buyer: str
    market: str
    lines: List[MediasolLine]
    rates_are_net: bool = True  # Mediasol IOs show STN Net — always gross up


def parse_mediasol_pdf(pdf_path: str) -> List[MediasolEstimate]:
    """
    Parse a Media Solutions / Pulsar Advertising PDF and extract all estimates.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        List of MediasolEstimate objects, one per estimate number
    """
    estimates = []

    with pdfplumber.open(pdf_path) as pdf:
        current_estimate = None

        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text() or ""

            # Skip summary pages
            if "Summary by" in text and "Crossings TV-TV" not in text:
                continue

            if "Estimate:" in text and ("Daypart" in text or "Crossings TV-TV" in text):
                estimate_data = _extract_estimate_header(text)

                if estimate_data:
                    est_num = estimate_data["estimate"]

                    existing = next(
                        (e for e in estimates if e.estimate_number == est_num),
                        current_estimate if (
                            current_estimate and current_estimate.estimate_number == est_num
                        ) else None,
                    )

                    if existing:
                        lines = _extract_lines_from_page(text)
                        if lines:
                            existing.lines.extend(lines)
                    else:
                        if current_estimate:
                            estimates.append(current_estimate)

                        current_estimate = MediasolEstimate(
                            estimate_number=est_num,
                            description=estimate_data["description"],
                            flight_start=estimate_data["flight_start"],
                            flight_end=estimate_data["flight_end"],
                            client=estimate_data["client"],
                            buyer=estimate_data["buyer"],
                            market=estimate_data["market"],
                            lines=[],
                            rates_are_net=True,
                        )

                        lines = _extract_lines_from_page(text)
                        if lines:
                            current_estimate.lines.extend(lines)
                        elif not current_estimate.lines:
                            current_estimate = None

        if current_estimate:
            estimates.append(current_estimate)

    return estimates


def _extract_estimate_header(text: str) -> Optional[Dict[str, str]]:
    """Extract header fields from an estimate page."""
    header: Dict[str, str] = {}

    estimate_match = re.search(r'Estimate:\s*(\d+)', text)
    if not estimate_match:
        return None
    header["estimate"] = estimate_match.group(1)

    desc_match = re.search(
        r'Description:\s*([^\n]+?)(?:\s+\d{3}\s+\w+\s+Street|\s+Flight Start Date:|\n)',
        text,
    )
    header["description"] = desc_match.group(1).strip() if desc_match else ""

    flight_start_match = re.search(r'Flight Start Date:\s*(\d{1,2}/\d{1,2}/\d{4})', text)
    flight_end_match = re.search(r'Flight End Date:\s*(\d{1,2}/\d{1,2}/\d{4})', text)
    header["flight_start"] = flight_start_match.group(1) if flight_start_match else "Unknown"
    header["flight_end"] = flight_end_match.group(1) if flight_end_match else "Unknown"

    client_match = re.search(r'Client:\s*([^\n]+?)(?:\s+Estimate:|\s+Vendor:)', text)
    header["client"] = client_match.group(1).strip() if client_match else "Unknown"

    buyer_match = re.search(r'Buyer:\s*([^\n]+?)(?:\s+E-Mail:|\s+Fax:|\n)', text)
    header["buyer"] = buyer_match.group(1).strip() if buyer_match else "Unknown"

    market_match = re.search(r'Market:\s*([^\n]+?)(?:\s+Flight End Date:|\n)', text)
    header["market"] = market_match.group(1).strip() if market_match else "Unknown"

    return header


def _extract_lines_from_page(text: str) -> List[MediasolLine]:
    """Extract all contract line items from a page."""
    lines = []
    text_lines = text.split("\n")

    # Find "Crossings TV-TV" as the table start marker
    table_start = None
    for i, line in enumerate(text_lines):
        if line.strip() == "Crossings TV-TV":
            table_start = i + 1
            break

    if table_start is None:
        return lines

    i = table_start
    while i < len(text_lines):
        line = text_lines[i]

        if any(
            kw in line
            for kw in ("Total Spots:", "Total Cost:", "Disclaimer:", "Signature:", "Station Monthly")
        ):
            break

        # Data line: starts with a day pattern and contains a time
        if re.match(r"^[MTWFS]", line.strip()) and re.search(r"\d+:\d+[ap]", line):
            line_obj, next_i = _parse_line_entry(text_lines, i)
            if line_obj:
                lines.append(line_obj)
                i = next_i
            else:
                i += 1
        else:
            i += 1

    return lines


def _parse_line_entry(
    text_lines: List[str], start_index: int
) -> Tuple[Optional[MediasolLine], int]:
    """
    Parse one contract line entry.

    Mediasol line format (no line number prefix):
        Days  Time  DaypartCode  GrossRate  NetRate  Duration  Wk1 Wk2 ... Total
        Program Name
        LANGUAGE TAG

    We skip GrossRate and use NetRate.
    The last integer on the data row is total spots (no trailing GRP float).
    """
    line = text_lines[start_index]

    try:
        parts = line.split()
        if len(parts) < 5:
            return None, start_index + 1

        idx = 0

        # Days
        days = parts[idx]
        idx += 1

        # Time — collect tokens until the 2-letter uppercase daypart code
        time_parts = []
        while idx < len(parts) and not (
            len(parts[idx]) == 2 and parts[idx].isupper() and parts[idx].isalpha()
        ):
            time_parts.append(parts[idx])
            idx += 1

        time_patterns = []
        for part in time_parts:
            if re.match(r"\d+:\d+[ap]", part) or re.match(r"\d+:\d+[ap]-$", part):
                time_patterns.append(part)

        if len(time_patterns) >= 2:
            time_str = f"{time_patterns[0].rstrip('-')}-{time_patterns[1]}"
        elif len(time_patterns) == 1:
            time_str = time_patterns[0]
        else:
            time_str = "".join(time_parts)

        # Complete split time ("11:00a-" + next line "1:00p")
        if time_str.endswith("-"):
            next_line_idx = start_index + 1
            if next_line_idx < len(text_lines):
                cont = re.match(r"^(\d+:\d+[ap])", text_lines[next_line_idx].strip())
                if cont:
                    time_str += cont.group(1)

        time_match = re.match(r"(\d+:\d+[ap])[-\s]*(\d+:\d+[ap])", time_str)
        time = f"{time_match.group(1)}-{time_match.group(2)}" if time_match else time_str

        # Daypart code
        if idx >= len(parts):
            return None, start_index + 1
        daypart_code = parts[idx]
        idx += 1

        # Gross rate — skip
        if idx >= len(parts):
            return None, start_index + 1
        idx += 1

        # STN Net rate — use this
        if idx >= len(parts):
            return None, start_index + 1
        net_str = parts[idx].replace("$", "").replace(",", "")
        rate = float(net_str)
        idx += 1

        # Duration
        if idx >= len(parts):
            return None, start_index + 1
        duration = int(parts[idx])
        idx += 1

        # Weekly spots + total spots (no trailing GRP float)
        remaining: List[float] = []
        while idx < len(parts):
            try:
                remaining.append(float(parts[idx].replace(",", "")))
                idx += 1
            except ValueError:
                break

        if len(remaining) < 2:
            return None, start_index + 1

        total_spots = int(remaining[-1])
        weekly_spots = [int(n) for n in remaining[:-1]]

        # Program name: on the next line (skip time-continuation fragment first)
        next_idx = start_index + 1
        program = "Unknown Program"

        if next_idx < len(text_lines):
            next_line = text_lines[next_idx].strip()

            if re.match(r"^\d+:\d+[ap]$", next_line):
                next_idx += 1
                if next_idx < len(text_lines):
                    next_line = text_lines[next_idx].strip()

            clean = re.sub(r"\$[\d,]+\.?\d*$", "", next_line).strip()
            is_next_data = re.match(r"^[MTWFS]", clean) and re.search(r"\d+:\d+[ap]", clean)
            if clean and not is_next_data:
                program = clean
                next_idx += 1

        # Skip language/category tag line (all-caps, 1-3 words: VIETNAMESE, BONUS, etc.)
        if next_idx < len(text_lines):
            tag = text_lines[next_idx].strip()
            if tag and tag.isupper() and len(tag.split()) <= 3:
                next_idx += 1

        line_obj = MediasolLine(
            station="Crossings TV-TV",
            days=days,
            daypart=daypart_code,
            time=time,
            program=program,
            duration=duration,
            weekly_spots=weekly_spots,
            rate=rate,
            total_spots=total_spots,
            total_cost=rate * total_spots,
        )
        return line_obj, next_idx

    except (IndexError, ValueError) as e:
        print(f"[MEDIASOL] Error parsing line at {start_index}: {e} — {line!r}")
        return None, start_index + 1


if __name__ == "__main__":
    import sys

    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "incoming/OC Links Crisis Crossings TV LA Order.pdf"
    print(f"Parsing Media Solutions PDF: {pdf_path}\n")

    try:
        estimates = parse_mediasol_pdf(pdf_path)
        print(f"Found {len(estimates)} estimate(s):\n")
        for est in estimates:
            print(f"Estimate {est.estimate_number}: {est.description}")
            print(f"  Client:  {est.client}")
            print(f"  Flight:  {est.flight_start} – {est.flight_end}")
            print(f"  Market:  {est.market}")
            print(f"  Lines:   {len(est.lines)}")
            total = sum(ln.total_spots for ln in est.lines)
            cost = sum(ln.total_cost for ln in est.lines)
            print(f"  Spots:   {total}  Cost: ${cost:,.2f}")
            for ln in est.lines:
                bonus = " [BONUS]" if ln.is_bonus() else ""
                print(f"    {ln.days} {ln.time} {ln.daypart} ${ln.rate} "
                      f"{ln.total_spots}sp — {ln.program}{bonus}")
            print()
    except Exception as e:
        import traceback
        print(f"Error: {e}")
        traceback.print_exc()
