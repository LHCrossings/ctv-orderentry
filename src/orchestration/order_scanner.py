"""
Order Scanner - Scan directories for order PDFs.

Responsible for discovering and organizing order files from the filesystem.
"""

import sys
from pathlib import Path

# Add src to path
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from business_logic.services.order_detection_service import detect_from_filename
from business_logic.services.pdf_order_detector import PDFOrderDetector
from domain.entities import Order
from domain.enums import OrderStatus, OrderType


class OrderScanner:
    """
    Scans directories for order PDF files.

    Discovers PDF files in the incoming directory and creates
    Order entities with detected types and customer information.
    """

    def __init__(
        self,
        detection_service: PDFOrderDetector,
        incoming_dir: Path
    ):
        """
        Initialize the order scanner.

        Args:
            detection_service: Service for detecting order types from PDF files
            incoming_dir: Directory to scan for orders
        """
        self._detection_service = detection_service
        self._incoming_dir = incoming_dir

    def scan_for_orders(self) -> list[Order]:
        """
        Scan the incoming directory for order PDFs.

        Automatically detects and splits multi-order PDFs (e.g., TCAA).

        Returns:
            List of Order entities, possibly multiple per PDF
        """
        if not self._incoming_dir.exists():
            return []

        orders = []

        # Find all order files (PDF and AAAA SpotTV XML)
        pdf_files = sorted(self._incoming_dir.glob("*.pdf"))
        xml_files = sorted(self._incoming_dir.glob("*.xml"))

        for pdf_path in pdf_files:
            try:
                # Check if this PDF contains multiple orders
                order_type, count = self._detection_service.detect_multi_order_pdf(pdf_path)

                if count > 1:
                    # Multi-order PDF - split it
                    print(f"\n[SCAN] {pdf_path.name}: Detected {count} orders")

                    # Get split data
                    split_orders = self._detection_service.split_multi_order_pdf(pdf_path, order_type)

                    # Create an Order entity for each sub-order
                    for order_data in split_orders:
                        estimate_number = order_data.get('estimate', 'Unknown')
                        order_text = order_data.get('text', '')

                        # Extract customer name from this specific order's text
                        customer_name = "Unknown"
                        try:
                            customer_name = self._detection_service.extract_customer_name_from_text(
                                order_text,
                                order_type
                            ) or "Unknown"
                        except Exception:
                            pass

                        # Create order entity
                        order = Order(
                            pdf_path=pdf_path,
                            order_type=order_type,
                            customer_name=customer_name,
                            status=OrderStatus.PENDING,
                            estimate_number=estimate_number
                        )

                        orders.append(order)
                else:
                    # Single order PDF
                    # FIXED: Also extract estimate number for single-order TCAA PDFs

                    # Extract customer name
                    customer_name = "Unknown"
                    try:
                        customer_name = self._detection_service.extract_customer_name(pdf_path, order_type)
                        if not customer_name:
                            customer_name = "Unknown"
                    except Exception:
                        pass

                    # Extract estimate number (especially important for TCAA)
                    estimate_number = None
                    if order_type == OrderType.TCAA:
                        try:
                            # Use split_multi_order_pdf to get estimate even for single orders
                            split_data = self._detection_service.split_multi_order_pdf(pdf_path, order_type)
                            if split_data and len(split_data) > 0:
                                estimate_number = split_data[0].get('estimate', 'Unknown')
                        except Exception:
                            pass

                    # Create order entity
                    order = Order(
                        pdf_path=pdf_path,
                        order_type=order_type,
                        customer_name=customer_name,
                        status=OrderStatus.PENDING,
                        estimate_number=estimate_number
                    )

                    orders.append(order)

            except Exception as e:
                # Log error but continue scanning
                print(f"Warning: Failed to process {pdf_path.name}: {e}")
                continue

        # Find all AAAA SpotTV XML files
        for xml_path in xml_files:
            try:
                import xml.etree.ElementTree as ET
                tree = ET.parse(xml_path)
                root = ET.ElementTree(tree.getroot()).getroot()
                # Extract advertiser name from XML for display
                ns = "http://www.AAAA.org/schemas/spotTVCableProposal"
                advertiser = root.find(f".//{{{ns}}}Advertiser")
                customer_name = advertiser.get("name", "Unknown") if advertiser is not None else "Unknown"

                order = Order(
                    pdf_path=xml_path,
                    order_type=OrderType.XML,
                    customer_name=customer_name,
                    status=OrderStatus.PENDING,
                    estimate_number=None,
                )
                orders.append(order)
            except Exception as e:
                print(f"Warning: Failed to process {xml_path.name}: {e}")
                continue

        # Find JPG / PNG / XLSX files (detected by filename, not content)
        # Include both lowercase and uppercase extensions for Linux compatibility
        image_xlsx_files: list[Path] = []
        for pattern in ("*.jpg", "*.JPG", "*.jpeg", "*.JPEG",
                         "*.png", "*.PNG", "*.xlsx", "*.XLSX"):
            image_xlsx_files.extend(self._incoming_dir.glob(pattern))
        image_xlsx_files.sort()

        for file_path in image_xlsx_files:
            try:
                order_type = detect_from_filename(file_path.name)
                if order_type == OrderType.UNKNOWN:
                    continue

                # Extract customer name hint from filename (best-effort)
                name_upper = file_path.stem.upper()
                if "LEXUS" in name_upper:
                    customer_name = "Lexus"
                else:
                    customer_name = "Unknown"

                order = Order(
                    pdf_path=file_path,
                    order_type=order_type,
                    customer_name=customer_name,
                    status=OrderStatus.PENDING,
                    estimate_number=None,
                )
                orders.append(order)

            except Exception as e:
                print(f"Warning: Failed to process {file_path.name}: {e}")
                continue

        return orders

    def get_pending_orders(self) -> list[Order]:
        """
        Get all pending orders (alias for scan_for_orders).

        Returns:
            List of pending orders
        """
        return self.scan_for_orders()

    def count_pending_orders(self) -> int:
        """
        Count the number of pending orders without creating Order objects.

        Returns:
            Number of PDF files in incoming directory
        """
        if not self._incoming_dir.exists():
            return 0

        return (
            len(list(self._incoming_dir.glob("*.pdf")))
            + len(list(self._incoming_dir.glob("*.xml")))
            + len(list(self._incoming_dir.glob("*.jpg")))
            + len(list(self._incoming_dir.glob("*.JPG")))
            + len(list(self._incoming_dir.glob("*.jpeg")))
            + len(list(self._incoming_dir.glob("*.JPEG")))
            + len(list(self._incoming_dir.glob("*.png")))
            + len(list(self._incoming_dir.glob("*.PNG")))
            + len(list(self._incoming_dir.glob("*.xlsx")))
            + len(list(self._incoming_dir.glob("*.XLSX")))
        )
