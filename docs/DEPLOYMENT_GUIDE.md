# 🚀 UNIVERSAL IMPROVEMENTS DEPLOYMENT GUIDE

## What We Did

We extracted duplicate code from TCAA and Misfit into **universal utilities** that ALL agencies can use.

### ✅ **What's Universal Now**

1. **ROS Schedules** - `ros_definitions.py`
2. **Language Block Prefixes** - `language_utils.py`
3. **Billing Settings** - `BillingType` enum in `enums.py`
4. **Max Daily Run** - Auto-calculated in `etere_client.py`

---

## 📦 **Files to Deploy**

### **NEW Files** (Create these)
```
browser_automation/ros_definitions.py
browser_automation/language_utils.py
```

### **UPDATED Files** (Replace existing)
```
src/domain/enums.py
browser_automation/tcaa_automation.py
browser_automation/misfit_automation.py
```

---

## 🔧 **Deployment Steps**

### Step 1: Create New Universal Files

```bash
# From outputs directory, copy to project:
copy ros_definitions.py C:\Users\scrib\windev\OrderEntry\browser_automation\
copy language_utils.py C:\Users\scrib\windev\OrderEntry\browser_automation\
```

### Step 2: Update Existing Files

```bash
# Replace these files:
copy enums.py C:\Users\scrib\windev\OrderEntry\src\domain\
copy tcaa_automation.py C:\Users\scrib\windev\OrderEntry\browser_automation\
copy misfit_automation.py C:\Users\scrib\windev\OrderEntry\browser_automation\
```

### Step 3: Test TCAA

```bash
cd C:\Users\scrib\windev\OrderEntry
python main.py
# Process a TCAA order
```

**Expected Output:**
```
[LINE] ℹ Auto-calculated max_daily_run: 3 spots/week ÷ 5 days = 1 spots/day
[LINE] Adding line to contract...
✓ Line added successfully
```

### Step 4: Test Misfit

```bash
# Process a Misfit order
```

**Expected Output:**
```
[LINE] ℹ Auto-calculated max_daily_run: 3 spots/week ÷ 7 days = 1 spots/day
[LINE] Adding line to contract...
✓ Line added successfully
```

---

## 📊 **What Changed**

### **ROS Schedules**

**Before:** Each agency had its own copy
```python
# In tcaa_automation.py
ROS_OPTIONS = {
    'Chinese': {'days': 'M-Su', 'time': '6a-11:59p'},
    ...
}

# In misfit_automation.py
ROS_SCHEDULES = {
    'Chinese': {'days': 'M-Su', 'time': '6a-11:59p'},
    ...
}
```

**After:** One source of truth
```python
# In ros_definitions.py (NEW)
ROS_SCHEDULES = {
    'Chinese': {'days': 'M-Su', 'time': '6a-11:59p'},
    ...
}

# All agencies import:
from browser_automation.ros_definitions import ROS_SCHEDULES
```

### **Language Block Prefixes**

**Before:** Each agency had its own function
```python
# In tcaa_automation.py
def get_language_block_prefixes(language):
    mapping = {'Chinese': ['C', 'M'], ...}
    return mapping.get(language, [])

# In misfit_automation.py  
def get_language_block_prefix(language):
    mapping = {'Chinese': ['C', 'M'], ...}
    return mapping.get(language, [])
```

**After:** One universal function
```python
# In language_utils.py (NEW)
def get_language_block_prefixes(language):
    """Universal language to block prefix mapping."""
    mapping = {'Chinese': ['C', 'M'], ...}
    return mapping.get(language, [])

# All agencies import:
from browser_automation.language_utils import get_language_block_prefixes
```

### **Billing Settings**

**Before:** Hardcoded strings everywhere
```python
# Every agency:
charge_to="Customer share indicating agency %",
invoice_header="Agency"
```

**After:** Type-safe enum
```python
# In enums.py (UPDATED)
class BillingType(Enum):
    CUSTOMER_SHARE_AGENCY = ("Customer share indicating agency %", "Agency")
    
    def get_charge_to(self) -> str:
        return self.value[0]
    
    def get_invoice_header(self) -> str:
        return self.value[1]

# All agencies use:
from src.domain.enums import BillingType

charge_to=BillingType.CUSTOMER_SHARE_AGENCY.get_charge_to(),
invoice_header=BillingType.CUSTOMER_SHARE_AGENCY.get_invoice_header()
```

### **Max Daily Run**

**Before:** Calculated in every agency
```python
# In tcaa_automation.py
max_daily_run = math.ceil(spots_per_week / adjusted_day_count)
etere.add_contract_line(..., max_daily_run=max_daily_run, ...)

# In misfit_automation.py
max_daily_run = math.ceil(spots_per_week / adjusted_day_count)
etere.add_contract_line(..., max_daily_run=max_daily_run, ...)
```

**After:** Auto-calculated in etere_client
```python
# In etere_client.py (UPDATED)
def add_contract_line(..., max_daily_run=None, ...):
    if max_daily_run is None:
        day_count = self._count_active_days(days)
        max_daily_run = math.ceil(spots_per_week / day_count)
    ...

# All agencies just pass spots_per_week:
etere.add_contract_line(..., spots_per_week=spots_per_week, ...)
# max_daily_run calculated automatically!
```

---

## 🎯 **Benefits**

### **Code Reduction**
- ✅ Removed ~100 lines of duplicate code
- ✅ TCAA: Simpler by 50 lines
- ✅ Misfit: Simpler by 50 lines

### **Consistency**
- ✅ One ROS schedule → all agencies use same
- ✅ One block prefix mapping → no inconsistencies
- ✅ One billing enum → type-safe, no typos

### **Maintainability**
- ✅ Update ROS schedule once → affects all agencies
- ✅ Fix block prefix bug once → fixed everywhere
- ✅ Add new billing type once → all agencies can use it

### **Future Agencies**
- ✅ WorldLink can be simplified
- ✅ opAD can be simplified
- ✅ RPM can be simplified
- ✅ All future agencies start simpler

---

## ⚠️ **Important Notes**

### **Backward Compatibility**

All changes are backward compatible:
- TCAA: `ROS_OPTIONS = ROS_SCHEDULES` (alias maintained)
- Both: Existing function calls work identically
- etere_client: Can still pass `max_daily_run` manually if needed

---

## 🧪 **Testing Checklist**

- [ ] TCAA contract creates successfully
- [ ] TCAA lines calculate max_daily_run automatically
- [ ] TCAA uses correct ROS schedules
- [ ] TCAA selects correct programming blocks
- [ ] Misfit contract creates successfully
- [ ] Misfit lines calculate max_daily_run automatically
- [ ] Misfit uses correct ROS schedules (including Hmong 4p-6p)
- [ ] Misfit selects correct programming blocks
- [ ] Both use BillingType enum correctly

---

## 🔄 **Next Steps (Future)**

These agencies can also be updated to use the universal utilities:

1. **WorldLink** - Update to use ROS_SCHEDULES, get_language_block_prefixes, BillingType
2. **opAD** - Update to use universal utilities
3. **RPM** - Update to use universal utilities
4. **H&L Partners** - Update to use universal utilities
5. **Daviselen** - Update to use universal utilities
6. **Impact** - Update to use universal utilities
7. **iGraphix** - Update to use universal utilities
8. **Admerasia** - Update to use universal utilities

**Estimated time per agency:** 10 minutes

---

## 📞 **Support**

If you encounter issues:

1. Check imports are correct
2. Verify files are in correct directories
3. Test with a simple TCAA order first
4. Check console output for error messages

**Common issues:**
- ImportError: Check file locations
- NameError: Check imports at top of file
- AttributeError: Check enum method names

---

## ✅ **Success Criteria**

Deployment is successful when:
- ✅ TCAA processes orders without errors
- ✅ Misfit processes orders without errors
- ✅ Both show "Auto-calculated max_daily_run" messages
- ✅ Both create lines with correct blocks and schedules
- ✅ No duplicate code in agency files

**You'll know it's working when you see:**
```
[LINE] ℹ Auto-calculated max_daily_run: 3 spots/week ÷ 5 days = 1 spots/day
```

This means the universal calculation is working! 🎉
