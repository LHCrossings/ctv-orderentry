#!/usr/bin/env python
"""
Test multi-order detection with duplicates (real-world scenario).
"""

import sys
from pathlib import Path

# Add src to path
_src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(_src_path))

from business_logic.services.order_detection_service import OrderDetectionService

# Test data simulating a real TCAA PDF with duplicate estimate numbers
# (estimate appears multiple times in the same order block)
test_text = """
CRTV-Cable Television
Estimate: 9709
Client: Western Washington Toyota Dlrs Adv Assoc
Estimate: 9709
Some more text for 9709...

CRTV-Cable Television  
Estimate: 9711
Client: Western Washington Toyota Dlrs Adv Assoc
Estimate: 9711
Some more text for 9711...

CRTV-Cable Television
Estimate: 9712
Client: Western Washington Toyota Dlrs Adv Assoc
Estimate: 9712
Some more text for 9712...
"""

print("Testing duplicate estimate removal...")
print("=" * 70)

service = OrderDetectionService()

# Count should return unique estimates
count = service.count_tcaa_orders(test_text)
print(f"\n1. Count unique orders:")
print(f"   Expected: 3 (not 6)")
print(f"   Got: {count}")
print(f"   {'✓ PASS' if count == 3 else '✗ FAIL'}")

# Split should return only unique estimates
orders = service.split_tcaa_orders(test_text)
print(f"\n2. Split returns unique orders:")
print(f"   Expected: 3 orders")
print(f"   Got: {len(orders)} orders")
print(f"   {'✓ PASS' if len(orders) == 3 else '✗ FAIL'}")

# Check estimate numbers are unique
estimates = [order['estimate'] for order in orders]
unique_estimates = list(set(estimates))
print(f"\n3. No duplicate estimates:")
print(f"   Estimates: {estimates}")
print(f"   Unique: {unique_estimates}")
print(f"   {'✓ PASS' if len(estimates) == len(unique_estimates) else '✗ FAIL'}")

# Test customer extraction
print(f"\n4. Customer name extraction:")
for order in orders:
    customer = service._extract_tcaa_client(order['text'])
    print(f"   Estimate {order['estimate']}: {customer}")
    # Should NOT include "Estimate:" in the name
    if customer and 'Estimate:' in customer:
        print(f"   ✗ FAIL - Customer name includes 'Estimate:'")
    else:
        print(f"   ✓ PASS - Customer name clean")

print("\n" + "=" * 70)
print("Duplicate removal test complete!")
