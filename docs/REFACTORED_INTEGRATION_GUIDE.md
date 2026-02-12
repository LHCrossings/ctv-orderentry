# TCAA Integration for Refactored Architecture

Integration instructions for adding TCAA browser automation to your orchestration-based order processing system.

## Architecture Overview

Your system uses:
- **main.py** - Entry point with orchestration
- **Orchestration layer** - Manages workflow
- **Service layer** - Business logic
- **Repository layer** - Data access

The TCAA integration follows this same pattern.

## Integration Steps

### Step 1: Place Files in Project Structure

Assuming your structure looks like:

```
your_project/
├── main.py
├── src/
│   ├── orchestration/
│   ├── business_logic/
│   │   └── services/
│   ├── data_access/
│   └── presentation/
├── browser_automation/       # NEW - Create this directory
│   ├── tcaa_automation.py
│   ├── etere_session.py
│   ├── customer_matcher_browser.py
│   └── parsers/
│       └── tcaa_parser.py
└── tcaa_order_processor.py   # NEW - Add this file
```

### Step 2: Locate Your Order Processing Logic

Find where TCAA orders are currently handled. This is likely in:
- `src/orchestration/order_processor.py`, OR
- `src/business_logic/services/order_processor.py`, OR
- A similar file that routes orders by type

The file that shows this error:
```
✗ Processing failed
  Order Type: TCAA
  Error: Browser automation not implemented - manual processing required.
```

### Step 3: Add TCAA Processor

In the file that handles order processing, add:

```python
# At the top of the file
from tcaa_order_processor import create_tcaa_processor

# In your initialization or setup
class OrderProcessor:  # Or whatever your class is called
    def __init__(self):
        # ... existing initialization ...
        self.tcaa_processor = create_tcaa_processor()
    
    def process_order(self, order_data):
        """Process an order based on type."""
        
        order_type = order_data.get('type')
        
        # ... handle other order types ...
        
        # Add TCAA handling
        if self.tcaa_processor.can_process(order_type):
            result = self.tcaa_processor.process_order(
                pdf_path=order_data['pdf_path'],
                order_code=order_data.get('code', 'AUTO'),
                description=order_data.get('description', 'Order'),
                customer_id=order_data.get('customer_id', 75)
            )
            
            return {
                'success': result['success'],
                'contracts_created': result['contracts_created'],
                'error': result.get('error')
            }
        
        # ... fallback for unknown types ...
```

### Step 4: Alternative - Service Layer Integration

If you have a service layer, create a TCAA service:

```python
# src/business_logic/services/tcaa_service.py

from pathlib import Path
import sys

# Add browser_automation to path
_browser_automation = Path(__file__).parent.parent.parent / "browser_automation"
sys.path.insert(0, str(_browser_automation))

from tcaa_order_processor import TCAAOrderProcessor


class TCAAService:
    """Service for processing TCAA orders."""
    
    def __init__(self):
        self.processor = TCAAOrderProcessor()
    
    def process(self, pdf_path: str, order_code: str, description: str) -> dict:
        """Process TCAA order."""
        return self.processor.process_order(
            pdf_path=pdf_path,
            order_code=order_code,
            description=description
        )
```

Then use in orchestration:

```python
# In orchestration layer
from business_logic.services.tcaa_service import TCAAService

class Orchestrator:
    def __init__(self):
        # ... existing services ...
        self.tcaa_service = TCAAService()
    
    def process_order(self, order):
        if order['type'] == 'TCAA':
            return self.tcaa_service.process(
                pdf_path=order['pdf_path'],
                order_code=order['code'],
                description=order['description']
            )
```

## Example Integration Patterns

### Pattern 1: Direct Integration (Simplest)

```python
# In your order processing file
from tcaa_order_processor import create_tcaa_processor

# Create processor once
tcaa_processor = create_tcaa_processor()

# In your order processing loop/function
def process_single_order(order):
    if order['type'] == 'TCAA':
        # Use TCAA processor
        result = tcaa_processor.process_order(
            pdf_path=order['pdf_path'],
            order_code=order.get('code', f"TCAA Toyota {order['estimate']}"),
            description=order.get('description', f"Toyota SEA Est {order['estimate']}"),
            customer_id=order.get('customer_id', 75)
        )
        
        if result['success']:
            print(f"✓ Created {result['contracts_created']} contract(s)")
            return True
        else:
            print(f"✗ Failed: {result.get('error', 'Unknown error')}")
            return False
    
    # Handle other order types...
```

### Pattern 2: Service Layer (Clean Architecture)

```python
# Create TCAA service
class TCAAOrderService:
    """Business logic for TCAA orders."""
    
    def __init__(self):
        from tcaa_order_processor import create_tcaa_processor
        self._processor = create_tcaa_processor()
    
    def validate_order(self, pdf_path: str) -> bool:
        """Validate TCAA order."""
        validation = self._processor.validate_order(pdf_path)
        return validation['valid']
    
    def get_order_info(self, pdf_path: str) -> dict:
        """Extract order information."""
        return self._processor.extract_order_info(pdf_path)
    
    def process_order(self, pdf_path: str, code: str, desc: str) -> dict:
        """Process TCAA order."""
        return self._processor.process_order(
            pdf_path=pdf_path,
            order_code=code,
            description=desc
        )

# Use in orchestrator
class OrderOrchestrator:
    def __init__(self):
        self.tcaa_service = TCAAOrderService()
    
    def handle_tcaa_order(self, order):
        # Validate
        if not self.tcaa_service.validate_order(order['pdf_path']):
            return {'success': False, 'error': 'Validation failed'}
        
        # Get info
        info = self.tcaa_service.get_order_info(order['pdf_path'])
        
        # Process
        result = self.tcaa_service.process_order(
            pdf_path=order['pdf_path'],
            code=f"TCAA Toyota {info['estimate_number']}",
            desc=f"Toyota SEA Est {info['estimate_number']}"
        )
        
        return result
```

### Pattern 3: Strategy Pattern (Most Flexible)

```python
# Define interface
class OrderProcessorStrategy:
    def can_process(self, order_type: str) -> bool:
        raise NotImplementedError
    
    def process(self, order: dict) -> dict:
        raise NotImplementedError

# TCAA implementation
class TCAAProcessorStrategy(OrderProcessorStrategy):
    def __init__(self):
        from tcaa_order_processor import create_tcaa_processor
        self._processor = create_tcaa_processor()
    
    def can_process(self, order_type: str) -> bool:
        return order_type.upper() == 'TCAA'
    
    def process(self, order: dict) -> dict:
        return self._processor.process_order(
            pdf_path=order['pdf_path'],
            order_code=order.get('code', 'AUTO'),
            description=order.get('description', 'Order'),
            customer_id=order.get('customer_id', 75)
        )

# Orchestrator uses strategies
class OrderOrchestrator:
    def __init__(self):
        self.strategies = [
            TCAAProcessorStrategy(),
            WorldLinkProcessorStrategy(),  # Your existing
            # ... other strategies
        ]
    
    def process_order(self, order):
        for strategy in self.strategies:
            if strategy.can_process(order['type']):
                return strategy.process(order)
        
        raise ValueError(f"No processor for type: {order['type']}")
```

## Finding Your Integration Point

To find where to integrate, search your codebase for:

1. **Error message**: Search for "Browser automation not implemented"
2. **TCAA references**: Search for "TCAA" or "tcaa"
3. **Order type handling**: Look for `if order_type == ` or similar patterns
4. **NotImplementedError**: Search for this exception

```bash
# Example search commands
grep -r "Browser automation not implemented" src/
grep -r "TCAA" src/
grep -r "order_type" src/
```

## Testing Integration

### Test 1: Validate-Only (No Browser)

```python
from tcaa_order_processor import create_tcaa_processor

processor = create_tcaa_processor()

# Test validation
validation = processor.validate_order("path/to/tcaa.pdf")
print(f"Valid: {validation['valid']}")
print(f"Estimates: {validation['estimates']}")
print(f"Issues: {validation['issues']}")

# Test info extraction
info = processor.extract_order_info("path/to/tcaa.pdf")
print(f"Estimate: {info['estimate_number']}")
print(f"Lines: {info['total_lines']}")
```

### Test 2: Full Processing (With Browser)

```python
from tcaa_order_processor import create_tcaa_processor

processor = create_tcaa_processor()

result = processor.process_order(
    pdf_path="MAY26 CROSSINGS.pdf",
    order_code="TCAA Toyota 9710",
    description="Toyota SEA Est 9710",
    customer_id=75
)

if result['success']:
    print(f"✓ Created {result['contracts_created']} contracts")
else:
    print(f"✗ Failed: {result['error']}")
```

### Test 3: Standalone Test

```bash
python tcaa_order_processor.py path/to/tcaa.pdf
```

## Common Issues & Solutions

### Issue: Import Error - tcaa_automation not found

**Solution**: Ensure `browser_automation/` directory is in the right location:

```python
# At top of file where you use TCAA processor
import sys
from pathlib import Path

_browser_automation = Path(__file__).parent / "browser_automation"
sys.path.insert(0, str(_browser_automation))
```

### Issue: Selenium/Chrome Issues

**Solution**: 
1. Install selenium: `pip install selenium --break-system-packages`
2. Ensure Chrome is installed
3. ChromeDriver auto-managed by Selenium 4+

### Issue: EtereSession Import Error

**Solution**: Ensure all required files are in `browser_automation/`:
- tcaa_automation.py
- etere_session.py
- customer_matcher_browser.py
- parsers/tcaa_parser.py

## Next Steps

1. **Find your integration point** - Search for TCAA error message
2. **Copy files** - Place files in correct directories
3. **Add import** - Import `create_tcaa_processor`
4. **Add handler** - Replace error message with processor call
5. **Test** - Run with a TCAA order

## Need Help?

If you can share:
- The file that shows "Browser automation not implemented"
- Your project structure (output of `tree src/` or similar)

I can provide exact integration code for your specific setup.
