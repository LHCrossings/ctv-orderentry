"""
Order Scanner Fix - Create separate Order objects for each estimate

This fixes the issue where annual buys with multiple estimates
were being treated as a single order.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List
from enum import Enum

# Assuming these exist in your refactored system
# from domain.entities import Order, OrderType, OrderStatus
# from parsers.tcaa_parser import TCAAParser


class OrderType(Enum):
    """Order types"""
    TCAA = "tcaa"
    WORLDLINK = "worldlink"
    DAVISELEN = "daviselen"
    # ... other types


class OrderStatus(Enum):
    """Order processing status"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Order:
    """Simplified Order entity for demonstration"""
    pdf_path: str
    order_type: OrderType
    status: OrderStatus
    customer_name: str
    estimate_number: str = "Unknown"
    description: str = ""
    

class OrderScanner:
    """
    Scans directory for order PDFs and creates Order objects.
    
    CRITICAL FIX: For annual buys with multiple estimates,
    creates separate Order objects for each estimate.
    """
    
    def __init__(self, orders_dir: str):
        self.orders_dir = Path(orders_dir)
    
    def scan(self) -> List[Order]:
        """
        Scan directory and create Order objects.
        
        Returns:
            List of Order objects (one per estimate, not per PDF)
        """
        orders = []
        
        if not self.orders_dir.exists():
            return orders
        
        for pdf_file in self.orders_dir.glob("*.pdf"):
            detected_orders = self._process_pdf(pdf_file)
            orders.extend(detected_orders)
        
        return orders
    
    def _process_pdf(self, pdf_path: Path) -> List[Order]:
        """
        Process a single PDF and create Order object(s).
        
        CRITICAL: This returns a LIST because one PDF can contain
        multiple estimates (annual buys).
        
        Returns:
            List of Order objects (one per estimate)
        """
        orders = []
        
        # Detect order type
        order_type = self._detect_order_type(pdf_path)
        
        if order_type == OrderType.TCAA:
            # TCAA can have multiple estimates
            orders = self._process_tcaa(pdf_path)
        else:
            # Other types (for now, single order per PDF)
            order = Order(
                pdf_path=str(pdf_path),
                order_type=order_type,
                status=OrderStatus.PENDING,
                customer_name="Unknown",
                estimate_number="Unknown",
                description=""
            )
            orders = [order]
        
        return orders
    
    def _detect_order_type(self, pdf_path: Path) -> OrderType:
        """Detect which agency/type this order is"""
        # This would use your actual detection logic
        # For now, simplified:
        
        # Import actual parser
        try:
            from parsers.tcaa_parser import TCAAParser
            if TCAAParser.detect(str(pdf_path)):
                return OrderType.TCAA
        except Exception:
            pass
        
        # Add other detections...
        return OrderType.TCAA  # Default for testing
    
    def _process_tcaa(self, pdf_path: Path) -> List[Order]:
        """
        Process TCAA PDF and create Order objects for each estimate.
        
        THIS IS THE KEY FIX: One PDF → Multiple Orders
        """
        orders = []
        
        try:
            # Use the fixed parser
            from parsers.tcaa_parser import TCAAParser
            
            estimates = TCAAParser.parse(str(pdf_path))
            
            print(f"[SCAN] {pdf_path.name}: Detected {len(estimates)} estimate(s)")
            
            for estimate in estimates:
                order = Order(
                    pdf_path=str(pdf_path),
                    order_type=OrderType.TCAA,
                    status=OrderStatus.PENDING,
                    customer_name="Western Washington Toyota Dlrs Adv Assoc",
                    estimate_number=estimate.estimate_number,
                    description=estimate.description
                )
                orders.append(order)
        
        except Exception as e:
            print(f"[ERROR] Failed to process TCAA PDF {pdf_path.name}: {e}")
            # Fallback: create single order with unknown estimate
            order = Order(
                pdf_path=str(pdf_path),
                order_type=OrderType.TCAA,
                status=OrderStatus.PENDING,
                customer_name="Western Washington Toyota Dlrs Adv Assoc",
                estimate_number="Unknown",
                description=""
            )
            orders = [order]
        
        return orders


# Example usage
if __name__ == '__main__':
    scanner = OrderScanner("/mnt/user-data/uploads")
    orders = scanner.scan()
    
    print(f"\nTotal orders found: {len(orders)}")
    for i, order in enumerate(orders, 1):
        print(f"[{i}] {Path(order.pdf_path).name}")
        print(f"    Estimate: {order.estimate_number}")
        print(f"    Description: {order.description}")
        print(f"    Type: {order.order_type.value}")
