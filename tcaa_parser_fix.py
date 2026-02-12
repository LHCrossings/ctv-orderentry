"""
TCAA Parser - Fixed to handle annual buys with multiple estimates

This parser detects and extracts individual estimates from TCAA PDFs,
including annual buy documents that contain multiple monthly contracts.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
import pdfplumber
import re


@dataclass
class TCAAEstimate:
    """Represents a single estimate within a TCAA order"""
    estimate_number: str
    description: str
    flight_start: str
    flight_end: str
    total_spots: int
    total_cost: Decimal
    lines: list  # Will contain line items
    

class TCAAParser:
    """Parser for TCAA (The Asian Channel) orders"""
    
    @staticmethod
    def detect(pdf_path: str) -> bool:
        """
        Detect if PDF is a TCAA order.
        
        Returns:
            True if TCAA order detected
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                first_page_text = pdf.pages[0].extract_text() or ""
                
                # TCAA indicators
                indicators = [
                    "Western Washington Toyota Dlrs Adv Assoc",
                    "Seattle-Tacoma",
                    "CRTV-Cable",
                    "CRTV-TV"
                ]
                
                return all(indicator in first_page_text for indicator in indicators)
        except Exception:
            return False
    
    @staticmethod
    def parse(pdf_path: str) -> list[TCAAEstimate]:
        """
        Parse TCAA PDF and extract all estimates.
        
        For annual buys, this will return multiple TCAAEstimate objects.
        For single orders, returns a list with one TCAAEstimate.
        
        Returns:
            List of TCAAEstimate objects
        """
        estimates = []
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                all_text = ""
                for page in pdf.pages:
                    all_text += page.extract_text() or ""
                
                # Find all estimate blocks
                estimate_sections = TCAAParser._find_estimate_sections(all_text)
                
                for section in estimate_sections:
                    estimate = TCAAParser._parse_estimate_section(section)
                    if estimate:
                        estimates.append(estimate)
        
        except Exception as e:
            print(f"[ERROR] Failed to parse TCAA PDF: {e}")
        
        return estimates
    
    @staticmethod
    def _find_estimate_sections(text: str) -> list[str]:
        """
        Split text into individual estimate sections.
        
        Each section starts with "Estimate:" and ends before the next estimate
        or at the end of document.
        """
        sections = []
        
        # Split by estimate headers
        # Pattern: "Estimate: 9709" followed by description, dates, etc.
        lines = text.split('\n')
        current_section = []
        in_estimate = False
        
        for line in lines:
            # Check if this is an estimate header line
            if re.search(r'Estimate:\s*\d{4}', line):
                # Save previous section if exists
                if current_section:
                    sections.append('\n'.join(current_section))
                # Start new section
                current_section = [line]
                in_estimate = True
            elif in_estimate:
                current_section.append(line)
                
                # Check if we hit the end of this estimate
                # (next estimate starts, or summary section)
                if 'Estimate Total:' in line or 'Page:' in line:
                    if 'of 2' in line or 'of 20' in line:  # End of estimate pages
                        sections.append('\n'.join(current_section))
                        current_section = []
                        in_estimate = False
        
        # Add final section
        if current_section:
            sections.append('\n'.join(current_section))
        
        return sections
    
    @staticmethod
    def _parse_estimate_section(section: str) -> Optional[TCAAEstimate]:
        """
        Parse a single estimate section into TCAAEstimate object.
        """
        try:
            # Extract estimate number
            estimate_match = re.search(r'Estimate:\s*(\d{4})', section)
            if not estimate_match:
                return None
            estimate_number = estimate_match.group(1)
            
            # Extract description
            desc_match = re.search(r'Description:\s*([^\n]+)', section)
            description = desc_match.group(1).strip() if desc_match else f"Estimate {estimate_number}"
            
            # Extract flight dates
            flight_match = re.search(
                r'Flight (?:Date|Start Date):\s*(\d{1,2}/\d{1,2}/\d{4}).*?'
                r'(?:Flight End Date:\s*)?(\d{1,2}/\d{1,2}/\d{4})',
                section,
                re.DOTALL
            )
            if not flight_match:
                return None
            
            flight_start = flight_match.group(1)
            flight_end = flight_match.group(2)
            
            # Extract total spots
            spots_match = re.search(r'TOTAL SPOTS:\s*(\d+)', section)
            total_spots = int(spots_match.group(1)) if spots_match else 0
            
            # Extract total cost
            cost_match = re.search(r'TOTAL COST:\s*\$?([\d,]+\.\d{2})', section)
            if cost_match:
                cost_str = cost_match.group(1).replace(',', '')
                total_cost = Decimal(cost_str)
            else:
                total_cost = Decimal('0.00')
            
            # Parse line items (simplified for now - can be expanded)
            lines = TCAAParser._parse_line_items(section)
            
            # CRITICAL FIX: Skip summary-only pages
            # Summary pages have estimate info but no line items and no schedule totals
            has_schedule = 'SCHEDULE TOTALS' in section or 'Station Total:' in section
            
            if not has_schedule and total_spots == 0:
                # This is a summary page, skip it
                return None
            
            return TCAAEstimate(
                estimate_number=estimate_number,
                description=description,
                flight_start=flight_start,
                flight_end=flight_end,
                total_spots=total_spots,
                total_cost=total_cost,
                lines=lines
            )
        
        except Exception as e:
            print(f"[ERROR] Failed to parse estimate section: {e}")
            return None
    
    @staticmethod
    def _parse_line_items(section: str) -> list[dict]:
        """
        Parse individual line items from estimate section.
        
        Returns list of dicts with line item details.
        """
        lines = []
        
        # Find the schedule table section
        table_match = re.search(
            r'Station\s+Day\s+DP\s+Time\s+Program.*?Station Total:',
            section,
            re.DOTALL
        )
        
        if not table_match:
            return lines
        
        table_text = table_match.group(0)
        
        # Parse each line (simplified - expand based on needs)
        # Pattern: CRTV-Cable M-Su RT 6:00a-7:00a CRTV-TV mandarin news 0.0 30 14 14 14 42 $44.00 $0.00
        line_pattern = re.compile(
            r'CRTV-Cable\s+'
            r'(M-Su|Sa-Su|M-F|[A-Z][a-z](?:-[A-Z][a-z])?)\s+'  # Days
            r'(RT|VE|AV)\s+'  # Daypart
            r'([\d:apm\-\s]+)\s+'  # Time
            r'([^\d]+?)\s+'  # Program name
            r'([\d.]+)\s+'  # Rating
            r'(\d+)\s+'  # Duration
            r'(.*?)$',  # Rest of line
            re.MULTILINE
        )
        
        for match in line_pattern.finditer(table_text):
            days, daypart, time_range, program, rating, duration, rest = match.groups()
            
            # Extract spot counts and rates from 'rest'
            numbers = re.findall(r'\d+', rest)
            
            lines.append({
                'days': days.strip(),
                'daypart': daypart.strip(),
                'time_range': time_range.strip(),
                'program': program.strip(),
                'duration': int(duration),
                'spot_counts': numbers[:-2] if len(numbers) > 2 else numbers,  # All but last 2
                'total_spots': int(numbers[-2]) if len(numbers) >= 2 else 0,
            })
        
        return lines


# Example usage for testing
if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
        
        if TCAAParser.detect(pdf_path):
            print(f"✓ Detected as TCAA order")
            estimates = TCAAParser.parse(pdf_path)
            print(f"✓ Found {len(estimates)} estimate(s)")
            
            for est in estimates:
                print(f"\nEstimate {est.estimate_number}:")
                print(f"  Description: {est.description}")
                print(f"  Flight: {est.flight_start} - {est.flight_end}")
                print(f"  Total Spots: {est.total_spots}")
                print(f"  Total Cost: ${est.total_cost}")
                print(f"  Lines: {len(est.lines)}")
        else:
            print("✗ Not a TCAA order")
