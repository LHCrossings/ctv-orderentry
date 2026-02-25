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

## Admerasia Day Selection Must Come From Calendar Grid, Not Program Bracket

**Session:** Admerasia McDonald's SEA 11-MD10-2603CT (2026-02-25)

**What happened:** Misread user complaint about day selection. Thought the fix was to use
the program name bracket `(M-F)` as the Etere day string. This was wrong — it caused Etere
to freely distribute 10 spots across 15 available M-F slots instead of placing them on the
exact days specified in the calendar grid.

Admerasia orders are ordered **day by day**. Each cell in the calendar grid specifies the
exact number of spots for that exact date. The Etere day selection must reflect precisely
which days have spots (and per_day_max must match the count in the cell).

**Rule:** Never use the program name bracket to override calendar-derived day strings for
Admerasia. The bracket describes when the program airs; the calendar grid is the purchase
order. Use the grid to build exact per-week Etere lines with precise day patterns and
per_day_max values.

## Admerasia Chinese Format Detection Must Check Col 0, Not Just Col 1

**Session:** Admerasia McDonald's SEA 11-MD10-2603CT (2026-02-25)

**What happened:** Parser detected "Vietnamese format" for a Chinese-language order, producing
0 lines. The Vietnamese/Chinese format detection checked `first_data_row[1]` for `:\d+s?` (spot
length). In this order the spot length (`:15`) was in col 0 and an ad title text was in col 1
(`ACM Yes/ACM Name/`), so the check failed and col offsets were set to Vietnamese mode
(`program_col=2`). Column 2 is `None` for all data rows → every line skipped silently.

Also found: PDF typo `10:300p` (3-digit minute). The normalizer's pre-process regex
`re.sub(r'(\d+):(\d{2})\d+([ap])', ...)` trims extra minute digits before pattern matching.

**Rules:**
1. Chinese format detection must check **both col 0 and col 1** for the `:\d+s?` spot length
   pattern — the column position varies across orders.
2. When 0 lines are found, immediately dump the raw table rows around `row_offset` to identify
   which skip condition is firing (no program, no rate, garbled time, etc.).
3. Add the `10:300p`-style 3-digit minute sanitization as a pre-process step in
   `_normalize_time_to_colon_format` to handle PDF OCR/typo artifacts silently.
