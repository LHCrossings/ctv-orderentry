"""
REPLACEMENT METHOD for order_detection_service.py

Replace the split_tcaa_orders() method (lines ~171-203) with this version.
"""

def split_tcaa_orders(self, full_text: str) -> list[dict[str, str]]:
    """
    Split a multi-order TCAA PDF into individual orders.
    
    FIXED: Now filters out summary-only pages that have estimate numbers
    but no actual schedule data.

    Args:
        full_text: Complete text from all pages of PDF

    Returns:
        List of dicts with 'estimate' and 'text' for each order
    """
    import re

    # Split text into sections by page breaks or major section markers
    # TCAA PDFs have distinct sections - each starting with estimate header
    
    # Strategy: Find sections that have both:
    # 1. "Estimate: XXXX" 
    # 2. "SCHEDULE TOTALS" or "Station Total:" (indicates actual schedule, not summary)
    
    sections = []
    
    # Split by estimate markers with more context
    # Pattern: Estimate number followed by actual schedule content
    estimate_pattern = r'Estimate:\s*(\d+)'
    
    # Find all estimates in the text
    all_estimates = re.findall(estimate_pattern, full_text)
    
    if not all_estimates:
        return [{'estimate': 'Unknown', 'text': full_text}]
    
    # Split text at each "Estimate:" occurrence
    parts = re.split(r'(?=Estimate:\s*\d+)', full_text)
    
    # Process each part
    for part in parts:
        if not part.strip():
            continue
            
        # Extract estimate number from this part
        est_match = re.search(estimate_pattern, part)
        if not est_match:
            continue
        
        estimate_num = est_match.group(1)
        
        # CRITICAL FIX: Check if this is a real schedule page or just summary
        # Real schedule pages have "SCHEDULE TOTALS" or multiple line items
        has_schedule = (
            'SCHEDULE TOTALS' in part or 
            'Station Total:' in part or
            part.count('CRTV-Cable') > 3  # Multiple line items
        )
        
        # Summary pages have "Summary by Market" or just totals
        is_summary = (
            'Summary by Market' in part or
            'Summary by Station/System' in part
        )
        
        # Only include sections with actual schedule data, not summaries
        if has_schedule and not is_summary:
            sections.append({
                'estimate': estimate_num,
                'text': part
            })
    
    # If we found sections, return them
    if sections:
        return sections
    
    # Fallback: just return unique estimates even if we can't filter properly
    unique_estimates = sorted(set(all_estimates))
    return [{'estimate': est, 'text': full_text} for est in unique_estimates]


# TESTING CODE - Run this to verify the fix works
if __name__ == '__main__':
    # Simulate what order_detection_service would do
    import sys
    sys.path.insert(0, r'C:\Users\scrib\windev\OrderEntry\src')
    
    from business_logic.services.order_detection_service import OrderDetectionService
    
    # Monkey-patch the method
    OrderDetectionService.split_tcaa_orders = split_tcaa_orders
    
    # Test with your annual PDF
    service = OrderDetectionService()
    
    import pdfplumber
    pdf_path = r'C:\Users\scrib\windev\OrderEntry\incoming\2026_Annual_CRTV-TV.pdf'
    
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += page.extract_text() or ""
    
    orders = service.split_tcaa_orders(full_text)
    
    print(f"\nFound {len(orders)} orders:")
    for i, order in enumerate(orders, 1):
        print(f"  {i}. Estimate {order['estimate']}")
        # Show snippet of text
        snippet = order['text'][:200].replace('\n', ' ')
        print(f"     Text: {snippet}...")
    
    print(f"\nExpected: 7-10 orders")
    print(f"Actual: {len(orders)} orders")
