# SAGENT Integration - FINAL COMPLETE VERSION

## ✅ All Requirements Implemented

### 1. Line Descriptions with Line Numbers ✅
```
(Line 1) CVC Chinese
(Line 2) BNS CVC Chinese
(Line 3) LAX Chinese
(Line 4) BNS LAX Chinese
```

**Format**: `(Line #) [BNS] <Market> <Language>`
- Paid lines: `(Line #) <Market> <Language>`
- Bonus lines: `(Line #) BNS <Market> <Language>`

### 2. Spot Length Parsing ✅
- Extracts from "Len" column: `:15` → 15 seconds
- Method: `line.get_duration_seconds()`
- Supports `:15`, `:30`, `:60`, etc.

### 3. Separation Intervals ✅
- Set to `(15, 0, 0)`
- Customer: 15 minutes
- Event: 0 minutes
- Order: 0 minutes

### 4. Rate Grossing ✅
- Formula: `gross_rate = net_rate / 0.85`
- $21.25 net → $25.00 gross
- $49.56 net → $58.31 gross
- **Uses gross rate in Etere (not net!)**

### 5. Time Format Conversion ✅
- PDF: `"6:00A to 11:59P"`
- Parser: `"6a-11:59p"`
- Etere: `"0600"` and `"2359"` (via `parse_time_range()`)
- **Fully automated - no manual entry!**

## Test Results from Cal Fire PDF

```
Advertiser: CAL FIRE
Campaign: 2026 CAL FIRE Fourth of July
Order #: Cros202601282125211
Estimate: 000202 → 202
Flight: 06/08/2026 - 07/04/2026

Line 1: (Line 1) CVC Chinese
  - Market: CVC
  - Rate: $21.25 net → $25.00 gross
  - Spots: 144 (72 + 72 + 0)
  - Duration: 15 seconds
  - Time: 6a-11:59p (M-Su)
  - Spot Code: 2 (Paid)

Line 2: (Line 2) BNS CVC Chinese
  - Market: CVC
  - Rate: $0.00 (Bonus)
  - Spots: 20 (0 + 0 + 20)
  - Duration: 15 seconds
  - Time: 6a-11:59p (M-Su)
  - Spot Code: 10 (Bonus)

Line 3: (Line 3) LAX Chinese
  - Market: LAX
  - Rate: $49.56 net → $58.31 gross
  - Spots: 144 (72 + 72 + 0)
  - Duration: 15 seconds
  - Time: 6a-11:59p (M-Su)
  - Spot Code: 2 (Paid)

Line 4: (Line 4) BNS LAX Chinese
  - Market: LAX
  - Rate: $0.00 (Bonus)
  - Spots: 20 (0 + 0 + 20)
  - Duration: 15 seconds
  - Time: 6a-11:59p (M-Su)
  - Spot Code: 10 (Bonus)

Total Markets: 2 (CVC, LAX)
Total Lines: 4
Total Spots: 328
```

## Contract Details

```
Customer: 175 (CAL FIRE) - hardcoded
Contract Code: Sagent CAL FIRE 202
Description: CAL FIRE 2026 CAL FIRE Fourth of July Est 202
Customer Order Ref: Cros202601282125211
Notes: Est 202
Flight: 06/08/2026 - 07/04/2026
Master Market: NYC
Billing: "Customer share indicating agency %" / "Agency"
Separation: (15, 0, 0)
```

## Files Delivered

1. **sagent_parser.py** (729 lines)
   - Parses SAGENT PDFs from GaleForceMedia
   - Multi-line text format handling
   - Rate grossing calculation
   - Line number tracking
   - Language extraction
   - Weekly spot parsing

2. **sagent_automation.py** (410 lines)
   - Multi-market order processing
   - Upfront input gathering
   - Uses etere_client.py exclusively
   - Shared session support
   - Follows TCAA/Misfit patterns

3. **LINE_NUMBER_GUIDELINE.md**
   - Universal rule for line numbers
   - Implementation checklist
   - Agency-by-agency status

## Universal Rule Established

**If an IO contains line numbers, include them in Etere descriptions.**

Format: `(Line #) <description>`

This ensures:
- Easy reference to original IO
- Clear agency communication
- Invoice reconciliation
- Error tracking

## Installation

```bash
cd C:\Users\scrib\windev\OrderEntry

# Copy files
copy sagent_parser.py parsers\
copy sagent_automation.py browser_automation\

# Test parser
python parsers\sagent_parser.py "path\to\sagent_pdf.pdf"

# Test automation
python browser_automation\sagent_automation.py
```

## Integration Checklist

- [x] Parser extracts all fields correctly
- [x] Line numbers captured and used in descriptions
- [x] Rates grossed up (net / 0.85)
- [x] Spot duration extracted from "Len" column
- [x] Time formats converted properly
- [x] Days parsed correctly
- [x] Markets detected (multi-market support)
- [x] Bonus lines identified ($0.00 rate)
- [x] Uses universal ROS/language utilities
- [x] Uses etere_client exclusively
- [x] Separation set to (15, 0, 0)
- [x] Customer hardcoded to 175 (CAL FIRE)
- [x] Contract naming follows format
- [x] Order number in Customer Order Ref
- [x] Estimate stripped of leading zeros
- [x] Shared session support
- [x] Follows TCAA/Misfit patterns

## Architecture Compliance

✅ **Clean Architecture** - Proper layer separation
✅ **Functional First** - Immutable dataclasses, pure functions
✅ **DRY Principle** - Reuses universal utilities
✅ **Type Safety** - Comprehensive type hints
✅ **Repository Pattern** - EtereClient for all Etere access
✅ **Single Responsibility** - Each layer has one job
✅ **Copy Working Code** - Uses proven TCAA/Misfit patterns

## Ready for Production

All requirements met:
- ✅ Line descriptions: `(Line #) [BNS] <Market> <Language>`
- ✅ Spot length parsing from "Len" column
- ✅ Separation intervals: (15, 0, 0)
- ✅ Rate grossing: net / 0.85
- ✅ Time format conversion automated
- ✅ Multi-market support (NYC master)
- ✅ Universal rule: Line numbers in descriptions

**Status**: Complete and tested
**Date**: February 11, 2026
**Tested with**: Cal Fire Fourth of July PDF
