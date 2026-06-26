# Fix HL Traffic Parser — multi-page / multi-flight PDFs

## Problem (confirmed on TOYOTA JUNE 2026 ACM TV #13933 R1)
The PDF has 3 separate flight tables, each with 4 dialects:
- Flight A 6/2-6/8  : TYRN3927/28/29/30 (Cant/Mand/Hindi/Tagalog)
- Flight B 6/9-6/30 : TYRN4140/41/42/43
- Flight C 6/30-7/6 : TYRN4127/28/29/30 (page 2)
Every spot's data (ISCI, dialect, dur, rotation, dates) is on one line.

Current bugs:
1. Per-spot dates never captured (date regex only scanned lines 2+; this format
   puts dates on line 1). Dataclass had no date fields.
2. All 12 spots inherited the header full-flight range 6/2-7/6.
3. Multi-page bleed: last ISCI block per page absorbed next page's header dates.
4. Downstream dialect_to_filmati keyed by dialect only -> 3 flights collapse.

## Fix
- [x] HLTrafficSpot: add date_from_sql/date_to_sql/start_date/end_date
- [x] Parser: extract dates from full block; close blocks on "Link to new spots"/"Page N of"
- [x] Parser: instruction dates = header full flight (display); clean title
- [x] Route orders.py hl branch: one dialect_assignment per unique
      (system_dialect, date_from, date_to); filters carry that flight's own dates+duration
- [x] Template: show flight dates per row

## Verify
- [x] Parser: 12 spots, 3 date windows, correct per-spot dates
- [x] Page renders 3 flights x dialects with correct filters (localhost)
