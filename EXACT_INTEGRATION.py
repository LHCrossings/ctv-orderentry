"""
EXACT INTEGRATION FOR order_processing_service.py

This shows you EXACTLY what to change in your refactored system
to add TCAA browser automation.
"""

# ============================================================================
# STEP 1: Add this import at the top of order_processing_service.py
# ============================================================================

# Add after your existing imports:
from pathlib import Path
import sys

# Add browser automation to path
_browser_automation_path = Path(__file__).parent.parent.parent / "browser_automation"
if str(_browser_automation_path) not in sys.path:
    sys.path.insert(0, str(_browser_automation_path))


# ============================================================================
# STEP 2: Find the OrderProcessingService class
# ============================================================================

# In the __init__ method, add:
class OrderProcessingService:
    def __init__(self, customer_repository, ...):  # Your existing params
        # ... your existing initialization ...
        
        # Add TCAA processor
        self._tcaa_processor = None  # Lazy load when needed


# ============================================================================
# STEP 3: Find the _process_single_order_without_browser method
# ============================================================================

# This is the method that currently shows:
# "Browser automation not implemented - manual processing required."

# REPLACE the entire section that creates the stub result with this:

def _process_single_order_without_browser(
    self,
    order: Order,
    browser_session: Optional[Any]
) -> ProcessingResult:
    """
    Process an order (with or without browser).
    
    Args:
        order: Order to process
        browser_session: Browser session if available (not used currently)
    
    Returns:
        ProcessingResult with success/failure info
    """
    # Check if this is a TCAA order
    if order.order_type == OrderType.TCAA:
        return self._process_tcaa_order(order)
    
    # For other order types, return the manual processing message
    from domain.entities import ProcessingResult, Contract
    
    message = (
        "Browser automation not implemented - manual processing required.\n"
        f"  Order Type: {order.order_type.name}\n"
        f"  Customer: {order.customer_name}\n"
    )
    
    if order.order_input:
        message += (
            f"  Order Code: {order.order_input.order_code}\n"
            f"  Description: {order.order_input.description}\n"
        )
    
    message += (
        "\nTo process this order:\n"
        "  1. Open Etere manually\n"
        "  2. Process the order using the information above\n"
        "  3. Browser automation will be added in a future update"
    )
    
    return ProcessingResult(
        success=False,
        contracts_created=[],
        error=message
    )


# ============================================================================
# STEP 4: Add the _process_tcaa_order method to OrderProcessingService
# ============================================================================

def _process_tcaa_order(self, order: Order) -> ProcessingResult:
    """
    Process TCAA order with browser automation.
    
    Args:
        order: TCAA order to process
    
    Returns:
        ProcessingResult with contracts created or error
    """
    from domain.entities import ProcessingResult, Contract
    
    try:
        # Lazy load TCAA processor
        if self._tcaa_processor is None:
            from tcaa_automation import process_tcaa_order
            from etere_session import EtereSession
            from tcaa_parser import parse_tcaa_pdf
            self._tcaa_processor = {
                'process': process_tcaa_order,
                'session_class': EtereSession,
                'parser': parse_tcaa_pdf
            }
        
        print(f"\n{'='*70}")
        print(f"TCAA BROWSER AUTOMATION")
        print(f"{'='*70}")
        print(f"Order: {order.filename}")
        print(f"Type: {order.order_type.name}")
        print(f"Customer: {order.customer_name}")
        
        if order.order_input:
            print(f"Code: {order.order_input.order_code}")
            print(f"Description: {order.order_input.description}")
        
        print(f"{'='*70}\n")
        
        # Start browser session
        with self._tcaa_processor['session_class']() as session:
            # Set market to SEA (Seattle)
            session.set_market("SEA")
            
            # Process order
            contract_url = "http://100.102.206.113/vendite/ordini/ordine"
            
            success = self._tcaa_processor['process'](
                driver=session.driver,
                pdf_path=str(order.pdf_path),
                contract_url=contract_url
            )
            
            if success:
                # Parse to get estimate count
                estimates = self._tcaa_processor['parser'](str(order.pdf_path))
                
                # Create contract objects for each estimate
                contracts = []
                for est in estimates:
                    contract = Contract(
                        contract_id=f"TCAA-{est.estimate_number}",
                        order_code=f"TCAA Toyota {est.estimate_number}",
                        description=f"Toyota SEA Est {est.estimate_number}",
                        customer_id=75,  # Toyota
                        start_date=est.flight_start,
                        end_date=est.flight_end
                    )
                    contracts.append(contract)
                
                return ProcessingResult(
                    success=True,
                    contracts_created=contracts,
                    error=None
                )
            else:
                return ProcessingResult(
                    success=False,
                    contracts_created=[],
                    error="TCAA processing failed - check browser output for details"
                )
    
    except Exception as e:
        import traceback
        error_detail = f"TCAA processing error: {str(e)}\n{traceback.format_exc()}"
        
        return ProcessingResult(
            success=False,
            contracts_created=[],
            error=error_detail
        )


# ============================================================================
# COMPLETE EXAMPLE - What Your File Should Look Like
# ============================================================================

"""
Your order_processing_service.py should have this structure:

```python
# ... existing imports ...
from pathlib import Path
import sys

# Add browser automation to path
_browser_automation_path = Path(__file__).parent.parent.parent / "browser_automation"
if str(_browser_automation_path) not in sys.path:
    sys.path.insert(0, str(_browser_automation_path))


class OrderProcessingService:
    def __init__(self, customer_repository, ...):
        # ... existing initialization ...
        self._tcaa_processor = None  # Add this
    
    # ... your existing methods ...
    
    def _process_single_order_without_browser(
        self,
        order: Order,
        browser_session: Optional[Any]
    ) -> ProcessingResult:
        # Check if this is a TCAA order
        if order.order_type == OrderType.TCAA:
            return self._process_tcaa_order(order)
        
        # For other types, return manual processing message
        # ... (existing stub code) ...
    
    def _process_tcaa_order(self, order: Order) -> ProcessingResult:
        # ... (TCAA automation code from STEP 4 above) ...
```
"""


# ============================================================================
# FILE PLACEMENT
# ============================================================================

"""
Make sure these files are in place:

your_project/
├── src/
│   └── orchestration/
│       └── order_processing_service.py  ← YOU EDIT THIS FILE
└── browser_automation/                   ← CREATE THIS DIRECTORY
    ├── tcaa_automation.py               ← FROM EARLIER
    ├── etere_session.py                 ← FROM EARLIER  
    ├── customer_matcher_browser.py      ← FROM EARLIER
    └── parsers/
        └── tcaa_parser.py               ← YOUR EXISTING PARSER
"""


# ============================================================================
# TESTING
# ============================================================================

"""
After making these changes, test with:

```bash
cd C:\\Users\\scrib\\windev\\OrderEntry
python main.py

# Select a TCAA order when prompted
# It should now run browser automation instead of showing the error message
```
"""
