"""
Global test configuration.

Mocks heavy optional dependencies (pdfplumber, selenium) so all tests
collect and run without the full browser automation stack installed.
Per-file mocks in test_time_parsing.py use `if _mod not in sys.modules`
so they safely become no-ops after this conftest runs.
"""
import sys
from unittest.mock import MagicMock

for _mod in (
    "pdfplumber",
    "selenium", "selenium.webdriver",
    "selenium.webdriver.common", "selenium.webdriver.common.by",
    "selenium.webdriver.support", "selenium.webdriver.support.ui",
    "selenium.webdriver.support.expected_conditions",
    "selenium.webdriver.common.keys",
    "selenium.common", "selenium.common.exceptions",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
