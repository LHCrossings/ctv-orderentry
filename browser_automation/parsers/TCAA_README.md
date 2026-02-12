# TCAA Order Automation System

## Overview

Automated system for processing TCAA (The Asian Channel) annual buy orders for Seattle market. The system parses multi-estimate PDF orders and creates contracts in Etere with proper block selection and weekly distribution handling.

## Key Features

- **Multi-Contract Processing**: Each estimate in the PDF becomes a separate contract
- **Weekly Distribution Analysis**: Automatically splits lines when there are gaps in weekly scheduling
- **Bonus Line Handling**: Prompts user for details on unspecified bonus lines
- **Language-Based Block Filtering**: Automatically selects appropriate program blocks by language
- **No Block Refresh Needed**: Single-market orders don't require the refresh blocks script

## Order Characteristics

### TCAA Orders
- **Customer**: Always 75
- **Agency**: TCAA (The Asian Channel)
- **Client**: Western Washington Toyota Dealers Adv Assoc
- **Market**: Seattle (SEA) only - no multi-market duplication
- **Contract Pattern**: Annual buy with monthly estimates (e.g., 9706, 9709, 9710...)

### Contract Naming
- **Code**: `TCAA Toyota [4-digit estimate]` (e.g., "TCAA Toyota 9706")
- **Description**: `Toyota SEA Est [4-digit estimate]` (e.g., "Toyota SEA Est 9706")
- **Order Notes**: Month description from PDF (e.g., "JAN26 Asian Cable")
- **Master Market**: NYC (like all orders)
- **Line Market**: SEA (Seattle)

### Line Description Format

**Regular Lines**: `[Days] [Time] [Language]`
- Example: "M-Su 6-7a Mandarin"
- Example: "M-Su 8-10a Korean"
- Example: "M-Su 1-4p South Asian"

**Bonus Lines**: `BNS [Days] [Time] [Language]`
- Example: "BNS M-Su 6a-12a Vietnamese"
- Example: "BNS M-F 7p-11p Korean"

## Files

### Core Modules
- **`tcaa_parser.py`**: PDF parsing engine
  - Extracts estimates, lines, and weekly distribution
  - Identifies bonus lines (rate = $0)
  - Analyzes weekly gaps for line splitting
  
- **`tcaa_functions.py`**: Etere automation functions
  - Contract creation
  - Line entry with block selection
  - User prompts for bonus lines
  - Weekly distribution handling

### Utilities
- **`tcaa_demo.py`**: Demonstration script showing parser output

## Usage

### 1. Parse PDF Only (Testing)
```python
from tcaa_parser import parse_tcaa_pdf

estimates = parse_tcaa_pdf("path/to/order.pdf")

for est in estimates:
    print(f"{est.estimate_number}: {est.description}")
    print(f"  Lines: {len(est.lines)}")
```

### 2. Full Automation
```python
from selenium import webdriver
from tcaa_functions import process_tcaa_order

# Setup browser (already logged into Etere)
driver = webdriver.Chrome()
# ... navigate to Etere and login ...

# Process entire order
success = process_tcaa_order(driver, "path/to/order.pdf")
```

### 3. Manual Contract Creation
```python
from tcaa_parser import parse_tcaa_pdf
from tcaa_functions import prompt_for_bonus_lines, create_tcaa_contract

# Parse
estimates = parse_tcaa_pdf("order.pdf")

# Process first estimate
est = estimates[0]
bonus_inputs = prompt_for_bonus_lines(est)
create_tcaa_contract(driver, est, bonus_inputs)
```

## Workflow

### Phase 1: Bonus Line Prompts
System prompts once for ALL bonus lines across ALL estimates:
```
=== Bonus Lines for Estimate 9706 ===
There are 5 bonus lines on this order that need specification.

Bonus line 1 of 5:
Current: M-Su 6:00a-12:00a (no language specified)

  Days of week (e.g., M-Su, M-F): M-Su
  Time period (e.g., 6a-12a, 7p-11p): 6a-12a
  Language: Vietnamese
```

### Phase 2: Contract Creation
For each estimate:
1. Create contract with header info
2. Add all lines (split by weekly distribution if needed)
3. For each line:
   - Add to contract
   - Select days, dates, times
   - Add all program blocks
   - Filter blocks by language prefix
4. Save and close contract
5. Move to next estimate

## Weekly Distribution Logic

The system honors the specific weekly distribution from the PDF. When there are gaps:

**Example**: January order shows [14, 0, 14, 14] spots
- Week 1: 14 spots
- Week 2: 0 spots (SKIP)
- Week 3: 14 spots
- Week 4: 14 spots

**System creates 2 Etere lines**:
1. Line 1: 12/29/2025 - 01/04/2026 (14 spots, 14/week)
2. Line 2: 01/12/2026 - 01/25/2026 (28 spots, 14/week)

## Language Block Filtering

### Block Prefix Mapping
```
Mandarin     → M
Korean       → K
South Asian  → SA (prompt: Hindi, Punjabi, or Both?)
Hindi        → SA
Punjabi      → P
Filipino     → T
Vietnamese   → V
Cantonese    → C
Chinese      → M + C (both dialects)
Hmong        → Hm
```

### South Asian Handling
When a line specifies "South Asian", the system prompts:
```
Line 'M-Su 1-4p South Asian' - Do you want Hindi, Punjabi, or Both?
  Enter choice: Both
```

Then selects blocks starting with both "SA - " and "P - "

### Block Selection Process
1. Click "Add All Blocks" for the daypart/time
2. Get list of all available blocks
3. Filter to only those matching language prefix(es)
4. Select matching blocks, deselect others
5. Confirm selection

## Special Cases

### Bonus Lines
- Always have $0.00 rate
- Require user input for: days, time, language
- Description starts with "BNS" prefix
- Use "Bonus Spot" as spot code

### Time Formatting
- PDF format: "6:00a- 7:00a" or "7:00p-12:00a"
- Description format: "6-7a" or "7p-12a"
- Etere format: 24-hour (06:00, 19:00)
- Midnight cap: Always use 23:59 for end times at midnight

### Max Per Day Calculation
```
active_days = count_of_active_days(day_pattern)
max_per_day = ceil(spots_per_week / active_days)
```

## Example Output

From the January 2026 estimate (9706):
```
Contract: TCAA Toyota 9706
Description: Toyota SEA Est 9706
Order Notes: JAN26 Asian Cable
Flight: 12/29/2025 - 1/25/2026

Lines created: 22 Etere lines
  - 6 paid lines (each split into 2 due to week 2 gap)
  - 5 bonus lines (each split into 2 due to week 2 gap)

Total: 11 PDF lines → 22 Etere lines
```

## Testing

Run the demo to see parser output:
```bash
python3 tcaa_demo.py
```

This shows:
- All estimates found
- Contract details
- Line descriptions
- Weekly distribution splits
- Total Etere lines needed

## Notes

### Why No Block Refresh?
- Single market only (SEA)
- No line duplication
- Blocks selected correctly during initial creation
- Unlike WorldLink multi-market orders

### Differences from WorldLink
1. **No CMP lines**: Only SEA market
2. **No block refresh**: Single market = no duplication
3. **Bonus prompts**: TCAA has unspecified bonus lines
4. **Weekly gaps**: Must split lines for distribution gaps
5. **Save between contracts**: Each estimate is independent

### Common Patterns
- All January orders typically have week 1, skip week 2, then weeks 3-4
- Most orders run 3-4 consecutive weeks
- 6 paid lines + 5 bonus lines = 11 total per estimate
- Bonus lines are always 6a-12a timeframe

## Future Enhancements

Potential improvements:
- [ ] Remember South Asian choice per order (not per line)
- [ ] Handle complex day patterns (M-Tu,Th-Su)
- [ ] Validate block selection success
- [ ] Add logging/error recovery
- [ ] Support other TCAA markets if added

## Related Systems

- **WorldLink Parser**: `worldlink_parser.py` - Multi-market with block refresh
- **Refresh Blocks**: `refresh_blocks_functions.py` - Not needed for TCAA
- **Browser Session**: `browser_session.py` - Shared session management
