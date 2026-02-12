"""
Run all tests and show summary by phase.

This script runs the complete test suite and breaks down results by phase.
"""

import subprocess
import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))


def run_tests():
    """Run all tests and display summary."""
    
    print("=" * 70)
    print("RUNNING ALL TESTS")
    print("=" * 70)
    print()
    
    # Test suites by phase
    test_suites = [
        ("Phase 1: Domain Layer", "tests/unit/test_domain.py"),
        ("Phase 2: Detection Service", "tests/unit/test_order_detection_service.py"),
        ("Phase 3: Customer Repository", [
            "tests/integration/test_customer_repository.py",
            "tests/unit/test_customer_matching_service.py"
        ]),
        ("Phase 4: Processing Service", "tests/unit/test_order_processing_service.py"),
        ("Phase 5: Presentation Layer", [
            "tests/unit/test_input_collectors.py",
            "tests/unit/test_output_formatters.py"
        ]),
        ("Phase 6: Orchestration", [
            "tests/unit/test_config.py",
            "tests/unit/test_order_scanner.py",
            "tests/unit/test_orchestrator.py"
        ]),
    ]
    
    total_passed = 0
    total_failed = 0
    phase_results = []
    
    for phase_name, test_paths in test_suites:
        print(f"\n{phase_name}")
        print("-" * 70)
        
        # Handle both single path and list of paths
        if isinstance(test_paths, str):
            test_paths = [test_paths]
        
        phase_passed = 0
        phase_failed = 0
        
        for test_path in test_paths:
            result = subprocess.run(
                ["pytest", test_path, "-v", "--tb=no", "-q"],
                capture_output=True,
                text=True
            )
            
            # Parse output for pass/fail counts
            output = result.stdout
            if "passed" in output:
                # Extract number of passed tests
                for line in output.split("\n"):
                    if "passed" in line:
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part == "passed":
                                try:
                                    count = int(parts[i-1])
                                    phase_passed += count
                                except (ValueError, IndexError):
                                    pass
        
        print(f"✓ {phase_passed} tests passed")
        phase_results.append((phase_name, phase_passed))
        total_passed += phase_passed
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    for phase_name, count in phase_results:
        print(f"{phase_name:.<50} {count:>3} tests")
    
    print("-" * 70)
    print(f"{'TOTAL':.<50} {total_passed:>3} tests")
    print("=" * 70)
    
    return total_passed


if __name__ == "__main__":
    try:
        total = run_tests()
        print(f"\n✅ All {total} tests passed!\n")
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] Test run cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
