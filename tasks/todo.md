# Task: Vision-based Admerasia parser

## Why
pdfplumber's `extract_tables` collapses merged calendar cells and loses exact column
alignment → count-preserving day shifts (2935 lines 3 & 6). The total-spots check can't
catch a same-count day shuffle. Vision reads the visual grid the way a human does.

## Design (keep the existing dataclasses + downstream)
- Keep `AdmerasiaOrder` / `AdmerasiaLine` and `analyze_daily_patterns_to_etere_lines`
  (the consolidation works once `_daily_spots` are correct).
- Replace ONLY the grid-reading step with Claude vision (model `claude-opus-4-8`,
  `messages.parse` + Pydantic, PDF document block — same pattern as `ai_parser.py`).
- Keep text-based header extraction (order #, date, markets, language, campaign period)
  — simple regex, reliable; vision only does the hard part (the grid).

## Guardrails (the point — catch errors before entry)
1. **Per-row arithmetic**: `sum(daily_spots) == printed Total Spots` for each row.
2. **Grand total**: sum of row totals == printed Order/Grand Total.
3. **Double-read**: 2 independent vision passes must AGREE on every cell of every row.
   (A count-preserving day shift passes #1/#2 but won't reproduce identically in #2.)
4. **Alignment**: `len(daily_spots) == len(calendar_days)`; first calendar day ==
   campaign start day (else dates would shift).
   Any guardrail failure → raise with specifics; do NOT enter silently.
- Cache extraction in a `<file>.adm.json` sidecar so preview + entry use the same read.

## Scheduling
- Admerasia buys specific dayparts → every line enters as **Priority** (scheduling_type=0),
  never Rotation. Fix in `admerasia_automation` (currently passes spots_per_week=0 which
  trips the monthly→Rotation rule).

## Files
- [ ] NEW `browser_automation/parsers/admerasia_vision.py` — schema, extract+guardrails+cache.
- [ ] `browser_automation/parsers/admerasia_parser.py` — `parse_admerasia_pdf` → vision grid.
- [ ] `browser_automation/admerasia_automation.py` — pass `scheduling_type=0` (Priority).

## Validate (we have ground truth!)
- [ ] Run vision parser on all 5 known-correct PDFs; compare `get_etere_lines()` to the
      entered contracts 2933/2934/2935/2936/2937. Must reproduce all days/dates/counts.
- [ ] Confirm guardrails fire on a deliberately corrupted read.

## Review — DONE & validated against ground truth
Validation surfaced that **pure vision can't reliably count the dense 12-row Chinese
grid** (the two passes disagreed on column alignment — guardrails caught it, but it
would never auto-enter). Meanwhile the Type3 font character-garbles the left metadata
columns ("M cV a lu e…"), so positional can't read daypart/rate either. Final design
plays each method to its strength:

- **POSITIONAL** (`admerasia_positional.py`) — each printed digit bucketed under its
  calendar column by x-coordinate. EXACT, deterministic. Source of truth for daily_spots.
- **VISION** (`admerasia_vision.py`) — Claude (claude-opus-4-8, high-res grid images +
  PDF) reads ROW STRUCTURE + METADATA (spot_length, daypart, net_rate, printed Total
  Spots). Robust to the garbled text. Two passes must agree on metadata.
- **Reconcile** (`parse_admerasia_pdf`) — zip rows top-to-bottom; each row's positional
  spots MUST equal vision's printed Total Spots (arithmetic guardrail) or it refuses;
  vision daily_spots are a soft cross-check (positional wins). Alignment guardrail on
  the first calendar day. Header facts stay text-based.
- **Scheduling** — `admerasia_automation` now passes `scheduling_type=0` (Priority).

**Validated against all 5 entered contracts (2933–2937): EXACT on spots, days, dates,
rates, lengths.** Chinese (12 rows, 4 colors, gap patterns, typo daypart `10:300p`)
reproduces perfectly — which pure vision could not.

Old pdfplumber table-extraction functions (`_parse_line_items*`, `_check_for_ambiguous_times`,
`_find_broadcast_table`) are now dead but left in place (self-contained; not referenced
elsewhere) — separate cleanup if desired.
