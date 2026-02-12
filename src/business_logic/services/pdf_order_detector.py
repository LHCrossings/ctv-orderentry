"""
PDF Detection Adapter - Bridges file I/O with detection service.

This adapter handles PDF file reading and delegates detection logic
to the OrderDetectionService. This separation allows easy testing
and keeps file I/O concerns separate from business logic.
"""

from pathlib import Path
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
        
        Args:
            pdf_path: Path to PDF file
            silent: If True, suppress interactive prompts
            
        Returns:
            OrderType enum value
            
        Examples:
            >>> detector = PDFOrderDetector()
            >>> order_type = detector.detect_order_type("worldlink_order.pdf")
            >>> print(order_type)
            OrderType.WORLDLINK
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
                
                # Use service for detection
                order_type = self._service.detect_from_text(
                    first_page_text,
                    second_page_text
                )
                
                # Handle encoding issues with user interaction if needed
                if order_type == OrderType.UNKNOWN and not silent:
                    if self._service.has_encoding_issues(first_page_text):
                        print("\n[DETECT] [!] WARNING: PDF has encoding issues - text cannot be read properly")
                        print("[DETECT] This PDF may need to be re-saved using Chrome's 'Print to PDF' feature")
                        print("[DETECT] If you know the agency, you can manually specify it below:")
                        print("\nIs this an H&L Partners order? (y/n): ", end="")
                        response = input().strip().lower()
                        if response in ['y', 'yes']:
                            return OrderType.HL_PARTNERS
                
                return order_type
                
        except Exception as e:
            print(f"[DETECT] Error detecting order type: {e}")
            return OrderType.UNKNOWN
    
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
            
        Examples:
            >>> detector = PDFOrderDetector()
            >>> order_type, count = detector.detect_multi_order_pdf("tcaa.pdf")
            >>> print(f"{order_type.name}: {count} orders")
            TCAA: 3 orders
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
                
                # Check for multiple orders (currently only TCAA)
                if order_type == OrderType.TCAA:
                    count = self._service.count_tcaa_orders(full_text)
                    return (order_type, count)
                
                # All other types: assume single order
                return (order_type, 1)
                
        except Exception as e:
            print(f"[DETECT] Error detecting multi-order PDF: {e}")
            return (OrderType.UNKNOWN, 1)
    
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
                
                return self._service.extract_client_name(
                    first_page_text,
                    second_page_text,
                    order_type
                )
                
        except Exception as e:
            print(f"[EXTRACT] Error extracting client name: {e}")
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
        return self._service.extract_client_name(
            text,
            None,  # No second page text for split orders
            order_type
        )
    
    def detect_order_type_legacy(
        self,
        pdf_path: Path | str,
        silent: bool = False
    ) -> str:
        """
        Legacy wrapper that returns string instead of OrderType enum.
        
        This method exists for backward compatibility with existing code.
        New code should use detect_order_type() instead.
        
        Args:
            pdf_path: Path to PDF file
            silent: If True, suppress interactive prompts
            
        Returns:
            Order type as string ("worldlink", "tcaa", etc.)
        """
        order_type = self.detect_order_type(pdf_path, silent)
        return order_type.value


# Convenience function for backward compatibility
def detect_order_type_from_pdf(
    pdf_path: Path | str,
    silent: bool = False
) -> OrderType:
    """
    Convenience function for detecting order type from PDF.
    
    This provides a simple functional interface for quick usage.
    
    Args:
        pdf_path: Path to PDF file
        silent: If True, suppress interactive prompts
        
    Returns:
        OrderType enum value
    """
    detector = PDFOrderDetector()
    return detector.detect_order_type(pdf_path, silent)
