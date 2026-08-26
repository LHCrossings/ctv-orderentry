"""
Global test configuration.

Mocks heavy optional dependencies (pdfplumber, selenium) so all tests
collect and run without the full browser automation stack installed.
Per-file mocks in test_time_parsing.py use `if _mod not in sys.modules`
so they safely become no-ops after this conftest runs.

Also neutralizes the opt-in routing flags — see `_clear_optin_routing_flags`.
"""

import sys
from unittest.mock import MagicMock

import pytest

for _mod in (
    "pdfplumber",
    "selenium",
    "selenium.webdriver",
    "selenium.webdriver.common",
    "selenium.webdriver.common.by",
    "selenium.webdriver.support",
    "selenium.webdriver.support.ui",
    "selenium.webdriver.support.expected_conditions",
    "selenium.webdriver.common.keys",
    "selenium.common",
    "selenium.common.exceptions",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


# Opt-in routing flags read straight from os.environ at call time
# (order_scanner._ai_fallback_enabled / _charmaine_ai_enabled). A developer has
# these ON in .env, and anything that calls load_dotenv() — importing
# etere_direct_client, or tests/integration/test_customer_repository.py — leaks
# them into the whole pytest process. That made the suite ORDER-DEPENDENT:
# tests/integration sorts before tests/unit, so a full run turned the AI fallback
# on underneath test_order_scanner and it failed, while `pytest tests/unit` alone
# passed. Tests must exercise documented DEFAULT behavior, never the developer's
# .env, so clear the flags for every test. monkeypatch undoes this afterwards, so
# a test that loads .env mid-run no longer leaks into its successors either. A
# test that wants a flag ON sets it explicitly with monkeypatch.setenv.
@pytest.fixture(autouse=True)
def _clear_optin_routing_flags(monkeypatch):
    for _var in ("CTV_AI_FALLBACK", "CTV_CHARMAINE_AI"):
        monkeypatch.delenv(_var, raising=False)


# Opt-in escape hatch from the pdfplumber MagicMock above, for tests that parse a
# real PDF fixture. Module-scoped and restored on teardown: leaving the real
# library in sys.modules would make the suite order-dependent in exactly the way
# the comment above warns about. Request it by name (`def test_x(real_pdfplumber)`)
# or via a module-level autouse shim.
@pytest.fixture(scope="module")
def real_pdfplumber():
    import importlib

    mock = sys.modules.pop("pdfplumber", None)
    real = importlib.import_module("pdfplumber")
    sys.modules["pdfplumber"] = real
    yield real
    if mock is not None:
        sys.modules["pdfplumber"] = mock
    else:
        sys.modules.pop("pdfplumber", None)
