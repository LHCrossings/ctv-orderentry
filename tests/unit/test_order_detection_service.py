"""
Unit tests for OrderDetectionService - Pure business logic testing.

These tests verify detection patterns work correctly without any file I/O.
We test with sample text strings that match each agency's patterns.
"""

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from domain.enums import OrderType
from business_logic.services.order_detection_service import OrderDetectionService


class TestOrderDetectionService:
    """Test the order detection service with sample text patterns."""
    
    @pytest.fixture
    def service(self):
        """Create service instance for tests."""
        return OrderDetectionService()
    
    # ========================================================================
    # WORLDLINK DETECTION
    # ========================================================================
    
    def test_detect_worldlink_with_wl_tracking(self, service):
        """Should detect WorldLink from 'WL Tracking No.' marker."""
        text = """
        WL Tracking No. 12345
        Agency:Some Agency
        Advertiser:Test Client
        """
        assert service.detect_from_text(text) == OrderType.WORLDLINK
    
    def test_detect_worldlink_with_unwired_tracking(self, service):
        """Should detect WorldLink from 'Unwired Tracking No.' marker."""
        text = """
        Unwired Tracking No. 67890
        Agency:Direct Donor TV
        """
        assert service.detect_from_text(text) == OrderType.WORLDLINK
    
    def test_detect_worldlink_with_tatari(self, service):
        """Should detect WorldLink from 'Agency:Tatari' marker."""
        text = """
        Order Details
        Agency:Tatari
        Advertiser:BrandName
        """
        assert service.detect_from_text(text) == OrderType.WORLDLINK
    
    def test_detect_worldlink_with_co_worldlink(self, service):
        """Should detect WorldLink from 'c/o WorldLink' marker."""
        text = """
        Agency:Direct Donor TV c/o WorldLink
        Campaign Info
        """
        assert service.detect_from_text(text) == OrderType.WORLDLINK
    
    def test_detect_worldlink_ventures(self, service):
        """Should detect WorldLink from 'WorldLink Ventures' marker."""
        text = """
        WorldLink Ventures
        Order Information
        """
        assert service.detect_from_text(text) == OrderType.WORLDLINK
    
    # ========================================================================
    # TCAA DETECTION
    # ========================================================================
    
    def test_detect_tcaa(self, service):
        """Should detect TCAA from 'CRTV-Cable' + 'Estimate:' markers."""
        text = """
        Client: Toyota
        Station: CRTV-Cable
        Estimate: EST-12345
        Schedule Information
        """
        assert service.detect_from_text(text) == OrderType.TCAA
    
    def test_tcaa_not_confused_with_hl(self, service):
        """CRTV-Cable should be TCAA, not H&L (which uses CRTV-TV)."""
        text = """
        Client: Some Client
        Station: CRTV-Cable
        Estimate: 12345
        Sacramento Market
        """
        assert service.detect_from_text(text) == OrderType.TCAA
    
    # ========================================================================
    # H&L PARTNERS DETECTION
    # ========================================================================
    
    def test_detect_hl_partners_direct(self, service):
        """Should detect H&L from 'H/L Agency' marker."""
        text = """
        H/L Agency San Francisco
        Client: Northern California Dealers Association
        Estimate: 12345
        """
        assert service.detect_from_text(text) == OrderType.HL
    
    def test_detect_hl_partners_with_crtv_tv(self, service):
        """Should detect H&L from CRTV-TV (not Cable) + location markers."""
        text = """
        Station: CRTV-TV
        Estimate: 12345
        Market: Sacramento
        Send Billing to: H&L Agency
        """
        assert service.detect_from_text(text) == OrderType.HL
    
    def test_detect_hl_with_encoding_issues(self, service):
        """Should detect H&L even with encoding damage ('HL Agency')."""
        text = """
        CRTV-TV
        Estimate: 12345
        SAN FRANCISCO
        HL Agency
        """
        assert service.detect_from_text(text) == OrderType.HL
    
    def test_hl_not_confused_with_tcaa(self, service):
        """H&L uses CRTV-TV, not CRTV-Cable."""
        text = """
        CRTV
        Estimate: 12345
        Sacramento
        Agency San Francisco
        """
        # No CRTV-Cable, has location and agency markers
        assert service.detect_from_text(text) == OrderType.HL
    
    # ========================================================================
    # OPAD DETECTION
    # ========================================================================
    
    def test_detect_opad(self, service):
        """Should detect opAD from unique column header."""
        text = """
        Client: NYC Restaurant
        Estimate: 12345
        # of SPOTS PER WEEK
        Schedule details...
        """
        assert service.detect_from_text(text) == OrderType.OPAD
    
    # ========================================================================
    # DAVISELEN DETECTION
    # ========================================================================
    
    def test_detect_daviselen_page1(self, service):
        """Should detect Daviselen from page 1 'DAVIS ELEN' marker."""
        text = """
        DAVIS ELEN ADVERTISING
        Client Information
        """
        assert service.detect_from_text(text) == OrderType.DAVISELEN
    
    def test_detect_daviselen_page2(self, service):
        """Should detect Daviselen from page 2 markers."""
        page1 = "Order Information"
        page2 = """
        DAVIS ELEN ADVERTISING
        Brand Time Schedule
        """
        assert service.detect_from_text(page1, page2) == OrderType.DAVISELEN
    
    def test_detect_daviselen_brand_schedule(self, service):
        """Should detect Daviselen from unique 'Brand Time Schedule - CLAN'."""
        page1 = "Some content"
        page2 = """
        Brand Time Schedule - CLAN
        Market: CVC
        """
        assert service.detect_from_text(page1, page2) == OrderType.DAVISELEN
    
    def test_detect_daviselen_lowercase(self, service):
        """Should detect Daviselen case-insensitively."""
        text = "daviselen advertising agency"
        assert service.detect_from_text(text) == OrderType.DAVISELEN
    
    # ========================================================================
    # MISFIT DETECTION
    # ========================================================================
    
    def test_detect_misfit_with_agency(self, service):
        """Should detect Misfit from agency marker + language block."""
        text = """
        Agency: Misfit
        Crossings TV Schedule
        Language Block column
        """
        assert service.detect_from_text(text) == OrderType.MISFIT
    
    def test_detect_misfit_with_email(self, service):
        """Should detect Misfit from email domain."""
        text = """
        Contact: john@agencymisfit.com
        Language Block schedule
        """
        assert service.detect_from_text(text) == OrderType.MISFIT
    
    def test_detect_misfit_with_combined_markers(self, service):
        """Should detect Misfit from Misfit + Crossings TV markers."""
        text = """
        Misfit Campaign
        Crossings TV Network
        Language Block: Chinese
        """
        assert service.detect_from_text(text) == OrderType.MISFIT
    
    def test_misfit_requires_language_block(self, service):
        """Misfit detection requires 'Language Block' column header."""
        text = """
        Agency: Misfit
        Crossings TV
        """
        # Missing "Language Block"
        assert service.detect_from_text(text) != OrderType.MISFIT
    
    # ========================================================================
    # IMPACT MARKETING DETECTION
    # ========================================================================
    
    def test_detect_impact_with_quarterly(self, service):
        """Should detect Impact from Impact Marketing + quarterly markers."""
        text = """
        Impact Marketing
        Big Valley Ford
        Q1-2025 Campaign
        """
        assert service.detect_from_text(text) == OrderType.IMPACT
    
    def test_detect_impact_with_email(self, service):
        """Should detect Impact from email domain + quarterly."""
        text = """
        Contact: sales@impactcalifornia.com
        Q2-2025 Schedule
        """
        assert service.detect_from_text(text) == OrderType.IMPACT
    
    def test_detect_impact_with_crossings_cv(self, service):
        """Should detect Impact from Crossings TV + Central Valley."""
        text = """
        Big Valley Ford
        Crossings TV
        Central Valley Market
        """
        assert service.detect_from_text(text) == OrderType.IMPACT
    
    def test_impact_requires_confirmation(self, service):
        """Impact requires quarterly or Crossings+CV markers."""
        text = """
        Impact Marketing
        Some campaign
        """
        # Missing Q1-Q4 or Crossings+CV
        assert service.detect_from_text(text) != OrderType.IMPACT
    
    # ========================================================================
    # IGRAPHIX DETECTION
    # ========================================================================
    
    def test_detect_igraphix_with_pechanga(self, service):
        """Should detect iGraphix with Pechanga client."""
        text = """
        Agency: iGraphix
        Client: Pechanga Resort Casino
        """
        assert service.detect_from_text(text) == OrderType.IGRAPHIX
    
    def test_detect_igraphix_with_sky_river(self, service):
        """Should detect iGraphix with Sky River client."""
        text = """
        IGraphix Agency
        Sky River Casino
        """
        assert service.detect_from_text(text) == OrderType.IGRAPHIX
    
    def test_detect_igraphix_with_co_crossings(self, service):
        """Should detect iGraphix from c/o pattern."""
        text = """
        iGraphix
        c/o Casino Client
        Crossings TV
        """
        assert service.detect_from_text(text) == OrderType.IGRAPHIX
    
    def test_igraphix_requires_client_confirmation(self, service):
        """iGraphix requires known client or c/o pattern."""
        text = """
        iGraphix Agency
        Some campaign
        """
        # Missing client markers
        assert service.detect_from_text(text) != OrderType.IGRAPHIX
    
    # ========================================================================
    # ADMERASIA DETECTION
    # ========================================================================
    
    def test_detect_admerasia_with_mcdonalds(self, service):
        """Should detect Admerasia with McDonald's client."""
        text = """
        Admerasia, Inc.
        Client: McDonald's
        Order Number: XX-MD01-123456
        """
        assert service.detect_from_text(text) == OrderType.ADMERASIA
    
    def test_detect_admerasia_with_order_number(self, service):
        """Should detect Admerasia from order number format."""
        text = """
        ADMERASIA INC
        Order Number: 25-MD02-654321
        Broadcast Schedule
        """
        assert service.detect_from_text(text) == OrderType.ADMERASIA
    
    def test_detect_admerasia_case_insensitive(self, service):
        """Should detect Admerasia case-insensitively."""
        text = """
        admerasia advertising
        Ref: McDonald's Campaign
        """
        assert service.detect_from_text(text) == OrderType.ADMERASIA
    
    def test_admerasia_requires_confirmation(self, service):
        """Admerasia requires McDonald's or MD order number."""
        text = """
        Admerasia, Inc.
        Some other client
        """
        # Missing McDonald's or MD order format
        assert service.detect_from_text(text) != OrderType.ADMERASIA
    
    # ========================================================================
    # RPM DETECTION
    # ========================================================================
    
    def test_detect_rpm_from_header(self, service):
        """Should detect RPM from 'RPM' in first 300 characters."""
        text = """
        RPM Advertising Agency
        Order Details
        """ + "x" * 500  # Add more text to test header detection
        assert service.detect_from_text(text) == OrderType.RPM
    
    def test_detect_rpm_from_markets(self, service):
        """Should detect RPM from market + estimate + header pattern."""
        text = """
        Market: Seattle-Tacoma
        Estimate: 12345
        CROSSINGS TV SEATTLE-TV
        Schedule Details
        """
        assert service.detect_from_text(text) == OrderType.RPM
    
    def test_detect_rpm_sacramento(self, service):
        """Should detect RPM from Sacramento market."""
        text = """
        Sacramento-Stockton Market
        Estimate: EST-456
        CROSSINGS TV SEATTLE-TV
        """
        assert service.detect_from_text(text) == OrderType.RPM
    
    # ========================================================================
    # UNKNOWN DETECTION
    # ========================================================================
    
    def test_detect_unknown_for_unrecognized(self, service):
        """Should return UNKNOWN for unrecognized patterns."""
        text = """
        Some Random Agency
        Client: Unknown Client
        Campaign Information
        """
        assert service.detect_from_text(text) == OrderType.UNKNOWN
    
    def test_detect_unknown_for_empty(self, service):
        """Should return UNKNOWN for empty text."""
        assert service.detect_from_text("") == OrderType.UNKNOWN
    
    # ========================================================================
    # ENCODING ISSUES DETECTION
    # ========================================================================
    
    def test_detect_encoding_issues(self, service):
        """Should detect PDFs with encoding issues (many CID markers)."""
        text = "(cid:1)(cid:2)(cid:3)" * 10  # 30 CID markers
        assert service.has_encoding_issues(text) is True
    
    def test_no_encoding_issues_for_normal_text(self, service):
        """Should not flag normal text as having encoding issues."""
        text = "Normal PDF text content"
        assert service.has_encoding_issues(text) is False
    
    def test_encoding_issues_threshold(self, service):
        """Should require more than 20 CID markers."""
        text = "(cid:1)" * 15  # Only 15 markers
        assert service.has_encoding_issues(text) is False
        
        text = "(cid:1)" * 25  # 25 markers
        assert service.has_encoding_issues(text) is True
    
    # ========================================================================
    # CLIENT NAME EXTRACTION
    # ========================================================================
    
    def test_extract_worldlink_client(self, service):
        """Should extract client from WorldLink 'Advertiser:' field."""
        text = """
        WL Tracking No. 12345
        Advertiser: Test Company Inc
        Campaign: Q1 2025
        """
        client = service.extract_client_name(text, None, OrderType.WORLDLINK)
        assert client == "Test Company Inc"
    
    def test_extract_tcaa_client(self, service):
        """Should extract client from TCAA 'Client:' field."""
        text = """
        CRTV-Cable
        Client: Toyota Motors
        Estimate: 456
        """
        client = service.extract_client_name(text, None, OrderType.TCAA)
        assert client == "Toyota Motors"
    
    def test_extract_hl_client_default(self, service):
        """Should return default H&L client."""
        text = "H/L Agency San Francisco"
        client = service.extract_client_name(text, None, OrderType.HL)
        assert "Northern California Dealers" in client
    
    def test_extract_daviselen_client_page2(self, service):
        """Should extract Daviselen client from page 2."""
        page1 = "Order info"
        page2 = """
        CLIENT MCDS McDonald's Corporation Market: CVC
        """
        client = service.extract_client_name(page1, page2, OrderType.DAVISELEN)
        assert client == "McDonald's Corporation"
    
    def test_extract_misfit_client(self, service):
        """Should extract Misfit client from 'Contact:' field."""
        text = """
        Agency: Misfit
        Contact: Brand Name LLC
        Language Block: Chinese
        """
        client = service.extract_client_name(text, None, OrderType.MISFIT)
        assert client == "Brand Name LLC"
    
    def test_extract_igraphix_client(self, service):
        """Should extract iGraphix client from c/o pattern."""
        text = """
        Advertiser:
         IGraphix
         c/o
         Pechanga Resort Casino
        
        **PLEASE NOTE
        """
        client = service.extract_client_name(text, None, OrderType.IGRAPHIX)
        assert client == "Pechanga Resort Casino"
    
    def test_extract_client_returns_none_if_not_found(self, service):
        """Should return None if client pattern not found."""
        text = "Order with no client field"
        client = service.extract_client_name(text, None, OrderType.WORLDLINK)
        assert client is None


class TestDetectionPrecedence:
    """Test that detection order/precedence works correctly."""
    
    @pytest.fixture
    def service(self):
        return OrderDetectionService()
    
    def test_daviselen_detected_before_others(self, service):
        """Daviselen should be checked first (most specific)."""
        text = """
        DAVIS ELEN ADVERTISING
        Some other patterns that might match other agencies
        """
        assert service.detect_from_text(text) == OrderType.DAVISELEN
    
    def test_hl_detected_before_tcaa(self, service):
        """H&L should be detected before TCAA (both use CRTV)."""
        text = """
        CRTV-TV
        Estimate: 123
        Sacramento
        H/L Agency
        """
        assert service.detect_from_text(text) == OrderType.HL
        
        # But TCAA should match when it's specifically CRTV-Cable
        text = """
        CRTV-Cable
        Estimate: 123
        Client: Toyota
        """
        assert service.detect_from_text(text) == OrderType.TCAA


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
