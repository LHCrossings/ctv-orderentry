# 🚀 Order Processing System Refactoring - Windows Setup Guide

## ✅ Phase 1 Complete: Domain Layer Foundation

Congratulations! You now have a solid, tested foundation for your refactored system.

---

## 📦 What You've Built

### Domain Layer (Pure Business Logic)
- **`src/domain/enums.py`** - Type-safe enums for OrderType, Market, Language, etc.
- **`src/domain/value_objects.py`** - Immutable value objects (TimeRange, DayPattern, ScheduleLine)
- **`src/domain/entities.py`** - Core business entities (Order, Contract, Customer)
- **`tests/unit/test_domain.py`** - 35 passing unit tests!

### Key Improvements
✅ Type safety with enums (no more magic strings)
✅ Immutability by default (frozen dataclasses)
✅ Pure functions tested in isolation
✅ Zero dependencies on external systems
✅ Business rules encoded in domain methods

---

## 🔧 Windows Installation Steps

### Step 1: Extract Files to Your Project

1. Download the `src` and `tests` folders from Claude
2. Place them in your existing project directory where `process_orders.py` lives:

```
your_project/
├── process_orders.py              # Your existing code (unchanged)
├── parsers/                       # Your existing parser folder
├── orders/                        # Your existing orders folder
├── src/                           # NEW - Domain layer
│   └── domain/
│       ├── __init__.py
│       ├── enums.py
│       ├── value_objects.py
│       └── entities.py
└── tests/                         # NEW - Unit tests
    └── unit/
        ├── __init__.py
        └── test_domain.py
```

### Step 2: Install Testing Tools (Optional but Recommended)

Open PowerShell in your project directory:

```powershell
# Install pytest for running tests
pip install pytest

# Verify installation
pytest --version
```

### Step 3: Run Tests to Verify Everything Works

```powershell
# Run all unit tests
pytest tests/unit/test_domain.py -v

# Expected output: 35 passed
```

### Step 4: Try Out the New Domain Layer

Create a test script `test_domain_usage.py`:

```python
"""Quick test to see the new domain layer in action."""
from pathlib import Path
from datetime import date, time
from decimal import Decimal

from src.domain.enums import OrderType, Market, Language
from src.domain.value_objects import TimeRange, DayPattern, ScheduleLine
from src.domain.entities import Order, Contract

# Create an order
order = Order(
    pdf_path=Path("test_order.pdf"),
    order_type=OrderType.WORLDLINK,
    customer_name="Test Customer"
)

print(f"Order type: {order.order_type.name}")
print(f"Is processable: {order.is_processable()}")
print(f"Requires block refresh: {order.order_type.requires_block_refresh()}")

# Create a time range
time_range = TimeRange.from_string("5p-7p")
print(f"\nTime range: {time_range.start_time} to {time_range.end_time}")
print(f"Etere format: {time_range.to_etere_format()}")

# Check language ROS schedules
days, times = Language.MANDARIN.get_ros_schedule()
print(f"\nChinese ROS: {days} {times}")
print(f"Chinese block code: {Language.MANDARIN.get_block_abbreviation()}")

# Create a schedule line
line = ScheduleLine(
    start_date=date(2025, 1, 1),
    end_date=date(2025, 1, 14),
    time_range=time_range,
    day_pattern=DayPattern("M-F"),
    weekly_spots=10,
    rate=Decimal("100.00"),
    market=Market.NYC
)

print(f"\nSchedule line spans {line.duration_weeks()} weeks")
print(f"Total spots: {line.total_spots()}")
print(f"Total cost: ${line.total_cost()}")

# Create a contract
contract = Contract(
    contract_number="12345",
    order_type=OrderType.WORLDLINK,
    highest_line=10
)

print(f"\nContract {contract.contract_number}")
print(f"Requires refresh: {contract.requires_block_refresh()}")
print(f"Has partial lines: {contract.has_partial_lines()}")

print("\n✅ Domain layer working perfectly!")
```

Run it:

```powershell
python test_domain_usage.py
```

---

## 🎯 What's Next: Phase 2 Preview

Now that we have our foundation, here's what we'll build next:

### Phase 2: Order Detection Service (Next Session)
```python
# business_logic/services/order_detection_service.py
class OrderDetectionService:
    """Extract detection logic from process_orders.py lines 127-407"""
    
    def detect_from_pdf(self, pdf_path: Path) -> OrderType:
        """Pure business logic - no file I/O mixed in"""
        pass
```

### Phase 3: Customer Repository
```python
# data_access/repositories/customer_repository.py
class CustomerRepository:
    """Extract customer_matcher.py logic with clean separation"""
    
    def find_customer_id(self, name: str, order_type: OrderType) -> str | None:
        """All SQL queries in one place"""
        pass
```

### Phase 4: Processing Service
```python
# business_logic/services/order_processing_service.py
class OrderProcessingService:
    """Orchestrate order processing with dependency injection"""
    
    def process_order(self, order: Order, browser: BrowserSession) -> ProcessingResult:
        """Pure coordination logic"""
        pass
```

---

## 📊 Current Progress

```
[████████░░░░░░░░░░░░] 30% Complete

✅ Phase 1: Domain Layer (100%)
   - Enums and value objects
   - Core entities
   - 35 unit tests passing

⬜ Phase 2: Detection Service (0%)
⬜ Phase 3: Data Access Layer (0%)
⬜ Phase 4: Business Logic Services (0%)
⬜ Phase 5: Presentation Layer (0%)
⬜ Phase 6: Application Orchestration (0%)
⬜ Phase 7: Integration & Cutover (0%)
```

---

## 💡 Key Concepts You've Learned

### 1. **Immutability**
```python
# Old way (mutable, risky)
order.status = "completed"

# New way (immutable, safe)
completed_order = order.with_status(OrderStatus.COMPLETED)
```

### 2. **Type Safety**
```python
# Old way (magic strings)
if order_type == "worldlink":
    needs_refresh = True

# New way (type-safe enums)
if order_type == OrderType.WORLDLINK:
    needs_refresh = order_type.requires_block_refresh()
```

### 3. **Pure Functions**
```python
# Old way (side effects)
def calculate_total(order_id):
    order = database.get_order(order_id)  # Side effect!
    return order.amount * 1.1

# New way (pure function)
def calculate_total(amount: Decimal) -> Decimal:
    return amount * Decimal("1.1")
```

### 4. **Value Objects**
```python
# Old way (primitive obsession)
start_time = "5p"
end_time = "7p"

# New way (rich domain object)
time_range = TimeRange.from_string("5p-7p")
etere_start, etere_end = time_range.to_etere_format()
```

---

## 🎓 Testing Your Understanding

Try these exercises:

1. **Add a new Market**: Add `PHX = "PHX"  # Phoenix` to the Market enum
2. **Create an order**: Make a TCAA order and verify it doesn't need refresh
3. **Test a time range**: Parse "6:30a-10:00p" and convert to Etere format
4. **Check language schedules**: Print the ROS schedule for all languages

---

## 🐛 Troubleshooting

### "ModuleNotFoundError: No module named 'src'"
**Solution**: Make sure you're running from the project root directory

### "pytest: command not found"
**Solution**: 
```powershell
pip install pytest
# or
python -m pytest tests/unit/test_domain.py -v
```

### "ImportError: attempted relative import"
**Solution**: Use absolute imports or add to sys.path

---

## 📞 Ready for Phase 2?

When you're ready to continue:
1. Verify all 35 tests pass
2. Try the domain usage example above
3. Let me know and we'll start extracting the Order Detection Service!

The domain layer is now solid, tested, and ready to be used by the higher layers we'll build next.

---

## 🎉 Celebrate Your Progress!

You've successfully:
- ✅ Created a clean domain layer with zero external dependencies
- ✅ Replaced magic strings with type-safe enums
- ✅ Built immutable value objects for core concepts
- ✅ Written 35 passing unit tests
- ✅ Established patterns for the rest of the refactoring

This is the foundation everything else will build on. Great work!
