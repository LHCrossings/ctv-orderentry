import io
import re

_ISCI_RE = re.compile(r'([A-Z]{4,6}\d{5}[A-Z]{2})')
_DUR_RE  = re.compile(r':(\d+)')


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
