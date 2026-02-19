"""
CLI Input Collectors - User interaction layer for gathering inputs.

This module handles all console-based user interactions, keeping them
separate from business logic.
"""

from pathlib import Path
from typing import Protocol, Callable
import sys

# Add src to path for imports
_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.entities import Order
from domain.enums import OrderType
from domain.value_objects import OrderInput


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
    def parse(user_input: str, max_value: int) -> list[int]:
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
        
        selected = set()
        
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


class InputCollector:
    """
    Collects user input from the command line.
    
    This class separates all user interaction from business logic,
    making it easy to test and potentially swap for GUI/web interface.
    """
    
    def get_yes_no(self, prompt: str) -> bool:
        """
        Get yes/no response from user.
        
        Args:
            prompt: Question to ask user
            
        Returns:
            True for yes, False for no
        """
        while True:
            response = input(prompt).strip().lower()
            if response in ['y', 'yes']:
                return True
            elif response in ['n', 'no']:
                return False
            else:
                print("Please enter 'y' or 'n'")
    
    def get_string(
        self,
        prompt: str,
        default: str | None = None,
        required: bool = True
    ) -> str:
        """
        Get string input from user.
        
        Args:
            prompt: Prompt to display
            default: Default value if user presses Enter
            required: If True, keeps prompting until non-empty input
            
        Returns:
            User's input or default value
        """
        while True:
            if default:
                user_input = input(f"{prompt} (default: {default}): ").strip()
            else:
                user_input = input(f"{prompt}: ").strip()
            
            if user_input:
                return user_input
            elif default is not None:
                return default
            elif not required:
                return ""
            else:
                print("Input is required. Please enter a value.")
    
    def get_integer(
        self,
        prompt: str,
        default: int | None = None,
        min_value: int | None = None,
        max_value: int | None = None
    ) -> int:
        """
        Get integer input from user.
        
        Args:
            prompt: Prompt to display
            default: Default value if user presses Enter
            min_value: Minimum acceptable value
            max_value: Maximum acceptable value
            
        Returns:
            User's input as integer
        """
        while True:
            if default is not None:
                user_input = input(f"{prompt} (default: {default}): ").strip()
            else:
                user_input = input(f"{prompt}: ").strip()
            
            if not user_input and default is not None:
                return default
            
            try:
                value = int(user_input)
                
                if min_value is not None and value < min_value:
                    print(f"Value must be at least {min_value}")
                    continue
                
                if max_value is not None and value > max_value:
                    print(f"Value must be at most {max_value}")
                    continue
                
                return value
                
            except ValueError:
                print("Please enter a valid integer")
    
    def get_choice(
        self,
        prompt: str,
        choices: list[str],
        display_list: bool = True
    ) -> str:
        """
        Get choice from list of options.
        
        Args:
            prompt: Prompt to display
            choices: List of valid choices
            display_list: If True, display numbered list of choices
            
        Returns:
            User's choice from the list
        """
        if display_list:
            print()
            for i, choice in enumerate(choices, 1):
                print(f"  {i}. {choice}")
            print()
        
        while True:
            response = input(f"{prompt}: ").strip()
            
            # Try as number first
            try:
                index = int(response) - 1
                if 0 <= index < len(choices):
                    return choices[index]
            except ValueError:
                pass
            
            # Try as direct choice
            if response in choices:
                return response
            
            # Try case-insensitive match
            response_lower = response.lower()
            for choice in choices:
                if choice.lower() == response_lower:
                    return choice
            
            print(f"Invalid choice. Please select from: {', '.join(choices)}")
    
    def collect_order_input(
        self,
        order: Order,
        default_code: str | None = None,
        default_description: str | None = None
    ) -> OrderInput:
        """
        Collect input for processing an order.
        
        Args:
            order: Order being processed
            default_code: Default order code (auto-generated if None)
            default_description: Default description (auto-generated if None)
            
        Returns:
            OrderInput with collected values
        """
        print()
        print(f"Order: {order.get_display_name()}")
        print(f"Type: {order.order_type.name}")
        print(f"Customer: {order.customer_name}")
        print()
        
        # Auto-generate smart defaults if not provided
        if default_code is None or default_description is None:
            auto_code, auto_desc = self._get_smart_defaults(order)
            if default_code is None:
                default_code = auto_code
            if default_description is None:
                default_description = auto_desc
        
        # Get order code
        order_code = self.get_string(
            "Enter order code",
            default=default_code,
            required=True
        )
        
        # Get description
        description = self.get_string(
            "Enter description",
            default=default_description,
            required=True
        )
        
        # Get separation intervals (from DB or default)
        separation = self._collect_separation_intervals(order)
        
        return OrderInput(
            order_code=order_code,
            description=description,
            separation_intervals=separation,
        )
    
    def _collect_separation_intervals(
        self,
        order: Order,
    ) -> tuple[int, int, int]:
        """
        Look up default separation from customer DB and prompt user to confirm.
        
        Universal for ALL agencies. Presents DB defaults (or system default 15,0,0)
        and lets user confirm or change.
        
        Args:
            order: Order being processed
            
        Returns:
            Tuple of (customer, event, order) separation intervals in minutes
        """
        # Look up from database
        default_sep = self._get_customer_separation(
            order.customer_name or "",
            order.order_type.name.lower(),
        )
        
        if default_sep is None:
            default_sep = (15, 0, 0)  # Industry standard default
        
        # Present to user
        default_str = f"{default_sep[0]},{default_sep[1]},{default_sep[2]}"
        print(f"\nSeparation intervals (customer, event, order)")
        user_input = input(f"  Confirm or change (default: {default_str}): ").strip()
        
        if not user_input:
            return default_sep
        
        # Parse user override
        try:
            parts = [int(x.strip()) for x in user_input.split(",")]
            if len(parts) == 3:
                return (parts[0], parts[1], parts[2])
            elif len(parts) == 1:
                # Single number = customer interval, rest stays default
                return (parts[0], default_sep[1], default_sep[2])
            else:
                print(f"[WARN] Expected 3 values (got {len(parts)}), using default")
                return default_sep
        except ValueError:
            print("[WARN] Invalid input, using default")
            return default_sep
    
    def _get_customer_separation(
        self,
        customer_name: str,
        order_type: str,
    ) -> tuple[int, int, int] | None:
        """
        Look up customer separation intervals from database.
        
        Args:
            customer_name: Customer name (from order detection)
            order_type: Agency type (e.g., "opad", "tcaa")
            
        Returns:
            (customer, event, order) tuple or None if not found
        """
        try:
            import sqlite3
            from pathlib import Path
            
            db_path = Path("data") / "customers.db"
            if not db_path.exists():
                return None
            
            with sqlite3.connect(str(db_path)) as conn:
                # Exact match
                cursor = conn.execute(
                    """SELECT separation_customer, separation_event, separation_order
                    FROM customers WHERE customer_name = ? AND order_type = ?""",
                    (customer_name, order_type),
                )
                row = cursor.fetchone()
                if row:
                    return (row[0], row[1], row[2])
                
                # Fuzzy containment match
                cursor = conn.execute(
                    """SELECT customer_name, separation_customer, separation_event, separation_order
                    FROM customers WHERE order_type = ?""",
                    (order_type,),
                )
                for db_name, sep_c, sep_e, sep_o in cursor.fetchall():
                    if (db_name.lower() in customer_name.lower()
                            or customer_name.lower() in db_name.lower()):
                        return (sep_c, sep_e, sep_o)
                
                return None

        except Exception:
            return None  # DB lookup is best-effort; caller handles None

    def _get_customer_abbreviation(
        self,
        client_name: str,
        order_type: str,
    ) -> str | None:
        """
        Look up customer abbreviation from the database.
        
        Uses fuzzy matching against customer_name to find the right entry,
        then returns the abbreviation field.
        
        Args:
            client_name: Client name from the PDF
            order_type: Agency type (e.g., "opad", "sagent")
            
        Returns:
            Abbreviation string if found, None otherwise
        """
        try:
            import sqlite3
            from pathlib import Path
            
            db_path = Path("data") / "customers.db"
            if not db_path.exists():
                return None
            
            with sqlite3.connect(str(db_path)) as conn:
                # Try exact match first
                cursor = conn.execute(
                    """
                    SELECT abbreviation FROM customers
                    WHERE customer_name = ? AND order_type = ? AND abbreviation IS NOT NULL
                    """,
                    (client_name, order_type),
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return row[0]
                
                # Try fuzzy match: check if any DB name is contained in PDF name or vice versa
                cursor = conn.execute(
                    """
                    SELECT customer_name, abbreviation FROM customers
                    WHERE order_type = ? AND abbreviation IS NOT NULL
                    """,
                    (order_type,),
                )
                for db_name, abbrev in cursor.fetchall():
                    if not abbrev:
                        continue
                    # Case-insensitive containment check
                    if (db_name.lower() in client_name.lower()
                            or client_name.lower() in db_name.lower()):
                        return abbrev
                
                return None

        except Exception:
            return None  # DB lookup is best-effort; caller handles None

    def _get_smart_defaults(self, order: Order) -> tuple[str, str]:
        """
        Generate smart default values for order code and description.
        
        Uses customer database abbreviation when available, falls back
        to parsing the PDF for client name.
        
        Args:
            order: Order to generate defaults for
        
        Returns:
            Tuple of (default_code, default_description)
        """
        if order.order_type == OrderType.TCAA:
            # TCAA Toyota orders
            if order.estimate_number:
                code = f"TCAA Toyota {order.estimate_number}"
                description = f"Toyota SEA Est {order.estimate_number}"
            else:
                code = "TCAA Toyota"
                description = "Toyota SEA"
            return (code, description)
        
        if order.order_type == OrderType.OPAD:
            # opAD orders - look up abbreviation from customer DB, fall back to PDF parsing
            try:
                from parsers.opad_parser import parse_opad_pdf
                parsed = parse_opad_pdf(str(order.pdf_path))
                
                # Try to get abbreviation from customer database
                abbrev = self._get_customer_abbreviation(
                    parsed.client, "opad"
                )
                
                if not abbrev:
                    # Fallback: first word of client name
                    abbrev = parsed.client.split()[0] if parsed.client else "CLIENT"
                
                code = f"opAD {abbrev} {parsed.estimate_number}"
                desc = parsed.description if parsed.description else parsed.product
                desc = f"{desc} Est {parsed.estimate_number}"
                return (code, desc)
            except Exception as e:
                print(f"[WARN] Could not parse opAD defaults: {e}")
                return ("opAD Order", "opAD Order")
        
        # Default fallback for other order types
        return ("AUTO", "Order")
    
    def select_orders(self, orders: list[Order]) -> list[Order]:
        """
        Let user select which orders to process.
        
        UPDATED: Now supports range selection like "1-4,7,9-11"
        
        Args:
            orders: List of available orders
            
        Returns:
            List of selected orders
        """
        if not orders:
            return []
        
        print("\n" + "=" * 70)
        print("SELECT ORDERS TO PROCESS")
        print("=" * 70)
        print()
        
        # Display orders
        for i, order in enumerate(orders, 1):
            print(f"  [{i}] {order.get_display_name()}")
            print(f"      Type: {order.order_type.name}")
            print(f"      Customer: {order.customer_name}")
            print()
        
        print("Selection options:")
        print("  - Enter order numbers (e.g., '1 3 5' or '1,3,5')")
        print("  - Enter ranges (e.g., '1-4' or '1-4,7,9-11')")
        print("  - Enter 'all' to process all orders")
        print("  - Press Enter to cancel")
        print()
        
        selection = input("Your selection: ").strip()
        
        if not selection:
            return []
        
        # Use RangeSelectionParser for parsing
        indices = RangeSelectionParser.parse(selection, len(orders))
        
        if not indices:
            print("[INFO] No valid orders selected")
            return []
        
        # Convert indices to orders
        selected = [orders[idx - 1] for idx in indices]
        
        return selected
    
    def confirm_processing(self, orders: list[Order]) -> bool:
        """
        Confirm with user before processing orders.
        
        Args:
            orders: Orders about to be processed
            
        Returns:
            True if user confirms, False otherwise
        """
        print("\n" + "=" * 70)
        print(f"READY TO PROCESS {len(orders)} ORDER(S)")
        print("=" * 70)
        
        for order in orders:
            print(f"  - {order.get_display_name()} ({order.order_type.name})")
        
        print()
        return self.get_yes_no("Proceed with processing? (y/n): ")


class BatchInputCollector(InputCollector):
    """
    Input collector optimized for batch processing.
    
    Collects all inputs upfront to enable unattended processing.
    """
    
    def collect_all_order_inputs(
        self,
        orders: list[Order],
        defaults_provider: Callable | None = None
    ) -> dict[str, OrderInput]:
        """
        Collect inputs for all orders upfront.
        
        Args:
            orders: Orders to collect inputs for
            defaults_provider: Optional function to get defaults for an order
                             Signature: defaults_provider(order) -> (code, description)
            
        Returns:
            Dictionary mapping order display name to OrderInput
        """
        print("\n" + "=" * 70)
        print("GATHERING INPUTS FOR ALL ORDERS")
        print("=" * 70)
        print("Please provide order codes and descriptions for all selected orders.")
        print("This allows unattended processing after inputs are collected.")
        print("=" * 70)
        
        inputs = {}
        
        for idx, order in enumerate(orders, 1):
            print(f"\n[{idx}/{len(orders)}] {order.get_display_name()}")
            print("-" * 70)
            
            # Get defaults if provider available
            default_code = None
            default_description = None
            
            if defaults_provider:
                try:
                    default_code, default_description = defaults_provider(order)
                except Exception as e:
                    print(f"Warning: Could not get defaults: {e}")
            
            # Collect input
            order_input = self.collect_order_input(
                order,
                default_code=default_code,
                default_description=default_description
            )
            
            inputs[order.get_display_name()] = order_input
        
        print("\n" + "=" * 70)
        print("[OK] All inputs collected! Beginning unattended processing...")
        print("=" * 70)
        
        return inputs


# Convenience instances for easy import
input_collector = InputCollector()
batch_input_collector = BatchInputCollector()
