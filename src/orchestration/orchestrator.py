"""
Application Orchestrator - Main coordinator for the order processing application.

This is the top-level component that ties together all layers:
- Domain models
- Detection service
- Customer repository
- Processing service
- Presentation layer
"""

import importlib
import sys
from pathlib import Path

# Add src to path
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from business_logic.services.order_processing_service import OrderProcessingService
from business_logic.services.pdf_order_detector import PDFOrderDetector
from data_access.repositories.customer_repository import CustomerRepository
from domain.entities import Order, ProcessingResult
from domain.enums import OrderType
from orchestration.config import ApplicationConfig
from orchestration.order_scanner import OrderScanner
from presentation.cli import BatchInputCollector, InputCollector
from presentation.formatters import OrderFormatter, ProcessingResultFormatter, ProgressFormatter

# Registry mapping OrderType → (module_path, function_name, display_name)
# for agencies that gather inputs upfront before the browser session opens.
_INPUT_GATHERERS: dict[OrderType, tuple[str, str, str]] = {
    OrderType.SAGENT:    ("browser_automation.sagent_automation",   "gather_sagent_inputs_from_pdf", "SAGENT"),
    OrderType.DAVISELEN: ("browser_automation.daviselen_automation", "gather_daviselen_inputs",       "DAVISELEN"),
    OrderType.ADMERASIA: ("browser_automation.admerasia_automation", "gather_admerasia_inputs",       "ADMERASIA"),
    OrderType.HL:        ("browser_automation.hl_automation",        "gather_hl_inputs",              "H&L PARTNERS"),
    OrderType.IGRAPHIX:  ("browser_automation.igraphix_automation",  "gather_igraphix_inputs",        "IGRAPHIX"),
    OrderType.IMPACT:    ("browser_automation.impact_automation",    "gather_impact_inputs",          "IMPACT"),
    OrderType.RPM:       ("browser_automation.rpm_automation",       "gather_rpm_inputs",             "RPM"),
    OrderType.WORLDLINK: ("browser_automation.worldlink_automation", "gather_worldlink_inputs",       "WORLDLINK"),
    OrderType.XML:       ("browser_automation.xml_automation",       "gather_xml_inputs_from_path",   "XML (AAAA SpotTV)"),
    OrderType.LEXUS:     ("browser_automation.lexus_automation",     "gather_lexus_inputs",           "IW Group / Lexus"),
}


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

    def _process_orders_interactive(self, orders: list[Order]) -> list[ProcessingResult]:
        """
        Process orders interactively with batch grouping for TCAA orders from same PDF.

        Args:
            orders: Orders to process

        Returns:
            List of processing results
        """
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

            display_name = _INPUT_GATHERERS.get(order.order_type, (None, None, order.order_type.name))[2]
            print(f"Type: {display_name}")
            print(f"Customer: {order.customer_name}")
            print()

            # CHARMAINE: handles all input internally — no pre-gathering needed
            if order.order_type == OrderType.CHARMAINE:
                orders_with_input.append(order)
                continue

            # Registry-driven agencies: dynamic import + gather inputs upfront
            if order.order_type in _INPUT_GATHERERS:
                module_path, fn_name, display_name = _INPUT_GATHERERS[order.order_type]
                try:
                    module = importlib.import_module(module_path)
                    inputs = getattr(module, fn_name)(str(order.pdf_path))
                    if not inputs:
                        print(f"\n[CANCELLED] {display_name} input gathering cancelled")
                        continue
                    inputs = self._confirm_separation(inputs)
                    orders_with_input.append(order.with_input(inputs))
                except ImportError as e:
                    print(f"\n[ERROR] Could not import {display_name} automation: {e}")
                    print("[INFO] Falling back to runtime input gathering")
                    orders_with_input.append(order)
                continue

            # All other types: generic input collector (TCAA, MISFIT, OPAD, etc.)
            order_input = self._input_collector.collect_order_input(order)
            orders_with_input.append(order.with_input(order_input))

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

    def _confirm_separation(self, inputs: dict) -> dict:
        """
        Show the separation that will be applied and let the user edit it.

        Called after every agency's gather-inputs phase so the user always
        has a chance to override the stored default before automation runs.
        """
        if 'separation' not in inputs:
            return inputs

        sep = inputs['separation']
        print(f"\n[SEPARATION] Will apply  Customer={sep[0]}, Event={sep[1]}, Order={sep[2]}")
        response = input(
            "  Press Enter to confirm, or type new values (e.g. 25,0,15): "
        ).strip()

        if not response:
            return inputs

        try:
            parts = [int(x.strip()) for x in response.replace(' ', '').split(',')]
            if len(parts) == 3:
                print(f"  ✓ Updated to  Customer={parts[0]}, Event={parts[1]}, Order={parts[2]}")
                return {**inputs, 'separation': tuple(parts)}
            else:
                print("  ⚠ Expected 3 comma-separated values — keeping original")
        except ValueError:
            print("  ⚠ Invalid format — keeping original")

        return inputs

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
                print("  ✓ Completed")

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
                print("  ✓ Completed")

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
    from business_logic.services.order_processing_service import create_processing_service
    from business_logic.services.pdf_order_detector import PDFOrderDetector
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
