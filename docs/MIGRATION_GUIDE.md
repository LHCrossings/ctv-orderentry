# Migration Guide - From Legacy Script to Refactored System

## 📋 Overview

This guide walks you through migrating from the legacy 2,136-line monolithic script to the new clean architecture system.

---

## 🔍 What's Changed

### Architecture

**Before (Legacy):**
```
single_file_script.py (2,136 lines)
├── Order detection logic
├── Customer matching logic
├── File I/O operations
├── Browser automation
├── Database operations
├── User input collection
├── Output formatting
└── All mixed together!
```

**After (Refactored):**
```
src/
├── domain/              # Core business entities
├── data_access/         # Database operations
├── business_logic/      # Services & detection
├── presentation/        # CLI & formatting
└── orchestration/       # Application coordination

main.py                  # Entry point
```

---

## 📊 Feature Comparison

### Order Detection

| Feature | Legacy Script | Refactored System |
|---------|--------------|-------------------|
| Detection logic | Mixed with file I/O | Pure business logic |
| PDF reading | Inline code | Separate adapter |
| Testing | Difficult | Easy - 49 tests |
| Adding new types | Modify core function | Add detection method |

**Migration:** All detection patterns preserved, now in `OrderDetectionService`

### Customer Management

| Feature | Legacy Script | Refactored System |
|---------|--------------|-------------------|
| Storage | JSON file | SQLite database |
| Matching | Simple string match | Fuzzy matching service |
| Testing | Impossible | Easy - 27 tests |
| Performance | Slow for many customers | Fast indexed queries |

**Migration:** Automatic migration from JSON to SQLite on first run

### User Input

| Feature | Legacy Script | Refactored System |
|---------|--------------|-------------------|
| Collection | Inline `input()` calls | `InputCollector` class |
| Batch mode | Not available | `BatchInputCollector` |
| Testing | Requires mocking everywhere | Mockable presentation layer |
| Reusability | Copy-paste code | Reusable components |

**Migration:** All prompts preserved, now organized in presentation layer

### File Organization

| Feature | Legacy Script | Refactored System |
|---------|--------------|-------------------|
| Directory structure | Hardcoded paths | Configurable |
| Scanning | One function | `OrderScanner` class |
| File operations | Mixed with logic | Separate orchestration |

**Migration:** Configure paths in `ApplicationConfig`

---

## 🚀 Migration Steps

### Step 1: Backup Current System

```powershell
# Backup your legacy script
Copy-Item legacy_script.py legacy_script_backup.py

# Backup your data
Copy-Item customers.json customers_backup.json
```

### Step 2: Install Refactored System

```powershell
# Extract the project
cd C:\Users\scrib\windev\OrderEntry
# Extract final_working_project.tar.gz here

# Verify setup
python verify_setup.py
```

### Step 3: Migrate Customer Data

The system automatically migrates customer data from JSON to SQLite on first run:

```powershell
# Place your customers.json in the project root
Copy-Item customers_backup.json customers.json

# First run will migrate automatically
python main.py --scan
```

**What happens:**
1. System detects `customers.json`
2. Creates SQLite database at `data/customers.db`
3. Imports all customer mappings
4. Preserves original `customers.json` as backup

**Verification:**
```powershell
# Check migration success
python -c "from pathlib import Path; print('✓ Migration complete' if (Path('data/customers.db').exists()) else '✗ Not migrated')"
```

### Step 4: Configure Directories

Edit configuration if needed:

```python
# Create custom_config.py
from pathlib import Path
from orchestration import ApplicationConfig

config = ApplicationConfig(
    incoming_dir=Path("C:/Orders/Incoming"),
    processed_dir=Path("C:/Orders/Processed"),
    error_dir=Path("C:/Orders/Errors"),
    customer_db_path=Path("C:/Data/customers.db")
)
```

### Step 5: Side-by-Side Testing

Run both systems in parallel to verify:

```powershell
# Process with legacy script
python legacy_script.py

# Process same orders with new system
python main.py

# Compare results
```

**What to verify:**
- ✅ Same order types detected
- ✅ Same customer names extracted
- ✅ Same inputs collected
- ✅ Same file organization

### Step 6: Gradual Cutover

**Week 1-2: Parallel Running**
- Run both systems
- Compare results daily
- Build confidence

**Week 3: Primary System**
- Use refactored as primary
- Keep legacy as backup
- Monitor for issues

**Week 4: Full Cutover**
- Refactored system only
- Archive legacy script
- Celebrate! 🎉

---

## 🔧 Configuration Migration

### Legacy Script Configuration

```python
# Old way - hardcoded
INCOMING_DIR = "C:/Orders/Incoming"
PROCESSED_DIR = "C:/Orders/Processed"
```

### Refactored System Configuration

```python
# New way - configurable
from orchestration import ApplicationConfig

config = ApplicationConfig.from_defaults()
# or
config = ApplicationConfig(
    incoming_dir=Path("C:/Orders/Incoming"),
    processed_dir=Path("C:/Orders/Processed"),
    ...
)
```

---

## 📝 Functionality Mapping

### Order Processing

**Legacy Script:**
```python
def process_order(pdf_path):
    # 200+ lines of mixed logic
    order_type = detect_order_type(pdf_path)
    customer = extract_customer(pdf_path)
    inputs = collect_inputs()
    create_contract(browser, inputs)
```

**Refactored System:**
```python
# Clean separation
order_type = detector.detect_order_type(pdf_path)
customer = detector.extract_customer_name(pdf_path)
inputs = input_collector.collect_order_input(order)
result = processing_service.process_order(order, browser, inputs)
```

### Customer Lookup

**Legacy Script:**
```python
# Simple matching
with open('customers.json') as f:
    customers = json.load(f)
    if name in customers:
        return customers[name]
```

**Refactored System:**
```python
# Fuzzy matching with database
customer = repository.find_customer(name, order_type)
# Handles variations: "McDonalds", "McDonald's", "McD"
```

### Batch Processing

**Legacy Script:**
```python
# Not available - process one at a time
```

**Refactored System:**
```python
# Built-in batch mode
python main.py --batch
# Collects all inputs upfront
# Processes unattended
```

---

## 🧪 Testing Migration

### Legacy Script Testing

```python
# Difficult to test - everything coupled
# Manual testing only
```

### Refactored System Testing

```python
# 214 automated tests
python run_all_tests.py

# Test specific components
python -m pytest tests/unit/test_order_detection_service.py
python -m pytest tests/unit/test_customer_repository.py
```

**Test Coverage:**
- ✅ Domain models: 35 tests
- ✅ Order detection: 49 tests
- ✅ Customer repository: 27 tests
- ✅ Processing service: 10 tests
- ✅ Presentation layer: 63 tests
- ✅ Orchestration: 30 tests

---

## ⚠️ Known Differences

### 1. Browser Automation

**Legacy:** Included Selenium automation  
**Refactored:** Not yet implemented (Phase 7+)

**Workaround:** System collects inputs and provides instructions for manual processing

### 2. Error Handling

**Legacy:** Basic error handling  
**Refactored:** Comprehensive error handling with specific error types

### 3. Performance

**Legacy:** Slower (reads JSON on every operation)  
**Refactored:** Faster (indexed SQLite database)

### 4. Customer Matching

**Legacy:** Exact string match only  
**Refactored:** Fuzzy matching with similarity scoring

---

## 🎯 Cutover Checklist

### Pre-Cutover (1 week before)

- [ ] Backup all data
  - [ ] Legacy script
  - [ ] customers.json
  - [ ] Any configuration files
  
- [ ] Install refactored system
  - [ ] Extract files
  - [ ] Run verify_setup.py
  - [ ] Run test_factory.py
  - [ ] Run all tests

- [ ] Migrate customer data
  - [ ] Copy customers.json to project root
  - [ ] Run system once to trigger migration
  - [ ] Verify data/customers.db created
  - [ ] Spot-check customer mappings

- [ ] Configure directories
  - [ ] Set incoming_dir
  - [ ] Set processed_dir
  - [ ] Set error_dir
  - [ ] Test with --scan mode

### Cutover Day

- [ ] Morning checks
  - [ ] Verify backup exists
  - [ ] Verify refactored system tests pass
  - [ ] Verify configuration correct
  
- [ ] Switch to new system
  - [ ] Archive legacy script
  - [ ] Update shortcuts/scripts to use main.py
  - [ ] Notify team of change
  
- [ ] Monitor first orders
  - [ ] Process 2-3 test orders
  - [ ] Verify detection works
  - [ ] Verify customer lookup works
  - [ ] Verify file organization works

### Post-Cutover (1 week after)

- [ ] Daily monitoring
  - [ ] Check for errors
  - [ ] Verify all order types detected
  - [ ] Monitor performance
  
- [ ] Team feedback
  - [ ] Collect user feedback
  - [ ] Document any issues
  - [ ] Address concerns
  
- [ ] Performance review
  - [ ] Compare processing times
  - [ ] Check database size
  - [ ] Verify tests still passing

### Success Criteria

✅ All order types detected correctly  
✅ Customer matching working (including fuzzy)  
✅ File organization correct  
✅ No critical errors  
✅ Team comfortable with new system  
✅ Tests passing  

---

## 🆘 Rollback Plan

If issues arise:

### Immediate Rollback

```powershell
# 1. Stop using new system
# 2. Switch back to legacy script
Copy-Item legacy_script_backup.py legacy_script.py

# 3. Restore customer data if needed
Copy-Item customers_backup.json customers.json

# 4. Continue with legacy
python legacy_script.py
```

### Investigation

- Review error logs
- Check test results
- Verify configuration
- Document issues
- Fix before retry

---

## 📞 Support Resources

### Documentation
- `README.md` - Setup and usage
- `PHASE6_COMPLETE.md` - Architecture details
- `BROWSER_FIX.md` - Current limitations

### Testing
- `verify_setup.py` - Verify installation
- `test_factory.py` - Test factories
- `run_all_tests.py` - Full test suite

### Examples
- `presentation_example.py` - Presentation layer examples
- `scan_incoming.py` - Scanning examples

---

## 🎉 Success!

Once migration is complete, you'll have:

✅ **214 automated tests** providing confidence  
✅ **Clean architecture** easy to understand and modify  
✅ **Fast database** with indexed customer lookups  
✅ **Fuzzy matching** catching customer name variations  
✅ **Batch processing** for efficient workflows  
✅ **Type safety** preventing common errors  
✅ **Comprehensive documentation** for maintenance  

**Welcome to the new system!** 🚀
