"""
RANGE SELECTION FIX for presentation/cli/input_collectors.py

Add this class and update the order selection prompt to use it.
"""

from typing import List, Set


class RangeSelectionParser:
    """
    Parse user input with support for ranges (e.g., "1-4,7,9-11").
    
    Supports:
    - Individual numbers: "1 3 5" or "1,3,5"
    - Ranges: "1-4" expands to [1,2,3,4]
    - Combined: "1-4,7,9-11" expands to [1,2,3,4,7,9,10,11]
    - Special: "all" selects everything
    """
    
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
            >>> RangeSelectionParser.parse("1 3 5", 10)
            [1, 3, 5]
            
            >>> RangeSelectionParser.parse("1-4", 10)
            [1, 2, 3, 4]
            
            >>> RangeSelectionParser.parse("1-4,7,9-11", 15)
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


# INTEGRATION INSTRUCTIONS:
# ========================
# 
# Option 1: Add to existing input_collectors.py
# ----------------------------------------------
# Just copy the RangeSelectionParser class above into your 
# src/presentation/cli/input_collectors.py file.
#
# Option 2: Use in your order selection prompt
# --------------------------------------------
# Find where you prompt for order selection (likely in main.py or orchestrator.py)
# 
# REPLACE THIS:
#     user_input = input("Your selection: ").strip()
#     # Parse comma or space separated numbers
#     if user_input.lower() == 'all':
#         selected = list(range(1, len(orders) + 1))
#     else:
#         selected = [int(x) for x in user_input.replace(',', ' ').split()]
#
# WITH THIS:
#     user_input = input("Your selection: ").strip()
#     selected = RangeSelectionParser.parse(user_input, len(orders))
#
#
# UPDATE PROMPT TEXT:
# ------------------
# Change your selection instructions from:
#     "Enter order numbers (e.g., '1 3 5' or '1,3,5')"
#
# To:
#     "Enter order numbers (e.g., '1 3 5' or '1,3,5')"
#     "Enter ranges (e.g., '1-4' or '1-4,7,9-11')"
#     "Enter 'all' to process all orders"


# TESTING CODE
if __name__ == '__main__':
    print("Testing RangeSelectionParser:")
    print("=" * 50)
    
    test_cases = [
        ("1 3 5", 10, "Space-separated"),
        ("1,3,5", 10, "Comma-separated"),
        ("1-4", 10, "Simple range"),
        ("1-4,7", 10, "Range + single"),
        ("1-4,7,9-11", 15, "Multiple ranges"),
        ("all", 5, "Select all"),
        ("", 10, "Empty input"),
        ("1-20", 10, "Out of range"),
        ("5-3", 10, "Invalid range (reversed)"),
    ]
    
    for input_str, max_val, description in test_cases:
        result = RangeSelectionParser.parse(input_str, max_val)
        print(f"\n{description}:")
        print(f"  Input: '{input_str}' (max={max_val})")
        print(f"  Result: {result}")
