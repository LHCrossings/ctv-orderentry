"""
Tests for time parsing utilities used in Etere contract entry.

Covers _normalize_time_to_colon_format (admerasia_parser) and
EtereClient.parse_time_range (etere_client).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# browser_automation is outside src/; add it directly
_ba_path = str(Path(__file__).parent.parent.parent / "browser_automation")
if _ba_path not in sys.path:
    sys.path.insert(0, _ba_path)

# Mock heavy dependencies before importing so tests run without a browser/pdfplumber
for _mod in ("pdfplumber", "selenium", "selenium.webdriver",
             "selenium.webdriver.common", "selenium.webdriver.common.by",
             "selenium.webdriver.support", "selenium.webdriver.support.ui",
             "selenium.webdriver.support.expected_conditions",
             "selenium.webdriver.common.keys",
             "selenium.common", "selenium.common.exceptions"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from etere_client import EtereClient  # noqa: E402
from parsers.admerasia_parser import _normalize_time_to_colon_format  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# _normalize_time_to_colon_format
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeTimeToColonFormat:
    """Tests for _normalize_time_to_colon_format in admerasia_parser."""

    # --- Already normalized (Pattern 1) ---
    def test_already_normalized_pm(self):
        assert _normalize_time_to_colon_format("7:00p-7:30p") == "7:00p-7:30p"

    def test_already_normalized_am(self):
        assert _normalize_time_to_colon_format("11:00a-11:30a") == "11:00a-11:30a"

    # --- Shared am/pm, with colons (Pattern 2) ---
    def test_colon_shared_pm(self):
        assert _normalize_time_to_colon_format("7:00-7:30p") == "7:00p-7:30p"

    def test_colon_noon_crossing(self):
        """11:30-12:00p must become 11:30a-12:00p (not 11:30pm)."""
        assert _normalize_time_to_colon_format("11:30-12:00p") == "11:30a-12:00p"

    def test_colon_noon_crossing_start_greater(self):
        """11:30-1:00p — start > end in 12h, so start is AM."""
        assert _normalize_time_to_colon_format("11:30-1:00p") == "11:30a-1:00p"

    def test_colon_noon_to_1pm(self):
        """12:00-1:00p stays PM — start is 12."""
        assert _normalize_time_to_colon_format("12:00-1:00p") == "12:00p-1:00p"

    # --- Shared am/pm, no colons (Pattern 3) ---
    def test_no_colon_pm(self):
        assert _normalize_time_to_colon_format("7-730p") == "7:00p-7:30p"

    def test_no_colon_am(self):
        assert _normalize_time_to_colon_format("6-7a") == "6:00a-7:00a"

    def test_no_colon_4digit_start_noon_crossing(self):
        """1130-12p is the bug case from McD's orders — must be 11:30a-12:00p."""
        assert _normalize_time_to_colon_format("1130-12p") == "11:30a-12:00p"

    def test_no_colon_4digit_start_4digit_end(self):
        assert _normalize_time_to_colon_format("11-1200p") == "11:00a-12:00p"

    def test_no_colon_noon_to_1pm(self):
        """12-1p stays PM."""
        assert _normalize_time_to_colon_format("12-1p") == "12:00p-1:00p"

    def test_no_colon_noon_crossing_start_greater(self):
        """11-2p — 11 > 2 so start is AM."""
        assert _normalize_time_to_colon_format("11-2p") == "11:00a-2:00p"

    # --- Each time has own am/pm (Pattern 4) ---
    def test_own_period_each(self):
        assert _normalize_time_to_colon_format("1030p-12a") == "10:30p-12:00a"

    # --- Simple hour-to-hour (Pattern 5) ---
    def test_simple_am(self):
        assert _normalize_time_to_colon_format("6a-7a") == "6:00a-7:00a"

    def test_simple_pm(self):
        assert _normalize_time_to_colon_format("7p-8p") == "7:00p-8:00p"


# ─────────────────────────────────────────────────────────────────────────────
# EtereClient.parse_time_range
# ─────────────────────────────────────────────────────────────────────────────

class TestParseTimeRange:
    """Tests for EtereClient.parse_time_range."""

    # --- Standard normalized inputs ---
    def test_am_range(self):
        assert EtereClient.parse_time_range("6:00a-7:00a") == ("06:00", "07:00")

    def test_pm_range(self):
        assert EtereClient.parse_time_range("7:00p-8:00p") == ("19:00", "20:00")

    def test_noon_normalized(self):
        """Normalized 11:30a-12:00p should parse correctly."""
        assert EtereClient.parse_time_range("11:30a-12:00p") == ("11:30", "12:00")

    def test_noon_to_1pm_normalized(self):
        assert EtereClient.parse_time_range("12:00p-1:00p") == ("12:00", "13:00")

    # --- Noon-crossing with raw (unnormalized) strings ---
    def test_noon_crossing_raw_colon(self):
        """11:30-12:00p raw — start must be inferred as AM."""
        assert EtereClient.parse_time_range("11:30-12:00p") == ("11:30", "12:00")

    def test_noon_crossing_raw_no_colon(self):
        """1130-12p — the exact bug from McD's orders."""
        assert EtereClient.parse_time_range("1130-12p") == ("11:30", "12:00")

    def test_noon_crossing_start_greater(self):
        """11-130p — start > end in 12h clock, so start is AM."""
        assert EtereClient.parse_time_range("11-130p") == ("11:00", "13:30")

    # --- Midnight handling ---
    def test_midnight_end(self):
        """12:00a = midnight = 23:59."""
        assert EtereClient.parse_time_range("11:00p-12:00a") == ("23:00", "23:59")

    def test_past_midnight_end(self):
        """1a = past midnight = 23:59."""
        assert EtereClient.parse_time_range("11:00p-1a") == ("23:00", "23:59")

    # --- Floor enforcement ---
    def test_floor_enforced(self):
        """Nothing before 06:00."""
        assert EtereClient.parse_time_range("5:00a-6:00a") == ("06:00", "06:00")

    # --- Semicolon ranges ---
    def test_semicolon_range(self):
        start, end = EtereClient.parse_time_range("4p-5p; 6p-7p")
        assert start == "16:00"
        assert end == "19:00"
