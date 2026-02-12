"""
TCAA Integration Module

Integrates TCAA browser automation into the main order processing system.
Provides the entry point for processing TCAA orders from process_orders.py.
"""

from pathlib import Path
import sys
from typing import Optional

# Add paths for imports
_current_dir = Path(__file__).parent
_browser_automation_dir = _current_dir / "browser_automation"

if str(_browser_automation_dir) not in sys.path:
    sys.path.insert(0, str(_browser_automation_dir))

# Import TCAA automation components
from tcaa_automation import process_tcaa_order, prompt_for_bonus_lines
from etere_session import EtereSession
from parsers.tcaa_parser import parse_tcaa_pdf


# Etere configuration
ETERE_CONTRACT_URL = "http://100.102.206.113/vendite/ordini/ordine"
TCAA_MARKET = "SEA"  # The Asian Channel operates in Seattle market


def process_tcaa_order_with_browser(
    pdf_path: str,
    order_code: str,
    description: str,
    customer_id: Optional[int] = None
) -> dict:
    """
    Process a TCAA order with browser automation.
    
    Main entry point called from the order processing system.
    
    Args:
        pdf_path: Path to the TCAA PDF file
        order_code: Order code (e.g., "TCAA Toyota 9710")
        description: Order description (e.g., "Toyota SEA Est 9710")
        customer_id: Customer ID (default: 75 for Toyota)
    
    Returns:
        Dict with processing results:
        {
            'success': bool,
            'contracts_created': int,
            'total_estimates': int,
            'error': str | None
        }
    """
    result = {
        'success': False,
        'contracts_created': 0,
        'total_estimates': 0,
        'error': None
    }
    
    try:
        print(f"\n{'='*70}")
        print(f"TCAA BROWSER AUTOMATION")
        print(f"{'='*70}")
        print(f"PDF: {pdf_path}")
        print(f"Order Code: {order_code}")
        print(f"Description: {description}")
        print(f"Customer ID: {customer_id or 75}")
        print(f"{'='*70}\n")
        
        # Initialize browser session
        with EtereSession() as session:
            # Set market to SEA (Seattle)
            session.set_market(TCAA_MARKET)
            
            # Process the TCAA order
            success = process_tcaa_order(
                driver=session.driver,
                pdf_path=pdf_path,
                contract_url=ETERE_CONTRACT_URL
            )
            
            # Parse PDF to get estimate count for reporting
            estimates = parse_tcaa_pdf(pdf_path)
            
            result['success'] = success
            result['total_estimates'] = len(estimates)
            
            if success:
                result['contracts_created'] = len(estimates)
            
            return result
    
    except Exception as e:
        result['error'] = str(e)
        print(f"\n✗ TCAA processing failed: {e}")
        import traceback
        traceback.print_exc()
        return result


def validate_tcaa_order(pdf_path: str) -> dict:
    """
    Validate a TCAA order before processing.
    
    Parses the PDF and checks for common issues.
    
    Args:
        pdf_path: Path to the TCAA PDF file
    
    Returns:
        Dict with validation results:
        {
            'valid': bool,
            'estimates': int,
            'issues': list[str]
        }
    """
    validation = {
        'valid': True,
        'estimates': 0,
        'issues': []
    }
    
    try:
        # Parse the PDF
        estimates = parse_tcaa_pdf(pdf_path)
        
        if not estimates:
            validation['valid'] = False
            validation['issues'].append("No estimates found in PDF")
            return validation
        
        validation['estimates'] = len(estimates)
        
        # Check each estimate
        for est in estimates:
            # Check for required fields
            if not est.estimate_number:
                validation['issues'].append(f"Missing estimate number")
            
            if not est.lines:
                validation['issues'].append(f"Estimate {est.estimate_number} has no lines")
            
            # Check for flight dates
            if not est.flight_start or not est.flight_end:
                validation['issues'].append(f"Estimate {est.estimate_number} missing flight dates")
        
        if validation['issues']:
            validation['valid'] = False
        
        return validation
    
    except Exception as e:
        validation['valid'] = False
        validation['issues'].append(f"PDF parsing error: {e}")
        return validation


def get_tcaa_order_info(pdf_path: str) -> dict:
    """
    Extract order information from TCAA PDF.
    
    Used by the main order processing system to get order details
    without full processing.
    
    Args:
        pdf_path: Path to the TCAA PDF file
    
    Returns:
        Dict with order information:
        {
            'customer_id': int,
            'estimate_number': str,
            'description': str,
            'flight_start': str,
            'flight_end': str,
            'total_lines': int,
            'bonus_lines': int
        }
    """
    try:
        estimates = parse_tcaa_pdf(pdf_path)
        
        if not estimates:
            return {}
        
        # Get first estimate as representative
        first_estimate = estimates[0]
        
        bonus_count = sum(1 for line in first_estimate.lines if line.is_bonus())
        
        return {
            'customer_id': 75,  # Toyota
            'estimate_number': first_estimate.estimate_number,
            'description': first_estimate.description,
            'flight_start': first_estimate.flight_start,
            'flight_end': first_estimate.flight_end,
            'total_lines': len(first_estimate.lines),
            'bonus_lines': bonus_count,
            'market': 'SEA'
        }
    
    except Exception as e:
        print(f"Error extracting TCAA order info: {e}")
        return {}


# Convenience function for testing
def test_tcaa_integration():
    """Test TCAA integration with a sample PDF."""
    print("TCAA Integration Test")
    print("="*70)
    
    pdf_path = input("Enter path to TCAA PDF: ").strip()
    
    if not Path(pdf_path).exists():
        print(f"✗ File not found: {pdf_path}")
        return
    
    # Validate order
    print("\n[1/3] Validating order...")
    validation = validate_tcaa_order(pdf_path)
    
    if not validation['valid']:
        print("✗ Validation failed:")
        for issue in validation['issues']:
            print(f"  - {issue}")
        return
    
    print(f"✓ Validation passed ({validation['estimates']} estimates)")
    
    # Get order info
    print("\n[2/3] Extracting order information...")
    order_info = get_tcaa_order_info(pdf_path)
    
    print(f"  Customer: {order_info.get('customer_id', 'N/A')}")
    print(f"  Estimate: {order_info.get('estimate_number', 'N/A')}")
    print(f"  Description: {order_info.get('description', 'N/A')}")
    print(f"  Flight: {order_info.get('flight_start', 'N/A')} - {order_info.get('flight_end', 'N/A')}")
    print(f"  Lines: {order_info.get('total_lines', 0)} ({order_info.get('bonus_lines', 0)} bonus)")
    
    # Process order
    print("\n[3/3] Processing order with browser automation...")
    
    proceed = input("\nProceed with browser automation? (y/n): ").strip().lower()
    
    if proceed != 'y':
        print("Cancelled")
        return
    
    result = process_tcaa_order_with_browser(
        pdf_path=pdf_path,
        order_code=f"TCAA Toyota {order_info.get('estimate_number', 'TEST')}",
        description=f"Toyota SEA Est {order_info.get('estimate_number', 'TEST')}",
        customer_id=75
    )
    
    print("\n" + "="*70)
    print("PROCESSING RESULT")
    print("="*70)
    
    if result['success']:
        print(f"✓ Success!")
        print(f"  Contracts created: {result['contracts_created']}/{result['total_estimates']}")
    else:
        print(f"✗ Failed")
        if result['error']:
            print(f"  Error: {result['error']}")


if __name__ == "__main__":
    test_tcaa_integration()
