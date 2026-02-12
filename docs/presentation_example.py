"""
Integration Example: Using the Presentation Layer

This example demonstrates how to use the CLI input collectors and
output formatters with the business logic layers.
"""

from pathlib import Path
import sys

# Add src to path
_src_path = Path(__file__).parent / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.entities import Order
from domain.enums import OrderType, OrderStatus
from presentation.cli import input_collector, batch_input_collector
from presentation.formatters import order_formatter, result_formatter


def example_1_basic_input_collection():
    """Example 1: Basic input collection."""
    print("\n" + "=" * 70)
    print("EXAMPLE 1: Basic Input Collection")
    print("=" * 70)
    
    # Create sample orders
    orders = [
        Order(
            pdf_path=Path("/incoming/worldlink_001.pdf"),
            order_type=OrderType.WORLDLINK,
            customer_name="ABC Company",
            status=OrderStatus.PENDING
        ),
        Order(
            pdf_path=Path("/incoming/tcaa_002.pdf"),
            order_type=OrderType.TCAA,
            customer_name="XYZ Corp",
            status=OrderStatus.PENDING
        ),
    ]
    
    # Display orders using formatter
    print(order_formatter.format_order_list(orders))
    
    # Collect yes/no input
    proceed = input_collector.get_yes_no("Continue with example? (y/n)")
    
    if proceed:
        # Get string input
        note = input_collector.get_string(
            "Enter a note",
            default="Test note",
            required=False
        )
        
        # Get integer input
        priority = input_collector.get_integer(
            "Enter priority",
            default=5,
            min_value=1,
            max_value=10
        )
        
        # Get choice input
        action = input_collector.get_choice(
            "Select action",
            ["Process", "Skip", "Review"]
        )
        
        print(f"\nCollected inputs:")
        print(f"  Note: {note}")
        print(f"  Priority: {priority}")
        print(f"  Action: {action}")


def example_2_order_selection():
    """Example 2: Order selection workflow."""
    print("\n" + "=" * 70)
    print("EXAMPLE 2: Order Selection Workflow")
    print("=" * 70)
    
    # Create sample orders
    orders = [
        Order(
            pdf_path=Path("/incoming/worldlink_001.pdf"),
            order_type=OrderType.WORLDLINK,
            customer_name="ABC Company",
            status=OrderStatus.PENDING
        ),
        Order(
            pdf_path=Path("/incoming/tcaa_002.pdf"),
            order_type=OrderType.TCAA,
            customer_name="XYZ Corp",
            status=OrderStatus.PENDING
        ),
        Order(
            pdf_path=Path("/incoming/opad_003.pdf"),
            order_type=OrderType.OPAD,
            customer_name="Test Client",
            status=OrderStatus.PENDING
        ),
    ]
    
    # Let user select orders
    selected = input_collector.select_orders(orders)
    
    if not selected:
        print("\n[CANCELLED] No orders selected")
        return
    
    # Confirm selection
    if input_collector.confirm_processing(selected):
        print(f"\n[OK] Processing {len(selected)} order(s)...")
        
        # In real application, you'd process orders here
        for order in selected:
            print(f"  - Processing {order.get_display_name()}...")
    else:
        print("\n[CANCELLED] Processing aborted")


def example_3_batch_input_collection():
    """Example 3: Batch input collection for unattended processing."""
    print("\n" + "=" * 70)
    print("EXAMPLE 3: Batch Input Collection")
    print("=" * 70)
    
    # Create sample orders
    orders = [
        Order(
            pdf_path=Path("/incoming/worldlink_001.pdf"),
            order_type=OrderType.WORLDLINK,
            customer_name="ABC Company",
            status=OrderStatus.PENDING
        ),
        Order(
            pdf_path=Path("/incoming/tcaa_002.pdf"),
            order_type=OrderType.TCAA,
            customer_name="XYZ Corp",
            status=OrderStatus.PENDING
        ),
    ]
    
    # Optional: Define defaults provider
    def get_defaults(order):
        """Provide default values based on order."""
        code = f"AUTO-{order.order_type.name}"
        description = f"Order for {order.customer_name}"
        return (code, description)
    
    # Collect all inputs upfront
    inputs = batch_input_collector.collect_all_order_inputs(
        orders,
        defaults_provider=get_defaults
    )
    
    print(f"\n[OK] Collected inputs for {len(inputs)} order(s)")
    print("Now processing can run unattended...")
    
    # In real application, you'd process each order with its input
    for order_name, order_input in inputs.items():
        print(f"\n  Processing: {order_name}")
        print(f"    Code: {order_input.order_code}")
        print(f"    Description: {order_input.description}")


def example_4_output_formatting():
    """Example 4: Output formatting for results."""
    print("\n" + "=" * 70)
    print("EXAMPLE 4: Output Formatting")
    print("=" * 70)
    
    from domain.entities import Contract, ProcessingResult
    
    # Simulate processing results
    results = [
        ProcessingResult(
            success=True,
            order_type=OrderType.WORLDLINK,
            contracts=[
                Contract(
                    contract_number="WL-2024-001",
                    order_type=OrderType.WORLDLINK,
                    market="NYC"
                ),
                Contract(
                    contract_number="WL-2024-002",
                    order_type=OrderType.WORLDLINK,
                    market="LAX"
                ),
            ],
            error_message=None
        ),
        ProcessingResult(
            success=True,
            order_type=OrderType.TCAA,
            contracts=[
                Contract(
                    contract_number="TCAA-2024-050",
                    order_type=OrderType.TCAA
                ),
            ],
            error_message=None
        ),
        ProcessingResult(
            success=False,
            order_type=OrderType.OPAD,
            contracts=[],
            error_message="Missing required field: customer_id"
        ),
    ]
    
    # Format and display results
    summary = result_formatter.format_batch_summary(results)
    print(summary)


def example_5_complete_workflow():
    """Example 5: Complete workflow combining all components."""
    print("\n" + "=" * 70)
    print("EXAMPLE 5: Complete Workflow")
    print("=" * 70)
    
    from domain.entities import Contract, ProcessingResult
    from presentation.formatters import progress_formatter
    
    # Create sample orders
    orders = [
        Order(
            pdf_path=Path("/incoming/worldlink_001.pdf"),
            order_type=OrderType.WORLDLINK,
            customer_name="ABC Company",
            status=OrderStatus.PENDING
        ),
        Order(
            pdf_path=Path("/incoming/tcaa_002.pdf"),
            order_type=OrderType.TCAA,
            customer_name="XYZ Corp",
            status=OrderStatus.PENDING
        ),
    ]
    
    # Step 1: Display available orders
    print(order_formatter.format_order_list(orders))
    
    # Step 2: Select orders
    selected = input_collector.select_orders(orders)
    if not selected:
        print("\n[CANCELLED]")
        return
    
    # Step 3: Confirm
    if not input_collector.confirm_processing(selected):
        print("\n[CANCELLED]")
        return
    
    # Step 4: Process with progress
    print("\n" + "=" * 70)
    print("PROCESSING")
    print("=" * 70)
    
    results = []
    for i, order in enumerate(selected, 1):
        # Show progress
        progress = progress_formatter.format_progress(
            i, len(selected),
            f"Processing {order.get_display_name()}"
        )
        print(f"\n{progress}")
        
        # Simulate processing (in real app, call processing service)
        result = ProcessingResult(
            success=True,
            order_type=order.order_type,
            contracts=[
                Contract(
                    contract_number=f"{order.order_type.name}-2024-{i:03d}",
                    order_type=order.order_type
                )
            ],
            error_message=None
        )
        results.append(result)
    
    # Step 5: Display results
    print("\n")
    print(result_formatter.format_batch_summary(results))


def main():
    """Run all examples."""
    print("\n" + "#" * 70)
    print("# PRESENTATION LAYER INTEGRATION EXAMPLES")
    print("#" * 70)
    
    print("\nThis demonstrates how to use the presentation layer components")
    print("for CLI interaction and output formatting.")
    
    try:
        # Run examples
        example_1_basic_input_collection()
        example_2_order_selection()
        example_3_batch_input_collection()
        example_4_output_formatting()
        example_5_complete_workflow()
        
        print("\n" + "#" * 70)
        print("# ALL EXAMPLES COMPLETE")
        print("#" * 70)
        print()
        
    except KeyboardInterrupt:
        print("\n\n[CANCELLED] Examples interrupted by user")
        sys.exit(1)


if __name__ == "__main__":
    main()
