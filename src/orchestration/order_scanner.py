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


def _detect_xlsx_content(file_path: Path) -> OrderType:
    """
    Peek inside an XLSX file (first 10 rows) to detect order type by content.
    Used when filename-based detection returns UNKNOWN.
    """
    try:
        try:
            import openpyxl
        except ImportError:
            print(f"[WARN] openpyxl not installed — cannot detect XLSX order type for {file_path.name}. Run: pip install openpyxl")
            return OrderType.UNKNOWN
        wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(max_row=10):
            for cell in row:
                v = str(cell.value or "").upper()
                if "IMPRENTA" in v:
                    wb.close()
                    return OrderType.IMPRENTA
                if "PROSIO" in v:
                    wb.close()
                    return OrderType.PROSIO
                if "POLARIS" in v:
                    wb.close()
                    return OrderType.POLARIS
                if "SIERRA DONOR" in v:
                    wb.close()
                    return OrderType.SIERRADONOR
                if "3OLIVESMEDIA" in v:
                    wb.close()
                    return OrderType.THREEOLIVES
        wb.close()
    except Exception as e:
        print(f"[WARN] Could not read {file_path.name}: {e}")
    return OrderType.UNKNOWN


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
        print(f"[SCAN] Looking in: {self._incoming_dir.resolve()}")
        if not self._incoming_dir.exists():
            print(f"[SCAN] Directory does not exist: {self._incoming_dir.resolve()}")
            return []

        all_files = [f for f in self._incoming_dir.iterdir() if f.is_file()]
        if all_files:
            print(f"[SCAN] Files found: {[f.name for f in all_files]}")
        else:
            print("[SCAN] Directory is empty")

        orders = []

        # Find all order files (PDF and AAAA SpotTV XML)
        # Use iterdir() instead of glob() — glob() silently fails on Windows
        # paths containing special characters like & in filenames.
        _all = [f for f in self._incoming_dir.iterdir() if f.is_file()]
        pdf_files = sorted(f for f in _all if f.suffix.lower() == ".pdf")
        xml_files = sorted(f for f in _all if f.suffix.lower() == ".xml")

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
        # Include both lowercase and uppercase extensions for Linux compatibility.
        # Deduplicate via set — case-insensitive filesystems (WSL2/NTFS) return
        # the same file for both *.xlsx and *.XLSX globs.
        # Skip ~$ Excel temp/lock files.
        print(f"[SCAN] _all suffixes: {[(f.name, repr(f.suffix)) for f in _all]}")
        print(f"[SCAN] pdf_files: {[f.name for f in pdf_files]}")
        _img_exts = {".jpg", ".jpeg", ".png", ".xlsx", ".xlsm"}
        image_xlsx_files = sorted(
            f for f in _all
            if f.suffix.lower() in _img_exts and not f.name.startswith("~$")
        )
        print(f"[SCAN] image_xlsx_files: {[f.name for f in image_xlsx_files]}")

        for file_path in image_xlsx_files:
            try:
                order_type = detect_from_filename(file_path.name)

                # For XLSX/XLSM files not identified by filename, peek inside for agency markers
                if order_type == OrderType.UNKNOWN and file_path.suffix.lower() in {".xlsx", ".xlsm"}:
                    order_type = _detect_xlsx_content(file_path)

                if order_type == OrderType.UNKNOWN:
                    print(f"[SCAN] Skipping (unknown type): {file_path.name}")
                    continue

                # Extract customer name hint
                name_upper = file_path.stem.upper()
                if "LEXUS" in name_upper:
                    customer_name = "Lexus"
                elif order_type == OrderType.IMPRENTA:
                    customer_name = "PG&E"
                elif order_type == OrderType.PROSIO:
                    customer_name = "AQMD"
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

        _count_exts = {".pdf", ".xml", ".jpg", ".jpeg", ".png", ".xlsx", ".xlsm"}
        return sum(
            1 for f in self._incoming_dir.iterdir()
            if f.is_file() and f.suffix.lower() in _count_exts
        )
