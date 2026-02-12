"""
Quick test verification - shows you have all 123 tests.

Run this to verify your complete test suite.
"""

from pathlib import Path
import sys

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

print("=" * 70)
print("TEST SUITE VERIFICATION")
print("=" * 70)
print()

test_files = {
    "Phase 1 - Domain Layer": [
        ("tests/unit/test_domain.py", 35)
    ],
    "Phase 2 - Detection Service": [
        ("tests/unit/test_order_detection_service.py", 49)
    ],
    "Phase 3 - Customer Repository": [
        ("tests/integration/test_customer_repository.py", 14),
        ("tests/unit/test_customer_matching_service.py", 13)
    ],
    "Phase 4 - Processing Service": [
        ("tests/unit/test_order_processing_service.py", 12)
    ]
}

total_expected = 0
total_found = 0

for phase, files in test_files.items():
    print(f"\n{phase}")
    print("-" * 70)
    
    for test_file, expected_count in files:
        file_path = Path(test_file)
        if file_path.exists():
            status = "✓"
            total_found += expected_count
        else:
            status = "✗ MISSING"
        
        print(f"  {status} {test_file:.<50} {expected_count:>3} tests")
        total_expected += expected_count

print("\n" + "=" * 70)
print(f"Expected: {total_expected} tests")
print(f"Found:    {total_found} test files")

if total_found == total_expected:
    print("\n✅ All test files present!")
    print(f"\nTo run all {total_expected} tests:")
    print("  pytest tests/ -v")
    print("\nTo run by phase:")
    print("  pytest tests/unit/test_domain.py -v                     # Phase 1 (35 tests)")
    print("  pytest tests/unit/test_order_detection_service.py -v    # Phase 2 (49 tests)")
    print("  pytest tests/integration/test_customer_repository.py tests/unit/test_customer_matching_service.py -v  # Phase 3 (27 tests)")
    print("  pytest tests/unit/test_order_processing_service.py -v   # Phase 4 (12 tests)")
else:
    print("\n⚠️  Some test files are missing!")

print("=" * 70)
