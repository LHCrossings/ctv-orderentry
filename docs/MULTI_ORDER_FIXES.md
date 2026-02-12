# Multi-Order Detection Fixes - February 6, 2026

## Issues Fixed

### Issue 1: Duplicate Estimates
**Problem:** Each estimate appeared twice in the order list
```
[1] 2026 Annual CRTV-TV.pdf (Estimate: 9709)
[2] 2026 Annual CRTV-TV.pdf (Estimate: 9709)  ← Duplicate!
[3] 2026 Annual CRTV-TV.pdf (Estimate: 9711)
[4] 2026 Annual CRTV-TV.pdf (Estimate: 9711)  ← Duplicate!
```

**Root Cause:** The regex was finding "Estimate: XXXX" multiple times per order (it appears in multiple places on each order page), so each estimate was being counted and split multiple times.

**Fix:** Modified `split_tcaa_orders()` to:
1. Group estimates by number (removing duplicates)
2. Only use the first occurrence of each unique estimate
3. Sort by position to maintain correct order

**Result:**
```
[1] 2026 Annual CRTV-TV.pdf (Estimate: 9709)
[2] 2026 Annual CRTV-TV.pdf (Estimate: 9711)
[3] 2026 Annual CRTV-TV.pdf (Estimate: 9712)
```
No more duplicates! ✅

### Issue 2: Customer Names Include Estimate Numbers
**Problem:** Customer names were showing with estimate numbers appended
```
Customer: Western Washington Toyota Dlrs Adv Assoc Estimate: 9709
```

**Root Cause:** 
1. The customer extraction was reading a line that included both the company name and the estimate
2. For multi-order PDFs, the scanner was extracting customer name from the full PDF instead of from the order-specific text

**Fix:** 
1. Updated `_extract_tcaa_client()` to:
   - Remove any "Estimate:" text from extracted customer names
   - Look for client name in the correct position relative to estimate
   - Clean up any trailing estimate references

2. Added `extract_customer_name_from_text()` to PDFOrderDetector:
   - Allows extracting customer from text directly (not just from PDF files)
   - Used for split orders where text is already separated

3. Updated `OrderScanner` to:
   - Use the order-specific text when extracting customer names
   - Not read the entire PDF again for each sub-order

**Result:**
```
Customer: Western Washington Toyota Dlrs Adv Assoc
```
Clean customer names! ✅

---

## Code Changes

### 1. OrderDetectionService (`src/business_logic/services/order_detection_service.py`)

**Modified `split_tcaa_orders()`:**
```python
# Before: Split at every occurrence of "Estimate:"
matches = list(re.finditer(r'Estimate:\s*(\d+)', full_text))
# Problem: Found duplicates

# After: Group by unique estimate numbers
estimate_positions = {}
for match in matches:
    estimate_num = match.group(1)
    if estimate_num not in estimate_positions:
        estimate_positions[estimate_num] = match.start()
# Solution: Only first occurrence of each estimate
```

**Improved `_extract_tcaa_client()`:**
```python
# Now removes "Estimate:" from extracted names
client = re.sub(r'\s*Estimate:.*$', '', client)
```

### 2. PDFOrderDetector (`src/business_logic/services/pdf_order_detector.py`)

**Added new method:**
```python
def extract_customer_name_from_text(
    self,
    text: str,
    order_type: OrderType
) -> str | None:
    """Extract customer name from text (not PDF file)."""
    return self._service.extract_client_name(text, None, order_type)
```

### 3. OrderScanner (`src/orchestration/order_scanner.py`)

**Modified multi-order handling:**
```python
# Before: Extract from PDF (reads whole file)
customer_name = self._detection_service.extract_customer_name(pdf_path, order_type)

# After: Extract from order-specific text
order_text = order_data.get('text', '')
customer_name = self._detection_service.extract_customer_name_from_text(
    order_text,
    order_type
)
```

---

## Testing

### Test 1: Basic Multi-Order Detection
```bash
python test_multi_order.py
```
**Results:**
```
✓ Count TCAA orders: 3
✓ Split TCAA orders: 3 orders  
✓ Extract estimate numbers: ['9709', '9710', '9715']
✓ Each order has text: True
```

### Test 2: Duplicate Removal
```bash
python test_duplicate_estimates.py
```
**Results:**
```
✓ Count unique orders: 3 (not 6)
✓ Split returns unique orders: 3 orders
✓ No duplicate estimates
✓ Customer name clean (no "Estimate:")
```

### Test 3: Full Test Suite
```bash
python run_all_tests.py
```
**Results:**
```
✅ All 206 tests passed!
```

---

## What to Expect Now

### Scanning a Multi-Order PDF

**Command:**
```powershell
python main.py --scan
```

**Output:**
```
[SCAN] 2026 Annual CRTV-TV.pdf: Detected 7 orders

AVAILABLE ORDERS
======================================================================

[1] 2026 Annual CRTV-TV.pdf (Estimate: 9709)
    Type: TCAA
    Customer: Western Washington Toyota Dlrs Adv Assoc
    Status: PENDING

[2] 2026 Annual CRTV-TV.pdf (Estimate: 9711)
    Type: TCAA
    Customer: Western Washington Toyota Dlrs Adv Assoc
    Status: PENDING

[3] 2026 Annual CRTV-TV.pdf (Estimate: 9712)
    Type: TCAA
    Customer: Western Washington Toyota Dlrs Adv Assoc
    Status: PENDING

... (continues for all estimates)

Total: 7 order(s)
```

**Key Points:**
- ✅ No duplicates
- ✅ Clean customer names
- ✅ Each estimate shown once
- ✅ Correct count

---

## Verification Steps

1. **Extract the fix:**
   ```powershell
   # Extract multi_order_fixed.tar.gz
   ```

2. **Run tests:**
   ```powershell
   python test_multi_order.py
   python test_duplicate_estimates.py
   python run_all_tests.py
   ```

3. **Test with your PDF:**
   ```powershell
   python main.py --scan
   ```

4. **Verify output:**
   - Count should match number of unique estimates
   - No duplicates in the list
   - Customer names should be clean (no "Estimate:" in them)

---

## Summary

**Before:**
- 14 orders shown (7 duplicates)
- Customer names included "Estimate: XXXX"
- Confusing and incorrect

**After:**
- 7 orders shown (correct count)
- Clean customer names
- Clear and accurate

Both issues are now fixed! ✅
