"""
Quick test to scan your orders\incoming directory and detect all order types.

This will show you what the new detection service finds in your incoming folder.
"""

from pathlib import Path
import sys

# Add src directory to Python path
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from business_logic.services.pdf_order_detector import PDFOrderDetector
from domain.enums import OrderType


def main():
    """Scan orders\incoming and detect all order types."""
    detector = PDFOrderDetector()
    orders_dir = Path("orders\\incoming")
    
    print("=" * 70)
    print("SCANNING: orders\\incoming")
    print("=" * 70)
    print()
    
    if not orders_dir.exists():
        print(f"❌ Directory not found: {orders_dir}")
        print("\nPlease make sure you have an 'orders\\incoming' folder")
        print("in your project directory.")
        return
    
    # Get all PDF files
    pdf_files = list(orders_dir.glob("*.pdf"))
    
    if not pdf_files:
        print(f"No PDF files found in {orders_dir}")
        print("\nPlace some order PDFs in the orders\\incoming folder and try again.")
        return
    
    print(f"Found {len(pdf_files)} PDF file(s):\n")
    
    # Track results by type
    results_by_type = {}
    errors = []
    
    for pdf_path in sorted(pdf_files):
        try:
            # Detect order type (silent mode - no prompts)
            order_type = detector.detect_order_type(pdf_path, silent=True)
            
            # Extract client name
            client = detector.extract_client_name(pdf_path, order_type)
            
            # Store result
            if order_type not in results_by_type:
                results_by_type[order_type] = []
            
            results_by_type[order_type].append({
                'filename': pdf_path.name,
                'client': client
            })
            
            # Display
            status = "✓" if order_type != OrderType.UNKNOWN else "?"
            print(f"{status} {pdf_path.name}")
            print(f"  Type: {order_type.name}")
            print(f"  Client: {client or '(not found)'}")
            print()
            
        except Exception as e:
            errors.append({
                'filename': pdf_path.name,
                'error': str(e)
            })
            print(f"❌ {pdf_path.name}")
            print(f"  Error: {e}")
            print()
    
    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    total = len(pdf_files)
    successful = sum(len(files) for order_type, files in results_by_type.items() 
                     if order_type != OrderType.UNKNOWN)
    unknown = len(results_by_type.get(OrderType.UNKNOWN, []))
    
    print(f"Total PDFs: {total}")
    print(f"Successfully detected: {successful}")
    if unknown > 0:
        print(f"Unknown type: {unknown}")
    if errors:
        print(f"Errors: {len(errors)}")
    
    # Breakdown by type
    if results_by_type:
        print("\nDetected order types:")
        for order_type in sorted(results_by_type.keys(), key=lambda x: x.name):
            files = results_by_type[order_type]
            print(f"\n  {order_type.name} ({len(files)} file(s)):")
            for file_info in files[:5]:  # Show first 5
                print(f"    - {file_info['filename']}")
                if file_info['client']:
                    print(f"      Client: {file_info['client']}")
            if len(files) > 5:
                print(f"    ... and {len(files) - 5} more")
    
    # Show unknown files if any
    if OrderType.UNKNOWN in results_by_type:
        unknown_files = results_by_type[OrderType.UNKNOWN]
        print(f"\n⚠️  Unknown order types ({len(unknown_files)}):")
        for file_info in unknown_files:
            print(f"  - {file_info['filename']}")
        print("\nThese PDFs might:")
        print("  - Be from a new agency not yet supported")
        print("  - Have encoding issues (try Chrome 'Print to PDF')")
        print("  - Be corrupted or incomplete")
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
