#!/usr/bin/env python
"""
Main entry point for the Order Processing Application.

Usage:
    python main.py                  # Interactive mode
    python main.py --batch          # Batch mode
    python main.py --auto           # Automatic mode
    python main.py --scan           # Just scan and show orders
"""

import sys
import argparse
from pathlib import Path

# Add src to path - must be done before any local imports
_src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(_src_path))

from orchestration import create_orchestrator, ApplicationConfig


def main():
    """Main entry point."""
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Order Processing Application",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  interactive (default)  - Process orders one by one with user input
  batch                  - Collect all inputs upfront, then process
  auto                   - Process all orders automatically
  scan                   - Just scan and display available orders
        """
    )
    
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Run in batch mode (collect all inputs upfront)"
    )
    
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run in automatic mode (no user input)"
    )
    
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan and display orders without processing"
    )
    
    parser.add_argument(
        "--incoming",
        type=Path,
        help="Override incoming directory path"
    )
    
    parser.add_argument(
        "--config",
        type=Path,
        help="Load configuration from file (not implemented yet)"
    )
    
    args = parser.parse_args()
    
    try:
        # Create configuration
        config = ApplicationConfig.from_defaults()
        
        # Override incoming directory if provided
        if args.incoming:
            config = ApplicationConfig(
                incoming_dir=args.incoming,
                processed_dir=config.processed_dir,
                error_dir=config.error_dir,
                customer_db_path=config.customer_db_path,
                batch_size=config.batch_size,
                auto_process=config.auto_process,
                require_confirmation=config.require_confirmation,
                headless=config.headless,
                browser_timeout=config.browser_timeout
            )
        
        # Create orchestrator
        orchestrator = create_orchestrator(config)
        
        # Run in appropriate mode
        if args.scan:
            # Just scan and display
            from orchestration.order_scanner import OrderScanner
            from business_logic.services.pdf_order_detector import PDFOrderDetector
            from presentation.formatters import order_formatter
            
            scanner = OrderScanner(
                PDFOrderDetector(),
                config.incoming_dir
            )
            orders = scanner.scan_for_orders()
            
            if orders:
                print(order_formatter.format_order_list(orders))
            else:
                print("\n[INFO] No orders found")
                
        elif args.auto:
            # Automatic mode
            orchestrator.run_auto()
            
        elif args.batch:
            # Batch mode
            orchestrator.run_batch()
            
        else:
            # Interactive mode (default)
            orchestrator.run_interactive()
        
        print("\n[COMPLETE] Application finished")
        sys.exit(0)
        
    except KeyboardInterrupt:
        print("\n\n[CANCELLED] Application interrupted by user")
        sys.exit(1)
        
    except Exception as e:
        print(f"\n[ERROR] Application failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
