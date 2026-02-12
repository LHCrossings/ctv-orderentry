# Misfit Automation - Integration Guide (Updated)

## ✅ **File Created: `misfit_automation.py`**

### 📂 **Installation**
Copy `misfit_automation.py` to:
```
C:\Users\scrib\windev\OrderEntry\browser_automation\misfit_automation.py
```

### 🎯 **System Entry Point**
**This system runs with `main.py`** - not process_orders.py!

```bash
cd C:\Users\scrib\windev\OrderEntry
python main.py
```

### 🔧 **Integration Steps**

**Step 1: Copy File**
Place `misfit_automation.py` in:
```
C:\Users\scrib\windev\OrderEntry\browser_automation\misfit_automation.py
```

**Step 2: Update Order Detection**
The system needs to detect Misfit PDFs. Find your order detection logic (likely in `src/business_logic/services/order_detection_service.py` or similar) and add:

```python
# In order detection service:
def detect_order_type(pdf_path: str) -> OrderType:
    """Detect order type from PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text()
        text_lower = text.lower()
        
        # ... existing detection logic ...
        
        # Add Misfit detection:
        if 'misfit' in text_lower or 'judy soper' in text_lower:
            return OrderType.MISFIT
```

**Step 3: Update Order Processing**
Find your order processing orchestration (likely in `src/business_logic/services/order_processing_service.py` or `src/orchestration/orchestrator.py`) and add:

```python
# At the top:
from browser_automation.misfit_automation import process_misfit_order

# In process_order() or similar method:
elif order_type == OrderType.MISFIT:
    from browser_automation.etere_session import EtereSession
    
    with EtereSession() as session:
        # Set master market to NYC for Misfit multi-market orders
        session.set_market("NYC")
        
        success = process_misfit_order(session.driver, pdf_path)
        
        if not success:
            raise Exception("Failed to process Misfit order")
```

**Step 4: Update OrderType Enum**
Add MISFIT to your OrderType enum (likely in `src/domain/enums.py`):

```python
class OrderType(Enum):
    """Order type enumeration."""
    WORLDLINK = "worldlink"
    TCAA = "tcaa"
    OPAD = "opad"
    RPM = "rpm"
    MISFIT = "misfit"  # Add this
    # ... other types ...
```

### 🧪 **Testing**

**Option 1: Standalone Test (Quick)**
Test just the Misfit automation directly:
```bash
cd C:\Users\scrib\windev\OrderEntry\browser_automation
python misfit_automation.py
```
- Prompts for PDF path
- Logs into Etere
- Creates contract
- Good for testing the automation logic

**Option 2: Full System Test (Recommended)**
Test through the main orchestrator:
```bash
cd C:\Users\scrib\windev\OrderEntry
python main.py
```
- Place Misfit PDF in `incoming/` folder
- System auto-detects as Misfit
- Full workflow with proper logging
- Tests the complete integration

### 📊 **Processing Flow**

```
1. main.py starts
   ↓
2. Scans incoming/ folder
   ↓
3. Detects Misfit PDF
   ↓
4. Creates Etere session (master market = NYC)
   ↓
5. Calls process_misfit_order()
   ↓
6. Gathers inputs upfront:
   - Customer detection (fuzzy match + database)
   - Spot duration
   - Contract code
   - Description
   ↓
7. Creates contract header
   ↓
8. Adds lines for each market:
   - LAX lines (with market=LAX)
   - SFO lines (with market=SFO)
   - CVC lines (with market=CVC)
   ↓
9. Saves contract
   ↓
10. Moves PDF to processed/
```

### 🎯 **What This File Does**

**Follows TCAA Template Exactly:**
- ✅ Uses `etere_client.py` for ALL Etere interactions
- ✅ NO Selenium code in automation file
- ✅ Clean separation of concerns
- ✅ Type hints throughout
- ✅ Comprehensive error handling

**Misfit-Specific Business Logic:**
- ✅ Multi-market handling (LAX, SFO, CVC)
- ✅ Master market always NYC
- ✅ Individual lines set their own market
- ✅ Universal customer detection (uses `customer_matching_service.py`)
- ✅ ROS schedules (all 7 languages from memory)
- ✅ Line description formats (paid vs bonus)
- ✅ Weekly distribution analysis with gap detection
- ✅ Upfront input gathering for unattended processing

### 🔍 **Key Features**

**1. Upfront Input Gathering**
```python
# Collects ALL inputs before automation starts:
- Customer ID (via universal detection)
- Spot duration (:15, :30, :45, :60)
- Contract code
- Description
```

**2. Universal Customer Detection**
```python
# Uses the refactored customer system:
from src.business_logic.services.customer_matching_service import CustomerMatchingService
from src.data_access.repositories.customer_repository import CustomerRepository

# Fuzzy matching with user confirmation
# Automatically saves new customers to database
# Works across all agencies
```

**3. Multi-Market Processing**
```python
# Processes each market separately:
for market in order.markets:  # LAX, SFO, CVC
    market_lines = order.get_lines_by_market(market_code)
    for line in market_lines:
        # Add line with market-specific setting
        etere.add_contract_line(market=market_code, ...)
```

**4. ROS Schedule Mapping**
```python
# Uses permanent memory ROS schedules:
ROS_SCHEDULES = {
    'Chinese': {'days': 'M-Su', 'time': '6a-11:59p'},
    'Filipino': {'days': 'M-Su', 'time': '4p-7p'},
    'Korean': {'days': 'M-Su', 'time': '8a-10a'},
    'Vietnamese': {'days': 'M-Su', 'time': '11a-1p'},
    'Hmong': {'days': 'Sa-Su', 'time': '6p-8p'},
    'South Asian': {'days': 'M-Su', 'time': '1p-4p'},
    'Japanese': {'days': 'M-F', 'time': '10a-11a'}
}
```

**5. Line Description Formats**
```python
# Uses parser's get_description() method:
# Paid: "M-F 7p-8p Cantonese News"
# Bonus: "M-Sun BNS Chinese ROS"
```

### 🗂️ **File Structure**

```
C:\Users\scrib\windev\OrderEntry\
├── main.py                  ← SYSTEM ENTRY POINT (run this!)
│
├── browser_automation/
│   ├── etere_client.py      ← Shared Etere methods
│   ├── etere_session.py     ← Session management
│   ├── separation_utils.py  ← Validation utilities
│   ├── tcaa_automation.py   ← Reference template
│   ├── misfit_automation.py ← NEW! Misfit automation
│   │
│   └── parsers/
│       ├── misfit_parser.py ← Already exists
│       └── ... other parsers
│
├── data/
│   └── customers.db         ← Universal customer database
│
├── src/
│   ├── business_logic/services/
│   │   ├── customer_matching_service.py  ← Customer detection
│   │   ├── order_detection_service.py    ← Add Misfit detection here
│   │   └── order_processing_service.py   ← Add Misfit processing here
│   │
│   ├── domain/
│   │   └── enums.py         ← Add MISFIT to OrderType enum
│   │
│   └── orchestration/
│       └── orchestrator.py  ← May need Misfit integration here
│
└── incoming/                ← Place Misfit PDFs here
```

### 📋 **Differences from TCAA**

| Aspect | TCAA | Misfit |
|--------|------|--------|
| Markets | Single (SEA) | Multi (LAX, SFO, CVC) |
| Master Market | NYC | NYC |
| Line Markets | All SEA | Per-line (LAX/SFO/CVC) |
| Customer | Hardcoded (75) | Universal detection |
| Line Processing | All at once | Loop through markets |
| Bonus Lines | ROS prompt | Parser handles |
| Customer Ref | Blank | "Misfit {date}" |

### ⚠️ **Important Notes**

**1. Parser Already Exists**
The file `browser_automation/parsers/misfit_parser.py` already exists and works well. No changes needed!

**2. Etere Client Methods**
Never modify `etere_client.py` field IDs or button IDs. If something breaks, fix it in `etere_client.py` - all agencies benefit!

**3. Separation Intervals**
Misfit uses: Customer=15, Event=0, Order=0

**4. Billing Settings**
Corrected to match TCAA:
- Old: "Agency with Credit Note" / "Customer"
- New: "Customer share indicating agency %" / "Agency" ✅

**5. Customer Database**
Uses the universal system at `data/customers.db`:
- Fuzzy matching with confidence scores
- User confirmation workflow
- Self-learning (adds new customers automatically)
- Works across ALL agencies (not just Misfit)

### ✅ **Pre-Flight Checklist**

Before running:
- [ ] `misfit_automation.py` copied to `browser_automation/`
- [ ] `misfit_parser.py` exists in `browser_automation/parsers/`
- [ ] `customers.db` exists in `data/`
- [ ] `etere_client.py` exists in `browser_automation/`
- [ ] OrderType.MISFIT added to enum
- [ ] Order detection updated for Misfit
- [ ] Order processing updated for Misfit
- [ ] Sample Misfit PDF ready in `incoming/`

First standalone test:
- [ ] Run: `python browser_automation/misfit_automation.py`
- [ ] Customer detection works
- [ ] Contract header creates (master market = NYC)
- [ ] Lines add for all markets
- [ ] ROS schedules applied correctly
- [ ] Block filtering works
- [ ] Separation intervals correct

Integration test via main.py:
- [ ] Run: `python main.py`
- [ ] Misfit PDF detected automatically
- [ ] Order processes without errors
- [ ] Contract visible in Etere
- [ ] All lines correct
- [ ] PDF moved to `processed/`

### 🎓 **Success Criteria**

✅ Contract created with NYC master market
✅ Lines added for all markets (LAX, SFO, CVC)
✅ Customer detected and saved to database
✅ Line descriptions formatted correctly (paid vs bonus)
✅ ROS schedules applied to Blocks tab
✅ Block filtering works (language prefixes)
✅ Gaps detected and lines split appropriately
✅ Separation intervals correct (15, 0, 0)
✅ Billing settings correct (Customer share / Agency)
✅ No errors in etere_client calls
✅ PDF moved to processed folder
✅ Proper logging throughout

### 🚀 **Quick Start Commands**

```bash
# Navigate to project
cd C:\Users\scrib\windev\OrderEntry

# Test Misfit automation standalone
python browser_automation\misfit_automation.py

# Run full system (recommended)
python main.py
```

### 💡 **Troubleshooting**

**Issue: "Module not found" errors**
- Check paths in imports
- Ensure you're running from project root
- Verify all files are in correct locations

**Issue: Customer detection fails**
- Check `data/customers.db` exists
- Verify CustomerMatchingService imports work
- Try entering customer ID manually first

**Issue: Lines not adding to contract**
- Check etere_client.py for errors
- Verify separation_intervals format
- Check block_prefixes are correct

**Issue: Main.py doesn't detect Misfit**
- Verify OrderType.MISFIT in enum
- Check detection logic in order_detection_service.py
- Ensure PDF has "Misfit" or "Judy Soper" in text

### 📚 **Related Documentation**

- `ETERE_CLIENT_GOLDEN_RULES.md` - Etere client usage rules
- `TCAA_AUTOMATION_README.md` - TCAA reference implementation
- `ARCHITECTURE.md` - System architecture overview
- `QUICK_START.py` - Quick start examples

### 🎉 **You're Ready!**

The Misfit automation is complete and follows the proven TCAA pattern. Copy the file, update your integration points, and test!

**Remember:** 
- Run system with `python main.py`
- All business logic stays in `misfit_automation.py`
- All Etere interactions use `etere_client.py`
- Customer detection uses universal database

Good luck! 🚀
