"""
PDF Detection Adapter - Bridges file I/O with detection service.

This adapter handles PDF file reading and delegates detection logic
to the OrderDetectionService. This separation allows easy testing
and keeps file I/O concerns separate from business logic.
"""

from pathlib import Path
import re
import sys
import pdfplumber

# Add src to path for imports
_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.enums import OrderType
from business_logic.services.order_detection_service import OrderDetectionService


class PDFOrderDetector:
    """
    Adapter that reads PDFs and uses OrderDetectionService for detection.
    
    This class handles the file I/O layer, keeping it separate from
    the pure business logic in OrderDetectionService.
    """
    
    def __init__(self, detection_service: OrderDetectionService | None = None):
        """
        Initialize detector with optional service dependency injection.
        
        Args:
            detection_service: Optional detection service instance.
                             If None, creates a new one.
        """
        self._service = detection_service or OrderDetectionService()
    
    def detect_order_type(
        self,
        pdf_path: Path | str,
        silent: bool = False
    ) -> OrderType:
        """
        Detect order type from PDF file.
        
        This is the main entry point that replaces the old detect_order_type
        function. It reads the PDF and delegates to the service.
        
        Detection order:
        1. Try known agency detection via OrderDetectionService
        2. If UNKNOWN, check for Charmaine-style template
        3. If still UNKNOWN and has encoding issues, prompt for H&L Partners
        
        Args:
            pdf_path: Path to PDF file
            silent: If True, suppress interactive prompts
            
        Returns:
            OrderType enum value
        """
        pdf_path = Path(pdf_path)
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                # Extract text from first page
                first_page_text = pdf.pages[0].extract_text() or ""
                
                # Extract text from second page if available
                second_page_text = None
                if len(pdf.pages) > 1:
                    second_page_text = pdf.pages[1].extract_text()
                
                # Use service for detection of known agencies
                order_type = self._service.detect_from_text(
                    first_page_text,
                    second_page_text
                )
                
                # If no known agency detected, check for Charmaine template
                if order_type == OrderType.UNKNOWN:
                    if self._is_charmaine_template(first_page_text, pdf):
                        return OrderType.CHARMAINE
                
                # Handle encoding issues with user interaction if needed
                if order_type == OrderType.UNKNOWN and not silent:
                    if self._service.has_encoding_issues(first_page_text):
                        print("\n[DETECT] [!] WARNING: PDF has encoding issues - text cannot be read properly")
                        print("[DETECT] This PDF may need to be re-saved using Chrome's 'Print to PDF' feature")
                        print("[DETECT] If you know the agency, you can manually specify it below:")
                        print("\nIs this an H&L Partners order? (y/n): ", end="")
                        response = input().strip().lower()
                        if response in ['y', 'yes']:
                            return OrderType.HL
                
                return order_type
                
        except Exception as e:
            print(f"[DETECT] Error detecting order type: {e}")
            return OrderType.UNKNOWN
    
    def _is_charmaine_template(
        self,
        text: str,
        pdf: pdfplumber.PDF
    ) -> bool:
        """
        Detect Charmaine's Excel-based template format.
        
        Key indicators (need 3 of 5):
        - "Crossings TV" in title (with colon, "Media Proposal", etc.)
        - "AIRTIME" or "Schedule" in header
        - "Advertiser" field present
        - "ROS Bonus" or "BONUS" in table rows
        - "Charmaine" in submitted by / AE email
        
        Args:
            text: First page text
            pdf: Open pdfplumber PDF object
            
        Returns:
            True if this matches Charmaine's template
        """
        text_lower = text.lower()
        
        # Marker 1: Crossings TV title (various formats)
        has_crossings_title = (
            "crossings tv:" in text_lower
            or "crossings tv media proposal" in text_lower
        )
        
        # Marker 2: Airtime/Schedule keywords
        has_airtime = (
            "airtime" in text_lower
            or ("flight" in text_lower and "schedule" in text_lower)
        )
        
        # Marker 3: Advertiser field
        has_advertiser = "advertiser" in text_lower
        
        # Marker 4: Bonus lines (ROS Bonus, or just BONUS with language)
        has_bonus = (
            "ros bonus" in text_lower
            or "bonus" in text_lower
        )
        
        # Marker 5: Charmaine's name (in submitted by or email)
        has_charmaine = (
            "charmaine" in text_lower
        )
        
        # Must have at least 3 of 5 markers
        markers = [has_crossings_title, has_airtime, has_advertiser, has_bonus, has_charmaine]
        if sum(markers) < 3:
            return False
        
        # Extra confidence: check for table with $ amounts
        try:
            tables = pdf.pages[0].extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        if row and any(
                            cell and '$' in str(cell)
                            for cell in row if cell
                        ):
                            return True
        except Exception:
            pass  # Table extraction is a heuristic; failure is non-fatal

        # If we had 3+ markers, still likely Charmaine even without table parse
        return sum(markers) >= 3
    
    def detect_multi_order_pdf(
        self,
        pdf_path: Path | str
    ) -> tuple[OrderType, int]:
        """
        Detect if PDF contains multiple orders and return count.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Tuple of (OrderType, count of orders)
        """
        pdf_path = Path(pdf_path)
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                # Extract ALL text from ALL pages
                full_text = ""
                for page in pdf.pages:
                    full_text += page.extract_text() or ""
                
                # Detect order type
                first_page_text = pdf.pages[0].extract_text() or ""
                second_page_text = None
                if len(pdf.pages) > 1:
                    second_page_text = pdf.pages[1].extract_text()
                
                order_type = self._service.detect_from_text(
                    first_page_text,
                    second_page_text
                )
                
                # If unknown, check Charmaine
                if order_type == OrderType.UNKNOWN:
                    if self._is_charmaine_template(first_page_text, pdf):
                        order_type = OrderType.CHARMAINE
                
                # Check for multiple orders
                if order_type == OrderType.TCAA:
                    count = self._service.count_tcaa_orders(full_text)
                    return (order_type, count)
                
                # Charmaine multi-page: each page with data = separate order
                if order_type == OrderType.CHARMAINE:
                    count = self._count_charmaine_orders(pdf)
                    return (order_type, count)
                
                # All other types: assume single order
                return (order_type, 1)
                
        except Exception as e:
            print(f"[DETECT] Error detecting multi-order PDF: {e}")
            return (OrderType.UNKNOWN, 1)
    
    def _count_charmaine_orders(self, pdf: pdfplumber.PDF) -> int:
        """
        Count the number of orders in a Charmaine-style PDF.
        
        Each page with actual order data (language rows, $ amounts)
        counts as a separate order. Signature/audit pages are excluded.
        
        Args:
            pdf: Open pdfplumber PDF object
            
        Returns:
            Number of orders found
        """
        count = 0
        for page in pdf.pages:
            try:
                tables = page.extract_tables()
                if tables:
                    has_data = any(
                        row for table in tables for row in table
                        if row and any(
                            cell and ('$' in str(cell) or 'ROS' in str(cell))
                            for cell in row if cell
                        )
                    )
                    if has_data:
                        count += 1
            except Exception:
                continue
        return max(count, 1)
    
    def split_multi_order_pdf(
        self,
        pdf_path: Path | str,
        order_type: OrderType
    ) -> list[dict]:
        """
        Split a multi-order PDF into separate order data.
        
        Args:
            pdf_path: Path to PDF file
            order_type: Type of orders in the PDF
            
        Returns:
            List of dicts with order-specific data
        """
        pdf_path = Path(pdf_path)
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                # Extract ALL text
                full_text = ""
                for page in pdf.pages:
                    full_text += page.extract_text() or ""
                
                # Split based on order type
                if order_type == OrderType.TCAA:
                    return self._service.split_tcaa_orders(full_text)
                
                # Default: single order
                return [{'estimate': 'Unknown', 'text': full_text}]
                
        except Exception as e:
            print(f"[SPLIT] Error splitting multi-order PDF: {e}")
            return [{'estimate': 'Unknown', 'text': ''}]
    
    def extract_client_name(
        self,
        pdf_path: Path | str,
        order_type: OrderType
    ) -> str | None:
        """
        Extract client/advertiser name from PDF.
        
        Args:
            pdf_path: Path to PDF file
            order_type: Detected order type
            
        Returns:
            Client name if found, None otherwise
        """
        pdf_path = Path(pdf_path)
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                first_page_text = pdf.pages[0].extract_text() or ""
                
                second_page_text = None
                if len(pdf.pages) > 1:
                    second_page_text = pdf.pages[1].extract_text()
                
                # Charmaine template: look for "Advertiser" field
                if order_type == OrderType.CHARMAINE:
                    return self._extract_charmaine_client_name(first_page_text)
                
                return self._service.extract_client_name(
                    first_page_text,
                    second_page_text,
                    order_type
                )
                
        except Exception as e:
            print(f"[EXTRACT] Error extracting client name: {e}")
            return None
    
    def _extract_charmaine_client_name(self, text: str) -> str | None:
        """
        Extract advertiser name from Charmaine-style PDF text.
        
        Looks for "Advertiser {name}" pattern in the header section.
        
        Args:
            text: First page text content
            
        Returns:
            Advertiser name or None
        """
        for line in text.split('\n'):
            line_stripped = line.strip()
            if line_stripped.startswith("Advertiser "):
                return line_stripped.replace("Advertiser ", "", 1).strip()
        return None
    
    def extract_customer_name(
        self,
        pdf_path: Path | str,
        order_type: OrderType | None = None
    ) -> str | None:
        """
        Extract customer/advertiser name from PDF (alias for extract_client_name).
        
        If order_type is not provided, detects it first.
        
        Args:
            pdf_path: Path to PDF file
            order_type: Optional detected order type (will detect if not provided)
            
        Returns:
            Customer name if found, None otherwise
        """
        if order_type is None:
            order_type = self.detect_order_type(pdf_path, silent=True)
        
        return self.extract_client_name(pdf_path, order_type)
    
    def extract_customer_name_from_text(
        self,
        text: str,
        order_type: OrderType
    ) -> str | None:
        """
        Extract customer/advertiser name from text.
        
        Useful for multi-order PDFs where text has already been split.
        
        Args:
            text: Order text
            order_type: Type of order
            
        Returns:
            Customer name if found, None otherwise
        """
        # Charmaine template: look for "Advertiser" field in text
        if order_type == OrderType.CHARMAINE:
            return self._extract_charmaine_client_name(text)
        
        return self._service.extract_client_name(
            text,
            None,  # No second page text for split orders
            order_type
        )
    
