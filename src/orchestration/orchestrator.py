"""
Application Orchestrator - Main coordinator for the order processing application.

This is the top-level component that ties together all layers:
- Domain models
- Detection service
- Customer repository
- Processing service  
- Presentation layer
"""

from pathlib import Path
from typing import Callable
import sys
import shutil

# Add src to path
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.entities import Order, ProcessingResult
from domain.enums import OrderStatus
from business_logic.services.pdf_order_detector import PDFOrderDetector
from business_logic.services.order_processing_service import OrderProcessingService
from data_access.repositories.customer_repository import CustomerRepository
from presentation.cli import InputCollector, BatchInputCollector
from presentation.formatters import (
    OrderFormatter,
    ProcessingResultFormatter,
    ProgressFormatter
)
from orchestration.config import ApplicationConfig
from orchestration.order_scanner import OrderScanner


class ApplicationOrchestrator:
    """
    Main application orchestrator.
    
    Coordinates all layers of the application to provide complete
    order processing workflows.
    """
    
    def __init__(
        self,
        config: ApplicationConfig,
        detection_service: PDFOrderDetector,
        customer_repository: CustomerRepository,
        processing_service: OrderProcessingService,
        input_collector: InputCollector | None = None,
        batch_input_collector: BatchInputCollector | None = None,
        order_formatter: OrderFormatter | None = None,
        result_formatter: ProcessingResultFormatter | None = None,
        progress_formatter: ProgressFormatter | None = None
    ):
        """
        Initialize the orchestrator.
        
        Args:
            config: Application configuration
            detection_service: Service for order detection
            customer_repository: Repository for customer data
            processing_service: Service for order processing
            input_collector: CLI input collector (creates default if None)
            batch_input_collector: Batch input collector (creates default if None)
            order_formatter: Order formatter (creates default if None)
            result_formatter: Result formatter (creates default if None)
            progress_formatter: Progress formatter (creates default if None)
        """
        self._config = config
        self._detection_service = detection_service
        self._customer_repository = customer_repository
        self._processing_service = processing_service
        
        # Create presentation layer components with defaults
        self._input_collector = input_collector or InputCollector()
        self._batch_input_collector = batch_input_collector or BatchInputCollector()
        self._order_formatter = order_formatter or OrderFormatter()
        self._result_formatter = result_formatter or ProcessingResultFormatter()
        self._progress_formatter = progress_formatter or ProgressFormatter()
        
        # Create scanner
        self._scanner = OrderScanner(detection_service, config.incoming_dir)
    
    def run_interactive(self) -> None:
        """
        Run the application in interactive mode.
        
        Scans for orders, lets user select which to process,
        and processes them one by one with confirmation.
        
        Browser automation is integrated for TCAA orders.
        Other order types will show manual processing instructions.
        """
        print("\n" + "=" * 70)
        print("ORDER PROCESSING - INTERACTIVE MODE")
        print("=" * 70)
        print()
        
        # Scan for orders
        print("Scanning for orders...")
        orders = self._scanner.scan_for_orders()
        
        if not orders:
            print("\n[INFO] No orders found in incoming directory")
            return
        
        # Display orders
        print(self._order_formatter.format_order_list(orders))
        
        # Let user select orders
        selected = self._input_collector.select_orders(orders)
        
        if not selected:
            print("\n[CANCELLED] No orders selected")
            return
        
        # Confirm processing
        if not self._input_collector.confirm_processing(selected):
            print("\n[CANCELLED] Processing aborted")
            return
        
        # Process each order
        results = self._process_orders_interactive(selected)
        
        # Display results
        print(self._result_formatter.format_batch_summary(results))
        
        # Move processed files
        self._move_processed_files(results)
    
    def run_batch(self) -> None:
        """
        Run the application in batch mode.
        
        Scans for orders, collects all inputs upfront,
        then processes all orders unattended.
        
        Browser automation is integrated for TCAA orders.
        Other order types will show manual processing instructions.
        """
        print("\n" + "=" * 70)
        print("ORDER PROCESSING - BATCH MODE")
        print("=" * 70)
        print()
        
        # Scan for orders
        print("Scanning for orders...")
        orders = self._scanner.scan_for_orders()
        
        if not orders:
            print("\n[INFO] No orders found in incoming directory")
            return
        
        # Display orders
        print(self._order_formatter.format_order_list(orders))
        
        # Let user select orders
        selected = self._input_collector.select_orders(orders)
        
        if not selected:
            print("\n[CANCELLED] No orders selected")
            return
        
        # Collect all inputs upfront
        inputs = self._batch_input_collector.collect_all_order_inputs(selected)
        
        # Process all orders unattended
        results = self._process_orders_batch(selected, inputs)
        
        # Display results
        print(self._result_formatter.format_batch_summary(results))
        
        # Move processed files
        self._move_processed_files(results)
    
    def run_auto(self) -> None:
        """
        Run the application in automatic mode.
        
        Processes all orders automatically without user interaction.
        Useful for scheduled/automated processing.
        """
        print("\n" + "=" * 70)
        print("ORDER PROCESSING - AUTOMATIC MODE")
        print("=" * 70)
        
        # Scan for orders
        print("\nScanning for orders...")
        orders = self._scanner.scan_for_orders()
        
        if not orders:
            print("\n[INFO] No orders found in incoming directory")
            return
        
        print(f"\nFound {len(orders)} order(s) to process")
        
        # Process all orders automatically
        results = self._process_orders_auto(orders)
        
        # Display results
        print(self._result_formatter.format_batch_summary(results))
        
        # Move processed files
        self._move_processed_files(results)
    
    def _process_orders_interactive(self, orders: list[Order]) -> list[ProcessingResult]:
        """
        Process orders interactively with batch grouping for TCAA orders from same PDF.
        
        Args:
            orders: Orders to process
            
        Returns:
            List of processing results
        """
        from domain.enums import OrderType
        
        # Collect inputs for all orders first
        orders_with_input = []
        for i, order in enumerate(orders, 1):
            print("\n" + "=" * 70)
            progress = self._progress_formatter.format_progress(
                i, len(orders),
                order.get_display_name()
            )
            print(progress)
            print("=" * 70)
            
            # SAGENT: Gather inputs upfront (before browser session)
            if order.order_type == OrderType.SAGENT:
                print("Type: SAGENT")
                print("Customer: CAL FIRE")
                print()
                
                # Import SAGENT input gathering function
                try:
                    from browser_automation.sagent_automation import gather_sagent_inputs_from_pdf
                    
                    # Gather inputs now (no browser needed!)
                    sagent_inputs = gather_sagent_inputs_from_pdf(str(order.pdf_path))
                    
                    if not sagent_inputs:
                        print("\n[CANCELLED] SAGENT input gathering cancelled")
                        continue
                    
                    # Store inputs in order
                    order_with_input = order.with_input(sagent_inputs)
                    orders_with_input.append(order_with_input)
                    
                except ImportError as e:
                    print(f"\n[ERROR] Could not import SAGENT automation: {e}")
                    print("[INFO] Falling back to runtime input gathering")
                    orders_with_input.append(order)
                
                continue
            
            # DAVISELEN: Gather inputs upfront (before browser session)
            if order.order_type == OrderType.DAVISELEN:
                print(f"Type: DAVISELEN")
                print(f"Customer: {order.customer_name}")
                print()
                
                # Import Daviselen input gathering function
                try:
                    from browser_automation.daviselen_automation import gather_daviselen_inputs
                    
                    # Gather inputs now (no browser needed!)
                    daviselen_inputs = gather_daviselen_inputs(str(order.pdf_path))
                    
                    if not daviselen_inputs:
                        print("\n[CANCELLED] Daviselen input gathering cancelled")
                        continue
                    
                    # Store inputs in order
                    order_with_input = order.with_input(daviselen_inputs)
                    orders_with_input.append(order_with_input)
                    
                except ImportError as e:
                    print(f"\n[ERROR] Could not import Daviselen automation: {e}")
                    print("[INFO] Falling back to runtime input gathering")
                    orders_with_input.append(order)
                
                continue
            
            # CHARMAINE: Skip generic input collection — process_charmaine_order
            # handles all input internally (customer lookup, code, description,
            # separation, South Asian disambiguation) with smart defaults.
            if order.order_type == OrderType.CHARMAINE:
                print(f"Type: CHARMAINE")
                print(f"Customer: {order.customer_name}")
                print()
                orders_with_input.append(order)
                continue
            
            # ADMERASIA: Gather inputs upfront (before browser session)
            if order.order_type == OrderType.ADMERASIA:
                print(f"Type: ADMERASIA")
                print(f"Customer: {order.customer_name}")
                print()
                
                try:
                    from browser_automation.admerasia_automation import gather_admerasia_inputs
                    
                    admerasia_inputs = gather_admerasia_inputs(str(order.pdf_path))
                    
                    if not admerasia_inputs:
                        print("\n[CANCELLED] Admerasia input gathering cancelled")
                        continue
                    
                    order_with_input = order.with_input(admerasia_inputs)
                    orders_with_input.append(order_with_input)
                    
                except ImportError as e:
                    print(f"\n[ERROR] Could not import Admerasia automation: {e}")
                    print("[INFO] Falling back to runtime input gathering")
                    orders_with_input.append(order)
                
                continue
            
            # H&L PARTNERS: Gather inputs upfront (before browser session)
            if order.order_type == OrderType.HL:
                print(f"Type: H&L PARTNERS")
                print(f"Customer: {order.customer_name}")
                print()
                
                try:
                    from browser_automation.hl_automation import gather_hl_inputs
                    
                    hl_inputs = gather_hl_inputs(str(order.pdf_path))
                    
                    if not hl_inputs:
                        print("\n[CANCELLED] H&L Partners input gathering cancelled")
                        continue
                    
                    order_with_input = order.with_input(hl_inputs)
                    orders_with_input.append(order_with_input)
                    
                except ImportError as e:
                    print(f"\n[ERROR] Could not import H&L Partners automation: {e}")
                    print("[INFO] Falling back to runtime input gathering")
                    orders_with_input.append(order)
                
                continue
            
            # IGRAPHIX: Gather inputs upfront (before browser session)
            if order.order_type == OrderType.IGRAPHIX:
                print(f"Type: IGRAPHIX")
                print(f"Customer: {order.customer_name}")
                print()
                
                try:
                    from browser_automation.igraphix_automation import gather_igraphix_inputs
                    
                    igraphix_inputs = gather_igraphix_inputs(str(order.pdf_path))
                    
                    if not igraphix_inputs:
                        print("\n[CANCELLED] iGraphix input gathering cancelled")
                        continue
                    
                    order_with_input = order.with_input(igraphix_inputs)
                    orders_with_input.append(order_with_input)
                    
                except ImportError as e:
                    print(f"\n[ERROR] Could not import iGraphix automation: {e}")
                    print("[INFO] Falling back to runtime input gathering")
                    orders_with_input.append(order)
                
                continue
            
            # Collect input for other order types
            order_input = self._input_collector.collect_order_input(order)
            
            # Add input to order
            order_with_input = order.with_input(order_input)
            orders_with_input.append(order_with_input)
        
        # Now process all orders using batch processing
        # (TCAA orders from same PDF will be grouped and processed together)
        print(f"\n{'='*70}")
        print("STARTING ORDER PROCESSING")
        print(f"{'='*70}\n")
        
        try:
            results = self._processing_service.process_orders_batch(orders_with_input)
            
            # Show results
            for result in results:
                print(self._result_formatter.format_processing_result(result))
                
        except Exception as e:
            print(f"\n[ERROR] Batch processing failed: {e}")
            import traceback
            traceback.print_exc()
            
            # Create error results for all orders
            from domain.entities import ProcessingResult
            results = [
                ProcessingResult(
                    success=False,
                    order_type=order.order_type,
                    contracts=[],
                    error_message=str(e)
                )
                for order in orders_with_input
            ]
        
        return results
    
    def _process_orders_batch(
        self,
        orders: list[Order],
        inputs: dict[str, any]
    ) -> list[ProcessingResult]:
        """
        Process orders in batch mode (all inputs collected upfront).
        
        Args:
            orders: Orders to process
            inputs: Dictionary mapping order name to input
            
        Returns:
            List of processing results
        """
        results = []
        
        print("\n" + "=" * 70)
        print("PROCESSING ORDERS")
        print("=" * 70)
        
        for i, order in enumerate(orders, 1):
            # Show progress
            progress = self._progress_formatter.format_progress(
                i, len(orders),
                order.get_display_name()
            )
            print(f"\n{progress}")
            
            # Get input for this order
            order_input = inputs.get(order.get_display_name())
            
            if not order_input:
                print(f"[WARNING] No input found for {order.get_display_name()}, skipping")
                continue
            
            # Add input to order
            order = order.with_input(order_input)
            
            # Process order
            try:
                result = self._processing_service.process_order(order)
                results.append(result)
                print(f"  ✓ Completed")
                
            except Exception as e:
                print(f"  ✗ Failed: {e}")
                from domain.entities import ProcessingResult
                error_result = ProcessingResult(
                    success=False,
                    order_type=order.order_type,
                    contracts=[],
                    error_message=str(e)
                )
                results.append(error_result)
        
        return results
    
    def _process_orders_auto(self, orders: list[Order]) -> list[ProcessingResult]:
        """
        Process orders automatically (no user interaction).
        
        Args:
            orders: Orders to process
            
        Returns:
            List of processing results
        """
        results = []
        
        print("\n" + "=" * 70)
        print("PROCESSING ORDERS")
        print("=" * 70)
        
        for i, order in enumerate(orders, 1):
            # Show progress
            progress = self._progress_formatter.format_progress(
                i, len(orders),
                order.get_display_name()
            )
            print(f"\n{progress}")
            
            # Process order (no user input required for auto mode)
            try:
                result = self._processing_service.process_order(order)
                results.append(result)
                print(f"  ✓ Completed")
                
            except Exception as e:
                print(f"  ✗ Failed: {e}")
                from domain.entities import ProcessingResult
                error_result = ProcessingResult(
                    success=False,
                    order_type=order.order_type,
                    contracts=[],
                    error_message=str(e)
                )
                results.append(error_result)
        
        return results
    
    def _move_processed_files(self, results: list[ProcessingResult]) -> None:
        """
        Move processed files to appropriate directories.
        
        Successful: incoming → processed
        Failed: incoming → error
        
        Args:
            results: Processing results to determine file movements
        """
        print("\n" + "=" * 70)
        print("ORGANIZING FILES")
        print("=" * 70)
        
        for result in results:
            # Note: In a real implementation, you'd need to track which
            # result corresponds to which file. For now, this is a placeholder.
            pass
        
        print("\n[OK] Files organized")


def create_orchestrator(config: ApplicationConfig | None = None) -> ApplicationOrchestrator:
    """
    Factory function to create a fully configured orchestrator.
    
    Args:
        config: Application configuration (uses defaults if None)
        
    Returns:
        Configured ApplicationOrchestrator instance
    """
    # Use default config if not provided
    if config is None:
        config = ApplicationConfig.from_defaults()
    
    # Ensure directories exist
    config.ensure_directories()
    
    # Create services
    from business_logic.services.pdf_order_detector import PDFOrderDetector
    from business_logic.services.order_processing_service import create_processing_service
    from data_access.repositories.customer_repository import create_customer_repository
    
    detection_service = PDFOrderDetector()
    customer_repository = create_customer_repository(config.customer_db_path)
    processing_service = create_processing_service(customer_repository)
    
    # Create orchestrator
    return ApplicationOrchestrator(
        config=config,
        detection_service=detection_service,
        customer_repository=customer_repository,
        processing_service=processing_service
    )
