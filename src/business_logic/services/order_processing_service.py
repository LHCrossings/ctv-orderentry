"""
Order Processing Service - Orchestrates order processing workflow.

This service coordinates between detection, parsing, customer matching,
and browser automation to process orders into Etere contracts.
"""

from pathlib import Path
from typing import Protocol, Any, Optional
import sys
import shutil

# Add src to path for imports
_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

# Add browser_automation to path
_browser_automation_path = _src_path.parent / "browser_automation"
if str(_browser_automation_path) not in sys.path:
    sys.path.insert(0, str(_browser_automation_path))

from domain.entities import Order, Contract, ProcessingResult, Customer
from domain.enums import OrderType, OrderStatus
from domain.value_objects import OrderInput


class OrderProcessor(Protocol):
    """
    Protocol defining the interface for order processors.
    
    Each agency type (WorldLink, TCAA, etc.) implements this protocol
    to handle its specific processing logic.
    """
    
    def process(
        self,
        browser_session: any,  # BrowserSession type
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
        browser_session: any = None
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
                order.order_type in [OrderType.TCAA, OrderType.MISFIT, OrderType.WORLDLINK]
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
        # Route to appropriate processor with shared session
        if order.order_type == OrderType.TCAA:
            return self._process_tcaa_order(order, shared_session)
        elif order.order_type == OrderType.MISFIT:
            return self._process_misfit_order(order, shared_session)
        elif order.order_type == OrderType.SAGENT:
            return self._process_sagent_order(order, shared_session)
        else:
            # For future agencies: process_order will handle them
            return self.process_order(order, shared_session)
    
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
    
    def _process_tcaa_orders_batch(self, orders: list[Order], shared_session: any = None) -> ProcessingResult:
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
                from tcaa_automation import process_tcaa_order
                from etere_session import EtereSession
                from parsers.tcaa_parser import parse_tcaa_pdf
                
                self._tcaa_processor = {
                    'process': process_tcaa_order,
                    'session_class': EtereSession,
                    'parser': parse_tcaa_pdf
                }
            
            # Use first order for display info
            first_order = orders[0]
            
            print(f"\n{'='*70}")
            print(f"TCAA BROWSER AUTOMATION (BATCH MODE)")
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
        browser_session: any = None,  # BrowserSession type, optional for now
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
    
    def _process_tcaa_order(self, order: Order, shared_session: any = None) -> ProcessingResult:
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
                from tcaa_automation import process_tcaa_order
                from etere_session import EtereSession
                from parsers.tcaa_parser import parse_tcaa_pdf
                
                self._tcaa_processor = {
                    'process': process_tcaa_order,
                    'session_class': EtereSession,
                    'parser': parse_tcaa_pdf
                }
            
            print(f"\n{'='*70}")
            print(f"TCAA BROWSER AUTOMATION")
            print(f"{'='*70}")
            print(f"Order: {order.get_display_name()}")
            print(f"Type: {order.order_type.name}")
            print(f"Customer: {order.customer_name}")
            
            if order.order_input:
                print(f"Code: {order.order_input.order_code}")
                print(f"Description: {order.order_input.description}")
            
            print(f"{'='*70}\n")
            
            # Use shared session if provided, otherwise create own
            if shared_session:
                # BATCH MODE: Using shared session
                print("[SESSION] ✓ Using shared browser session")
                shared_session.set_market("NYC")
                
                success = self._tcaa_processor['process'](
                    driver=shared_session.driver,
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
                        success=True,
                        contracts=[contract],
                        order_type=OrderType.TCAA,
                        error_message=None
                    )
                else:
                    return ProcessingResult(
                        success=False,
                        contracts=[],
                        order_type=OrderType.TCAA,
                        error_message="TCAA processing failed"
                    )
            else:
                # STANDALONE MODE: Create own session
                with self._tcaa_processor['session_class']() as session:
                    session.set_market("NYC")
                    
                    success = self._tcaa_processor['process'](
                        driver=session.driver,
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
                            success=True,
                            contracts=[contract],
                            order_type=OrderType.TCAA,
                            error_message=None
                        )
                    else:
                        return ProcessingResult(
                            success=False,
                            contracts=[],
                            order_type=OrderType.TCAA,
                            error_message="TCAA processing failed - check browser output for details"
                        )
        
        except Exception as e:
            import traceback
            error_detail = f"TCAA processing error: {str(e)}\n{traceback.format_exc()}"
            
            print(f"\n✗ TCAA processing failed: {e}")
            
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.TCAA,
                error_message=error_detail
            )
    
    def _process_misfit_order(self, order: Order, shared_session: any = None) -> ProcessingResult:
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
                from misfit_automation import process_misfit_order
                from etere_session import EtereSession
                from parsers.misfit_parser import parse_misfit_pdf
                
                self._misfit_processor = {
                    'process': process_misfit_order,
                    'session_class': EtereSession,
                    'parser': parse_misfit_pdf
                }
            
            print(f"\n{'='*70}")
            print(f"MISFIT BROWSER AUTOMATION")
            print(f"{'='*70}")
            print(f"Order: {order.get_display_name()}")
            print(f"Type: {order.order_type.name}")
            print(f"Customer: {order.customer_name}")
            
            if order.order_input:
                print(f"Code: {order.order_input.order_code}")
                print(f"Description: {order.order_input.description}")
            
            print(f"{'='*70}\n")
            
            # Use shared session if provided, otherwise create own
            if shared_session:
                # BATCH MODE: Using shared session
                print("[SESSION] ✓ Using shared browser session")
                shared_session.set_market("NYC")
                
                success = self._misfit_processor['process'](
                    driver=shared_session.driver,
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
                    
                    print(f"\n✓ Successfully created Misfit contract")
                    
                    return ProcessingResult(
                        success=True,
                        contracts=[contract],
                        order_type=OrderType.MISFIT,
                        error_message=None
                    )
                else:
                    return ProcessingResult(
                        success=False,
                        contracts=[],
                        order_type=OrderType.MISFIT,
                        error_message="Misfit processing failed"
                    )
            else:
                # STANDALONE MODE: Create own session
                with self._misfit_processor['session_class']() as session:
                    session.set_market("NYC")
                    
                    success = self._misfit_processor['process'](
                        driver=session.driver,
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
                        
                        print(f"\n✓ Successfully created Misfit contract")
                        
                        return ProcessingResult(
                            success=True,
                            contracts=[contract],
                            order_type=OrderType.MISFIT,
                            error_message=None
                        )
                    else:
                        return ProcessingResult(
                            success=False,
                            contracts=[],
                            order_type=OrderType.MISFIT,
                            error_message="Misfit processing failed - check browser output for details"
                        )
        
        except Exception as e:
            import traceback
            error_detail = f"Misfit processing error: {str(e)}\n{traceback.format_exc()}"
            
            print(f"\n✗ Misfit processing failed: {e}")
            
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.MISFIT,
                error_message=error_detail
            )
    
    def _process_sagent_order(
        self,
        order: Order,
        shared_session: any
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
            # Import SAGENT automation
            from sagent_automation import process_sagent_order
            
            print(f"\n{'='*70}")
            print(f"PROCESSING SAGENT ORDER")
            print(f"{'='*70}")
            print(f"File: {order.pdf_path.name}")
            if order.customer_name:
                print(f"Customer: {order.customer_name}")
            print(f"{'='*70}\n")
            
            # If no shared session, create one for this order
            if shared_session is None:
                try:
                    from etere_session import EtereSession
                    
                    print("[SESSION] Creating browser session for SAGENT order...")
                    
                    with EtereSession() as session:
                        # Set master market to NYC
                        session.set_market("NYC")
                        print("[SESSION] Master market set to NYC")
                        
                        # Get pre-gathered inputs from order if available
                        pre_gathered_inputs = None
                        if hasattr(order, 'order_input') and order.order_input:
                            pre_gathered_inputs = order.order_input
                        
                        # Process order with pre-gathered inputs
                        success = process_sagent_order(
                            session.driver,
                            str(order.pdf_path),
                            shared_session=session,
                            pre_gathered_inputs=pre_gathered_inputs
                        )
                        
                        if success:
                            print(f"\n✓ SAGENT order processed successfully")
                            return ProcessingResult(
                                success=True,
                                contracts=[],
                                order_type=OrderType.SAGENT
                            )
                        else:
                            print(f"\n✗ SAGENT order processing failed")
                            return ProcessingResult(
                                success=False,
                                contracts=[],
                                order_type=OrderType.SAGENT,
                                error_message="SAGENT processing failed - check browser output for details"
                            )
                            
                except ImportError:
                    print("[ERROR] Could not import EtereSession")
                    return ProcessingResult(
                        success=False,
                        contracts=[],
                        order_type=OrderType.SAGENT,
                        error_message="EtereSession import failed"
                    )
            
            # Use existing shared session
            if hasattr(shared_session, 'set_market'):
                shared_session.set_market("NYC")
                print("[SESSION] Master market set to NYC for SAGENT multi-market order")
            
            # Get pre-gathered inputs from order if available
            pre_gathered_inputs = None
            if hasattr(order, 'order_input') and order.order_input:
                pre_gathered_inputs = order.order_input
            
            # Process order using sagent_automation with shared session
            success = process_sagent_order(
                shared_session.driver if hasattr(shared_session, 'driver') else shared_session,
                str(order.pdf_path),
                shared_session=shared_session,
                pre_gathered_inputs=pre_gathered_inputs
            )
            
            if success:
                print(f"\n✓ SAGENT order processed successfully")
                
                return ProcessingResult(
                    success=True,
                    contracts=[],
                    order_type=OrderType.SAGENT
                )
            else:
                print(f"\n✗ SAGENT order processing failed")
                
                return ProcessingResult(
                    success=False,
                    contracts=[],
                    order_type=OrderType.SAGENT,
                    error_message="SAGENT processing failed - check browser output for details"
                )
        
        except Exception as e:
            import traceback
            error_detail = f"SAGENT processing error: {str(e)}\n{traceback.format_exc()}"
            
            print(f"\n✗ SAGENT processing failed: {e}")
            
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=OrderType.SAGENT,
                error_message=error_detail
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


class LegacyProcessorAdapter:
    """
    Adapter to wrap legacy processing functions into the OrderProcessor protocol.
    
    This allows us to use existing worldlink_functions.py, tcaa_functions.py, etc.
    without rewriting them immediately.
    """
    
    def __init__(self, legacy_function: callable):
        """
        Initialize adapter with legacy processing function.
        
        Args:
            legacy_function: Function with signature:
                func(browser, pdf_path, inputs=None) -> (success, needs_refresh, contracts)
        """
        self._legacy_function = legacy_function
    
    def process(
        self,
        browser_session: any,
        pdf_path: Path,
        order_input: OrderInput | None
    ) -> ProcessingResult:
        """
        Process order using legacy function.
        
        Adapts the legacy return format to ProcessingResult.
        """
        try:
            # Call legacy function
            if order_input:
                # Convert OrderInput to dict for legacy functions
                legacy_inputs = {
                    'order_code': order_input.order_code,
                    'description': order_input.description,
                    'customer_id': order_input.customer_id,
                    'time_overrides': order_input.time_overrides,
                }
                success, needs_refresh, contract_data = self._legacy_function(
                    browser_session,
                    pdf_path,
                    legacy_inputs
                )
            else:
                success, needs_refresh, contract_data = self._legacy_function(
                    browser_session,
                    pdf_path
                )
            
            # Determine order type from contract data or infer it
            # Legacy functions return different formats, normalize here
            order_type = self._infer_order_type(contract_data)
            
            # Convert legacy contract format to Contract entities
            contracts = self._convert_contracts(contract_data, order_type, needs_refresh)
            
            return ProcessingResult(
                success=success,
                contracts=contracts,
                order_type=order_type
            )
            
        except Exception as e:
            # Extract order type if possible
            order_type = OrderType.UNKNOWN
            
            return ProcessingResult(
                success=False,
                contracts=[],
                order_type=order_type,
                error_message=str(e)
            )
    
    def _infer_order_type(self, contract_data: list) -> OrderType:
        """Infer order type from contract data."""
        # This is a simplified version - in reality, you'd need more context
        # For now, we'll need to pass order type explicitly or enhance this logic
        return OrderType.UNKNOWN  # Placeholder
    
    def _convert_contracts(
        self,
        contract_data: list,
        order_type: OrderType,
        needs_refresh: bool
    ) -> list[Contract]:
        """
        Convert legacy contract format to Contract entities.
        
        Legacy format: list of tuples like (contract_number, highest_line)
        """
        contracts = []
        
        for item in contract_data:
            if isinstance(item, tuple) and len(item) >= 2:
                contract_number, highest_line = item[0], item[1]
                contracts.append(Contract(
                    contract_number=str(contract_number),
                    order_type=order_type,
                    highest_line=highest_line
                ))
            elif isinstance(item, str):
                # Just a contract number
                contracts.append(Contract(
                    contract_number=item,
                    order_type=order_type
                ))
        
        return contracts


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
    
    elif order.order_type == OrderType.TCAA:
        # TCAA Toyota orders
        if order.estimate_number:
            code = f"TCAA Toyota {order.estimate_number}"
            description = f"Toyota SEA Est {order.estimate_number}"
        else:
            code = "TCAA Toyota"
            description = "Toyota SEA"
        return (code, description)
    
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
