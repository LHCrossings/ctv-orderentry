"""
Claude-based IO parser fallback.

Called when no registered parser recognises the uploaded IO PDF.
Extracts PDF text, sends it to Claude, and returns an io_detail dict
compatible with transformer._sc_lines_from_io.
"""

import json
import os
import re
from pathlib import Path


def _read_api_key() -> str:
    """Read ANTHROPIC_API_KEY from credentials.env or environment."""
    env_var = os.environ.get("ANTHROPIC_API_KEY", "")
    if env_var and env_var != "your_api_key_here":
        return env_var
    creds = Path(__file__).parent.parent.parent / "credentials.env"
    if creds.exists():
        for line in creds.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val and val != "your_api_key_here":
                    return val
    return ""


_PROMPT = """You are parsing a TV advertising insertion order (IO) or media proposal.
Extract every line item from the text below and return a JSON array.

Each element must have these fields:
- "description": program/daypart name (e.g. "Cantonese News & Talk")
- "days": day pattern string (e.g. "M-F", "Sat-Sun", "M-Su", or "" if unknown)
- "time": time range string (e.g. "7p-8p", "11a-12p", or "")
- "rate": cost per :30 spot as a number (0 for bonus/free lines)
- "total_spots": total spots as an integer
- "weekly_spots": list of per-week spot counts if weekly columns exist (e.g. [3,3,3,3,3,2]), otherwise []
- "is_bonus": true if this is a bonus, ROS, or free line with no charge to the client
- "duration": spot length in seconds (default 30)
- "start_date": flight start as "M/D/YYYY" if visible in the document, else ""
- "end_date": flight end as "M/D/YYYY" if visible in the document, else ""

Rules:
- Bonus/ROS lines have rate=0 and is_bonus=true even if a rate appears in an adjacent cell.
- A line marked "Bonus" is always is_bonus=true regardless of rate.
- weekly_spots should match the number of date columns in the schedule grid.
- The flight start/end dates appear on every line (copy from the document header if needed).
- Return ONLY the JSON array with no explanation, markdown fences, or extra text.

IO text:
"""


def parse_io_with_claude(pdf_bytes: bytes) -> dict | None:
    """
    Parse an IO PDF using Claude when no registered parser recognises the format.

    Returns an io_detail dict (keys: lines, flight_start, flight_end, warnings)
    compatible with transformer._sc_lines_from_io, or None on any failure.
    """
    api_key = _read_api_key()
    if not api_key:
        print("[Claude IO] No ANTHROPIC_API_KEY found — skipping Claude fallback")
        return None

    try:
        import anthropic
        import fitz
    except ImportError as e:
        print(f"[Claude IO] Missing dependency: {e}")
        return None

    # Extract text from PDF
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
    except Exception as e:
        print(f"[Claude IO] PDF text extraction failed: {e}")
        return None

    if not text.strip():
        print("[Claude IO] PDF yielded no text")
        return None

    # Pull flight dates from text before sending to Claude
    flight_start = ""
    flight_end = ""
    date_m = re.search(r'(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+through\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)', text, re.IGNORECASE)
    if date_m:
        def _normalise_date(s: str) -> str:
            parts = s.split("/")
            if len(parts) == 2:
                year = "2026"
                parts.append(year)
            if len(parts[2]) == 2:
                parts[2] = "20" + parts[2]
            return "/".join(parts)
        flight_start = _normalise_date(date_m.group(1))
        flight_end   = _normalise_date(date_m.group(2))

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": _PROMPT + text}],
        )
        response_text = message.content[0].text.strip()
    except Exception as e:
        print(f"[Claude IO] API call failed: {e}")
        return None

    # Extract JSON array from response (handles any stray markdown)
    json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
    if not json_match:
        print(f"[Claude IO] No JSON array in response: {response_text[:200]}")
        return None

    try:
        lines = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        print(f"[Claude IO] JSON parse error: {e}")
        return None

    if not isinstance(lines, list) or not lines:
        print("[Claude IO] Empty or non-list response")
        return None

    # Propagate flight dates onto each line if they don't have their own
    for ln in lines:
        ln.setdefault("start_date", flight_start)
        ln.setdefault("end_date",   flight_end)

    print(f"[Claude IO] Parsed {len(lines)} lines (flight {flight_start}–{flight_end})")
    return {
        "lines":        lines,
        "flight_start": flight_start,
        "flight_end":   flight_end,
        "warnings":     ["IO lines parsed via Claude AI — verify spot counts and rates"],
    }
