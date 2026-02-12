"""
QUICK FIX APPLICATION SCRIPT
============================

This script shows exactly what needs to change in your existing files.
Run this to see the specific code changes needed.
"""

print("""
╔════════════════════════════════════════════════════════════════════╗
║                    TCAA ANNUAL BUY FIX                             ║
║                    Quick Start Guide                               ║
╚════════════════════════════════════════════════════════════════════╝

THREE ISSUES FIXED:
  ✓ Annual buy showing as 1 order (should be 7+)
  ✓ Estimate showing as "Unknown" (should be 9709, 9711, etc.)
  ✓ Range selection "1-4,5,9" not working


FILES PROVIDED:
  • tcaa_parser_fix.py      → Replace parsers/tcaa_parser.py
  • order_scanner_fix.py    → Reference for application/order_scanner.py
  • range_selection_fix.py  → Add to presentation/input_handler.py
  • INTEGRATION_GUIDE.txt   → Full integration instructions


QUICK START (5 minutes):
═══════════════════════════════════════════════════════════════════

Step 1: Backup Current System
------------------------------
Already done! (You made exact copy)


Step 2: Update TCAA Parser
---------------------------
Location: C:\\Users\\scrib\\windev\\OrderEntry\\parsers\\tcaa_parser.py

Action: REPLACE entire file with tcaa_parser_fix.py

Why: Your current parser returns 1 order, new one returns list of estimates


Step 3: Update Order Scanner  
----------------------------
Location: C:\\Users\\scrib\\windev\\OrderEntry\\application\\order_scanner.py

Find the method _process_tcaa() and update it to:

```python
def _process_tcaa(self, pdf_path: Path) -> List[Order]:
    '''Process TCAA PDF - creates multiple orders for annual buys'''
    orders = []
    
    try:
        from parsers.tcaa_parser import TCAAParser
        
        estimates = TCAAParser.parse(str(pdf_path))  # Returns LIST now
        
        print(f"[SCAN] {pdf_path.name}: Detected {len(estimates)} estimate(s)")
        
        for estimate in estimates:  # Loop through each estimate
            order = Order(
                pdf_path=str(pdf_path),
                order_type=OrderType.TCAA,
                status=OrderStatus.PENDING,
                customer_name="Western Washington Toyota Dlrs Adv Assoc",
                estimate_number=estimate.estimate_number,  # ← Extract this
                description=estimate.description           # ← And this
            )
            orders.append(order)
    
    except Exception as e:
        print(f"[ERROR] {e}")
        # Fallback to single order
        orders = [Order(..., estimate_number="Unknown", ...)]
    
    return orders  # Returns LIST not single order
```


Step 4: Add Range Selection
----------------------------
Location: Find where you prompt for order selection
         (Likely in main.py or presentation/cli.py)

Add this class (copy from range_selection_fix.py):

```python
class RangeSelectionParser:
    @staticmethod
    def parse(user_input: str, max_value: int) -> List[int]:
        # ... full implementation in range_selection_fix.py ...
```

Then update your input handling:

```python
# OLD:
selected = [int(x) for x in user_input.split()]

# NEW:
selected = RangeSelectionParser.parse(user_input, len(orders))
```


Step 5: Test It
---------------
Run: python main.py

Expected Output:
```
[SCAN] 2026 Annual CRTV-TV.pdf: Detected 7 estimate(s)

AVAILABLE ORDERS:
[1] 2026 Annual CRTV-TV.pdf (Estimate: 9709)
    Description: APR26 Asian Cable
[2] 2026 Annual CRTV-TV.pdf (Estimate: 9711)
    Description: JUN26 Asian Cable
...
[9] OCT26_CRTV.pdf (Estimate: 9715)

Selection: 1-4,7    ← Try range selection
Selected: [1, 2, 3, 4, 7]
```


TROUBLESHOOTING:
════════════════════════════════════════════════════════════════════

Problem: Still seeing "Detected 7 orders" but only 1 in list
Solution: order_scanner.py not updated - check _process_tcaa()

Problem: ImportError for TCAAParser
Solution: Check file location and import path

Problem: "Estimate: Unknown" still showing
Solution: Check Order() creation - ensure estimate_number=estimate.estimate_number

Problem: Range selection not working
Solution: RangeSelectionParser class not added - check import


TESTING CHECKLIST:
════════════════════════════════════════════════════════════════════
□ Annual buy shows 7+ separate orders (not 1)
□ Each order has estimate number (9709, not "Unknown")
□ Each order has description (APR26 Asian Cable, etc.)
□ Can select "1-4" and get [1,2,3,4]
□ Can select "1-4,7,9-11" and get [1,2,3,4,7,9,10,11]
□ Total order count correct (9 orders for your 3 PDFs)


NEED HELP?
════════════════════════════════════════════════════════════════════
1. Read INTEGRATION_GUIDE.txt for detailed instructions
2. Check console for [ERROR] messages
3. Verify each file change individually
4. Test with single PDF first, then all three


Good luck! The fixes are solid - just need careful integration.
""")
