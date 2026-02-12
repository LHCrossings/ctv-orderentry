# 🎯 Domain Layer Quick Reference

## Common Operations

### Working with OrderType

```python
from src.domain.enums import OrderType

# Check if order needs block refresh
if order_type.requires_block_refresh():
    # Only WorldLink returns True
    refresh_blocks(contract)

# Check if order supports multiple markets
if order_type.supports_multiple_markets():
    # WorldLink, Misfit, RPM return True
    handle_multi_market(order)

# All available types
OrderType.WORLDLINK
OrderType.TCAA
OrderType.OPAD
OrderType.RPM
OrderType.HL_PARTNERS
OrderType.DAVISELEN
OrderType.MISFIT
OrderType.IMPACT
OrderType.IGRAPHIX
OrderType.ADMERASIA
OrderType.UNKNOWN
```

### Working with Markets

```python
from src.domain.enums import Market

# All Crossings TV markets
Market.CVC  # Central Valley (Sacramento)
Market.SFO  # San Francisco
Market.LAX  # Los Angeles
Market.SEA  # Seattle
Market.HOU  # Houston
Market.CMP  # Chicago/Minneapolis
Market.WDC  # Washington DC
Market.MMT  # Multimarket National
Market.NYC  # New York City/New Jersey

# Asian Channel
Market.DAL  # Dallas

# Check market type
if market.is_crossings_tv_market():
    # Handle Crossings TV logic
    pass

if market.is_asian_channel_market():
    # Handle Asian Channel logic
    pass
```

### Working with Languages

```python
from src.domain.enums import Language

# Get ROS schedule for a language
days, time_range = Language.MANDARIN.get_ros_schedule()
# Returns: ("M-Su", "6a-11:59p")

# Get block abbreviation for Etere
block_code = Language.FILIPINO.get_block_abbreviation()
# Returns: "T"

# All languages
Language.MANDARIN      # "M"  -> Block: "C/M",  ROS: M-Su 6a-11:59p
Language.CANTONESE     # "C"  -> Block: "C/M",  ROS: M-Su 6a-11:59p
Language.FILIPINO      # "T"  -> Block: "T",    ROS: M-Su 4p-7p
Language.KOREAN        # "K"  -> Block: "K",    ROS: M-Su 8a-10a
Language.VIETNAMESE    # "V"  -> Block: "V",    ROS: M-Su 11a-1p
Language.HMONG         # "Hm" -> Block: "Hm",   ROS: Sa-Su 6p-8p
Language.SOUTH_ASIAN   # "SA" -> Block: "SA/P", ROS: M-Su 1p-4p
Language.PUNJABI       # "P"  -> Block: "SA/P", ROS: M-Su 1p-4p
Language.JAPANESE      # "J"  -> Block: "J",    ROS: M-F 10a-11a
```

### Working with Separation Intervals

```python
from src.domain.enums import SeparationInterval, OrderType

# Get separation for an order type
customer, event, order = SeparationInterval.for_order_type(OrderType.WORLDLINK)
# Returns: (5, 0, 15)

# Standard intervals
SeparationInterval.WORLDLINK.value      # (5, 0, 15)
SeparationInterval.OPAD.value           # (15, 0, 15)
SeparationInterval.RPM.value            # (25, 0, 15)
SeparationInterval.HL_PARTNERS.value    # (25, 0, 0)
SeparationInterval.DEFAULT.value        # (15, 0, 0)
```

### Working with TimeRange

```python
from src.domain.value_objects import TimeRange

# Parse from string
tr = TimeRange.from_string("5p-7p")
# Result: TimeRange(start_time=time(17, 0), end_time=time(19, 0))

# Parse with minutes
tr = TimeRange.from_string("6:30a-10:45p")
# Result: TimeRange(start_time=time(6, 30), end_time=time(22, 45))

# Convert to Etere format
start, end = tr.to_etere_format()
# Returns: ("17:00", "19:00")

# Midnight handling (12:00a -> 23:59)
tr = TimeRange.from_string("11:00p-12:00a")
# end_time will be time(23, 59)
```

### Working with DayPattern

```python
from src.domain.value_objects import DayPattern

# Create pattern
pattern = DayPattern("M-F")

# Expand to list
days = pattern.to_day_list()
# Returns: ["M", "Tu", "W", "Th", "F"]

# Check for Sunday
if pattern.includes_sunday():
    # Handle paid programming restrictions
    no_sunday = pattern.remove_sunday()

# Common patterns
DayPattern("M-F")    # Weekdays
DayPattern("Sa-Su")  # Weekend
DayPattern("M-Su")   # Full week
DayPattern("M")      # Monday only
```

### Working with Orders

```python
from pathlib import Path
from src.domain.entities import Order
from src.domain.enums import OrderType, OrderStatus
from src.domain.value_objects import OrderInput

# Create an order
order = Order(
    pdf_path=Path("orders/incoming/worldlink_order.pdf"),
    order_type=OrderType.WORLDLINK,
    customer_name="McDonald's"
)

# Check if processable
if order.is_processable():
    process(order)

# Check if needs upfront input
if order.requires_upfront_input():
    # Collect input from user
    input_data = OrderInput(
        order_code="AUTO123",
        description="McDonald's Q1 Campaign"
    )
    order = order.with_input(input_data)

# Update status (immutable pattern)
processing = order.with_status(OrderStatus.PROCESSING)
completed = processing.with_status(OrderStatus.COMPLETED)

# Display name
print(f"Processing: {order.get_display_name()}")
```

### Working with Contracts

```python
from src.domain.entities import Contract
from src.domain.enums import OrderType

# Create contract
contract = Contract(
    contract_number="12345",
    order_type=OrderType.WORLDLINK,
    highest_line=10,
    market="NYC"
)

# Check refresh requirements
if contract.requires_block_refresh():
    start, end = contract.get_refresh_range()
    if contract.has_partial_lines():
        print(f"Refresh lines {start} to end")
    else:
        print("Refresh all lines")
```

### Working with ScheduleLine

```python
from datetime import date
from decimal import Decimal
from src.domain.value_objects import ScheduleLine, TimeRange, DayPattern
from src.domain.enums import Market, Language

# Create schedule line
line = ScheduleLine(
    start_date=date(2025, 1, 1),
    end_date=date(2025, 1, 14),
    time_range=TimeRange.from_string("5p-7p"),
    day_pattern=DayPattern("M-F"),
    weekly_spots=10,
    rate=Decimal("100.00"),
    market=Market.NYC,
    language=Language.MANDARIN
)

# Calculate metrics
weeks = line.duration_weeks()        # 2 weeks
total = line.total_spots()          # 20 spots
cost = line.total_cost()            # Decimal("2000.00")

# Check if needs splitting
next_line = create_next_line()
if line.needs_splitting(next_line):
    # Split because weekly spots differ
    split_lines = split_schedule(line, next_line)
```

### Working with ProcessingResult

```python
from src.domain.entities import ProcessingResult, Contract
from src.domain.enums import OrderType

# Create result
result = ProcessingResult(
    success=True,
    contracts=[
        Contract("12345", OrderType.WORLDLINK),
        Contract("12346", OrderType.WORLDLINK)
    ],
    order_type=OrderType.WORLDLINK
)

# Check results
if result.success and result.has_contracts():
    if result.needs_block_refresh():
        refresh_contracts = result.get_refresh_contracts()
        for contract in refresh_contracts:
            refresh_blocks(contract)
```

## Migration from Old Code

### Before (Magic Strings)
```python
order_type = "worldlink"
if order_type == "worldlink":
    needs_refresh = True

market = "NYC"
if market in ["NYC", "LAX", "SFO"]:
    process_market(market)

status = "pending"
if status == "pneding":  # Typo! Runtime error
    process()
```

### After (Type Safety)
```python
order_type = OrderType.WORLDLINK
if order_type.requires_block_refresh():
    needs_refresh = True

market = Market.NYC
if market.is_crossings_tv_market():
    process_market(market)

status = OrderStatus.PENDING
if status == OrderStatus.PNEDING:  # Compile error caught by IDE!
    process()
```

## Common Patterns

### Creating Orders from PDFs
```python
def create_order_from_pdf(pdf_path: Path, detected_type: str) -> Order:
    """Convert old string-based detection to new domain objects."""
    order_type = OrderType(detected_type)  # Validates string
    
    # Extract customer name from PDF
    customer_name = extract_customer_name(pdf_path)
    
    return Order(
        pdf_path=pdf_path,
        order_type=order_type,
        customer_name=customer_name,
        status=OrderStatus.PENDING
    )
```

### Handling Results
```python
def process_order(order: Order) -> ProcessingResult:
    """Process order and return typed result."""
    try:
        contracts = create_contracts(order)
        return ProcessingResult(
            success=True,
            contracts=contracts,
            order_type=order.order_type
        )
    except Exception as e:
        return ProcessingResult(
            success=False,
            contracts=[],
            order_type=order.order_type,
            error_message=str(e)
        )
```

### Batch Processing with Results
```python
def process_batch(orders: list[Order]) -> dict[OrderType, list[Contract]]:
    """Process multiple orders and group results by type."""
    results: dict[OrderType, list[Contract]] = {}
    
    for order in orders:
        result = process_order(order)
        if result.success:
            results.setdefault(result.order_type, []).extend(result.contracts)
    
    return results
```

## IDE Benefits

With the new domain layer, your IDE will provide:

✅ **Auto-completion** - Type `OrderType.` and see all options
✅ **Type checking** - Catch errors before running
✅ **Refactoring support** - Rename enum values across entire codebase
✅ **Documentation** - Hover over methods to see docstrings
✅ **Find usages** - See where each enum/class is used

## Testing

Run tests anytime:
```powershell
# All tests
pytest tests/unit/test_domain.py -v

# Specific test class
pytest tests/unit/test_domain.py::TestOrderType -v

# Specific test
pytest tests/unit/test_domain.py::TestOrderType::test_worldlink_requires_block_refresh -v

# With coverage
pytest tests/unit/test_domain.py --cov=src/domain
```
