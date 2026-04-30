import io
import re

_ISCI_RE = re.compile(r'([A-Z]{4,6}\d{5,6}[A-Z]{2})')
_DUR_RE  = re.compile(r':(\d+)')


def parse_admerasia_io_lines(pdf_bytes: bytes) -> list:
    """
    Parse the IO traffic grid lines in order of appearance.
    Returns [{description, duration_sec}] deduped by (duration_sec, time).
    Used as group headers in the traffic assignment UI.
    """
    try:
        from browser_automation.parsers.admerasia_parser import parse_admerasia_pdf
        order = parse_admerasia_pdf(pdf_bytes)
    except Exception:
        return []

    seen = set()
    result = []
    for line in order.lines:
        key = (line.spot_length, line.time)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "description":  line.time,
            "duration_sec": line.spot_length,
        })
    return result


def parse_admerasia_io_iscis(pdf_bytes: bytes) -> list:
    """
    Parse the ISCI key from an Admerasia IO PDF.
    Returns list of {duration_sec, title, isci_code} dicts, deduplicated by isci_code.
    """
    import pdfplumber

    results = []
    seen = set()

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = pdf.pages[0].extract_text() or ""

    for line in text.splitlines():
        m = _ISCI_RE.search(line)
        if not m:
            continue
        isci_code = m.group(1)
        # McDonald's ISCIs are 4 letters + 6 digits + 2 letters; 'O' at position 4 is a typo for '0'
        if len(isci_code) >= 5 and isci_code[4] == 'O':
            isci_code = isci_code[:4] + '0' + isci_code[5:]
        if isci_code in seen:
            continue
        seen.add(isci_code)

        before = line[:m.start()]
        dur_m = _DUR_RE.search(before)
        duration_sec = int(dur_m.group(1)) if dur_m else 15

        if dur_m:
            title = before[dur_m.end():].strip()
        else:
            title = before.strip()
        title = re.sub(r'^[\s:]+', '', title).strip()

        results.append({
            "duration_sec": duration_sec,
            "title":        title,
            "isci_code":    isci_code,
        })

    return results
