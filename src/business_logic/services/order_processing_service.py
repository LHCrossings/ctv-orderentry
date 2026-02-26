"""
Order Processing Service - Orchestrates order processing workflow.

This service coordinates between detection, parsing, customer matching,
and browser automation to process orders into Etere contracts.
"""

import shutil
import sys
from pathlib import Path
from typing import Any, Protocol

# Add src to path for imports
_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

# Add browser_automation to path
_browser_automation_path = _src_path.parent / "browser_automation"
if str(_browser_automation_path) not in sys.path:
    sys.path.insert(0, str(_browser_automation_path))

from domain.entities import Contract, Order, ProcessingResult
from domain.enums import OrderType
from domain.value_objects import OrderInput


class OrderProcessor(Protocol):
    """
    Protocol defining the interface for order processors.

    Each agency type (WorldLink, TCAA, etc.) implements this protocol
    to handle its specific processing logic.
    """

    def process(
        self,
        browser_session: Any,  # BrowserSession type
        pdf_path: Path,
        order_input: OrderInput | None
    ) -> ProcessingResult:
        """
        Process an order PDF into Etere contracts.

        Args:
            browser_session: Active browser session with Etere
            pdf_path: Path to order PDF file
            order_input: Optional pre-gathered user inputs

        Returns:
            ProcessingResult with success status and contracts created
        """
        ...


class OrderProcessingService:
    """
    Service for processing orders through the complete workflow.

    This service orchestrates the entire processing pipeline:
    1. Order detection
    2. File management (move to processing folder)
    3. Routing to appropriate processor
    4. Error handling and recovery
    5. File cleanup (move to completed/failed)
    """

    _PROCESSOR_DISPATCH: dict[OrderType, str] = {
        OrderType.TCAA:      "_process_tcaa_order",
        OrderType.MISFIT:    "_process_misfit_order",
        OrderType.DAVISELEN: "_process_daviselen_order",
        OrderType.SAGENT:    "_process_sagent_order",
        OrderType.GALEFORCE: "_process_galeforce_order",
        OrderType.CHARMAINE: "_process_charmaine_order",
        OrderType.ADMERASIA: "_process_admerasia_order",
        OrderType.HL:        "_process_hl_order",
        OrderType.OPAD:      "_process_opad_order",
        OrderType.IGRAPHIX:  "_process_igraphix_order",
        OrderType.IMPACT:    "_process_impact_order",
        OrderType.RPM:              "_process_rpm_order",
        OrderType.WORLDLINK:        "_process_worldlink_order",
        OrderType.SACCOUNTYVOTERS:  "_process_saccountyvoters_order",
    }

    def __init__(
        self,
        processors: dict[OrderType, OrderProcessor],
        orders_dir: Path | None = None
    ):
        """
        Initialize service with processor registry.

        Args:
            processors: Map of OrderType to processor implementations
            orders_dir: Base directory for order files (default: orders/)
        """
        self._processors = processors
        self._orders_dir = Path(orders_dir) if orders_dir else Path("orders")

        # TCAA processor components (lazy loaded)
        self._tcaa_processor = None

        # Misfit processor components (lazy loaded)
        self._misfit_processor = None

        # Charmaine processor components (lazy loaded)
        self._charmaine_processor = None

        # Ensure directory structure exists
        self._setup_directories()

    def _setup_directories(self) -> None:
        """Create orders directory structure if it doesn't exist."""
        (self._orders_dir / "incoming").mkdir(parents=True, exist_ok=True)
        (self._orders_dir / "processing").mkdir(parents=True, exist_ok=True)
        (self._orders_dir / "completed").mkdir(parents=True, exist_ok=True)
        (self._orders_dir / "failed").mkdir(parents=True, exist_ok=True)

    def process_orders_batch(
        self,
        orders: list[Order],
        browser_session: Any = None
    ) -> list[ProcessingResult]:
        """
        UNIVERSAL BATCH PROCESSING: Process multiple orders with SINGLE shared browser session.

        This works automatically for:
        - TCAA, Misfit, WorldLink, opAD, RPM, H&L, Daviselen, iGraphix, Admerasia, Impact
        - ANY combination of order types in the same batch
        - ALL current and future agencies

        User logs in ONCE, all orders process, then browser closes.

        Args:
            orders: List of orders to process
            browser_session: Optional pre-existing browser session

        Returns:
            List of ProcessingResults for each order
        """
        # If no session provided and we have processable orders, create ONE shared session
        if browser_session is None and orders:
            # Check if any orders need browser automation
            needs_browser = any(
                order.order_type in [
                    OrderType.TCAA, OrderType.MISFIT, OrderType.WORLDLINK,
                    OrderType.DAVISELEN, OrderType.SAGENT, OrderType.GALEFORCE,
                    OrderType.CHARMAINE, OrderType.ADMERASIA,
                    OrderType.OPAD, OrderType.HL, OrderType.IGRAPHIX,
                    OrderType.IMPACT, OrderType.RPM,
                    OrderType.SACCOUNTYVOTERS,
                ]
                for order in orders
            )

            if needs_browser:
                try:
                    from etere_session import EtereSession
                except ImportError:
                    print("[ERROR] Could not import EtereSession")
                    return self._process_orders_fallback(orders)

                # Create ONE session for entire batch
                print(f"\n{'='*70}")
                print(f"BATCH SESSION: {len(orders)} order(s) - SINGLE BROWSER")
                print(f"{'='*70}")
                print("✓ All orders will share the same browser session")
                print("✓ You only need to log in ONCE")
                print("✓ Browser will stay open until all orders complete")
                print(f"{'='*70}\n")

                with EtereSession() as shared_session:
                    # Set master market ONCE for the entire batch.
                    # All Crossings TV agencies use NYC. The individual contract
                    # lines set their own market (LAX, SEA, WDC, etc.) — master
                    # market only affects the top-level session context.
                    print("[SESSION] Setting master market to NYC...")
                    shared_session.set_market("NYC")
                    print("[SESSION] \u2713 Master market set \u2014 beginning batch\n")
                    return self._process_orders_with_session(orders, shared_session)

        # Session provided or no orders need browser
        return self._process_orders_with_session(orders, browser_session)

    def _process_orders_with_session(
        self,
        orders: list[Order],
        shared_session: any
    ) -> list[ProcessingResult]:
        """
        UNIVERSAL: Process orders with shared session (works for ALL agencies).

        Args:
            orders: Orders to process
            shared_session: Shared browser session (or None for no-browser orders)

        Returns:
            List of ProcessingResults
        """
        results = []

        # Group TCAA orders by PDF (for multi-estimate batch processing)
        tcaa_groups = {}
        other_orders = []

        for order in orders:
            if order.order_type == OrderType.TCAA:
                pdf_key = str(order.pdf_path)
                if pdf_key not in tcaa_groups:
                    tcaa_groups[pdf_key] = []
                tcaa_groups[pdf_key].append(order)
            else:
                other_orders.append(order)

        # Process TCAA groups (multiple estimates from same PDF together)
        for pdf_path, tcaa_orders in tcaa_groups.items():
            if len(tcaa_orders) > 1:
                print(f"\n{'='*70}")
                print(f"TCAA BATCH: {len(tcaa_orders)} estimates from same PDF")
                print(f"{'='*70}")
                for order in tcaa_orders:
                    print(f"  - Estimate {order.estimate_number}")
                print()

                result = self._process_tcaa_orders_batch(tcaa_orders, shared_session)
                results.append(result)
            else:
                result = self._process_single_order(tcaa_orders[0], shared_session)
                results.append(result)

        # Process all other orders (Misfit, WorldLink, opAD, etc.) with shared session
        for order in other_orders:
            result = self._process_single_order(order, shared_session)
            results.append(result)

        return results

    def _process_single_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        UNIVERSAL: Process single order with optional shared session.

        Automatically routes to correct processor based on order type.
        Works for TCAA, Misfit, WorldLink, opAD, RPM, etc.

        Args:
            order: Order to process
            shared_session: Optional shared browser session

        Returns:
            ProcessingResult
        """
        method_name = self._PROCESSOR_DISPATCH.get(order.order_type)
        if method_name:
            return getattr(self, method_name)(order, shared_session)
        return self.process_order(order, shared_session)  # RPM + future agencies

    def _process_orders_fallback(self, orders: list[Order]) -> list[ProcessingResult]:
        """
        Fallback: Process without shared session (backward compatible).

        Args:
            orders: Orders to process

        Returns:
            List of ProcessingResults
        """
        results = []

        # Group TCAA orders by PDF path
        tcaa_groups = {}
        non_tcaa_orders = []

        for order in orders:
            if order.order_type == OrderType.TCAA:
                pdf_key = str(order.pdf_path)
                if pdf_key not in tcaa_groups:
                    tcaa_groups[pdf_key] = []
                tcaa_groups[pdf_key].append(order)
            else:
                non_tcaa_orders.append(order)

        # Process TCAA groups (multiple estimates from same PDF together)
        for pdf_path, tcaa_orders in tcaa_groups.items():
            if len(tcaa_orders) > 1:
                # Multiple TCAA orders from same PDF - batch process
                print(f"\n{'='*70}")
                print(f"BATCH PROCESSING: {len(tcaa_orders)} TCAA orders from same PDF")
                print(f"{'='*70}")
                for order in tcaa_orders:
                    print(f"  - Estimate {order.estimate_number}")
                print()

                # Process all estimates together
                result = self._process_tcaa_orders_batch(tcaa_orders, None)
                results.append(result)
            else:
                result = self.process_order(tcaa_orders[0], None)
                results.append(result)

        # Process non-TCAA orders
        for order in non_tcaa_orders:
            result = self.process_order(order, None)
            results.append(result)

        return results

    def _process_tcaa_orders_batch(self, orders: list[Order], shared_session: Any = None) -> ProcessingResult:
        """
        Process multiple TCAA orders from same PDF together.

        Args:
            orders: List of TCAA orders from same PDF

        Returns:
            ProcessingResult combining all contracts created
        """
        try:
            # Lazy load TCAA processor
            if self._tcaa_processor is None:
                from etere_session import EtereSession
                from parsers.tcaa_parser import parse_tcaa_pdf
                from tcaa_automation import process_tcaa_order

                self._tcaa_processor = {
                    'process': process_tcaa_order,
                    'session_class': EtereSession,
                    'parser': parse_tcaa_pdf
                }

            # Use first order for display info
            first_order = orders[0]

            print(f"\n{'='*70}")
            print("TCAA BROWSER AUTOMATION (BATCH MODE)")
            print(f"{'='*70}")
            print(f"PDF: {first_order.pdf_path.name}")
            print(f"Estimates: {', '.join(o.estimate_number for o in orders)}")
            print(f"Customer: {first_order.customer_name}")
            print(f"{'='*70}\n")

            # Start browser session
            with self._tcaa_processor['session_class']() as session:
                # Set master market to NYC
                session.set_market("NYC")

                # Process all estimates together (pass None for estimate_number)
                # This triggers batch mode in tcaa_automation
                success = self._tcaa_processor['process'](
                    driver=session.driver,
                    pdf_path=str(first_order.pdf_path),
                    estimate_number=None  # Process ALL estimates in batch
                )

                if success:
                    # Create contracts for all processed estimates
                    contracts = [
                        Contract(
                            contract_number=f"TCAA-{order.estimate_number}",
                            order_type=OrderType.TCAA
                        )
                        for order in orders
                    ]

                    print(f"\n✓ Successfully created {len(contracts)} contracts")

                    return ProcessingResult(
                        success=True,
                        contracts=contracts,
                        order_type=OrderType.TCAA
                    )
                else:
                    return ProcessingResult(
                        success=False,
                        contracts=[],
                        order_type=OrderType.TCAA,
                        error_message="TCAA batch processing failed"
                    )

        except Exception as e:
            import traceback
            error_msg = f"TCAA batch processing error: {str(e)}"
            print(f"✗ {error_msg}")
            traceback.print_exc()

            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.TCAA,
                error_message=error_msg
            )

    def process_order(
        self,
        order: Order,
        browser_session: Any = None,  # BrowserSession type, optional
        order_input: OrderInput | None = None
    ) -> ProcessingResult:
        """
        Process a single order through the complete workflow.

        Args:
            order: Order to process
            browser_session: Optional browser session (None returns stub result)
            order_input: Optional pre-collected input for the order

        Returns:
            ProcessingResult with success/failure status
        """
        # If no browser session, check if we can auto-process TCAA or Misfit
        if browser_session is None:
            # Check if this is a TCAA order that we can auto-process
            if order.order_type == OrderType.TCAA:
                return self._process_tcaa_order(order)

            # Check if this is a Misfit order that we can auto-process
            if order.order_type == OrderType.MISFIT:
                return self._process_misfit_order(order)

            # Check if this is a Charmaine order that we can auto-process
            if order.order_type == OrderType.CHARMAINE:
                return self._process_charmaine_order(order)

            # For other types, return stub result
            return self._create_stub_result(order)

        # Continue with normal processing if browser_session provided
        print("\n" + "="*70)
        print(f"PROCESSING: {order.get_display_name()}")
        print("="*70)
        print(f"[DETECT] Order type: {order.order_type.name}")

        # Check if order is processable
        if not order.is_processable():
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=order.order_type,
                error_message="Order is not in processable state"
            )

        # Move to processing folder
        processing_path = self._move_to_processing(order.pdf_path)
        if not processing_path:
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=order.order_type,
                error_message="Failed to move file to processing folder"
            )

        try:
            # Get processor for this order type
            processor = self._processors.get(order.order_type)

            if not processor:
                error_msg = f"No processor registered for {order.order_type.name}"
                print(f"[ERROR] {error_msg}")
                print("  Please process manually or add processor for this type")
                self._move_to_failed(processing_path)
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=order.order_type,
                    error_message=error_msg
                )

            # Process the order
            result = processor.process(browser_session, processing_path, order_input)

            # Move to appropriate folder based on result
            if result.success:
                self._move_to_completed(processing_path)
            else:
                self._move_to_failed(processing_path)

            return result

        except Exception as e:
            print(f"[ERROR] Exception during processing: {e}")
            import traceback
            traceback.print_exc()

            self._move_to_failed(processing_path)

            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=order.order_type,
                error_message=str(e)
            )

    def _create_stub_result(self, order: Order) -> ProcessingResult:
        """
        Create stub result for orders without browser automation.

        Args:
            order: Order that can't be auto-processed

        Returns:
            ProcessingResult with manual processing instructions
        """
        message = (
            "Browser automation not implemented - manual processing required.\n"
            f"  Order Type: {order.order_type.name}\n"
            f"  Customer: {order.customer_name}\n"
        )

        if order.order_input:
            message += (
                f"  Order Code: {order.order_input.order_code}\n"
                f"  Description: {order.order_input.description}\n"
            )

        message += (
            "\nTo process this order:\n"
            "  1. Open Etere manually\n"
            "  2. Process the order using the information above\n"
            "  3. Browser automation will be added in a future update"
        )

        return ProcessingResult(
            success=False,
            order_type=order.order_type,
            contracts=[],
            error_message=message
        )

    def _run_tcaa_with_driver(self, order: Order, driver: Any) -> ProcessingResult:
        """Call TCAA processor with an already-open driver and build ProcessingResult."""
        success = self._tcaa_processor['process'](
            driver=driver,
            pdf_path=str(order.pdf_path),
            estimate_number=order.estimate_number,
            order_code=order.order_input.order_code if order.order_input else None,
            description=order.order_input.description if order.order_input else None
        )
        if success:
            contract = Contract(
                contract_number=f"TCAA-{order.estimate_number}",
                order_type=OrderType.TCAA
            )
            print(f"\n✓ Successfully created contract for estimate {order.estimate_number}")
            return ProcessingResult(
                success=True, contracts=[contract], order_type=OrderType.TCAA, error_message=None
            )
        return ProcessingResult(
            success=False, contracts=[], order_type=OrderType.TCAA,
            error_message="TCAA processing failed - check browser output for details"
        )

    def _process_tcaa_order(self, order: Order, shared_session: Any = None) -> ProcessingResult:
        """
        UNIVERSAL: Process TCAA order with optional shared browser session.

        Args:
            order: TCAA order to process
            shared_session: Optional shared EtereSession (for batch processing)

        Returns:
            ProcessingResult with contracts created or error
        """
        try:
            # Lazy load TCAA processor components
            if self._tcaa_processor is None:
                from etere_session import EtereSession
                from parsers.tcaa_parser import parse_tcaa_pdf
                from tcaa_automation import process_tcaa_order

                self._tcaa_processor = {
                    'process': process_tcaa_order,
                    'session_class': EtereSession,
                    'parser': parse_tcaa_pdf
                }

            print(f"\n{'='*70}")
            print("TCAA BROWSER AUTOMATION")
            print(f"{'='*70}")
            print(f"Order: {order.get_display_name()}")
            print(f"Type: {order.order_type.name}")
            print(f"Customer: {order.customer_name}")

            if order.order_input:
                print(f"Code: {order.order_input.order_code}")
                print(f"Description: {order.order_input.description}")

            print(f"{'='*70}\n")

            if shared_session:
                print("[SESSION] ✓ Using shared browser session")
                return self._run_tcaa_with_driver(order, shared_session.driver)
            else:
                with self._tcaa_processor['session_class']() as session:
                    session.set_market("NYC")
                    return self._run_tcaa_with_driver(order, session.driver)

        except Exception as e:
            import traceback
            error_detail = f"TCAA processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ TCAA processing failed: {e}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.TCAA, error_message=error_detail
            )

    def _run_misfit_with_driver(self, order: Order, driver: Any) -> ProcessingResult:
        """Call Misfit processor with an already-open driver and build ProcessingResult."""
        success = self._misfit_processor['process'](
            driver=driver,
            pdf_path=str(order.pdf_path),
            order_code=order.order_input.order_code if order.order_input else None,
            description=order.order_input.description if order.order_input else None,
            customer_id=None
        )
        if success:
            parsed_order = self._misfit_processor['parser'](str(order.pdf_path))
            contract = Contract(
                contract_number=f"MISFIT-{parsed_order.date.replace('/', '')}",
                order_type=OrderType.MISFIT
            )
            print("\n✓ Successfully created Misfit contract")
            return ProcessingResult(
                success=True, contracts=[contract], order_type=OrderType.MISFIT, error_message=None
            )
        return ProcessingResult(
            success=False, contracts=[], order_type=OrderType.MISFIT,
            error_message="Misfit processing failed - check browser output for details"
        )

    def _process_misfit_order(self, order: Order, shared_session: Any = None) -> ProcessingResult:
        """
        UNIVERSAL: Process Misfit order with optional shared browser session.

        Misfit orders are multi-market (LAX, SFO, CVC) with:
        - Master market always NYC
        - Individual lines set their own market
        - No customer on PDF - uses universal detection

        Args:
            order: Misfit order to process
            shared_session: Optional shared EtereSession (for batch processing)

        Returns:
            ProcessingResult with contracts created or error
        """
        try:
            # Lazy load Misfit processor components
            if self._misfit_processor is None:
                from etere_session import EtereSession
                from misfit_automation import process_misfit_order
                from parsers.misfit_parser import parse_misfit_pdf

                self._misfit_processor = {
                    'process': process_misfit_order,
                    'session_class': EtereSession,
                    'parser': parse_misfit_pdf
                }

            print(f"\n{'='*70}")
            print("MISFIT BROWSER AUTOMATION")
            print(f"{'='*70}")
            print(f"Order: {order.get_display_name()}")
            print(f"Type: {order.order_type.name}")
            print(f"Customer: {order.customer_name}")

            if order.order_input:
                print(f"Code: {order.order_input.order_code}")
                print(f"Description: {order.order_input.description}")

            print(f"{'='*70}\n")

            if shared_session:
                print("[SESSION] ✓ Using shared browser session")
                return self._run_misfit_with_driver(order, shared_session.driver)
            else:
                with self._misfit_processor['session_class']() as session:
                    session.set_market("NYC")
                    return self._run_misfit_with_driver(order, session.driver)

        except Exception as e:
            import traceback
            error_detail = f"Misfit processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ Misfit processing failed: {e}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.MISFIT, error_message=error_detail
            )

    def _process_daviselen_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        Process Daviselen order using daviselen_automation.

        Daviselen orders have:
        - Customer lookup from database (4 known customers)
        - Smart contract code/description defaults
        - Single market per order
        - Universal agency billing
        - Master market: NYC (set by session before calling)

        Args:
            order: Daviselen order to process
            shared_session: Shared browser session (EtereSession)

        Returns:
            ProcessingResult with success status
        """
        try:
            # Import Daviselen automation
            from daviselen_automation import process_daviselen_order

            print(f"\n{'='*70}")
            print("PROCESSING DAVISELEN ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            # Daviselen REQUIRES a browser session (no standalone mode)
            if shared_session is None:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.DAVISELEN,
                    error_message="Browser session required for Daviselen orders"
                )

            # Get inputs from order (already collected by orchestrator)
            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.DAVISELEN,
                    error_message="Order inputs not collected"
                )

            print("[SESSION] ✓ Using shared browser session")
            # Market already set to NYC once at batch start

            # Process the order with pre-collected inputs (matching TCAA pattern)
            success = process_daviselen_order(
                driver=shared_session.driver,  # ← Pass driver, not session!
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            # Daviselen automation drives the browser directly and does not return
            # contract numbers — the session is one-directional (Python → browser).
            # Contract numbers can be retrieved from Etere manually if needed.
            contracts = []

            if success:
                print("\n✓ Daviselen order processed successfully")
            else:
                print("\n✗ Daviselen order processing failed")

            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.DAVISELEN,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"Daviselen processing error: {str(e)}\n{traceback.format_exc()}"

            print(f"\n✗ Daviselen processing failed: {e}")

            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.DAVISELEN,
                error_message=error_detail
            )

    def _run_sagent_with_driver(
        self, order: Order, driver: Any, session: Any, pre_gathered_inputs: Any, process_fn: Any
    ) -> ProcessingResult:
        """Call SAGENT processor with an already-open driver and build ProcessingResult."""
        success = process_fn(
            driver,
            str(order.pdf_path),
            shared_session=session,
            pre_gathered_inputs=pre_gathered_inputs
        )
        if success:
            print("\n✓ SAGENT order processed successfully")
            return ProcessingResult(success=True, contracts=[], order_type=OrderType.SAGENT)
        print("\n✗ SAGENT order processing failed")
        return ProcessingResult(
            success=False, contracts=[], order_type=OrderType.SAGENT,
            error_message="SAGENT processing failed - check browser output for details"
        )

    def _process_sagent_order(
        self,
        order: Order,
        shared_session: Any
    ) -> ProcessingResult:
        """
        Process SAGENT order using sagent_automation.

        SAGENT orders are multi-market (like Misfit) with:
        - Hardcoded customer (CAL FIRE, ID 175)
        - Rate grossing (net / 0.85)
        - Line numbers in descriptions
        - Master market: NYC

        Args:
            order: SAGENT order to process
            shared_session: Shared browser session (creates one if None)

        Returns:
            ProcessingResult with success status
        """
        try:
            from sagent_automation import process_sagent_order

            print(f"\n{'='*70}")
            print("PROCESSING SAGENT ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            pre_gathered_inputs = order.order_input if order.order_input else None

            if shared_session is None:
                try:
                    from etere_session import EtereSession
                except ImportError:
                    print("[ERROR] Could not import EtereSession")
                    return ProcessingResult(
                        success=False, contracts=[], order_type=OrderType.SAGENT,
                        error_message="EtereSession import failed"
                    )
                print("[SESSION] Creating browser session for SAGENT order...")
                with EtereSession() as session:
                    session.set_market("NYC")
                    print("[SESSION] Master market set to NYC")
                    return self._run_sagent_with_driver(
                        order, session.driver, session, pre_gathered_inputs, process_sagent_order
                    )

            if hasattr(shared_session, 'set_market'):
                print("[SESSION] ✓ Using shared browser session (market pre-set to NYC)")
            driver = shared_session.driver if hasattr(shared_session, 'driver') else shared_session
            return self._run_sagent_with_driver(
                order, driver, shared_session, pre_gathered_inputs, process_sagent_order
            )

        except Exception as e:
            import traceback
            error_detail = f"SAGENT processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ SAGENT processing failed: {e}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.SAGENT, error_message=error_detail
            )

    def _run_galeforce_with_driver(
        self, order: Order, driver: Any, session: Any, pre_gathered_inputs: Any, process_fn: Any
    ) -> ProcessingResult:
        """Call GaleForce processor with an already-open driver and build ProcessingResult."""
        success = process_fn(
            driver,
            str(order.pdf_path),
            shared_session=session,
            pre_gathered_inputs=pre_gathered_inputs,
        )
        if success:
            print("\n✓ GaleForce order processed successfully")
            return ProcessingResult(success=True, contracts=[], order_type=OrderType.GALEFORCE)
        print("\n✗ GaleForce order processing failed")
        return ProcessingResult(
            success=False, contracts=[], order_type=OrderType.GALEFORCE,
            error_message="GaleForce processing failed - check browser output for details",
        )

    def _process_galeforce_order(
        self,
        order: Order,
        shared_session: Any,
    ) -> ProcessingResult:
        """
        Process GaleForceMedia order using galeforce_automation.

        Single-market orders from agencies using the generic GaleForceMedia PDF
        system (e.g. BMO/PACO Collective). Master market is NYC.
        """
        try:
            from galeforce_automation import process_galeforce_order

            print(f"\n{'='*70}")
            print("PROCESSING GALEFORCE ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            pre_gathered_inputs = order.order_input if order.order_input else None

            if shared_session is None:
                try:
                    from etere_session import EtereSession
                except ImportError:
                    print("[ERROR] Could not import EtereSession")
                    return ProcessingResult(
                        success=False, contracts=[], order_type=OrderType.GALEFORCE,
                        error_message="EtereSession import failed",
                    )
                print("[SESSION] Creating browser session for GaleForce order...")
                with EtereSession() as session:
                    session.set_market("NYC")
                    print("[SESSION] Master market set to NYC")
                    return self._run_galeforce_with_driver(
                        order, session.driver, session, pre_gathered_inputs, process_galeforce_order
                    )

            if hasattr(shared_session, 'set_market'):
                print("[SESSION] ✓ Using shared browser session (market pre-set to NYC)")
            driver = shared_session.driver if hasattr(shared_session, 'driver') else shared_session
            return self._run_galeforce_with_driver(
                order, driver, shared_session, pre_gathered_inputs, process_galeforce_order
            )

        except Exception as exc:
            import traceback
            error_detail = f"GaleForce processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ GaleForce processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.GALEFORCE,
                error_message=error_detail,
            )

    def _run_charmaine_with_driver(self, order: Order, driver: Any, process_fn: Any) -> ProcessingResult:
        """Call CHARMAINE processor with an already-open driver and build ProcessingResult."""
        from etere_client import EtereClient
        etere_client = EtereClient(driver)
        success = process_fn(str(order.pdf_path), shared_session=etere_client)
        if success:
            print("\n✓ CHARMAINE order processed successfully")
            return ProcessingResult(success=True, contracts=[], order_type=OrderType.CHARMAINE)
        print("\n✗ CHARMAINE order processing failed")
        return ProcessingResult(
            success=False, contracts=[], order_type=OrderType.CHARMAINE,
            error_message="CHARMAINE processing failed - check browser output"
        )

    def _process_charmaine_order(
        self,
        order: Order,
        shared_session: Any = None
    ) -> ProcessingResult:
        """
        Process Charmaine order using charmaine_automation.

        Charmaine orders are generic client template orders with:
        - Single market per order (detected from PDF)
        - Agency vs Client billing detection
        - Customer DB integration (self-learning)
        - Weekly line entry (each week = separate Etere line)
        - Master market: NYC

        Args:
            order: CHARMAINE order to process
            shared_session: Shared browser session (creates one if None)

        Returns:
            ProcessingResult with success status
        """
        try:
            from charmaine_automation import process_charmaine_order

            print(f"\n{'='*70}")
            print("PROCESSING CHARMAINE ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            if shared_session is None:
                try:
                    from etere_session import EtereSession
                except ImportError:
                    print("[ERROR] Could not import EtereSession")
                    return ProcessingResult(
                        success=False, contracts=[], order_type=OrderType.CHARMAINE,
                        error_message="EtereSession import failed"
                    )
                print("[SESSION] Creating browser session for CHARMAINE order...")
                with EtereSession() as session:
                    session.set_market("NYC")
                    print("[SESSION] Master market set to NYC")
                    return self._run_charmaine_with_driver(order, session.driver, process_charmaine_order)

            if hasattr(shared_session, 'set_market'):
                print("[SESSION] ✓ Using shared browser session (market pre-set to NYC)")
            driver = shared_session.driver if hasattr(shared_session, 'driver') else shared_session
            return self._run_charmaine_with_driver(order, driver, process_charmaine_order)

        except Exception as e:
            import traceback
            error_detail = f"CHARMAINE processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ CHARMAINE processing failed: {e}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.CHARMAINE, error_message=error_detail
            )

    def _process_admerasia_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        Process Admerasia order using admerasia_automation.

        Admerasia orders have:
        - McDonald's as sole customer (ID: 42)
        - Single market per order (detected from DMA field)
        - NET rates grossed up by /0.85 (parser handles this)
        - Daily spot calendar grid analysis (parser handles this)
        - Separation: customer=3, event=0, order=5
        - Universal agency billing
        - Master market: NYC (set by session before calling)

        Args:
            order: Admerasia order to process
            shared_session: Shared browser session (EtereSession)

        Returns:
            ProcessingResult with success status
        """
        try:
            # Import Admerasia automation
            from admerasia_automation import process_admerasia_order

            print(f"\n{'='*70}")
            print("PROCESSING ADMERASIA ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            # Admerasia REQUIRES a browser session
            if shared_session is None:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.ADMERASIA,
                    error_message="Browser session required for Admerasia orders"
                )

            # Get inputs from order (already collected by orchestrator)
            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.ADMERASIA,
                    error_message="Order inputs not collected"
                )

            print("[SESSION] ✓ Using shared browser session")
            # Market already set to NYC once at batch start

            # Process the order with pre-collected inputs (matching Daviselen pattern)
            success = process_admerasia_order(
                driver=shared_session.driver,  # ← Pass driver, not session!
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            contracts = []

            if success:
                print("\n✓ Admerasia order processed successfully")
            else:
                print("\n✗ Admerasia order processing failed")

            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.ADMERASIA,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"Admerasia processing error: {str(e)}\n{traceback.format_exc()}"

            print(f"\n✗ Admerasia processing failed: {e}")

            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.ADMERASIA,
                error_message=error_detail
            )


    def _process_hl_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        Process H&L Partners order using hl_automation.

        H&L Partners orders have:
        - SFO or CVC markets only (detected from PDF header)
        - Multiple clients possible → customer DB lookup with manual fallback
        - Separation: customer=25, event=0, order=0
        - Universal agency billing
        - Multiple estimates per PDF → each becomes a separate contract
        - Master market: NYC (set by session before calling)

        Args:
            order: H&L Partners order to process
            shared_session: Shared browser session (EtereSession)

        Returns:
            ProcessingResult with success status
        """
        try:
            from browser_automation.hl_automation import process_hl_order

            print(f"\n{'='*70}")
            print("PROCESSING H&L PARTNERS ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            if shared_session is None:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.HL,
                    error_message="Browser session required for H&L Partners orders"
                )

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.HL,
                    error_message="Order inputs not collected"
                )

            print("[SESSION] ✓ Using shared browser session")
            # Market already set to NYC once at batch start

            success = process_hl_order(
                driver=shared_session.driver,
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            contracts = []

            if success:
                print("\n✓ H&L Partners order processed successfully")
            else:
                print("\n✗ H&L Partners order processing failed")

            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.HL,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"H&L Partners processing error: {str(e)}\n{traceback.format_exc()}"

            print(f"\n✗ H&L Partners processing failed: {e}")

            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.HL,
                error_message=error_detail
            )

    def _process_igraphix_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        Process iGraphix order using igraphix_automation.

        iGraphix orders have:
        - Two known clients: Pechanga Resort Casino (LAX) and Sky River Casino (SFO/CVC)
        - Customer IDs hardcoded in parser (26 and 191) with DB self-learning
        - Rate grossing: net / 0.85
        - Paid/bonus split by top-to-bottom allocation across ad codes
        - One Etere line per ad code entry (after split)
        - Language-specific separation intervals
        - Universal agency billing
        - Master market: NYC (set by session before calling)

        Args:
            order: iGraphix order to process
            shared_session: Shared browser session (EtereSession)

        Returns:
            ProcessingResult with success status
        """
        try:
            from igraphix_automation import process_igraphix_order

            print(f"\n{'='*70}")
            print("PROCESSING IGRAPHIX ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            # iGraphix REQUIRES a browser session
            if shared_session is None:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.IGRAPHIX,
                    error_message="Browser session required for iGraphix orders"
                )

            # Get inputs from order (already collected by orchestrator)
            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.IGRAPHIX,
                    error_message="Order inputs not collected"
                )

            print("[SESSION] ✓ Using shared browser session")
            # Market already set to NYC once at batch start

            # Process the order with pre-collected inputs (matching Daviselen pattern)
            success = process_igraphix_order(
                driver=shared_session.driver,  # ← Pass driver, not session!
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            contracts = []

            if success:
                print("\n✓ iGraphix order processed successfully")
            else:
                print("\n✗ iGraphix order processing failed")

            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.IGRAPHIX,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"iGraphix processing error: {str(e)}\n{traceback.format_exc()}"

            print(f"\n✗ iGraphix processing failed: {e}")

            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.IGRAPHIX,
                error_message=error_detail
            )

    def _process_impact_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        Process Impact Marketing order using impact_automation.

        Impact orders have:
        - Big Valley Ford as sole client (Customer ID: 252)
        - CVC market only (Central Valley)
        - One PDF = up to 4 quarters = up to 4 separate Etere contracts
        - Quarterly line splitting (different spot counts per week = split lines)
        - Bookend option (:15 spots placed first/last in break)
        - Separation: customer=15, event=0, order=0
        - Universal agency billing
        - Master market: NYC (set by session before calling)

        Args:
            order: Impact order to process
            shared_session: Shared browser session (EtereSession)

        Returns:
            ProcessingResult with success status
        """
        try:
            from browser_automation.impact_automation import process_impact_order

            print(f"\n{'='*70}")
            print("PROCESSING IMPACT MARKETING ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            # Impact REQUIRES a browser session
            if shared_session is None:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.IMPACT,
                    error_message="Browser session required for Impact Marketing orders"
                )

            # Get inputs from order (already collected by orchestrator)
            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.IMPACT,
                    error_message="Order inputs not collected"
                )

            print("[SESSION] ✓ Using shared browser session")
            # Market already set to NYC once at batch start

            # Process the order with pre-collected inputs (matching iGraphix pattern)
            success = process_impact_order(
                driver=shared_session.driver,  # ← Pass driver, not session!
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            contracts = []

            if success:
                print("\n✓ Impact Marketing order processed successfully")
            else:
                print("\n✗ Impact Marketing order processing failed")

            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.IMPACT,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"Impact Marketing processing error: {str(e)}\n{traceback.format_exc()}"

            print(f"\n✗ Impact Marketing processing failed: {e}")

            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.IMPACT,
                error_message=error_detail
            )

    def _process_opad_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        Process opAD order using opad_automation.

        opAD orders have:
        - NYC market ONLY (always)
        - Multiple clients possible → customer DB lookup with manual fallback
        - Separation intervals: Customer=15, Event=0, Order=15
        - Universal agency billing
        - Bonus lines (rate=0) → BNS spot code
        - Weekly distribution splitting (gaps + differing counts)
        - Master market: NYC (set by session before calling)

        Args:
            order: opAD order to process
            shared_session: Shared browser session (EtereSession)

        Returns:
            ProcessingResult with success status
        """
        try:
            # Import opAD automation
            from opad_automation import process_opad_order

            print(f"\n{'='*70}")
            print("PROCESSING opAD ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            # opAD REQUIRES a browser session
            if shared_session is None:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.OPAD,
                    error_message="Browser session required for opAD orders"
                )

            # Get inputs from order (already collected by orchestrator)
            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.OPAD,
                    error_message="Order inputs not collected"
                )

            print("[SESSION] ✓ Using shared browser session")
            # Market already set to NYC once at batch start

            # Process the order with pre-collected inputs (matching Daviselen/Admerasia pattern)
            success = process_opad_order(
                driver=shared_session.driver,
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            contracts = []

            if success:
                print("\n✓ opAD order processed successfully")
            else:
                print("\n✗ opAD order processing failed")

            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.OPAD,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"opAD processing error: {str(e)}\n{traceback.format_exc()}"

            print(f"\n✗ opAD processing failed: {e}")

            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.OPAD,
                error_message=error_detail
            )

    def _process_rpm_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        Process RPM order using rpm_automation.

        RPM orders have:
        - Various clients (Muckleshoot, Pechanga, etc.) — customer DB lookup
        - Markets: SEA, SFO, or CVC (single market per order)
        - Language-specific lines (Chinese/Vietnamese/Asian Rotation)
        - Weekly spot distribution, bonus lines
        - Separation: customer=25, event=0, order=15
        - Universal agency billing
        - Master market: NYC (set by session before calling)

        Args:
            order: RPM order to process
            shared_session: Shared browser session (EtereSession)

        Returns:
            ProcessingResult with success status
        """
        try:
            from browser_automation.rpm_automation import process_rpm_order

            print(f"\n{'='*70}")
            print("PROCESSING RPM ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            if shared_session is None:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.RPM,
                    error_message="Browser session required for RPM orders"
                )

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.RPM,
                    error_message="Order inputs not collected"
                )

            print("[SESSION] ✓ Using shared browser session")
            # Market already set to NYC once at batch start

            success = process_rpm_order(
                driver=shared_session.driver,
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            if success:
                print("\n✓ RPM order processed successfully")
            else:
                print("\n✗ RPM order processing failed")

            return ProcessingResult(
                success=success,
                contracts=[],
                order_type=OrderType.RPM,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"RPM processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ RPM processing failed: {e}")
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.RPM,
                error_message=error_detail
            )

    def _process_worldlink_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        Process WorldLink order using worldlink_automation.

        WorldLink orders have:
        - Two networks: Crossings TV (NYC+CMP two-line) or Asian Channel (DAL)
        - New contracts and revisions (revision_add / revision_change)
        - Separation: customer=5, event=0, order=15
        - Universal agency billing
        - Block refresh required after CMP lines are added

        Args:
            order: WorldLink order to process
            shared_session: Shared browser session (EtereSession)

        Returns:
            ProcessingResult with contract for block-refresh tracking
        """
        try:
            from browser_automation.worldlink_automation import process_worldlink_order

            print(f"\n{'='*70}")
            print("PROCESSING WORLDLINK ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            if shared_session is None:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.WORLDLINK,
                    error_message="Browser session required for WorldLink orders"
                )

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.WORLDLINK,
                    error_message="Order inputs not collected"
                )

            print("[SESSION] ✓ Using shared browser session")

            contract_num = process_worldlink_order(
                driver=shared_session.driver,
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            success = contract_num is not None
            contracts = []
            if success:
                highest_line = order.order_input.get('highest_line') if isinstance(order.order_input, dict) else None
                contracts = [Contract(
                    contract_number=contract_num,
                    order_type=OrderType.WORLDLINK,
                    highest_line=highest_line,
                )]

            if success:
                print("\n✓ WorldLink order processed successfully")
            else:
                print("\n✗ WorldLink order processing failed")

            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.WORLDLINK,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"WorldLink processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ WorldLink processing failed: {e}")
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.WORLDLINK,
                error_message=error_detail
            )

    def _run_saccountyvoters_with_driver(
        self, order: Any, driver: Any, session: Any, pre_gathered_inputs: Any, process_fn: Any
    ) -> "ProcessingResult":
        """Call SacCountyVoters processor with an already-open driver."""
        success = process_fn(
            driver,
            str(order.pdf_path),
            shared_session=session,
            pre_gathered_inputs=pre_gathered_inputs,
        )
        if success:
            print("\n✓ SacCountyVoters order processed successfully")
            return ProcessingResult(success=True, contracts=[], order_type=OrderType.SACCOUNTYVOTERS)
        print("\n✗ SacCountyVoters order processing failed")
        return ProcessingResult(
            success=False, contracts=[], order_type=OrderType.SACCOUNTYVOTERS,
            error_message="SacCountyVoters processing failed - check browser output for details",
        )

    def _process_saccountyvoters_order(
        self,
        order: Any,
        shared_session: Any,
    ) -> "ProcessingResult":
        """
        Process Sacramento County Voter Registration order.

        Creates two Etere contracts (Phase 1: :15s, Phase 2: :30s).
        Market: CVC, Separation: (15, 0, 0).
        """
        try:
            from browser_automation.saccountyvoters_automation import process_saccountyvoters_order

            print(f"\n{'='*70}")
            print("PROCESSING SACRAMENTO COUNTY VOTERS ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            pre_gathered_inputs = order.order_input if order.order_input else None

            if shared_session is None:
                try:
                    from etere_session import EtereSession
                except ImportError:
                    print("[ERROR] Could not import EtereSession")
                    return ProcessingResult(
                        success=False, contracts=[], order_type=OrderType.SACCOUNTYVOTERS,
                        error_message="EtereSession import failed",
                    )
                print("[SESSION] Creating browser session for SacCountyVoters order...")
                with EtereSession() as session:
                    session.set_market("NYC")
                    print("[SESSION] Master market set to NYC")
                    return self._run_saccountyvoters_with_driver(
                        order, session.driver, session, pre_gathered_inputs, process_saccountyvoters_order
                    )

            if hasattr(shared_session, 'set_market'):
                print("[SESSION] ✓ Using shared browser session (market pre-set to NYC)")
            driver = shared_session.driver if hasattr(shared_session, 'driver') else shared_session
            return self._run_saccountyvoters_with_driver(
                order, driver, shared_session, pre_gathered_inputs, process_saccountyvoters_order
            )

        except Exception as exc:
            import traceback
            error_detail = f"SacCountyVoters processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ SacCountyVoters processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.SACCOUNTYVOTERS,
                error_message=error_detail,
            )

    def _move_to_processing(self, pdf_path: Path) -> Path | None:
        """
        Move PDF to processing folder.

        Args:
            pdf_path: Current path to PDF

        Returns:
            New path in processing folder, or None if move failed
        """
        try:
            processing_dir = self._orders_dir / "processing"
            new_path = processing_dir / pdf_path.name
            shutil.move(str(pdf_path), str(new_path))
            return new_path
        except Exception as e:
            print(f"[ERROR] Failed to move file to processing: {e}")
            return None

    def _move_to_completed(self, pdf_path: Path) -> Path | None:
        """Move PDF to completed folder."""
        try:
            completed_dir = self._orders_dir / "completed"
            new_path = completed_dir / pdf_path.name
            shutil.move(str(pdf_path), str(new_path))
            return new_path
        except Exception as e:
            print(f"[ERROR] Failed to move file to completed: {e}")
            return None

    def _move_to_failed(self, pdf_path: Path) -> Path | None:
        """Move PDF to failed folder."""
        try:
            failed_dir = self._orders_dir / "failed"
            new_path = failed_dir / pdf_path.name
            shutil.move(str(pdf_path), str(new_path))
            return new_path
        except Exception as e:
            print(f"[ERROR] Failed to move file to failed: {e}")
            return None

    def register_processor(
        self,
        order_type: OrderType,
        processor: OrderProcessor
    ) -> None:
        """
        Register a processor for a specific order type.

        This allows dynamic addition of processors without modifying the service.

        Args:
            order_type: Order type to handle
            processor: Processor implementation
        """
        self._processors[order_type] = processor

    def get_supported_order_types(self) -> list[OrderType]:
        """Get list of order types that have registered processors."""
        return list(self._processors.keys())




def get_default_order_values(order: Order) -> tuple[str, str]:
    """
    Get smart default values for order code and description.

    Args:
        order: Order to get defaults for

    Returns:
        Tuple of (default_code, default_description)
    """
    if order.order_type == OrderType.SAGENT:
        # SAGENT orders - parse PDF to get defaults
        try:
            from parsers.sagent_parser import parse_sagent_pdf
            sagent_order = parse_sagent_pdf(str(order.pdf_path))
            code = sagent_order.get_default_contract_code()
            description = sagent_order.get_default_description()
            return (code, description)
        except Exception as e:
            print(f"[WARN] Could not parse SAGENT defaults: {e}")
            return ("Sagent Order", "SAGENT Order")

    elif order.order_type == OrderType.GALEFORCE:
        # GaleForce orders - parse PDF to get defaults
        try:
            from parsers.galeforce_parser import parse_galeforce_pdf
            gf_order = parse_galeforce_pdf(str(order.pdf_path))
            return (gf_order.get_default_contract_code(), gf_order.get_default_description())
        except Exception as e:
            print(f"[WARN] Could not parse GaleForce defaults: {e}")
            return ("GaleForce Order", "GaleForce Order")

    elif order.order_type == OrderType.TCAA:
        # TCAA Toyota orders
        if order.estimate_number:
            code = f"TCAA Toyota {order.estimate_number}"
            description = f"Toyota SEA Est {order.estimate_number}"
        else:
            code = "TCAA Toyota"
            description = "Toyota SEA"
        return (code, description)

    elif order.order_type == OrderType.CHARMAINE:
        # Charmaine client orders - parse PDF for smart defaults
        try:
            from browser_automation.parsers.charmaine_parser import parse_charmaine_pdf
            parsed = parse_charmaine_pdf(str(order.pdf_path))
            # Default code from advertiser abbreviation + YYMM
            from datetime import datetime
            yymm = datetime.now().strftime("%y%m")
            code = f"{parsed.get('advertiser', 'CLIENT')[:4].upper()} {yymm}"
            description = parsed.get('campaign', 'Client Order')
            return (code, description)
        except Exception:
            return ("CLIENT", "Client Order")

    elif order.order_type == OrderType.OPAD:
        # opAD orders - parse PDF to get smart defaults
        try:
            from opad_automation import get_opad_defaults
            return get_opad_defaults(str(order.pdf_path))
        except Exception as e:
            print(f"[WARN] Could not parse opAD defaults: {e}")
            return ("opAD Order", "opAD Order")

    # Default fallback
    return ("AUTO", "Order")


def create_processing_service(customer_repository) -> OrderProcessingService:
    """
    Factory function to create a fully configured OrderProcessingService.

    Args:
        customer_repository: Customer repository instance

    Returns:
        Configured OrderProcessingService instance
    """
    return OrderProcessingService(customer_repository)
