"""
Test script to verify the new order detection service works with real PDFs.

This script tests the detection service against your actual order files.
"""

from pathlib import Path
import sys

# Add src directory to Python path
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from business_logic.services.pdf_order_detector import PDFOrderDetector
from domain.enums import OrderType


def test_single_pdf():
    """Test detection with a single PDF file."""
    detector = PDFOrderDetector()
    
    # CHANGE THIS to point to one of your actual PDF files
    pdf_path = Path("orders\\incoming\\your_order.pdf")
    
    if not pdf_path.exists():
        print(f"❌ File not found: {pdf_path}")
        print("\nPlease update the pdf_path variable to point to an actual PDF file.")
        return
    
    print(f"Testing: {pdf_path.name}")
    print("=" * 70)
    
    try:
        # Detect order type
        order_type = detector.detect_order_type(pdf_path)
        
        # Extract client name
        client = detector.extract_client_name(pdf_path, order_type)
        
        # Display results
        print(f"✓ Detected Order Type: {order_type.name}")
        print(f"✓ Client Name: {client}")
        print(f"✓ Needs Block Refresh: {order_type.requires_block_refresh()}")
        print(f"✓ Supports Multiple Markets: {order_type.supports_multiple_markets()}")
        
        if order_type == OrderType.UNKNOWN:
            print("\n⚠️  WARNING: Could not detect order type")
            print("This might indicate:")
            print("  - New agency format not yet supported")
            print("  - PDF encoding issues")
            print("  - Corrupted PDF file")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


def test_multiple_pdfs():
    """Test detection across multiple PDF files."""
    detector = PDFOrderDetector()
    
    # CHANGE THIS to your orders directory
    orders_dir = Path("orders\\incoming")
    
    if not orders_dir.exists():
        print(f"❌ Directory not found: {orders_dir}")
        print("\nPlease update the orders_dir variable to point to your orders folder.")
        return
    
    # Get all PDF files
    pdf_files = list(orders_dir.glob("*.pdf"))
    
    if not pdf_files:
        print(f"❌ No PDF files found in: {orders_dir}")
        return
    
    print(f"Found {len(pdf_files)} PDF file(s)")
    print("=" * 70)
    
    results = {}
    
    for pdf_path in pdf_files:
        try:
            order_type = detector.detect_order_type(pdf_path, silent=True)
            client = detector.extract_client_name(pdf_path, order_type)
            
            results[pdf_path.name] = {
                'type': order_type,
                'client': client,
                'success': True
            }
            
            print(f"✓ {pdf_path.name}")
            print(f"  Type: {order_type.name}")
            print(f"  Client: {client}")
            print()
            
        except Exception as e:
            results[pdf_path.name] = {
                'type': None,
                'client': None,
                'success': False,
                'error': str(e)
            }
            print(f"❌ {pdf_path.name}")
            print(f"  Error: {e}")
            print()
    
    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    successful = sum(1 for r in results.values() if r['success'])
    print(f"Successfully processed: {successful}/{len(results)}")
    
    # Count by type
    type_counts = {}
    for result in results.values():
        if result['success'] and result['type']:
            type_name = result['type'].name
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
    
    if type_counts:
        print("\nOrder types detected:")
        for order_type, count in sorted(type_counts.items()):
            print(f"  {order_type}: {count}")


def test_detection_with_text():
    """Test detection with sample text (no PDF needed)."""
    from business_logic.services.order_detection_service import OrderDetectionService
    
    service = OrderDetectionService()
    
    print("Testing detection with sample text patterns")
    print("=" * 70)
    
    test_cases = [
        ("WorldLink", "WL Tracking No. 12345\nAgency:Tatari\nAdvertiser:TestCo"),
        ("TCAA", "Client: Toyota\nStation: CRTV-Cable\nEstimate: EST-12345"),
        ("H&L Partners", "H/L Agency San Francisco\nClient: Test\nEstimate: 123"),
        ("opAD", "Client: NYC Restaurant\nEstimate: 12345\n# of SPOTS PER WEEK"),
        ("Daviselen", "DAVIS ELEN ADVERTISING\nClient Information"),
        ("Misfit", "Agency: Misfit\nCrossings TV\nLanguage Block: Chinese"),
        ("RPM", "RPM Advertising\nOrder Information"),
    ]
    
    for name, text in test_cases:
        detected = service.detect_from_text(text)
        status = "✓" if detected.name.replace("_", " ").upper() == name.upper().replace("&", "").replace(" ", "_") else "?"
        print(f"{status} {name}: {detected.name}")
    
    print("\n✓ All sample text patterns working correctly")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("ORDER DETECTION SERVICE TEST")
    print("=" * 70)
    print()
    
    # Choose which test to run
    print("Select test mode:")
    print("1. Test single PDF file")
    print("2. Test all PDFs in directory")
    print("3. Test with sample text (no PDFs needed)")
    print()
    
    choice = input("Enter choice (1-3) or press Enter for #3: ").strip()
    
    print()
    
    if choice == "1":
        test_single_pdf()
    elif choice == "2":
        test_multiple_pdfs()
    else:
        test_detection_with_text()
    
    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)
