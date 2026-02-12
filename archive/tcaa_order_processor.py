"""
TCAA Order Processor for Refactored Architecture

Integrates TCAA browser automation into the orchestration-based order processing system.
This follows the clean architecture pattern used in main.py.
"""

from pathlib import Path
from typing import Optional
import sys

# Ensure browser_automation is in path
_browser_automation_path = Path(__file__).parent / "browser_automation"
if str(_browser_automation_path) not in sys.path:
    sys.path.insert(0, str(_browser_automation_path))


class TCAAOrderProcessor:
    """
    TCAA order processor for the refactored architecture.
    
    Integrates browser automation into the orchestration layer.
    Follows the repository/service pattern used throughout the system.
    """
    
    def __init__(self):
        """Initialize TCAA processor."""
        self.etere_url = "http://100.102.206.113"
        self.contract_url = f"{self.etere_url}/vendite/ordini/ordine"
        self.market = "SEA"  # TCAA always uses Seattle market
    
    def can_process(self, order_type: str) -> bool:
        """
        Check if this processor can handle the order type.
        
        Args:
            order_type: The detected order type
        
        Returns:
            True if order type is TCAA
        """
        return order_type.upper() == "TCAA"
    
    def validate_order(self, pdf_path: str) -> dict:
        """
        Validate TCAA order before processing.
        
        Args:
            pdf_path: Path to TCAA PDF file
        
        Returns:
            Dict with validation results:
            {
                'valid': bool,
                'estimates': int,
                'issues': list[str]
            }
        """
        try:
            from tcaa_parser import parse_tcaa_pdf
            
            estimates = parse_tcaa_pdf(pdf_path)
            
            if not estimates:
                return {
                    'valid': False,
                    'estimates': 0,
                    'issues': ['No estimates found in PDF']
                }
            
            issues = []
            
            for est in estimates:
                if not est.estimate_number:
                    issues.append("Missing estimate number")
                
                if not est.lines:
                    issues.append(f"Estimate {est.estimate_number} has no lines")
                
                if not est.flight_start or not est.flight_end:
                    issues.append(f"Estimate {est.estimate_number} missing flight dates")
            
            return {
                'valid': len(issues) == 0,
                'estimates': len(estimates),
                'issues': issues
            }
            
        except Exception as e:
            return {
                'valid': False,
                'estimates': 0,
                'issues': [f"PDF parsing error: {str(e)}"]
            }
    
    def extract_order_info(self, pdf_path: str) -> dict:
        """
        Extract order information from TCAA PDF.
        
        Args:
            pdf_path: Path to TCAA PDF file
        
        Returns:
            Dict with order information
        """
        try:
            from tcaa_parser import parse_tcaa_pdf
            
            estimates = parse_tcaa_pdf(pdf_path)
            
            if not estimates:
                return {
                    'customer_id': 75,
                    'estimate_number': 'Unknown',
                    'description': 'Unknown',
                    'flight_start': '',
                    'flight_end': '',
                    'total_lines': 0,
                    'bonus_lines': 0,
                    'market': 'SEA'
                }
            
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
            print(f"[WARNING] Error extracting TCAA order info: {e}")
            return {
                'customer_id': 75,
                'estimate_number': 'Unknown',
                'description': 'Unknown',
                'flight_start': '',
                'flight_end': '',
                'total_lines': 0,
                'bonus_lines': 0,
                'market': 'SEA'
            }
    
    def process_order(
        self,
        pdf_path: str,
        order_code: str,
        description: str,
        customer_id: Optional[int] = None
    ) -> dict:
        """
        Process TCAA order with browser automation.
        
        Args:
            pdf_path: Path to TCAA PDF file
            order_code: Order code (e.g., "TCAA Toyota 9710")
            description: Order description (e.g., "Toyota SEA Est 9710")
            customer_id: Customer ID (default: 75 for Toyota)
        
        Returns:
            Dict with processing results:
            {
                'success': bool,
                'contracts_created': int,
                'total_estimates': int,
                'error': Optional[str]
            }
        """
        result = {
            'success': False,
            'contracts_created': 0,
            'total_estimates': 0,
            'error': None
        }
        
        try:
            # Import automation components
            from tcaa_automation import process_tcaa_order
            from etere_session import EtereSession
            from tcaa_parser import parse_tcaa_pdf
            
            print(f"\n{'='*70}")
            print(f"TCAA BROWSER AUTOMATION")
            print(f"{'='*70}")
            print(f"PDF: {Path(pdf_path).name}")
            print(f"Order Code: {order_code}")
            print(f"Description: {description}")
            print(f"Customer ID: {customer_id or 75}")
            print(f"{'='*70}\n")
            
            # Initialize browser session
            with EtereSession() as session:
                # Set market to SEA (Seattle)
                session.set_market(self.market)
                
                # Process the order
                success = process_tcaa_order(
                    driver=session.driver,
                    pdf_path=pdf_path,
                    contract_url=self.contract_url
                )
                
                # Get estimate count for reporting
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


# ============================================================================
# Factory function for orchestration layer
# ============================================================================

def create_tcaa_processor() -> TCAAOrderProcessor:
    """
    Factory function to create TCAA processor instance.
    
    Use this in your orchestration layer:
    
    Example:
        from tcaa_order_processor import create_tcaa_processor
        
        tcaa_processor = create_tcaa_processor()
        
        if tcaa_processor.can_process(order_type):
            result = tcaa_processor.process_order(
                pdf_path=pdf_path,
                order_code=order_code,
                description=description
            )
    """
    return TCAAOrderProcessor()


# ============================================================================
# Convenience function for testing
# ============================================================================

def test_tcaa_processor():
    """Test TCAA processor with sample PDF."""
    import sys
    
    print("TCAA Processor Test")
    print("="*70)
    
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        pdf_path = input("Enter path to TCAA PDF: ").strip()
    
    if not Path(pdf_path).exists():
        print(f"✗ File not found: {pdf_path}")
        return
    
    processor = create_tcaa_processor()
    
    # Validate
    print("\n[1/3] Validating order...")
    validation = processor.validate_order(pdf_path)
    
    if not validation['valid']:
        print("✗ Validation failed:")
        for issue in validation['issues']:
            print(f"  - {issue}")
        return
    
    print(f"✓ Validation passed ({validation['estimates']} estimates)")
    
    # Extract info
    print("\n[2/3] Extracting order information...")
    info = processor.extract_order_info(pdf_path)
    
    print(f"  Customer: {info['customer_id']}")
    print(f"  Estimate: {info['estimate_number']}")
    print(f"  Description: {info['description']}")
    print(f"  Flight: {info['flight_start']} - {info['flight_end']}")
    print(f"  Lines: {info['total_lines']} ({info['bonus_lines']} bonus)")
    
    # Process
    print("\n[3/3] Processing order...")
    
    proceed = input("\nProceed with browser automation? (y/n): ").strip().lower()
    
    if proceed != 'y':
        print("Cancelled")
        return
    
    result = processor.process_order(
        pdf_path=pdf_path,
        order_code=f"TCAA Toyota {info['estimate_number']}",
        description=f"Toyota SEA Est {info['estimate_number']}",
        customer_id=75
    )
    
    print(f"\n{'='*70}")
    print("PROCESSING RESULT")
    print(f"{'='*70}")
    
    if result['success']:
        print(f"✓ Success!")
        print(f"  Contracts created: {result['contracts_created']}/{result['total_estimates']}")
    else:
        print(f"✗ Failed")
        if result['error']:
            print(f"  Error: {result['error']}")


if __name__ == "__main__":
    test_tcaa_processor()
