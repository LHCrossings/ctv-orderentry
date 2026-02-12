"""
Input Handler Fix - Support range selection like "1-4,5,9"

This allows users to select orders using:
- Individual numbers: "1 3 5" or "1,3,5"
- Ranges: "1-4" (selects 1,2,3,4)
- Combined: "1-4,7,9-11" (selects 1,2,3,4,7,9,10,11)
"""
import re
from typing import List, Set


class RangeSelectionParser:
    """Parse user input with support for ranges"""
    
    @staticmethod
    def parse(user_input: str, max_value: int) -> List[int]:
        """
        Parse user input and return list of selected numbers.
        
        Args:
            user_input: User's selection (e.g., "1-4,7,9-11")
            max_value: Maximum valid selection number
        
        Returns:
            Sorted list of unique selected numbers
        
        Examples:
            >>> parse("1 3 5", 10)
            [1, 3, 5]
            
            >>> parse("1-4", 10)
            [1, 2, 3, 4]
            
            >>> parse("1-4,7,9-11", 15)
            [1, 2, 3, 4, 7, 9, 10, 11]
        """
        if not user_input or not user_input.strip():
            return []
        
        user_input = user_input.strip().lower()
        
        # Handle 'all'
        if user_input == 'all':
            return list(range(1, max_value + 1))
        
        selected: Set[int] = set()
        
        # Replace spaces with commas for uniform processing
        user_input = user_input.replace(' ', ',')
        
        # Split by commas
        parts = user_input.split(',')
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Check if it's a range (e.g., "1-4")
            if '-' in part:
                try:
                    start, end = part.split('-', 1)
                    start_num = int(start.strip())
                    end_num = int(end.strip())
                    
                    # Validate range
                    if start_num < 1 or end_num > max_value:
                        print(f"[WARNING] Range {part} contains invalid numbers (valid: 1-{max_value})")
                        continue
                    
                    if start_num > end_num:
                        print(f"[WARNING] Invalid range {part} (start > end)")
                        continue
                    
                    # Add all numbers in range
                    for num in range(start_num, end_num + 1):
                        selected.add(num)
                
                except ValueError:
                    print(f"[WARNING] Invalid range format: {part}")
                    continue
            
            else:
                # Single number
                try:
                    num = int(part)
                    
                    # Validate number
                    if num < 1 or num > max_value:
                        print(f"[WARNING] Number {num} out of range (valid: 1-{max_value})")
                        continue
                    
                    selected.add(num)
                
                except ValueError:
                    print(f"[WARNING] Invalid number: {part}")
                    continue
        
        # Return sorted list
        return sorted(list(selected))


class OrderSelectionUI:
    """UI handler for order selection with range support"""
    
    @staticmethod
    def prompt_for_selection(orders: List, order_formatter=None) -> List[int]:
        """
        Display orders and prompt user for selection.
        
        Args:
            orders: List of Order objects
            order_formatter: Optional function to format order display
        
        Returns:
            List of selected order indices (1-based)
        """
        if not orders:
            print("[INFO] No orders available")
            return []
        
        # Display orders
        print("\n" + "=" * 70)
        print("SELECT ORDERS TO PROCESS")
        print("=" * 70)
        
        for i, order in enumerate(orders, 1):
            if order_formatter:
                print(order_formatter(i, order))
            else:
                print(f"  [{i}] {order}")
        
        # Display instructions
        print("\nSelection options:")
        print("  - Enter order numbers (e.g., '1 3 5' or '1,3,5')")
        print("  - Enter ranges (e.g., '1-4' or '1-4,7,9-11')")
        print("  - Enter 'all' to process all orders")
        print("  - Press Enter to cancel")
        
        # Get input
        while True:
            user_input = input("\nYour selection: ").strip()
            
            # Handle cancel
            if not user_input:
                print("[INFO] Selection cancelled")
                return []
            
            # Parse selection
            selected = RangeSelectionParser.parse(user_input, len(orders))
            
            if not selected:
                print("[ERROR] No valid orders selected. Please try again.")
                continue
            
            # Confirm selection
            print(f"\n[INFO] Selected {len(selected)} order(s): {selected}")
            confirm = input("Proceed? (y/n): ").strip().lower()
            
            if confirm in ('y', 'yes'):
                return selected
            else:
                print("[INFO] Selection cancelled. Please try again.")
                continue


# Testing
if __name__ == '__main__':
    # Test range parser
    print("Testing RangeSelectionParser:")
    print("=" * 50)
    
    test_cases = [
        ("1 3 5", 10),
        ("1,3,5", 10),
        ("1-4", 10),
        ("1-4,7", 10),
        ("1-4,7,9-11", 15),
        ("all", 5),
        ("", 10),
        ("1-20", 10),  # Out of range
        ("5-3", 10),   # Invalid range
    ]
    
    for input_str, max_val in test_cases:
        result = RangeSelectionParser.parse(input_str, max_val)
        print(f"Input: '{input_str}' (max={max_val})")
        print(f"Result: {result}")
        print()
