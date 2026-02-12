# TCAA Browser Automation Module

Refactored TCAA (The Asian Channel) order automation following clean architecture principles.

## Overview

This module automates the creation of TCAA contracts in the Etere broadcast management system. It handles Toyota advertising for the Seattle market (SEA) across multiple language programming blocks.

## Architecture

The module follows clean architecture with clear separation of concerns:

```
┌─────────────────────────────────────────┐
│  High-Level Orchestration               │  process_tcaa_order()
│  create_tcaa_contract()                  │  
├─────────────────────────────────────────┤
│  Presentation Layer                      │  User Input Gathering
│  prompt_for_bonus_lines()                │  
├─────────────────────────────────────────┤
│  Browser Automation Layer                │  Etere UI Interactions
│  create_contract_header()                │  
│  add_contract_line()                     │  
│  add_and_filter_blocks()                 │  
├─────────────────────────────────────────┤
│  Business Logic Layer                    │  Pure Functions
│  parse_start_time()                      │  
│  parse_end_time()                        │  
│  apply_sunday_6_7a_rule()                │  
│  get_language_block_prefixes()           │  
├─────────────────────────────────────────┤
│  Domain Layer                            │  Entities (from tcaa_parser.py)
│  TCAAEstimate                            │  
│  TCAALine                                │  
│  BonusLineInput                          │  
└─────────────────────────────────────────┘
```

## Key Features

### 1. Upfront Input Gathering
All bonus line specifications are collected before browser automation begins, enabling unattended processing:
- Days of week
- Time periods
- Language selection
- South Asian disambiguation (Hindi/Punjabi/Both)

### 2. Pure Business Logic
All business rules implemented as pure, testable functions:
- Time parsing (12-hour to 24-hour conversion)
- Day pattern handling
- Sunday 6-7a paid programming rule
- Language block prefix mapping

### 3. Automatic Line Splitting
Lines are automatically split when weekly spot counts differ, following the universal rule: never combine weeks with different spot counts.

### 4. Language Block Filtering
Automatically filters programming blocks by language prefix:
- Mandarin: M
- Cantonese: C
- Chinese: M, C (both)
- Korean: K
- Filipino: T
- Vietnamese: V
- South Asian/Hindi: SA
- Punjabi: P
- Japanese: J
- Hmong: Hm

## Business Rules

### Universal Rules (Applied to All Orders)

1. **Sunday 6-7a Paid Programming**: Sunday 6:00am-7:00am has paid programming (no commercial spots). If days include Sunday and time is exactly 6:00a-7:00a, Sunday is removed from the pattern.

2. **Midnight Conversion**: Midnight (12:00a) always converts to 23:59 in Etere.

3. **Line Splitting**: Lines must be split when weekly spot counts differ. Never combine weeks with different spot counts into a single Etere line.

4. **End Date Capping**: Calculated end dates are always capped at the contract/flight end date.

### TCAA-Specific Rules

1. **Customer ID**: Always 75 (Toyota)

2. **Market**: Always SEA (Seattle) for The Asian Channel

3. **Contract Naming**:
   - Code: `TCAA Toyota {estimate_number}`
   - Description: `Toyota SEA Est {estimate_number}`

4. **Billing Settings**:
   - Agency Commission: 0%
   - Cash Discount: 2%

5. **Separation Intervals**: All zeros (0, 0, 0)

6. **Spot Codes**:
   - Paid lines: "Paid Commercial"
   - Bonus lines: "Bonus Spot"

7. **Block Handling**: Add all blocks first, then filter by language prefix(es)

## Usage

### Basic Usage

```python
from etere_session import EtereSession
from tcaa_automation import process_tcaa_order

# Contract creation URL for SEA market
contract_url = "http://100.102.206.113/vendite/ordini/ordine"

with EtereSession() as session:
    # Set market to SEA (Seattle)
    session.set_market("SEA")
    
    # Process the order (with upfront bonus line input)
    success = process_tcaa_order(
        driver=session.driver,
        pdf_path="/path/to/tcaa_order.pdf",
        contract_url=contract_url
    )
```

### Workflow

1. **PDF Parsing**: Parse TCAA PDF to extract estimates and lines
2. **Input Gathering**: Prompt user for bonus line specifications upfront
3. **Contract Creation**: For each estimate:
   - Create contract header
   - Process each line (paid and bonus)
   - Apply Sunday 6-7a rule
   - Split lines when spot counts differ
   - Filter blocks by language
   - Save contract

### Bonus Line Input Prompts

For each bonus line, the user is prompted for:

```
Bonus line 1 of 3:
Current: M-Su 6:00a-7:00a (no language specified)

  Days of week (e.g., M-Su, M-F): M-F
  Time period (e.g., 6a-12a, 7p-11p): 8a-10a
  Language options: Mandarin, Korean, South Asian, Filipino, Vietnamese, Cantonese, Chinese, Hmong
  Language: Korean
```

If South Asian is selected:
```
  Do you want Hindi, Punjabi, or Both? Both
```

## Testing

The module includes comprehensive test coverage (42 tests):

```bash
pytest test_tcaa_automation.py -v
```

### Test Coverage

- **Time Parsing**: 8 tests covering 12-hour to 24-hour conversion
- **Day Patterns**: 10 tests for day counting, Sunday rule, checkbox conversion
- **Language Prefixes**: 13 tests for all language mappings
- **Value Objects**: 3 tests for BonusLineInput immutability
- **Integration Scenarios**: 4 tests for complete workflows
- **Edge Cases**: 4 tests for boundary conditions

All business logic functions are pure and fully tested without requiring browser automation.

## Dependencies

- **selenium**: Browser automation
- **pdfplumber**: PDF parsing (via tcaa_parser.py)
- **pytest**: Testing framework

## File Structure

```
browser_automation/
├── tcaa_automation.py          # Main automation module
├── test_tcaa_automation.py     # Comprehensive test suite
├── etere_session.py            # Browser session manager
├── customer_matcher_browser.py # Customer matching integration
└── parsers/
    └── tcaa_parser.py          # PDF parser (domain entities)
```

## Integration with Existing System

### With EtereSession

```python
from etere_session import EtereSession
from tcaa_automation import create_tcaa_contract, prompt_for_bonus_lines
from parsers.tcaa_parser import parse_tcaa_pdf

# Parse PDF
estimates = parse_tcaa_pdf("order.pdf")

# Gather bonus inputs upfront
all_bonus_inputs = {}
for estimate in estimates:
    bonus_inputs = prompt_for_bonus_lines(estimate)
    all_bonus_inputs[estimate.estimate_number] = bonus_inputs

# Process with browser
with EtereSession() as session:
    session.set_market("SEA")
    
    for estimate in estimates:
        success = create_tcaa_contract(
            driver=session.driver,
            estimate=estimate,
            bonus_inputs=all_bonus_inputs[estimate.estimate_number],
            contract_url="http://100.102.206.113/vendite/ordini/ordine"
        )
```

### Pure Business Logic (No Browser)

All business logic functions can be used independently:

```python
from tcaa_automation import (
    parse_start_time,
    parse_end_time,
    apply_sunday_6_7a_rule,
    get_language_block_prefixes
)

# Parse times
start_time = parse_start_time("6:00a-7:00a")  # "06:00"
end_time = parse_end_time("7:00p-12:00a")     # "23:59"

# Apply Sunday rule
adjusted_days, count = apply_sunday_6_7a_rule("M-Su", "6:00a-7:00a")
# ("M-Sa", 6)

# Get block prefixes
prefixes = get_language_block_prefixes("Korean")  # ["K"]
prefixes = get_language_block_prefixes("South Asian", "Both")  # ["SA", "P"]
```

## Key Design Decisions

### 1. Upfront Input Gathering

**Decision**: Collect all bonus line inputs before starting browser automation.

**Rationale**: 
- Enables unattended processing once inputs are collected
- Prevents mid-automation interruptions
- Allows user to review all requirements upfront
- Improves error recovery (can retry without re-prompting)

### 2. Pure Business Logic

**Decision**: Implement all business rules as pure functions separate from browser automation.

**Rationale**:
- Enables comprehensive testing without browser
- Makes logic reusable across different automation contexts
- Improves maintainability and debugging
- Clear separation of concerns

### 3. Immutable Value Objects

**Decision**: Use frozen dataclasses for domain entities.

**Rationale**:
- Prevents accidental mutations
- Makes data flow explicit
- Enables safe concurrent processing (future)
- Aligns with functional programming principles

### 4. Block Filtering Strategy

**Decision**: Add all blocks first, then filter by language prefix.

**Rationale**:
- Simpler than selective block addition
- More reliable with Etere's UI
- Matches TCAA-specific workflow
- Easier to verify visually

## Comparison with Legacy Code

| Aspect | Legacy (tcaa_functions.py) | Refactored (tcaa_automation.py) |
|--------|---------------------------|--------------------------------|
| **Lines of code** | 632 | 850 (with tests: 1,270) |
| **Functions** | Mixed concerns | Clear separation |
| **Testing** | None | 42 comprehensive tests |
| **Pure functions** | Few | All business logic |
| **Type hints** | Partial | Complete |
| **Documentation** | Minimal | Comprehensive |
| **Reusability** | Low | High |

## Future Enhancements

1. **Validation Layer**: Add pre-automation validation of all inputs
2. **Retry Logic**: Implement automatic retry for transient failures
3. **Error Recovery**: Save progress for partial completion scenarios
4. **Logging**: Add structured logging for debugging
5. **Configuration**: Extract magic strings to configuration

## Related Modules

- **etere_session.py**: Browser session lifecycle management
- **customer_matcher_browser.py**: Customer ID detection and matching
- **tcaa_parser.py**: PDF parsing and domain entities

## Support

For issues or questions about TCAA automation:
1. Check test suite for expected behavior
2. Review business rules documentation
3. Verify Etere field IDs haven't changed
4. Test pure functions independently before full automation
