# Lessons Learned

## Tests Are Not Authoritative for String Constants

**Session:** Market code mismatch fix (2026-02-19)

**What happened:** `test_chinese_block_abbreviation` expected `"C/M"` but
`Language.MANDARIN.get_block_abbreviation()` returned `"M/C"`. The test was stale — the code
was correct. Correctly flagged this as a pre-existing failure (not introduced by the change),
then fixed the test when user confirmed `"M/C"` is correct.

**Rule:** When a test and implementation disagree on a string constant, do NOT silently fix
either side. Surface the conflict explicitly, state which side you believe is correct and why,
and let the user confirm before touching anything.

## OCR Parser Failures Are Silent by Default — Always Verify Spot/Line Counts

**Session:** RPM Muckleshoot 10868 (2026-02-24)

**What happened:** RPM parser silently dropped 3 of 8 lines (37% of spots, $1,932). No error
was raised — the parser just returned fewer lines. Two distinct OCR artifact patterns:

1. **Space in time range:** `6:00a- 8:00p` → column shift → rate field received `RT` → Decimal
   parse failed → line silently skipped. Fix: preprocess `(\d+:\d+[ap])-\s+(\d+:\d+[ap])` → join.

2. **Doubled letter in day code:** `MTuWTHhF` (OCR doubled the `h` in `Th`) → exact-match regex
   `MTuWThF` didn't match → line skipped. Fix: use `MT[A-Za-z]+F` pattern everywhere the day
   code is matched or parsed.

**Rule:** After any RPM parser change, run `parse_rpm_pdf` on the PDF and verify:
- Line count matches the PDF's line count
- Total spots match the PDF's "Total Spots" footer
- Total cost matches the PDF's "Total Cost" footer
Never trust "parsed successfully" without checking the numbers.

## Image-Based PDFs Have Structural Variants — Min-Column Guards Must Be Dynamic

**Session:** Misfit Supplemental Budget (2026-02-24)

**What happened:** Misfit parser used `len(row) < 10` to skip short rows. Supplemental budget
PDFs cover only 3 weeks → 8 columns → ALL data rows skipped silently. Additionally the header
Market cell was Python None (not a string), producing `order.markets = ['None']` which matched
no parsed lines. 0 lines entered despite a valid contract being created.

**Rule:** Column count guards should reflect the minimum structure (≥5 for Misfit tables), not
the typical case. Always derive `markets` from parsed `line.market` values, not the header field
which can be absent in supplemental/non-standard PDFs.
