#!/usr/bin/env python
"""
Quick test to verify the factory function works correctly.
"""

import sys
from pathlib import Path

# Add src to path
_src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(_src_path))

print("Testing factory function...")

# Test 1: Create detection service
from business_logic.services.order_detection_service import create_detection_service

detector = create_detection_service()
print(f"✓ Created detection service: {type(detector).__name__}")

# Test 2: Check it has the right methods
assert hasattr(detector, 'detect_order_type'), "Missing detect_order_type method"
print("✓ Has detect_order_type method")

assert hasattr(detector, 'extract_customer_name'), "Missing extract_customer_name method"
print("✓ Has extract_customer_name method")

# Test 3: Create orchestrator
from orchestration import create_orchestrator, ApplicationConfig

config = ApplicationConfig.for_testing()
print(f"✓ Created test config")

orchestrator = create_orchestrator(config)
print(f"✓ Created orchestrator: {type(orchestrator).__name__}")

print("\n" + "=" * 70)
print("✅ ALL CHECKS PASSED - Factory functions work correctly!")
print("=" * 70)
print()
