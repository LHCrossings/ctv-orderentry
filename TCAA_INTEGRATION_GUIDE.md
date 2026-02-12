# TCAA Integration Guide

How to integrate TCAA browser automation into your main order processing system.

## Quick Start

Replace the existing TCAA handling in `process_orders.py` with the new browser automation.

## Integration Steps

### 1. File Organization

Place these files in your project structure:

```
your_project/
├── process_orders.py           # Your main script
├── browser_automation/
│   ├── tcaa_automation.py      # TCAA automation module
│   ├── etere_session.py        # Browser session manager
│   ├── customer_matcher_browser.py
│   └── parsers/
│       └── tcaa_parser.py      # PDF parser (existing)
├── tcaa_integration.py         # Integration module (NEW)
└── test_tcaa_automation.py     # Test suite
```

### 2. Code Changes in process_orders.py

#### Option A: Simple Replacement (Recommended)

Find the section that currently handles TCAA orders and replace with:

```python
# At the top of process_orders.py, add import
from tcaa_integration import process_tcaa_order_with_browser

# In your order processing function, replace TCAA handling:
if order_type == "TCAA":
    result = process_tcaa_order_with_browser(
        pdf_path=pdf_path,
        order_code=order_code,
        description=description,
        customer_id=customer_id
    )
    
    if result['success']:
        print(f"✓ Successfully created {result['contracts_created']} contract(s)")
        return True
    else:
        print(f"✗ Processing failed: {result.get('error', 'Unknown error')}")
        return False
```

#### Option B: Detailed Integration

For more control, use the detailed approach:

```python
from tcaa_integration import (
    process_tcaa_order_with_browser,
    validate_tcaa_order,
    get_tcaa_order_info
)

def process_tcaa_order_detailed(pdf_path: str, order_code: str, description: str):
    """Process TCAA order with validation."""
    
    # Step 1: Validate
    print("Validating TCAA order...")
    validation = validate_tcaa_order(pdf_path)
    
    if not validation['valid']:
        print("✗ Validation failed:")
        for issue in validation['issues']:
            print(f"  - {issue}")
        return False
    
    print(f"✓ Validation passed ({validation['estimates']} estimates)")
    
    # Step 2: Get order info
    order_info = get_tcaa_order_info(pdf_path)
    print(f"  Customer: {order_info.get('customer_id', 75)}")
    print(f"  Estimate: {order_info.get('estimate_number', 'Unknown')}")
    print(f"  Lines: {order_info.get('total_lines', 0)}")
    
    # Step 3: Process with browser automation
    result = process_tcaa_order_with_browser(
        pdf_path=pdf_path,
        order_code=order_code,
        description=description,
        customer_id=order_info.get('customer_id', 75)
    )
    
    return result['success']
```

### 3. Update Your Order Detection Logic

If you're currently detecting TCAA orders, ensure your detection correctly identifies them:

```python
def detect_order_type(pdf_path: str) -> str:
    """Detect order type from PDF."""
    
    # Existing detection logic...
    
    # TCAA detection (if not already present)
    if is_tcaa_order(pdf_path):
        return "TCAA"
    
    return "Unknown"

def is_tcaa_order(pdf_path: str) -> bool:
    """Check if PDF is a TCAA order."""
    try:
        from parsers.tcaa_parser import parse_tcaa_pdf
        estimates = parse_tcaa_pdf(pdf_path)
        return len(estimates) > 0
    except:
        return False
```

### 4. Error Handling Pattern

Recommended error handling for TCAA orders:

```python
def process_order(order_data: dict) -> dict:
    """Process a single order."""
    
    if order_data['type'] == 'TCAA':
        try:
            result = process_tcaa_order_with_browser(
                pdf_path=order_data['pdf_path'],
                order_code=order_data.get('order_code', 'AUTO'),
                description=order_data.get('description', 'Order'),
                customer_id=order_data.get('customer_id', 75)
            )
            
            return {
                'success': result['success'],
                'contracts_created': result['contracts_created'],
                'error': result.get('error')
            }
            
        except Exception as e:
            return {
                'success': False,
                'contracts_created': 0,
                'error': f"TCAA processing error: {str(e)}"
            }
    
    # Handle other order types...
```

## Testing Integration

### Test Without Browser

Test the integration module independently:

```python
from tcaa_integration import validate_tcaa_order, get_tcaa_order_info

# Validate
validation = validate_tcaa_order("path/to/tcaa.pdf")
print(f"Valid: {validation['valid']}")
print(f"Issues: {validation['issues']}")

# Get info
info = get_tcaa_order_info("path/to/tcaa.pdf")
print(f"Estimate: {info['estimate_number']}")
print(f"Lines: {info['total_lines']}")
```

### Test With Browser

Test the full browser automation:

```bash
python tcaa_integration.py
```

This will prompt for a PDF and walk through the full process.

## Configuration

### Etere URL Configuration

Update the Etere URL in `tcaa_integration.py` if different:

```python
ETERE_CONTRACT_URL = "http://100.102.206.113/vendite/ordini/ordine"
```

### Market Configuration

TCAA always uses SEA (Seattle) market. This is hardcoded but can be modified:

```python
TCAA_MARKET = "SEA"  # The Asian Channel operates in Seattle market
```

## Expected User Experience

### Before (Manual Processing)

```
✗ Processing failed
  Order Type: TCAA
  Error: Browser automation not implemented - manual processing required.
To process this order:
  1. Open Etere manually
  2. Process the order using the information above
```

### After (Automated Processing)

```
======================================================================
TCAA BROWSER AUTOMATION
======================================================================
PDF: MAY26 CROSSINGS.pdf
Order Code: TCAA Toyota 9710
Description: Toyota SEA Est 9710
Customer ID: 75
======================================================================

[BROWSER] Initializing Chrome driver...
[BROWSER] ✓ Browser started

======================================================================
PLEASE LOG IN TO ETERE
======================================================================
[...login instructions...]

[MARKET] Setting master market to SEA...
[MARKET] ✓ Master market set to SEA

Parsing PDF...
Found 1 estimates (contracts) in PDF

======================================================================
GATHERING BONUS LINE INPUTS
======================================================================

Bonus line 1 of 2:
Current: M-Su 6:00a-7:00a (no language specified)

  Days of week (e.g., M-Su, M-F): M-F
  Time period (e.g., 6a-12a, 7p-11p): 8a-10a
  Language options: Mandarin, Korean, South Asian, Filipino, Vietnamese, Cantonese, Chinese, Hmong
  Language: Korean

======================================================================
STARTING CONTRACT CREATION
======================================================================

======================================================================
CREATING CONTRACT FOR ESTIMATE 9710
======================================================================
Filling contract header...
✓ Contract header filled: TCAA Toyota 9710

  Line 1: M-Su 6-7a Mandarin
    Splits into 1 Etere line(s)
    Creating line 1: 05/01/2026 - 05/31/2026
    ✓ Line added: M-Sa 6-7a Mandarin

[... additional lines ...]

Saving contract...
✓ Contract saved successfully

✓ Estimate 9710 completed successfully

======================================================================
TCAA ORDER PROCESSING COMPLETE
======================================================================
Successfully created: 1/1 contracts

✓ Successfully processed: 1/1 order(s)
  Total contracts created: 1
```

## Troubleshooting

### Browser Automation Fails

1. **Check Etere URL**: Ensure `ETERE_CONTRACT_URL` is correct
2. **Check Field IDs**: Etere field IDs may have changed
3. **Check Network**: Ensure Etere is accessible from your machine
4. **Check Chrome**: Ensure Chrome/Chromedriver is installed

### PDF Parsing Fails

1. **Check PDF Format**: Ensure PDF matches expected TCAA format
2. **Check Parser**: Test `tcaa_parser.py` independently
3. **Re-export PDF**: Try Chrome "Print to PDF" for corrupted files

### Import Errors

1. **Check Path**: Ensure `browser_automation/` directory exists
2. **Check Files**: Ensure all required files are present
3. **Check Python Version**: Requires Python 3.12+

## Performance Considerations

### Processing Time

- **Per Contract**: ~2-3 minutes (including user input)
- **Upfront Input**: All bonus line inputs gathered first
- **Unattended**: Once inputs collected, runs unattended

### Resource Usage

- **Browser**: Chrome runs in visible mode (can monitor progress)
- **Memory**: Moderate (browser + Selenium)
- **Network**: Requires connection to Etere

## Future Enhancements

Potential improvements to consider:

1. **Headless Mode**: Option to run browser in background
2. **Batch Processing**: Process multiple TCAA orders in sequence
3. **Resume Capability**: Save progress for partial failures
4. **Validation Reports**: Generate detailed validation reports
5. **Screenshot Capture**: Capture screenshots for verification

## Support

If issues arise:

1. Check test suite: `pytest test_tcaa_automation.py -v`
2. Test integration: `python tcaa_integration.py`
3. Review logs for specific errors
4. Check Etere field IDs haven't changed

## Related Documentation

- **TCAA_AUTOMATION_README.md**: Detailed module documentation
- **test_tcaa_automation.py**: Test suite with examples
- **tcaa_automation.py**: Source code with inline documentation
