"""
Parse RPM Advertising "TRAFFIC INSTRUCTIONS" PDFs.

One PDF per market. Returns estimate number, advertiser, market label,
and all ISCI codes with duration and rotation percentage.
"""
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional

import pdfplumber


@dataclass
class RpmTrafficSpot:
    isci: str
    title: str
    duration_sec: int
    rotation_pct: float


@dataclass
class RpmTrafficInstruction:
    estimate: str
    advertiser: str
    market: str
    duration_sec: int
    date_to_display: str
    date_to_sql: Optional[str]
    spots: List[RpmTrafficSpot] = field(default_factory=list)


def _parse_end_date(run_str: str) -> Optional[str]:
    """Extract end date from 'ASAP – 6/14' → '2026-06-14' (nearest future date)."""
    import datetime
    m = re.search(r'(\d{1,2})/(\d{1,2})', run_str)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    today = datetime.date.today()
    candidate = today.replace(month=month, day=day)
    if candidate < today:
        candidate = candidate.replace(year=today.year + 1)
    return candidate.strftime('%Y-%m-%d')


def parse_rpm_traffic_pdf(pdf_bytes: bytes) -> RpmTrafficInstruction:
    """Parse a single RPM Advertising traffic instructions PDF."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    # Estimate number: "Estimate #: 10907 – Crossings TV - Sacramento"
    est_m = re.search(r'Estimate\s+#\s*:\s*(\d+)', text, re.IGNORECASE)
    estimate = est_m.group(1) if est_m else ""

    # Advertiser
    adv_m = re.search(r'ADVERTISER\s*:\s*(.+)', text)
    advertiser = adv_m.group(1).strip() if adv_m else ""

    # Market label: text after "Crossings TV" on the estimate line
    market_m = re.search(
        r'Estimate\s+#\s*:.+?Crossings\s+TV\s*[-–]\s*(.+)',
        text, re.IGNORECASE,
    )
    market = market_m.group(1).strip() if market_m else ""

    # Flight end date: "Spots to Run: ASAP – 6/14"
    run_m = re.search(r'Spots\s+to\s+Run\s*:\s*(.+)', text, re.IGNORECASE)
    run_str = run_m.group(1).strip() if run_m else ""
    date_to_sql = _parse_end_date(run_str)
    date_m = re.search(r'(\d{1,2}/\d{1,2})', run_str)
    date_to_display = date_m.group(1) if date_m else run_str

    result = RpmTrafficInstruction(
        estimate=estimate,
        advertiser=advertiser,
        market=market,
        duration_sec=30,
        date_to_display=date_to_display,
        date_to_sql=date_to_sql,
    )

    # Spots table: rows between "ISCI# TITLE ..." header and the dashes separator
    in_table = False
    for line in text.split("\n"):
        stripped = line.strip()
        if re.match(r'ISCI#\s+TITLE', stripped, re.IGNORECASE):
            in_table = True
            continue
        if re.match(r'-{10,}', stripped):
            in_table = False
            continue
        if not in_table or not stripped:
            continue

        # ISCI TITLE_WITH_POSSIBLE_COLONS :DURATION ROTATION%
        # Use greedy title match so embedded ":30" in the title is captured correctly.
        m = re.match(r'^(RPM-[\w-]+)\s+(.+)\s+:(\d+)\s+([\d.]+)%\s*$', stripped)
        if m:
            dur = int(m.group(3))
            result.spots.append(RpmTrafficSpot(
                isci=m.group(1),
                title=m.group(2).strip(),
                duration_sec=dur,
                rotation_pct=float(m.group(4)),
            ))
            result.duration_sec = dur

    return result
