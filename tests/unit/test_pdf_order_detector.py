"""
Unit tests for PDFOrderDetector.extract_client_name — the file-I/O adapter.

Regression guard: a 2026-06-25 edit accidentally nested the final
`return self._service.extract_client_name(...)` inside the image-only
(`len(text) < 50`) branch, so every TEXT-based PDF fell through and returned
None → the UI showed "Unknown" for all text orders (e.g. WorldLink Redfin).
These tests pin the control flow for both the text path and the image-only path
without touching real files, OCR, or the Claude API (all mocked).
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))            # browser_automation (vision parser)
sys.path.insert(0, str(_ROOT / "src"))    # business_logic / domain

import business_logic.services.pdf_order_detector as detector_mod  # noqa: E402
from business_logic.services.pdf_order_detector import PDFOrderDetector  # noqa: E402
from domain.enums import OrderType  # noqa: E402


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePDF:
    def __init__(self, page_texts):
        self.pages = [_FakePage(t) for t in page_texts]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_pdf(monkeypatch, *page_texts):
    """Make pdfplumber.open() (as used by the detector) yield controlled pages."""
    monkeypatch.setattr(detector_mod.pdfplumber, "open", lambda _p: _FakePDF(page_texts))


_WL_TEXT = (
    "Agency:Tatari Inc Station/Region:CROSSINGS TV\n"
    "Advertiser:Redfin Product Desc:Real Estate/Mortgage Estimate No.:N/A\n"
    "Buyer:Someone\n"
)


def test_text_pdf_returns_client(monkeypatch):
    """REGRESSION: a text PDF (>=50 chars) must return the extracted client,
    not None. This is the exact path the bad indentation broke."""
    _patch_pdf(monkeypatch, _WL_TEXT)
    det = PDFOrderDetector()
    assert det.extract_client_name("x.pdf", OrderType.WORLDLINK) == "Redfin"


def test_text_pdf_extract_customer_name_alias(monkeypatch):
    """The public alias used by the scanner must also resolve text PDFs."""
    _patch_pdf(monkeypatch, _WL_TEXT)
    det = PDFOrderDetector()
    assert det.extract_customer_name("x.pdf", OrderType.WORLDLINK) == "Redfin"


def test_image_only_worldlink_uses_vision(monkeypatch):
    """Image-only WorldLink PDF: label comes from Claude vision (clean), not the
    OCR misread."""
    _patch_pdf(monkeypatch, "")  # no extractable text → image-only branch
    import browser_automation.parsers.worldlink_parser as wl
    monkeypatch.setattr(wl, "_vision_extract_worldlink",
                        lambda _p, **_k: {"advertiser": "Feeding America"})
    det = PDFOrderDetector()
    assert det.extract_client_name("scan.pdf", OrderType.WORLDLINK) == "Feeding America"


def test_image_only_falls_back_to_ocr_when_vision_unavailable(monkeypatch):
    """If vision yields nothing, fall back to OCR for a rough label."""
    _patch_pdf(monkeypatch, "")
    import browser_automation.parsers.worldlink_parser as wl
    monkeypatch.setattr(wl, "_vision_extract_worldlink", lambda _p, **_k: None)
    det = PDFOrderDetector()
    monkeypatch.setattr(det, "_ocr_first_page", lambda _p, dpi=200: _WL_TEXT)
    assert det.extract_client_name("scan.pdf", OrderType.WORLDLINK) == "Redfin"


def test_image_only_no_text_returns_none(monkeypatch):
    """Image-only PDF with no vision and no OCR text → None (caller shows
    'Unknown'), and never raises."""
    _patch_pdf(monkeypatch, "")
    import browser_automation.parsers.worldlink_parser as wl
    monkeypatch.setattr(wl, "_vision_extract_worldlink", lambda _p, **_k: None)
    det = PDFOrderDetector()
    monkeypatch.setattr(det, "_ocr_first_page", lambda _p, dpi=200: "")
    assert det.extract_client_name("scan.pdf", OrderType.WORLDLINK) is None
