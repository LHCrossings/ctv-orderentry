# Task: Claude-vision extraction for image-only (scanned) WorldLink PDFs

## Problem
The "AATV July 3Q26" orders are image-only/scanned WorldLink "uwOrderPrintVersion"
PDFs (0 extractable text). They (1) load slowly (OCR at detect + parse) and
(2) show "client unknown"; the OCR+regex path misreads text
("Feeding"→"Eeeding") and dropped all lines on one file. OCR only reads page 1.

## Approach (user-approved)
- WorldLink-specific Claude vision extraction (claude-opus-4-8 native PDF vision).
- For image-only WorldLink PDFs, REPLACE OCR+regex with vision entirely.
- Reuse the existing ai_parser.py pattern (messages.parse + Pydantic schema, sidecar cache).

## Plan
- [ ] 1. worldlink_parser.py: WorldLink Pydantic schema + `_vision_extract_worldlink(pdf_path)`
      - base64 whole PDF → client.messages.parse(model=claude-opus-4-8, output_format=schema)
      - vision returns RAW per-line facts (action, dates, raw time_range, 7 day booleans,
        LEN, weeks, spots/wk, total, rate) + header (tracking, agency, advertiser, network)
      - build order_data via the SAME helpers as the text path so the dict is identical in shape
      - cache result in `<file>.wl.json` sidecar (scan/preview/entry share one API call)
- [ ] 2. worldlink_parser.py: restructure `parse_worldlink_pdf` so image-only → vision,
      text PDFs unchanged; common post-processing (description, order_type) runs for both
- [ ] 3. pdf_order_detector.py: add OCR fallback to `extract_client_name` (fixes "client unknown")
- [ ] 4. Verify on all 11 AATV PDFs: advertiser, full line counts (esp. FAM = 0 before),
      ADD/CHANGE detection; diff vision dict vs source PDFs.

## Review — DONE

Files changed:
- `browser_automation/parsers/worldlink_parser.py` — added `_vision_extract_worldlink()`
  (claude-opus-4-8, base64 whole-PDF document block, `messages.parse` + WorldLink Pydantic
  schema), `_vision_line_to_dict()` (reuses `_parse_days_pattern` / `_convert_to_24hr` /
  `_apply_time_bounds` / `_format_duration_for_etere` so the dict is identical to the regex
  path), `<file>.wl.json` sidecar cache. `parse_worldlink_pdf` routes image-only PDFs to vision;
  text PDFs unchanged.
- `src/business_logic/services/pdf_order_detector.py` — `extract_client_name` now OCR-falls-back
  on image-only PDFs (fixes "client unknown" label).
- `src/business_logic/services/order_detection_service.py` — `_extract_worldlink_client` stops at
  the next field label so single-line OCR text doesn't swallow trailing fields.

Verified:
- [x] All 11 AATV PDFs extract: correct advertisers (no "Eeeding America"), full line counts.
      The Asian CH FAM went 0 → 3 lines; every Crossings/Asian campaign pair now matches
      (AHA 2/2, CVH-120 4/4, FAM 3/3, SHC 2/2, STC 2/2).
- [x] FAM line detail correct: dates, 120s duration, $25 paid / $0 bonus, "6:00 AM-6:00 AM"→06:00-23:59,
      M-Su, ASIAN→TAC prefix, order_type=new (all ADD).
- [x] "client unknown" label resolves: "Feeding America", "Shriners Hospital".
- [x] 258 unit tests pass (incl. 59 detection/worldlink); text-PDF path unchanged.

Notes / follow-ups:
- Vision cost ~$0.02-0.04/order (claude-opus-4-8); sidecar means one call shared across
  detect/preview/entry. `.wl.json` sidecars now exist next to the 11 PDFs (intended cache).
- Not yet live-entered into Etere — extraction verified only. Recommend entering one
  (e.g. FAM) and eyeballing the contract before bulk entry.
- Detection still OCRs page 1 (~2.4s) to classify as WORLDLINK before the vision path runs;
  acceptable, but could be revisited if scan latency matters.
