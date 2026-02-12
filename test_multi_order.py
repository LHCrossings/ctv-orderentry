#!/usr/bin/env python
"""
Test multi-order TCAA detection.
"""

import sys
from pathlib import Path

# Add src to path
_src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(_src_path))

from business_logic.services.order_detection_service import OrderDetectionService

# Test data simulating a multi-order TCAA PDF
test_text = """
CRTV-Cable Television

Estimate: 9709
Western Washington Toyota Dlrs Adv Assoc
Some order details here...

CRTV-Cable Television

Estimate: 9710
Western Washington Toyota Dlrs Adv Assoc
More order details...

CRTV-Cable Television

Estimate: 9715
Western Washington Toyota Dlrs Adv Assoc
Even more details...
"""

print("Testing multi-order TCAA detection...")
print("=" * 70)

service = OrderDetectionService()

# Test 1: Count orders
count = service.count_tcaa_orders(test_text)
print(f"\n1. Count TCAA orders:")
print(f"   Expected: 3")
print(f"   Got: {count}")
print(f"   {'✓ PASS' if count == 3 else '✗ FAIL'}")

# Test 2: Split orders
orders = service.split_tcaa_orders(test_text)
print(f"\n2. Split TCAA orders:")
print(f"   Expected: 3 separate orders")
print(f"   Got: {len(orders)} orders")
print(f"   {'✓ PASS' if len(orders) == 3 else '✗ FAIL'}")

# Test 3: Check estimate numbers
estimates = [order['estimate'] for order in orders]
expected = ['9709', '9710', '9715']
print(f"\n3. Extract estimate numbers:")
print(f"   Expected: {expected}")
print(f"   Got: {estimates}")
print(f"   {'✓ PASS' if estimates == expected else '✗ FAIL'}")

# Test 4: Each order has text
print(f"\n4. Each order has text:")
all_have_text = all('text' in order and len(order['text']) > 0 for order in orders)
print(f"   All orders have text: {all_have_text}")
print(f"   {'✓ PASS' if all_have_text else '✗ FAIL'}")

print("\n" + "=" * 70)
print("Multi-order detection test complete!")
