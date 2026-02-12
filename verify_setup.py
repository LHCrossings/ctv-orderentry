"""
Setup verification script - checks if project structure is correct.
"""

import sys
from pathlib import Path

def verify_setup():
    """Verify that the project structure is correct."""
    
    print("=" * 70)
    print("ORDER PROCESSING SYSTEM - SETUP VERIFICATION")
    print("=" * 70)
    
    # Check current directory
    current_dir = Path.cwd()
    print(f"\nCurrent directory: {current_dir}")
    
    # Check for required directories
    required_dirs = [
        "src",
        "src/domain",
        "src/data_access",
        "src/business_logic",
        "src/presentation",
        "src/orchestration",
        "tests",
        "tests/unit",
        "tests/integration"
    ]
    
    print("\n" + "-" * 70)
    print("Checking directory structure...")
    print("-" * 70)
    
    all_present = True
    for dir_path in required_dirs:
        full_path = current_dir / dir_path
        exists = full_path.exists()
        status = "✓" if exists else "✗"
        print(f"{status} {dir_path:<40} {'OK' if exists else 'MISSING'}")
        if not exists:
            all_present = False
    
    # Check for key files
    required_files = [
        "main.py",
        "run_all_tests.py",
        "src/orchestration/__init__.py",
        "src/orchestration/config.py",
        "src/orchestration/order_scanner.py",
        "src/orchestration/orchestrator.py"
    ]
    
    print("\n" + "-" * 70)
    print("Checking key files...")
    print("-" * 70)
    
    for file_path in required_files:
        full_path = current_dir / file_path
        exists = full_path.exists()
        status = "✓" if exists else "✗"
        print(f"{status} {file_path:<40} {'OK' if exists else 'MISSING'}")
        if not exists:
            all_present = False
    
    # Try to import modules
    print("\n" + "-" * 70)
    print("Checking module imports...")
    print("-" * 70)
    
    # Add src to path
    src_path = current_dir / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    
    modules_to_test = [
        "domain",
        "domain.entities",
        "domain.enums",
        "business_logic.services.order_detection_service",
        "business_logic.services.order_processing_service",
        "data_access.repositories.customer_repository",
        "presentation.cli",
        "presentation.formatters",
        "orchestration",
        "orchestration.config",
        "orchestration.orchestrator"
    ]
    
    for module_name in modules_to_test:
        try:
            __import__(module_name)
            print(f"✓ {module_name:<50} OK")
        except Exception as e:
            print(f"✗ {module_name:<50} FAILED: {e}")
            all_present = False
    
    # Summary
    print("\n" + "=" * 70)
    if all_present:
        print("✓ SETUP VERIFIED - All checks passed!")
        print("\nYou can now run:")
        print("  python main.py")
    else:
        print("✗ SETUP INCOMPLETE - Some components are missing")
        print("\nPlease ensure you have extracted all files from the project archive")
        print("and are running from the project root directory.")
    print("=" * 70)
    print()
    
    return all_present


if __name__ == "__main__":
    success = verify_setup()
    sys.exit(0 if success else 1)
