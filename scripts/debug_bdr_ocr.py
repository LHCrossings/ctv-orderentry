"""
Debug script: print raw OCR text from each page of a BDR PDF.
Usage: uv run python scripts/debug_bdr_ocr.py <path_to_pdf>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from browser_automation.parsers.hl_bdr_parser import _ocr_page

pdf_path = sys.argv[1] if len(sys.argv) > 1 else None
if not pdf_path:
    print("Usage: uv run python scripts/debug_bdr_ocr.py <path_to_pdf>")
    sys.exit(1)

try:
    import fitz
    doc = fitz.open(pdf_path)
    page_count = len(doc)
    doc.close()
except Exception as e:
    print(f"Cannot open PDF: {e}")
    sys.exit(1)

for i in range(page_count):
    print(f"\n{'='*70}")
    print(f"PAGE {i+1} RAW OCR TEXT:")
    print(f"{'='*70}")
    text = _ocr_page(pdf_path, i)
    print(text if text else "(empty)")
