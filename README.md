# Order Processing System - Setup Guide

## 📁 Project Structure

Your project directory should look like this:

```
OrderEntry/
├── main.py                    # Main entry point
├── run_all_tests.py          # Test runner
├── verify_setup.py           # Setup verification script
├── src/
│   ├── domain/
│   ├── data_access/
│   ├── business_logic/
│   ├── presentation/
│   └── orchestration/
├── tests/
│   ├── unit/
│   └── integration/
├── incoming/                  # (created automatically)
├── processed/                 # (created automatically)
└── errors/                    # (created automatically)
```

## 🚀 Quick Start

### 1. Verify Setup

First, check if everything is in place:

```powershell
python verify_setup.py
```

This will check:
- ✓ All required directories exist
- ✓ All required files are present
- ✓ All Python modules can be imported

### 2. Run the Application

If verification passes, you can run the application:

```powershell
# Interactive mode (default)
python main.py

# Batch mode
python main.py --batch

# Automatic mode
python main.py --auto

# Just scan
python main.py --scan
```

## 🔧 Troubleshooting

### "ModuleNotFoundError: No module named 'orchestration'"

This means Python can't find the modules. Check:

1. **Are you in the right directory?**
   ```powershell
   cd C:\Users\scrib\windev\OrderEntry
   ```

2. **Does the `src` directory exist?**
   ```powershell
   dir src
   ```

3. **Run the verification script:**
   ```powershell
   python verify_setup.py
   ```

### Missing Dependencies

Install required packages:

```powershell
pip install pytest pdfplumber thefuzz python-Levenshtein
```

### Directory Structure Missing

If you're missing the `src` directory or subdirectories, you need to extract the complete project archive. Make sure you have:

- `src/domain/`
- `src/data_access/`
- `src/business_logic/`
- `src/presentation/`
- `src/orchestration/`

## 📊 Running Tests

To verify everything works:

```powershell
python run_all_tests.py
```

You should see:
```
✅ All 213 tests passed!
```

## 🎯 Usage Examples

### Interactive Mode

```powershell
python main.py
```

1. Application scans for PDF orders
2. Displays available orders
3. You select which to process
4. Collects input for each order
5. Processes and shows results

### Batch Mode

```powershell
python main.py --batch
```

1. Scans and displays orders
2. You select orders to process
3. Collects ALL inputs upfront
4. Processes everything unattended
5. Shows summary at end

### Automatic Mode

```powershell
python main.py --auto
```

Processes all orders automatically without any user input. Perfect for scheduled tasks.

### Scan Only

```powershell
python main.py --scan
```

Just shows what orders are available without processing anything.

## 📍 Configuration

The application looks for orders in:
- **Incoming:** `./incoming/`
- **Processed:** `./processed/`
- **Errors:** `./errors/`
- **Database:** `./data/customers.db`

These directories are created automatically on first run.

## 🆘 Getting Help

1. **Run verification:** `python verify_setup.py`
2. **Check tests:** `python run_all_tests.py`
3. **Review logs:** Check console output for error messages

## 📖 More Information

- `PHASE6_COMPLETE.md` - Complete Phase 6 documentation
- `SESSION_STATUS.md` - Project status and progress
- `presentation_example.py` - Example usage of presentation layer
