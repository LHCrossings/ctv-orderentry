# Fix: TOYOTA CRSF-TV Q3 ORDERS (clean-text H/L Buy Detail Report)

## Problem
`TOYOTA CRSF-TV Q3 ORDERS_NEW.pdf` is a 3-estimate H/L **Buy Detail Report**
(Est 13934 Jul, 13935 Aug, 13936 Sep). It fails parsing:

1. **Detection misroute** — `is_bdr_pdf()` only recognizes BDRs by a **Type3 font
   fingerprint**. This newer export has a normal embedded font + extractable text,
   so `is_bdr_pdf` → False and detection falls through to `OrderType.HL`
   (shares the "H/L Agency San Francisco" marker).
2. **`hl_parser` can't read this layout** → returns 0 estimates silently
   (rows have no line number, no daypart code, station is `CRSF-TV` not `CRTV-TV`,
   no rating column).
3. **`parse_bdr_pdf` is OCR-only** — always rasterizes with rotation and OCRs.
   On this clean, un-rotated PDF that yields garbage → 0 orders even when called
   directly.

The whole HL_BDR pipeline (gather → select estimates → one contract per estimate →
process) already exists and already handles multiple estimates. It just never
receives them.

## Verified
- `_parse_bdr_page()` fed the **clean pdfplumber text** parses all 3 pages
  correctly (Est 13934 SFO 3 lines, etc.). The row regex already matches this layout.

## Plan (minimal, 2 files)
- [ ] `hl_bdr_parser.py`: add `_extract_page_text(pdf_path, page_num)` — pdfplumber
      text first; fall back to `_ocr_page` only when text is `(cid:`-garbled or
      < 50 chars. Use it in `parse_bdr_pdf` instead of always calling `_ocr_page`.
- [ ] `hl_bdr_parser.py`: add `is_bdr_text(text)` — content-based BDR detector
      (markers + the day-pattern-first row layout). Self-validating so it never
      steals genuine line-numbered `hl_parser` orders.
- [ ] `order_detection_service.py`: add `_is_bdr(text)` and check it **before**
      `_is_hl_partners` in `detect_from_text`. Covers both detection entry points.

## Verification
- [ ] `parse_bdr_pdf(toyota_pdf)` returns 3 orders with correct spots
      (Jul 31, Aug 31, Sep 24).
- [ ] `detect_from_text(page1)` → `OrderType.HL_BDR`.
- [ ] Old Type3/rotated BDRs still detect (is_bdr_pdf) and still OCR.
- [ ] Genuine `hl_parser` order still detects as `OrderType.HL`.

## Review
Done. Two files changed, no new pipeline code needed (HL_BDR gather/process/
multi-estimate selection already existed).

- `hl_bdr_parser.py`:
  - `_extract_page_text()` — pdfplumber text first, OCR fallback only on
    `(cid:` garble or <50 chars. `parse_bdr_pdf` now uses it.
  - `is_bdr_text()` — content-based detector; row-layout guard makes it
    self-validating (rejects line-numbered `hl_parser` rows).
- `order_detection_service.py`:
  - `_is_bdr()` delegates to `is_bdr_text`; checked before `_is_hl_partners`
    in `detect_from_text`.

Verified:
- [x] `parse_bdr_pdf(toyota_pdf)` → 3 orders: Est 13934 (31 spots, 7/7–8/2),
      13935 (31 spots, 8/4–8/30), 13936 (24 spots, 9/1–9/30). Totals match the
      PDF "Total Spots" footers.
- [x] `detect_from_text(page1)` → `OrderType.HL_BDR`; runtime
      `PDFOrderDetector.detect_order_type` → `OrderType.HL_BDR`.
- [x] Genuine line-numbered HL row → `is_bdr_text` False (stays `OrderType.HL`).
- [x] Old Type3 path unchanged (is_bdr_pdf still first; OCR fallback preserved).
- [x] 55 existing bdr/hl/detect tests pass.
