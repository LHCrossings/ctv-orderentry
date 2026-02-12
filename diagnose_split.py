"""
DIAGNOSTIC SCRIPT - See what split_tcaa_orders() actually returns

Run this to understand why only 1 order is being created.
"""
import sys
from pathlib import Path

# Add src to path
src_path = Path(r'C:\Users\scrib\windev\OrderEntry\src')
sys.path.insert(0, str(src_path))

from business_logic.services.order_detection_service import OrderDetectionService
import pdfplumber

# Path to your annual PDF
pdf_path = Path(r'C:\Users\scrib\windev\OrderEntry\incoming\2026_Annual_CRTV-TV.pdf')

print("=" * 70)
print("DIAGNOSTIC: Testing split_tcaa_orders()")
print("=" * 70)

# Extract full text
with pdfplumber.open(pdf_path) as pdf:
    full_text = ""
    for page in pdf.pages:
        full_text += page.extract_text() or ""

print(f"\nPDF has {len(full_text)} characters of text")

# Create service and split
service = OrderDetectionService()
orders = service.split_tcaa_orders(full_text)

print(f"\n{'='*70}")
print(f"RESULT: split_tcaa_orders() returned {len(orders)} order(s)")
print(f"{'='*70}")

for i, order in enumerate(orders, 1):
    print(f"\nOrder {i}:")
    print(f"  Type: {type(order)}")
    print(f"  Keys: {order.keys() if isinstance(order, dict) else 'N/A'}")
    
    if isinstance(order, dict):
        estimate = order.get('estimate', 'NOT FOUND')
        text = order.get('text', '')
        
        print(f"  Estimate: {estimate}")
        print(f"  Text length: {len(text)} chars")
        
        # Show snippet
        snippet = text[:200].replace('\n', ' ')
        print(f"  Text preview: {snippet}...")
        
        # Check for schedule markers
        has_schedule = 'SCHEDULE TOTALS' in text
        has_station = 'Station Total:' in text
        has_lines = text.count('CRTV-Cable')
        
        print(f"  Has 'SCHEDULE TOTALS': {has_schedule}")
        print(f"  Has 'Station Total:': {has_station}")
        print(f"  Count of 'CRTV-Cable': {has_lines}")

print(f"\n{'='*70}")
print("EXPECTED: 7 orders with unique estimate numbers")
print(f"ACTUAL: {len(orders)} orders")
print(f"{'='*70}")

if len(orders) == 1 and orders[0].get('estimate') == 'Unknown':
    print("\n⚠️  PROBLEM FOUND:")
    print("   split_tcaa_orders() is returning fallback value")
    print("   This means it's not finding any valid sections")
    print("\n   Possible causes:")
    print("   1. Regex pattern not matching estimate format")
    print("   2. Section split logic not working")
    print("   3. All sections being filtered out")
    
elif len(orders) < 7:
    print(f"\n⚠️  PROBLEM FOUND:")
    print(f"   Expected 7 orders, got {len(orders)}")
    print("   Some sections are being filtered incorrectly")

else:
    print("\n✅ SUCCESS!")
    print("   split_tcaa_orders() is working correctly")
