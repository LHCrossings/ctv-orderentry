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


def _print_pre_close_summary(results: list[ProcessingResult]) -> None:
    """Print contract codes before the browser close prompt appears."""
    contracts = [c for r in results if r.success for c in r.contracts]
    if not contracts:
        return
    print(f"\n{'='*70}")
    print(f"CONTRACTS CREATED — {len(contracts)}")
    print(f"{'='*70}")
    for c in contracts:
        etere_id = f" (ID: {c.etere_id})" if c.etere_id else ""
        print(f"  ✓ {c.contract_number}{etere_id}")
    print(f"{'='*70}\n")


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
        OrderType.TCAA_AV:   "_process_tcaa_av_order",
        OrderType.MISFIT:    "_process_misfit_order",
        OrderType.DAVISELEN: "_process_daviselen_order",
        OrderType.SAGENT:    "_process_sagent_order",
        OrderType.GALEFORCE:        "_process_galeforce_order",
        OrderType.HYPHEN:           "_process_hyphen_order",
        OrderType.TIMEADVERTISING:  "_process_timeadvertising_order",
        OrderType.CHARMAINE: "_process_charmaine_order",
        OrderType.ADMERASIA: "_process_admerasia_order",
        OrderType.HL:        "_process_hl_order",
        OrderType.HL_BDR:    "_process_hl_bdr_order",
        OrderType.LEXUS:     "_process_lexus_order",
        OrderType.IMPRENTA:  "_process_imprenta_order",
        OrderType.OPAD:      "_process_opad_order",
        OrderType.WALLRICH:  "_process_wallrich_order",
        OrderType.IGRAPHIX:  "_process_igraphix_order",
        OrderType.IMPACT:    "_process_impact_order",
        OrderType.RPM:              "_process_rpm_order",
        OrderType.WORLDLINK:        "_process_worldlink_order",
        OrderType.SACCOUNTYVOTERS:  "_process_saccountyvoters_order",
        OrderType.SCWA:             "_process_scwa_order",
        OrderType.PROSIO:           "_process_prosio_order",
        OrderType.DART:             "_process_dart_order",
        OrderType.POLARIS:          "_process_polaris_order",
        OrderType.SIERRADONOR:      "_process_sierra_order",
        OrderType.THREEOLIVES:      "_process_threeolives_order",
        OrderType.BVK:              "_process_bvk_order",
        OrderType.INTERTREND:       "_process_intertrend_order",
        OrderType.MEDIASOL:         "_process_mediasol_order",
        OrderType.RWNY:             "_process_rwny_order",
        OrderType.FIGHTTHEBITE:     "_process_fightthebite_order",
        OrderType.ACM:               "_process_acm_order",
    }

    # Order types that use direct DB entry — no browser session needed
    _DIRECT_DB_ORDER_TYPES = {
        OrderType.LEXUS,
        OrderType.RPM,
        OrderType.WORLDLINK,
        OrderType.TIMEADVERTISING,
        OrderType.IGRAPHIX,
        OrderType.CHARMAINE,
        OrderType.HL,
        OrderType.HL_BDR,
        OrderType.ADMERASIA,
        OrderType.SAGENT,
        OrderType.GALEFORCE,
        OrderType.HYPHEN,
        OrderType.INTERTREND,
        OrderType.SIERRADONOR,
        OrderType.PROSIO,
        OrderType.SCWA,
        OrderType.RWNY,
        OrderType.TCAA_AV,
        OrderType.SACCOUNTYVOTERS,
        OrderType.TCAA,
        OrderType.MISFIT,
        OrderType.IMPACT,
        OrderType.IMPRENTA,
        OrderType.DAVISELEN,
        OrderType.BVK,
        OrderType.DART,
        OrderType.MEDIASOL,
        OrderType.OPAD,
        OrderType.POLARIS,
        OrderType.THREEOLIVES,
        OrderType.WALLRICH,
        OrderType.XML,
        OrderType.FIGHTTHEBITE,
        OrderType.ACM,
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
        self._tcaa_av_processor = None

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
            # Check if any orders need browser automation.
            # Any type with a dedicated processor requires a live browser session.
            needs_browser = any(
                order.order_type in self._PROCESSOR_DISPATCH
                and order.order_type not in self._DIRECT_DB_ORDER_TYPES
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
                    results = self._process_orders_with_session(orders, shared_session)
                    _print_pre_close_summary(results)
                return results

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
                from parsers.tcaa_parser import parse_tcaa_pdf
                from tcaa_automation import process_tcaa_order

                self._tcaa_processor = {
                    'process': process_tcaa_order,
                    'parser': parse_tcaa_pdf,
                }

            # Use first order for display info
            first_order = orders[0]

            print(f"\n{'='*70}")
            print("TCAA DIRECT DB ENTRY (BATCH MODE)")
            print(f"{'='*70}")
            print(f"PDF: {first_order.pdf_path.name}")
            print(f"Estimates: {', '.join(o.estimate_number for o in orders)}")
            print(f"Customer: {first_order.customer_name}")
            print(f"{'='*70}\n")

            success = self._tcaa_processor['process'](
                pdf_path=str(first_order.pdf_path),
                estimate_number=None,  # batch — process all estimates in PDF
            )

            if success:
                contracts = [
                    Contract(contract_number=f"TCAA-{o.estimate_number}", order_type=OrderType.TCAA)
                    for o in orders
                ]
                print(f"\n✓ Successfully created {len(contracts)} contracts")
                return ProcessingResult(success=True, contracts=contracts, order_type=OrderType.TCAA)
            else:
                return ProcessingResult(
                    success=False, contracts=[], order_type=OrderType.TCAA,
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
            inp = order.order_input
            get = inp.get if isinstance(inp, dict) else lambda k, d="": getattr(inp, k, d)
            message += (
                f"  Order Code: {get('order_code')}\n"
                f"  Description: {get('description')}\n"
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
        """Process TCAA order via direct DB (no browser)."""
        try:
            if self._tcaa_processor is None:
                from parsers.tcaa_parser import parse_tcaa_pdf
                from tcaa_automation import process_tcaa_order

                self._tcaa_processor = {
                    'process': process_tcaa_order,
                    'parser': parse_tcaa_pdf,
                }

            print(f"\n{'='*70}")
            print("TCAA DIRECT DB ENTRY")
            print(f"{'='*70}")
            print(f"Order: {order.get_display_name()}")
            print(f"Customer: {order.customer_name}")

            print(f"{'='*70}\n")

            inp = order.order_input
            selected_estimates = inp.get('selected_estimates') if isinstance(inp, dict) else None
            if not selected_estimates and order.estimate_number:
                selected_estimates = [order.estimate_number]

            code_prefix = inp.get('order_code_prefix') if isinstance(inp, dict) else None
            shared_desc = inp.get('description') if isinstance(inp, dict) else None

            if selected_estimates:
                contracts, all_ok = [], True
                for est_num in selected_estimates:
                    per_est_code = f"{code_prefix} {est_num}" if code_prefix else None
                    ok = self._tcaa_processor['process'](
                        pdf_path=str(order.pdf_path),
                        estimate_number=est_num,
                        order_code=per_est_code,
                        description=shared_desc,
                    )
                    if ok:
                        label = per_est_code or f"TCAA-{est_num}"
                        contracts.append(Contract(contract_number=label, order_type=OrderType.TCAA))
                    else:
                        all_ok = False
                return ProcessingResult(
                    success=all_ok, contracts=contracts, order_type=OrderType.TCAA,
                    error_message=None if all_ok else "One or more TCAA estimates failed — check output"
                )
            else:
                # No estimate filter — process all
                success = self._tcaa_processor['process'](
                    pdf_path=str(order.pdf_path),
                    estimate_number=None,
                    order_code=code_prefix,
                    description=shared_desc,
                )
                label = code_prefix or "TCAA"
                contracts = [Contract(contract_number=label, order_type=OrderType.TCAA)] if success else []
                return ProcessingResult(
                    success=success, contracts=contracts, order_type=OrderType.TCAA,
                    error_message=None if success else "TCAA processing failed — check output for details"
                )

        except Exception as e:
            import traceback
            error_detail = f"TCAA processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ TCAA processing failed: {e}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.TCAA, error_message=error_detail
            )

    def _process_tcaa_av_order(self, order: Order, shared_session: Any = None) -> ProcessingResult:
        """Process a Toyota AAPI Added Value flight schedule order."""
        try:
            if self._tcaa_av_processor is None:
                from etere_session import EtereSession
                from tcaa_av_automation import process_toyota_av_order

                self._tcaa_av_processor = {
                    'process': process_toyota_av_order,
                    'session_class': EtereSession,
                }

            print(f"\n{'='*70}")
            print("TOYOTA AAPI AV — BROWSER AUTOMATION")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            print(f"{'='*70}\n")

            inp = order.order_input
            pre_gathered = inp if isinstance(inp, dict) else None
            contract_label = (
                inp.get('contract_code') if isinstance(inp, dict) else None
            ) or "TCAA-AV"

            success = self._tcaa_av_processor['process'](
                pdf_path=str(order.pdf_path),
                pre_gathered_inputs=pre_gathered,
            )
            if success:
                return ProcessingResult(
                    success=True,
                    contracts=[Contract(contract_number=contract_label, order_type=OrderType.TCAA_AV)],
                    order_type=OrderType.TCAA_AV,
                    error_message=None,
                )
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.TCAA_AV,
                error_message="TCAA AV processing failed — check browser output for details"
            )

        except Exception as e:
            import traceback
            error_detail = f"TCAA AV processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ TCAA AV processing failed: {e}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.TCAA_AV,
                error_message=error_detail
            )

    def _run_misfit_with_driver(self, order: Order, driver: Any) -> ProcessingResult:
        """Call Misfit processor with an already-open driver and build ProcessingResult."""
        success = self._misfit_processor['process'](
            driver=driver,
            pdf_path=str(order.pdf_path),
            user_input=order.order_input,
        )
        if success:
            inp = order.order_input
            code = inp.get('order_code') if isinstance(inp, dict) else None
            if not code:
                try:
                    parsed_order = self._misfit_processor['parser'](str(order.pdf_path))
                    code = f"MISFIT-{parsed_order.date.replace('/', '')}" if parsed_order.date else 'MISFIT'
                except Exception:
                    code = 'MISFIT'
            contract = Contract(contract_number=code, order_type=OrderType.MISFIT)
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
                inp = order.order_input
                if isinstance(inp, dict):
                    print(f"Code: {inp.get('order_code', '')}")
                    print(f"Description: {inp.get('description', '')}")
                else:
                    print(f"Code: {inp.order_code}")
                    print(f"Description: {inp.description}")

            print(f"{'='*70}\n")

            return self._run_misfit_with_driver(order, None)

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

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.DAVISELEN,
                    error_message="Order inputs not collected"
                )

            success = process_daviselen_order(
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            inp = order.order_input
            code = (inp.get('order_code') or inp.get('contract_code') or 'DAVISELEN') if isinstance(inp, dict) else 'DAVISELEN'
            contracts = [Contract(contract_number=code, order_type=OrderType.DAVISELEN)] if success else []

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

    def _process_intertrend_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        Process Intertrend order using intertrend_automation.

        Intertrend orders have:
        - California State Lottery as the only known client (ID: 280)
        - SFO market
        - Net rates that require agency gross-up (default 15%)
        - Chinese programming (Mandarin + Cantonese mixed)
        - RS = paid, AV = bonus lines
        """
        try:
            from intertrend_automation import process_intertrend_order

            print(f"\n{'='*70}")
            print("PROCESSING INTERTREND ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.INTERTREND,
                    error_message="Order inputs not collected"
                )

            success = process_intertrend_order(
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            inp = order.order_input
            code = (inp.get('order_code') or inp.get('contract_code') or 'INTERTREND') if isinstance(inp, dict) else 'INTERTREND'
            contracts = [Contract(contract_number=code, order_type=OrderType.INTERTREND)] if success else []
            if success:
                print("\n✓ Intertrend order processed successfully")
            else:
                print("\n✗ Intertrend order processing failed")

            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.INTERTREND,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"Intertrend processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ Intertrend processing failed: {e}")
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.INTERTREND,
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
            from sagent_automation import process_sagent_order_direct

            print(f"\n{'='*70}")
            print("PROCESSING SAGENT ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False, contracts=[], order_type=OrderType.SAGENT,
                    error_message="Order inputs not collected"
                )

            contract_id = process_sagent_order_direct(
                pdf_path=str(order.pdf_path),
                user_input=order.order_input,
            )
            success = contract_id is not None
            inp = order.order_input
            contract_label = (inp.get('contract_code') if isinstance(inp, dict) else None) or str(contract_id)
            contracts = [Contract(contract_number=contract_label, order_type=OrderType.SAGENT)] if success else []

            if success:
                print(f"\n✓ SAGENT order processed successfully — contract {contract_id}")
            else:
                print("\n✗ SAGENT order processing failed")

            return ProcessingResult(
                success=success, contracts=contracts, order_type=OrderType.SAGENT,
                error_message="" if success else "SAGENT direct DB entry failed"
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
            print("\n✓ PACO order processed successfully")
            return ProcessingResult(success=True, contracts=[], order_type=OrderType.GALEFORCE)
        print("\n✗ PACO order processing failed")
        return ProcessingResult(
            success=False, contracts=[], order_type=OrderType.GALEFORCE,
            error_message="PACO processing failed - check browser output for details",
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
            from galeforce_automation import process_galeforce_order_direct

            print(f"\n{'='*70}")
            print("PROCESSING PACO ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False, contracts=[], order_type=OrderType.GALEFORCE,
                    error_message="Order inputs not collected"
                )

            contract_id = process_galeforce_order_direct(
                pdf_path=str(order.pdf_path),
                user_input=order.order_input,
            )
            success = contract_id is not None
            inp = order.order_input
            label = (inp.get('contract_code') if isinstance(inp, dict) else None) or str(contract_id)
            contracts = [Contract(contract_number=label, order_type=OrderType.GALEFORCE)] if success else []

            if success:
                print(f"\n✓ PACO order processed successfully — contract {contract_id}")
            else:
                print("\n✗ PACO order processing failed")

            return ProcessingResult(
                success=success, contracts=contracts, order_type=OrderType.GALEFORCE,
                error_message="" if success else "PACO direct DB entry failed"
            )

        except Exception as exc:
            import traceback
            error_detail = f"PACO processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ PACO processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.GALEFORCE,
                error_message=error_detail,
            )

    def _run_hyphen_with_driver(
        self, order: Order, driver: Any, session: Any, pre_gathered_inputs: Any, process_fn: Any
    ) -> ProcessingResult:
        """Call Hyphen processor with an already-open driver and build ProcessingResult."""
        success = process_fn(
            driver,
            str(order.pdf_path),
            shared_session=session,
            pre_gathered_inputs=pre_gathered_inputs,
        )
        if success:
            print("\n✓ Hyphen order processed successfully")
            return ProcessingResult(success=True, contracts=[], order_type=OrderType.HYPHEN)
        print("\n✗ Hyphen order processing failed")
        return ProcessingResult(
            success=False, contracts=[], order_type=OrderType.HYPHEN,
            error_message="Hyphen processing failed - check browser output for details",
        )

    def _process_hyphen_order(
        self,
        order: Order,
        shared_session: Any,
    ) -> ProcessingResult:
        """
        Process a Hyphen Buy Detail Report order.

        Single-market orders (CVC or LAX). Master market is NYC.
        """
        try:
            from hyphen_automation import process_hyphen_order_direct

            print(f"\n{'='*70}")
            print("PROCESSING HYPHEN ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False, contracts=[], order_type=OrderType.HYPHEN,
                    error_message="Order inputs not collected"
                )

            contract_id = process_hyphen_order_direct(
                pdf_path=str(order.pdf_path),
                user_input=order.order_input,
            )
            success = contract_id is not None
            inp = order.order_input
            label = (inp.get('contract_code') if isinstance(inp, dict) else None) or str(contract_id)
            contracts = [Contract(contract_number=label, order_type=OrderType.HYPHEN)] if success else []

            if success:
                print(f"\n✓ Hyphen order processed successfully — contract {contract_id}")
            else:
                print("\n✗ Hyphen order processing failed")

            return ProcessingResult(
                success=success, contracts=contracts, order_type=OrderType.HYPHEN,
                error_message="" if success else "Hyphen direct DB entry failed"
            )

        except Exception as exc:
            import traceback
            error_detail = f"Hyphen processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ Hyphen processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.HYPHEN,
                error_message=error_detail,
            )

    def _run_prosio_with_driver(
        self, order: Order, driver: Any, session: Any, pre_gathered_inputs: Any, process_fn: Any
    ) -> ProcessingResult:
        """Call Prosio processor with an already-open driver and build ProcessingResult."""
        success = process_fn(
            driver,
            str(order.pdf_path),
            shared_session=session,
            pre_gathered_inputs=pre_gathered_inputs,
        )
        if success:
            inp = order.order_input
            code = inp.get('order_code') if isinstance(inp, dict) else None
            contracts = [Contract(contract_number=code or 'PROSIO', order_type=OrderType.PROSIO)]
            print("\n✓ Prosio order processed successfully")
            return ProcessingResult(success=True, contracts=contracts, order_type=OrderType.PROSIO)
        print("\n✗ Prosio order processing failed")
        return ProcessingResult(
            success=False, contracts=[], order_type=OrderType.PROSIO,
            error_message="Prosio processing failed - check browser output for details",
        )

    def _process_prosio_order(
        self,
        order: Order,
        shared_session: Any,
    ) -> ProcessingResult:
        """
        Process a Prosio Media Contract Excel order.

        Single-market orders (typically CVC for Sacramento). Master market is NYC.
        """
        try:
            from prosio_automation import process_prosio_order

            print(f"\n{'='*70}")
            print("PROCESSING PROSIO ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            print(f"{'='*70}\n")

            pre_gathered_inputs = order.order_input if order.order_input else None

            return self._run_prosio_with_driver(
                order, None, None, pre_gathered_inputs, process_prosio_order
            )

        except Exception as exc:
            import traceback
            error_detail = f"Prosio processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ Prosio processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.PROSIO,
                error_message=error_detail,
            )

    def _process_timeadvertising_order(
        self,
        order: Order,
        shared_session: Any,
    ) -> ProcessingResult:
        """
        Process Time Advertising broadcast order via direct DB entry.

        Single-market orders for Graton Casino (SFO or CVC). No browser needed.
        """
        try:
            from browser_automation.timeadvertising_automation import process_timeadvertising_order

            print(f"\n{'='*70}")
            print("PROCESSING TIME ADVERTISING ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False, contracts=[], order_type=OrderType.TIMEADVERTISING,
                    error_message="Order inputs not collected",
                )

            success = process_timeadvertising_order(
                pdf_path=str(order.pdf_path),
                pre_gathered_inputs=order.order_input,
            )

            inp = order.order_input
            contract_label = (inp.get('contract_code') if isinstance(inp, dict) else None) or "TIMEADVERTISING"
            contracts = [Contract(contract_number=contract_label, order_type=OrderType.TIMEADVERTISING)] if success else []
            return ProcessingResult(
                success=success, contracts=contracts, order_type=OrderType.TIMEADVERTISING,
                error_message=None if success else "Processing failed",
            )

        except Exception as exc:
            import traceback
            error_detail = f"Time Advertising processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ Time Advertising processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.TIMEADVERTISING,
                error_message=error_detail,
            )

    def _process_charmaine_order(
        self,
        order: Order,
        shared_session: Any = None,
    ) -> ProcessingResult:
        """
        Process Charmaine order via direct DB entry. No browser needed.
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

            success = process_charmaine_order(str(order.pdf_path))
            if success:
                print("\n✓ CHARMAINE order processed successfully")
                return ProcessingResult(success=True, contracts=[], order_type=OrderType.CHARMAINE)
            print("\n✗ CHARMAINE order processing failed")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.CHARMAINE,
                error_message="CHARMAINE processing failed — check output",
            )

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

            # Get inputs from order (already collected by orchestrator)
            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.ADMERASIA,
                    error_message="Order inputs not collected"
                )

            # DirectDB — no browser session needed
            contract_num = process_admerasia_order(
                pdf_path=str(order.pdf_path),
                user_input=order.order_input,
            )

            success = bool(contract_num)
            contracts = [Contract(contract_number=str(contract_num), order_type=OrderType.ADMERASIA)] if contract_num else []

            if success:
                print(f"\n✓ Admerasia order processed successfully — contract {contract_num}")
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
        shared_session: any,
    ) -> ProcessingResult:
        """
        Process H&L Partners order via direct DB entry. No browser needed.

        H&L Partners orders have:
        - SFO or CVC markets only (detected from PDF header)
        - Multiple clients possible → customer DB lookup with manual fallback
        - Separation: customer=25, event=0, order=0
        - Agency billing
        - Multiple estimates per PDF → each becomes a separate contract
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

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.HL,
                    error_message="Order inputs not collected",
                )

            success = process_hl_order(
                pdf_path=str(order.pdf_path),
                pre_gathered_inputs=order.order_input,
            )

            if success:
                print("\n✓ H&L Partners order processed successfully")
            else:
                print("\n✗ H&L Partners order processing failed")

            return ProcessingResult(
                success=success,
                contracts=[],
                order_type=OrderType.HL,
                error_message=None if success else "Processing failed",
            )

        except Exception as e:
            import traceback
            error_detail = f"H&L Partners processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ H&L Partners processing failed: {e}")
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.HL,
                error_message=error_detail,
            )

    def _process_hl_bdr_order(
        self,
        order: Order,
        shared_session: any,
    ) -> ProcessingResult:
        """Process H/L Buy Detail Report order via direct DB entry. No browser needed."""
        try:
            from browser_automation.hl_bdr_automation import process_hl_bdr_order

            print(f"\n{'='*70}")
            print("PROCESSING H/L BUY DETAIL REPORT")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.HL_BDR,
                    error_message="Order inputs not collected",
                )

            contracts = process_hl_bdr_order(
                pdf_path=str(order.pdf_path),
                pre_gathered_inputs=order.order_input,
            )

            success = bool(contracts)

            if success:
                print(f"\n✓ H/L BDR order processed — {len(contracts)} contract(s): {', '.join(contracts)}")
            else:
                print("\n✗ H/L BDR order processing failed")

            return ProcessingResult(
                success=success,
                contracts=[Contract(c, OrderType.HL_BDR) for c in contracts],
                order_type=OrderType.HL_BDR,
                error_message=None if success else "Processing failed",
            )

        except Exception as e:
            import traceback
            error_detail = f"H/L BDR processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ H/L BDR processing failed: {e}")
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.HL_BDR,
                error_message=error_detail,
            )

    def _process_lexus_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """
        Process Lexus / IW Group order using lexus_automation.
        """
        try:
            from browser_automation.lexus_automation import process_lexus_order

            print(f"\n{'='*70}")
            print("PROCESSING LEXUS / IW GROUP ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.LEXUS,
                    error_message="Order inputs not collected"
                )

            success = process_lexus_order(
                file_path=str(order.pdf_path),
                user_input=order.order_input
            )

            inp = order.order_input
            quarter_contracts = inp.get('contracts', []) if isinstance(inp, dict) else []
            contracts = [
                Contract(contract_number=q.get('contract_code', 'LEXUS'), order_type=OrderType.LEXUS)
                for q in quarter_contracts
            ] if success and quarter_contracts else (
                [Contract(contract_number='LEXUS', order_type=OrderType.LEXUS)] if success else []
            )
            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.LEXUS,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"Lexus processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ Lexus processing failed: {e}")
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.LEXUS,
                error_message=error_detail
            )

    def _process_imprenta_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """Process Imprenta / PG&E order using imprenta_automation."""
        try:
            from browser_automation.imprenta_automation import process_imprenta_order

            print(f"\n{'='*70}")
            print("PROCESSING IMPRENTA ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.IMPRENTA,
                    error_message="Order inputs not collected"
                )

            success = process_imprenta_order(
                file_path=str(order.pdf_path),
                user_input=order.order_input
            )

            contracts = [Contract(contract_number=order.order_input.get("contract_code", "imprenta"), order_type=OrderType.IMPRENTA)] if success else []
            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.IMPRENTA,
                error_message=None if success else "Processing failed"
            )

        except Exception as e:
            import traceback
            error_detail = f"Imprenta processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ Imprenta processing failed: {e}")
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.IMPRENTA,
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

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.IGRAPHIX,
                    error_message="Order inputs not collected"
                )

            success = process_igraphix_order(
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

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.IMPACT,
                    error_message="Order inputs not collected"
                )

            success = process_impact_order(
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
        - Separation intervals: Customer=15, Order=15, Event=0
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

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.OPAD,
                    error_message="Order inputs not collected"
                )

            success = process_opad_order(
                pdf_path=str(order.pdf_path),
                user_input=order.order_input
            )

            inp = order.order_input
            code = inp.get('order_code') if isinstance(inp, dict) else None
            contracts = [Contract(contract_number=code or 'OPAD', order_type=OrderType.OPAD)] if success else []

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

    def _process_wallrich_order(
        self,
        order: Order,
        shared_session: any
    ) -> ProcessingResult:
        """Process Wallrich order using wallrich_automation."""
        try:
            from browser_automation.wallrich_automation import process_wallrich_order

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.WALLRICH,
                    error_message="Order inputs not collected"
                )

            success = process_wallrich_order(
                pdf_path=str(order.pdf_path),
                user_input=order.order_input,
            )

            inp = order.order_input
            label = (inp.get('order_code') if isinstance(inp, dict) else None) or "WALLRICH"
            contracts = [Contract(contract_number=label, order_type=OrderType.WALLRICH)] if success else []
            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.WALLRICH,
                error_message=None if success else "Processing failed",
            )

        except Exception as e:
            import traceback
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.WALLRICH,
                error_message=f"Wallrich processing error: {str(e)}\n{traceback.format_exc()}",
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

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.RPM,
                    error_message="Order inputs not collected"
                )

            success = process_rpm_order(
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
            from browser_automation.worldlink_automation import process_worldlink_order_direct

            print(f"\n{'='*70}")
            print("PROCESSING WORLDLINK ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.WORLDLINK,
                    error_message="Order inputs not collected"
                )

            contract_num = process_worldlink_order_direct(user_input=order.order_input)

            success = contract_num is not None
            contracts = []
            if success:
                highest_line = order.order_input.get('highest_line') if isinstance(order.order_input, dict) else None
                etere_id = None
                try:
                    from browser_automation.etere_direct_client import connect as _db_connect
                    with _db_connect() as _conn:
                        _ph = '%s' if type(_conn).__module__.startswith('pymssql') else '?'
                        _cur = _conn.cursor()
                        _cur.execute(
                            f"SELECT TOP 1 ID_CONTRATTITESTATA FROM CONTRATTITESTATA "
                            f"WHERE COD_CONTRATTO = {_ph}",
                            (contract_num,)
                        )
                        _row = _cur.fetchone()
                        if _row:
                            etere_id = int(_row[0])
                except Exception:
                    pass
                contracts = [Contract(
                    contract_number=contract_num,
                    order_type=OrderType.WORLDLINK,
                    highest_line=highest_line,
                    etere_id=etere_id,
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

    def _process_dart_order(
        self,
        order: Order,
        shared_session: Any = None,
    ) -> ProcessingResult:
        """
        Process DART (Dallas Area Rapid Transit) order using dart_automation.

        DART orders are direct-client xlsx insertion orders for
        The Asian Channel (KLEG 44.3 Dallas). Master market: DAL.
        """
        try:
            from browser_automation.dart_automation import process_dart_order

            print(f"\n{'='*70}")
            print("PROCESSING DART ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.DART,
                    error_message="Order inputs not collected",
                )

            contract_num = process_dart_order(
                xlsx_path=str(order.pdf_path),
                user_input=order.order_input,
            )

            success = contract_num is not None
            if success:
                print("\n✓ DART order processed successfully")
            else:
                print("\n✗ DART order processing failed")

            return ProcessingResult(
                success=success,
                contracts=[],
                order_type=OrderType.DART,
                error_message=None if success else "Processing failed",
            )

        except Exception as e:
            import traceback
            error_detail = f"DART processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ DART processing failed: {e}")
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.DART,
                error_message=error_detail,
            )

    def _process_polaris_order(
        self,
        order: Order,
        shared_session: Any = None,
    ) -> ProcessingResult:
        """
        Process Polaris Media Group order using polaris_automation.

        Polaris orders are agency xlsx insertion orders. Master market: NYC.
        """
        try:
            from browser_automation.polaris_automation import process_polaris_order

            print(f"\n{'='*70}")
            print("PROCESSING POLARIS ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.POLARIS,
                    error_message="Order inputs not collected",
                )

            contract_num = process_polaris_order(
                xlsx_path=str(order.pdf_path),
                user_input=order.order_input,
            )

            success = contract_num is not None
            if success:
                print("\n✓ Polaris order processed successfully")
            else:
                print("\n✗ Polaris order processing failed")

            inp = order.order_input
            code = inp.get('order_code') if isinstance(inp, dict) else None
            contracts = [Contract(contract_number=code or 'POLARIS', order_type=OrderType.POLARIS)] if success else []
            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=OrderType.POLARIS,
                error_message=None if success else "Processing failed",
            )

        except Exception as e:
            import traceback
            error_detail = f"Polaris processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ Polaris processing failed: {e}")
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.POLARIS,
                error_message=error_detail,
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
            inp = order.order_input
            contracts = []
            if isinstance(inp, dict):
                ph1 = inp.get('phase1_inputs', {})
                ph2 = inp.get('phase2_inputs', {})
                if ph1.get('contract_code'):
                    contracts.append(Contract(contract_number=ph1['contract_code'], order_type=OrderType.SACCOUNTYVOTERS))
                if ph2.get('contract_code'):
                    contracts.append(Contract(contract_number=ph2['contract_code'], order_type=OrderType.SACCOUNTYVOTERS))
            if not contracts:
                contracts = [Contract(contract_number='SACCOUNTYVOTERS', order_type=OrderType.SACCOUNTYVOTERS)]
            return ProcessingResult(success=True, contracts=contracts, order_type=OrderType.SACCOUNTYVOTERS)
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

            return self._run_saccountyvoters_with_driver(
                order, None, None, pre_gathered_inputs, process_saccountyvoters_order
            )

        except Exception as exc:
            import traceback
            error_detail = f"SacCountyVoters processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ SacCountyVoters processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.SACCOUNTYVOTERS,
                error_message=error_detail,
            )

    def _process_scwa_order(
        self,
        order: Any,
        shared_session: Any,
    ) -> "ProcessingResult":
        """Process Sacramento County Water Agency (SCWA) order."""
        try:
            from browser_automation.scwa_automation import process_scwa_order

            print(f"\n{'='*70}")
            print("PROCESSING SACRAMENTO COUNTY WATER AGENCY ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            pre_gathered_inputs = order.order_input if order.order_input else None

            def _run(driver, session):
                success = process_scwa_order(
                    driver,
                    str(order.pdf_path),
                    shared_session=session,
                    pre_gathered_inputs=pre_gathered_inputs,
                )
                if success:
                    print("\n✓ SCWA order processed successfully")
                    inp = order.order_input
                    code = (inp.get('order_code') or inp.get('contract_code') or 'SCWA') if isinstance(inp, dict) else 'SCWA'
                    return ProcessingResult(success=True, contracts=[Contract(contract_number=code, order_type=OrderType.SCWA)], order_type=OrderType.SCWA)
                print("\n✗ SCWA order processing failed")
                return ProcessingResult(
                    success=False, contracts=[], order_type=OrderType.SCWA,
                    error_message="SCWA processing failed - check browser output for details",
                )

            return _run(None, None)

        except Exception as exc:
            import traceback
            error_detail = f"SCWA processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ SCWA processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.SCWA,
                error_message=error_detail,
            )

    def _process_sierra_order(
        self,
        order: Any,
        shared_session: Any,
    ) -> "ProcessingResult":
        """Process Sierra Donor Services order."""
        try:
            from browser_automation.sierra_automation import process_sierra_order

            print(f"\n{'='*70}")
            print("PROCESSING SIERRA DONOR SERVICES ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            pre_gathered_inputs = order.order_input if order.order_input else None

            def _run(driver, session):
                success = process_sierra_order(
                    driver,
                    str(order.pdf_path),
                    shared_session=session,
                    pre_gathered_inputs=pre_gathered_inputs,
                )
                if success:
                    print("\n✓ Sierra Donor order processed successfully")
                    inp = order.order_input
                    code = (inp.get('order_code') or inp.get('contract_code') or 'SIERRADONOR') if isinstance(inp, dict) else 'SIERRADONOR'
                    return ProcessingResult(success=True, contracts=[Contract(contract_number=code, order_type=OrderType.SIERRADONOR)], order_type=OrderType.SIERRADONOR)
                print("\n✗ Sierra Donor order processing failed")
                return ProcessingResult(
                    success=False, contracts=[], order_type=OrderType.SIERRADONOR,
                    error_message="Sierra Donor processing failed - check browser output for details",
                )

            return _run(None, None)

        except Exception as exc:
            import traceback
            error_detail = f"Sierra Donor processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ Sierra Donor processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.SIERRADONOR,
                error_message=error_detail,
            )

    def _process_rwny_order(
        self,
        order: Any,
        shared_session: Any,
    ) -> "ProcessingResult":
        """Process Resorts World New York (RWNY) order."""
        try:
            from browser_automation.rwny_automation import process_rwny_order

            print(f"\n{'='*70}")
            print("PROCESSING RESORTS WORLD NEW YORK ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            print(f"{'='*70}\n")

            pre_gathered_inputs = order.order_input if order.order_input else None

            def _run(driver, session):
                success = process_rwny_order(
                    driver,
                    str(order.pdf_path),
                    shared_session=session,
                    pre_gathered_inputs=pre_gathered_inputs,
                )
                if success:
                    print("\n✓ RWNY order processed successfully")
                    inp = order.order_input
                    code = inp.get('order_code') if isinstance(inp, dict) else None
                    contracts = [Contract(contract_number=code or 'RWNY', order_type=OrderType.RWNY)]
                    return ProcessingResult(success=True, contracts=contracts, order_type=OrderType.RWNY)
                print("\n✗ RWNY order processing failed")
                return ProcessingResult(
                    success=False, contracts=[], order_type=OrderType.RWNY,
                    error_message="RWNY processing failed - check browser output for details",
                )

            return _run(None, None)

        except Exception as exc:
            import traceback
            error_detail = f"RWNY processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ RWNY processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.RWNY,
                error_message=error_detail,
            )

    def _process_fightthebite_order(
        self,
        order: Any,
        shared_session: Any,
    ) -> "ProcessingResult":
        """Process Fight the Bite media partnership order."""
        try:
            from browser_automation.fightthebite_automation import process_fightthebite_order

            print(f"\n{'='*70}")
            print("PROCESSING FIGHT THE BITE ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            print(f"{'='*70}\n")

            pre_gathered_inputs = order.order_input if order.order_input else None

            success = process_fightthebite_order(
                file_path=str(order.pdf_path),
                shared_session=None,
                pre_gathered_inputs=pre_gathered_inputs,
            )
            if success:
                print("\n✓ Fight the Bite order processed successfully")
                inp = pre_gathered_inputs
                code = (inp.get('contract_code') if isinstance(inp, dict) else None) or 'FTB'
                return ProcessingResult(
                    success=True,
                    contracts=[Contract(contract_number=code, order_type=OrderType.FIGHTTHEBITE)],
                    order_type=OrderType.FIGHTTHEBITE,
                )
            print("\n✗ Fight the Bite order processing failed")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.FIGHTTHEBITE,
                error_message="Fight the Bite processing failed — check output for details",
            )

        except Exception as exc:
            import traceback
            error_detail = f"Fight the Bite processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ Fight the Bite processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.FIGHTTHEBITE,
                error_message=error_detail,
            )

    def _process_threeolives_order(
        self,
        order: Any,
        shared_session: Any,
    ) -> "ProcessingResult":
        """Process 3 Olives Media order."""
        try:
            from browser_automation.threeolives_automation import process_threeolives_order

            print(f"\n{'='*70}")
            print("PROCESSING 3 OLIVES MEDIA ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            pre_gathered_inputs = order.order_input if order.order_input else None

            success = process_threeolives_order(
                file_path=str(order.pdf_path),
                shared_session=None,
                pre_gathered_inputs=pre_gathered_inputs,
            )
            if success:
                print("\n✓ 3 Olives Media order processed successfully")
                inp = pre_gathered_inputs
                code = (inp.get('order_code') or inp.get('contract_code') or 'THREEOLIVES') if isinstance(inp, dict) else 'THREEOLIVES'
                return ProcessingResult(success=True, contracts=[Contract(contract_number=code, order_type=OrderType.THREEOLIVES)], order_type=OrderType.THREEOLIVES)
            print("\n✗ 3 Olives Media order processing failed")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.THREEOLIVES,
                error_message="3 Olives Media processing failed",
            )

        except Exception as exc:
            import traceback
            error_detail = f"3 Olives Media processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ 3 Olives Media processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.THREEOLIVES,
                error_message=error_detail,
            )

    def _process_bvk_order(self, order, shared_session=None):
        try:
            from browser_automation.bvk_automation import process_bvk_order

            print(f"\n{'='*70}")
            print("PROCESSING BVK ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            pre_gathered_inputs = order.order_input if order.order_input else None

            success = process_bvk_order(
                pdf_path=str(order.pdf_path),
                shared_session=None,
                pre_gathered_inputs=pre_gathered_inputs,
            )
            if success:
                print("\n✓ BVK order processed successfully")
                inp = pre_gathered_inputs
                code = (inp.get('order_code') or inp.get('contract_code') or 'BVK') if isinstance(inp, dict) else 'BVK'
                return ProcessingResult(success=True, contracts=[Contract(contract_number=code, order_type=OrderType.BVK)], order_type=OrderType.BVK)
            print("\n✗ BVK order processing failed")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.BVK,
                error_message="BVK processing failed",
            )

        except Exception as exc:
            import traceback
            error_detail = f"BVK processing error: {str(exc)}\n{traceback.format_exc()}"
            print(f"\n✗ BVK processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.BVK,
                error_message=error_detail,
            )

    def _process_acm_order(self, order: Order, shared_session=None) -> ProcessingResult:
        """Process an ACM (American Community Media) order — one contract per market."""
        inp = order.order_input if isinstance(order.order_input, dict) else {}
        try:
            from browser_automation.parsers.acm_parser import parse_acm_xlsx
            from browser_automation.acm_automation import run_acm_order

            parsed  = parse_acm_xlsx(str(order.pdf_path))
            results = run_acm_order(parsed, inp)  # list of (label, success)

            contracts = [
                Contract(contract_number=label, order_type=OrderType.ACM)
                for label, ok in results if ok
            ]
            overall_success = bool(contracts)

            if not overall_success:
                return ProcessingResult(
                    success=False, contracts=[], order_type=OrderType.ACM,
                    error_message="ACM processing failed — check output above",
                )
            return ProcessingResult(success=True, contracts=contracts, order_type=OrderType.ACM)

        except Exception as exc:
            import traceback
            error_detail = f"ACM processing error: {exc}\n{traceback.format_exc()}"
            print(f"\n✗ ACM processing failed: {exc}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.ACM,
                error_message=error_detail,
            )

    def _process_mediasol_order(self, order: Order, shared_session: any) -> ProcessingResult:
        """Process a Media Solutions / Pulsar Advertising order."""
        try:
            from browser_automation.mediasol_automation import process_mediasol_order

            print(f"\n{'='*70}")
            print("PROCESSING MEDIA SOLUTIONS ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")

            if not order.order_input:
                return ProcessingResult(
                    success=False, contracts=[], order_type=OrderType.MEDIASOL,
                    error_message="Order inputs not collected",
                )

            success = process_mediasol_order(
                pdf_path=str(order.pdf_path),
                user_input=order.order_input,
            )

            if success:
                print("\n✓ Media Solutions order processed successfully")
            else:
                print("\n✗ Media Solutions order processing failed")

            inp = order.order_input
            code = (inp.get('order_code') or inp.get('contract_code') or 'MEDIASOL') if isinstance(inp, dict) else 'MEDIASOL'
            contracts = [Contract(contract_number=code, order_type=OrderType.MEDIASOL)] if success else []
            return ProcessingResult(
                success=success, contracts=contracts, order_type=OrderType.MEDIASOL,
                error_message=None if success else "Processing failed",
            )

        except Exception as e:
            import traceback
            error_detail = f"Media Solutions processing error: {str(e)}\n{traceback.format_exc()}"
            print(f"\n✗ Media Solutions processing failed: {e}")
            return ProcessingResult(
                success=False, contracts=[], order_type=OrderType.MEDIASOL,
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
            print(f"[WARN] Could not parse PACO defaults: {e}")
            return ("PACO Order", "PACO Order")

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
