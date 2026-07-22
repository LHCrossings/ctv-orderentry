"""
Order Detection Service - Pure business logic for identifying order types.

This service separates the detection logic from file I/O. The detection
logic is pure string matching - given text content, return the order type.
"""

import re
import sys
from pathlib import Path
from typing import Protocol

# Add src to path for absolute imports
_src_path = Path(__file__).parent.parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from domain.enums import OrderType


class TextExtractor(Protocol):
    """
    Protocol defining how to extract text from PDFs.

    This allows the service to work with any PDF library without
    tight coupling to pdfplumber.
    """

    def extract_text(self, page_number: int) -> str:
        """Extract text from a specific page (0-indexed)."""
        ...

    def get_page_count(self) -> int:
        """Get total number of pages in PDF."""
        ...


class OrderDetectionService:
    """
    Pure business logic for detecting order types from PDF text.

    This service contains no file I/O - it works with text strings.
    All detection patterns are encoded as methods for easy testing
    and modification.
    """

    def detect_from_text(
        self,
        first_page_text: str,
        second_page_text: str | None = None
    ) -> OrderType:
        """
        Detect order type from extracted PDF text.

        This is the main detection method - pure business logic with
        no side effects. Given text, return the detected type.

        Args:
            first_page_text: Text content from first page
            second_page_text: Optional text content from second page

        Returns:
            Detected OrderType enum value

        Examples:
            >>> service = OrderDetectionService()
            >>> text = "WL Tracking No. 12345\\nAgency:Tatari"
            >>> service.detect_from_text(text)
            OrderType.WORLDLINK
        """
        # Detection order matters! More specific checks first

        # SacRT (check BEFORE LRCCD — SacRT media plans also come through
        # 3Fold, so the 3foldcomm marker alone would misroute them)
        if self._is_sacrt(first_page_text):
            return OrderType.SACRT

        # LRCCD / 3Fold (check first — very distinctive 3foldcomm.com marker)
        if self._is_lrccd(first_page_text):
            return OrderType.LRCCD

        # SAGENT (check early - distinctive GaleForceMedia marker)
        if self._is_sagent(first_page_text):
            return OrderType.SAGENT

        # Intertrend (check before Daviselen — both use Brand Time Schedule format)
        if self._is_intertrend(first_page_text):
            return OrderType.INTERTREND

        # Daviselen (check first - has unique markers)
        if self._is_daviselen(first_page_text, second_page_text):
            return OrderType.DAVISELEN

        # WorldLink (check early - common and distinctive)
        if self._is_worldlink(first_page_text):
            return OrderType.WORLDLINK

        # H/L Buy Detail Report, clean-text variant (before HL — shares markers).
        # Type3-font/rotated BDRs are caught earlier in pdf_order_detector via
        # is_bdr_pdf(); this catches newer exports with extractable text.
        if self._is_bdr(first_page_text):
            return OrderType.HL_BDR

        # H&L Partners (before TCAA - both use CRTV)
        if self._is_hl_partners(first_page_text):
            return OrderType.HL

        # TCAA AV — Toyota AAPI flight schedule (check before regular TCAA)
        if self._is_tcaa_av(first_page_text):
            return OrderType.TCAA_AV

        # TCAA (specific CRTV-Cable marker)
        if self._is_tcaa(first_page_text):
            return OrderType.TCAA

        # Wallrich (Strata IO for Sacramento/CVC, KBTV station — check before opAD)
        if self._is_wallrich(first_page_text):
            return OrderType.WALLRICH

        # opAD
        if self._is_opad(first_page_text):
            return OrderType.OPAD

        # Misfit
        if self._is_misfit(first_page_text):
            return OrderType.MISFIT

        # Impact Marketing
        if self._is_impact(first_page_text):
            return OrderType.IMPACT

        # iGraphix
        if self._is_igraphix(first_page_text):
            return OrderType.IGRAPHIX

        # Admerasia
        if self._is_admerasia(first_page_text):
            return OrderType.ADMERASIA

        # Media Solutions / Pulsar Advertising c/o Mediasol — check before RPM
        # (Mediasol PDFs contain "Sacramento" in billing address, which trips RPM's market check)
        if self._is_mediasol(first_page_text):
            return OrderType.MEDIASOL

        # RPM
        if self._is_rpm(first_page_text):
            return OrderType.RPM

        # Hyphen (formerly JP Marketing) — "Buy Detail Report" format
        if self._is_hyphen(first_page_text):
            return OrderType.HYPHEN

        # GaleForceMedia (generic, not Sagent) — check AFTER Sagent
        if self._is_galeforce(first_page_text):
            return OrderType.GALEFORCE

        # Time Advertising broadcast orders (Graton Casino, etc.)
        if self._is_timeadvertising(first_page_text):
            return OrderType.TIMEADVERTISING

        # Sacramento County Voter Registration
        if self._is_saccountyvoters(first_page_text, second_page_text):
            return OrderType.SACCOUNTYVOTERS

        # Resorts World New York (check before SCWA — both use "Crossings TV Media Proposal")
        if self._is_rwny(first_page_text):
            return OrderType.RWNY

        # Sierra Donor Services (Crossings TV Media Plan — check before SCWA)
        if self._is_sierra_donor(first_page_text):
            return OrderType.SIERRADONOR

        # Sacramento County Water Agency (Crossings TV house template)
        if self._is_scwa(first_page_text):
            return OrderType.SCWA

        # Imprenta (PDF version — XLSX is detected via content scan in order_scanner)
        if self._is_imprenta(first_page_text):
            return OrderType.IMPRENTA

        # 3 Olives Media (Riverside County Voters and similar)
        if self._is_threeolives(first_page_text):
            return OrderType.THREEOLIVES

        # BVK (Milwaukee agency — "Billing To: BVK" + "CPE:" header)
        if self._is_bvk(first_page_text):
            return OrderType.BVK

        # Fight the Bite public health campaign
        if self._is_fightthebite(first_page_text):
            return OrderType.FIGHTTHEBITE

        return OrderType.UNKNOWN

    def _is_fightthebite(self, text: str) -> bool:
        """Definitive marker: 'Fight The Bite' (campaign title)."""
        return "Fight The Bite" in text or "Fight the Bite" in text

    def _is_sacrt(self, text: str) -> bool:
        """
        Check if text matches a SacRT (Sacramento Regional Transit) media plan.

        SacRT orders arrive on the same 3Fold Communications media-plan form as
        LRCCD, so this MUST run before _is_lrccd. Definitive marker: the
        advertiser name (the billing header sometimes typos it, so also accept
        the SacRT short name on a media plan).
        """
        lower = text.lower()
        if "sacramento regional transit" in lower:
            return True
        return "sacrt" in lower and "media plan" in lower

    def _is_lrccd(self, text: str) -> bool:
        """
        Check if text matches a 3Fold Communications / LRCCD media plan.

        3Fold patterns:
        - "3foldcomm.com"  (agency email domain — decisive UNLESS the
          advertiser is another 3Fold client, e.g. SacRT — checked earlier)
        - "3Fold" + "MEDIA PLAN"  (agency name on the Crossings TV media-plan form)
        - "Los Rios Community College"  (the advertiser)
        """
        lower = text.lower()
        if self._is_sacrt(text):
            return False
        if "3foldcomm" in lower:
            return True
        if "3fold" in lower and "media plan" in lower:
            return True
        return "los rios community college" in lower and "media plan" in lower

    def _is_rwny(self, text: str) -> bool:
        """
        Check if text matches a Resorts World New York media proposal.

        Definitive marker: "Resorts World New York" (client name).
        """
        return "Resorts World New York" in text

    def _is_sierra_donor(self, text: str) -> bool:
        """
        Check if text matches a Sierra Donor Services Crossings TV Media Plan.

        Markers:
        - "Sierra Donor Services" (advertiser name, highly specific)
        - "Crossings TV Media Plan" (house template title, distinct from SCWA's "Media Proposal")
        """
        return "Sierra Donor Services" in text and "Crossings TV Media Plan" in text

    def _is_scwa(self, text: str) -> bool:
        """
        Check if text matches Sacramento County Water Agency (SCWA) order patterns.

        SCWA patterns:
        - "Crossings TV Media Proposal" (house template title)
        - "Sacramento County Water Agency" (advertiser)
        """
        return (
            "Crossings TV Media Proposal" in text and
            "Sacramento County Water Agency" in text
        )

    def _is_sagent(self, text: str) -> bool:
        """
        Check if text matches SAGENT order patterns.

        SAGENT patterns:
        - "Sagent" (company name)
        - "generated by GaleForceMedia" (PDF generator)
        - "2215 21st St" + "Sacramento, CA 95818" (address)
        - "sagentmarketing.com" (website)

        Require at least 2 markers for confident detection.
        """
        sagent_markers = [
            "Sagent",
            "generated by GaleForceMedia",
            "2215 21st St",
            "Sacramento, CA 95818",
            "sagentmarketing.com"
        ]

        marker_count = sum(1 for marker in sagent_markers if marker in text)
        return marker_count >= 2

    def _is_intertrend(self, text: str) -> bool:
        """
        Check if text matches Intertrend Communications order patterns.

        Intertrend patterns:
        - "INTERTREND" appears in the cover page terms text
        - "InterTrend Communications" as agency name
        """
        return "INTERTREND" in text.upper()

    def _is_daviselen(
        self,
        first_page: str,
        second_page: str | None
    ) -> bool:
        """
        Check if text matches Daviselen order patterns.

        Daviselen patterns:
        - "DAVIS ELEN" or "DAVISELEN" anywhere
        - "Brand Time Schedule - CLAN" on page 2
        """
        first_upper = first_page.upper()

        if "DAVIS ELEN" in first_upper or "DAVISELEN" in first_upper:
            return True

        if second_page:
            second_upper = second_page.upper()
            if "DAVIS ELEN" in second_upper or "DAVISELEN" in second_upper:
                return True
            # Check for unique format
            if "Brand Time Schedule" in second_page and "CLAN" in second_page:
                return True

        return False

    def _is_worldlink(self, text: str) -> bool:
        """
        Check if text matches WorldLink order patterns.

        WorldLink patterns:
        - "WL Tracking No."
        - "Unwired Tracking No."
        - "Agency:Tatari"
        - "c/o WorldLink"
        - "WorldLink Ventures"
        """
        patterns = [
            "WL Tracking No.",
            "Unwired Tracking No.",
            "Agency:Tatari",
            "c/o WorldLink",
            "WorldLink Ventures"
        ]
        return any(pattern in text for pattern in patterns)

    def _is_bdr(self, text: str) -> bool:
        """
        Detect a clean-text H/L Buy Detail Report (non-Type3 font).

        Delegates to the parser's content-based check so the row-layout
        signature lives in one place. Must be checked before _is_hl_partners
        since both share the "H/L Agency" header marker.
        """
        try:
            from browser_automation.parsers.hl_bdr_parser import is_bdr_text
        except Exception:
            return False
        return is_bdr_text(text)

    def _is_hl_partners(self, text: str) -> bool:
        """
        Check if text matches H&L Partners order patterns.

        H&L Partners patterns:
        - "H/L Agency" or "H/L Agency San Francisco"
        - CRTV-TV (not CRTV-Cable) + Sacramento/San Francisco + Estimate
        - Encoding-damaged variants: "HL Agency", "H Agency"

        Note: Must check before TCAA since both use CRTV
        """
        # Direct H&L agency mention
        if "H/L Agency" in text or "H/L Agency San Francisco" in text:
            return True

        # H&L variant detection for PDFs with encoding issues
        text_upper = text.upper()
        has_crtv = "CRTV-TV" in text or "CRTV" in text_upper
        has_estimate = "Estimate:" in text
        has_location = any(loc in text or loc in text_upper for loc in [
            "Sacramento", "San Francisco",
            "SACRAMENTO", "SAN FRANCISCO"
        ])

        if has_crtv and has_estimate and has_location:
            # Additional check: NOT TCAA (TCAA uses CRTV-Cable)
            if "CRTV-Cable" not in text:
                # Check for H&L-specific markers
                hl_markers = [
                    "Send Billing",
                    "HL Agency",
                    "H Agency",
                    "Agency San Francisco"
                ]
                if any(marker in text for marker in hl_markers):
                    return True

        return False

    def _is_tcaa_av(self, text: str) -> bool:
        """Toyota AAPI Added Value flight schedule — distinctive header line."""
        return "AAPI Heritage Month" in text and "Month Sponsorship" in text

    def _is_tcaa(self, text: str) -> bool:
        """
        Check if text matches TCAA order patterns.

        TCAA patterns:
        - "CRTV-Cable" (not CRTV-TV) + "Estimate:"
        """
        return "CRTV-Cable" in text and "Estimate:" in text

    def count_tcaa_orders(self, text: str) -> int:
        """
        Count the number of TCAA orders in a PDF.

        TCAA PDFs can have multiple orders, each with its own "Estimate: XXXX".

        Args:
            text: Full PDF text (all pages)

        Returns:
            Number of distinct orders found
        """
        import re

        # Find all estimate numbers
        estimate_pattern = r'Estimate:\s*(\d+)'
        estimates = re.findall(estimate_pattern, text)

        # Return count of unique estimate numbers
        return len(set(estimates))

    def split_tcaa_orders(self, full_text: str) -> list[dict[str, str]]:
        """
        Split a multi-order TCAA PDF into individual orders.

        FIXED: Filters out summary pages instead of grouping by estimate number.

        Args:
            full_text: Complete text from all pages of PDF

        Returns:
            List of dicts with 'estimate' and 'text' for each order
        """
        import re

        # Find all estimate numbers and split at each occurrence
        estimate_pattern = r'Estimate:\s*(\d+)'

        # Find all estimates
        all_estimates = re.findall(estimate_pattern, full_text)

        if not all_estimates:
            return [{'estimate': 'Unknown', 'text': full_text}]

        # Split text at each "Estimate:" marker (keeping the marker with the text)
        parts = re.split(r'(?=Estimate:\s*\d+)', full_text)

        sections = []

        # Process each part
        for part in parts:
            if not part.strip():
                continue

            # Extract estimate number from this section
            est_match = re.search(estimate_pattern, part)
            if not est_match:
                continue

            estimate_num = est_match.group(1)

            # CRITICAL FIX: Determine if this is a schedule page or summary page
            # Schedule pages have actual line items and "SCHEDULE TOTALS"
            has_schedule = (
                'SCHEDULE TOTALS' in part or
                'Station Total:' in part or
                part.count('CRTV-Cable') > 3  # Has multiple line items
            )

            # Summary pages only have aggregate data
            is_summary = (
                'Summary by Market' in part or
                'Summary by Station/System' in part
            )

            # Only include sections with actual schedule data (not summaries)
            if has_schedule and not is_summary:
                sections.append({
                    'estimate': estimate_num,
                    'text': part
                })

        # Return filtered sections
        if sections:
            return sections

        # Fallback: if we filtered everything out, return unique estimates
        unique_estimates = sorted(set(all_estimates))
        return [{'estimate': est, 'text': full_text} for est in unique_estimates]


    def _is_wallrich(self, text: str) -> bool:
        """
        Check if text matches Wallrich agency order (Strata IO, KBTV station).

        Wallrich uses the same "# of SPOTS PER WEEK" Strata format as opAD,
        but the station is KBTV (Sacramento CTV) rather than CROSSINGS TV-TV.
        """
        return (
            "# of SPOTS PER WEEK" in text and
            "Estimate:" in text and
            "KBTV" in text
        )

    def _is_opad(self, text: str) -> bool:
        """
        Check if text matches opAD order patterns.

        opAD patterns:
        - "Estimate:" + "# of SPOTS PER WEEK"
        """
        return "Estimate:" in text and "# of SPOTS PER WEEK" in text

    def _is_misfit(self, text: str) -> bool:
        """
        Check if text matches Misfit order patterns.

        Misfit patterns:
        - "Agency: Misfit"
        - "@agencymisfit.com"
        - "Misfit" + "Crossings TV"
        Must have "Language Block" column header
        """
        has_misfit = (
            "Agency: Misfit" in text or
            "@agencymisfit.com" in text or
            ("Misfit" in text and "Crossings TV" in text)
        )
        has_language_block = "Language Block" in text

        return has_misfit and has_language_block

    def _is_impact(self, text: str) -> bool:
        """
        Check if text matches Impact Marketing order patterns.

        Impact patterns:
        - "Impact Marketing" or "Big Valley Ford" or "@impactcalifornia.com"
        Must have quarterly structure (Q1-, Q2-, etc.) or Crossings TV + Central Valley
        """
        has_impact = (
            "Impact Marketing" in text or
            "Big Valley Ford" in text or
            "@impactcalifornia.com" in text
        )

        has_quarterly = any(q in text for q in ["Q1-", "Q2-", "Q3-", "Q4-"])
        has_crossings_cv = "Crossings TV" in text and "Central Valley" in text

        return has_impact and (has_quarterly or has_crossings_cv)

    def _is_igraphix(self, text: str) -> bool:
        """
        Check if text matches iGraphix order patterns.

        iGraphix patterns:
        - "iGraphix" or "IGraphix"
        Must have known clients (Pechanga, Sky River) or c/o + Crossings TV
        """
        has_igraphix = "iGraphix" in text or "IGraphix" in text

        has_client = (
            "Pechanga" in text or
            "Sky River" in text or
            ("c/o" in text and "Crossings TV" in text)
        )

        return has_igraphix and has_client

    def _is_admerasia(self, text: str) -> bool:
        """
        Check if text matches Admerasia order patterns.

        Primary: "Admerasia" present with McDonald's branding.
        Fallback: Order Number + -MD pattern (e.g. "07-MD10-...") without
        requiring "Admerasia" text — covers clean McDonald's-branded IOs.
        """
        text_upper = text.upper()
        has_admerasia = "Admerasia" in text or "ADMERASIA" in text_upper

        # Admerasia name + McDonald's → definitive
        if has_admerasia and ("McDonald" in text or "Ref: McDonald" in text):
            return True

        # Order Number + -MD pattern — unique to Admerasia (no "Admerasia" required)
        if "Order Number:" in text and "-MD" in text:
            return True

        return False

    def _is_rpm(self, text: str) -> bool:
        """
        Check if text matches RPM order patterns.

        RPM patterns:
        - "RPM" in first 300 characters
        - Seattle-Tacoma/Sacramento-Stockton/San Francisco + Estimate + specific header
        """
        # Check header (first 300 chars)
        header = text[:300]
        if "RPM" in header:
            return True

        # Check for market-specific patterns
        # "Sacramento-Stockton" for clean text; "Sacramento" alone for OCR output
        has_market = any(market in text for market in [
            "Seattle-Tacoma",
            "Sacramento-Stockton",
            "Sacramento",
            "San Francisco"
        ])
        has_estimate = "Estimate:" in text
        # Any Crossings TV station header (Seattle, Sacramento, SFO vary).
        # PDFs with embedded text layers render "CROSSINGS TV" as "CROSSINGST V"
        # or "CROSSINGST TV" (space dropped) — match on "CROSSINGS" alone since
        # it's distinctive enough when combined with market + estimate.
        has_header = "CROSSINGS" in text.upper()

        return has_market and has_estimate and has_header

    def _is_saccountyvoters(self, text: str, second_page: str | None = None) -> bool:
        """
        Check if text matches Sacramento County Voter Registration order.

        Markers:
        - "Sacramento County Voter" (client name)
        - "Phase 1 Length" (unique two-phase structure, may appear on page 2)
        """
        combined = text + (second_page or "")
        return "Sacramento County Voter" in combined and "Phase 1 Length" in combined

    def _is_imprenta(self, text: str) -> bool:
        """
        Check if text matches an Imprenta PDF broadcast order.

        Marker: "IMPRENTA" appears in the header (Format A: "IMPRENTA: <client>",
        Format B: "Agency: IMPRENTA").
        """
        return "IMPRENTA" in text.upper()

    def _is_timeadvertising(self, text: str) -> bool:
        """
        Check if text matches Time Advertising broadcast order format.

        Time Advertising patterns (need both):
        - "BROADCAST ORDER" header
        - "Time Advertising" in the BILL TO line
        """
        return "BROADCAST ORDER" in text and "Time Advertising" in text

    def _is_hyphen(self, text: str) -> bool:
        """
        Check if text matches a Hyphen (formerly JP Marketing) Buy Detail Report.

        Two definitive markers: the "Buy Detail Report" header and "HYPHEN"
        in the Send Billing To field.
        """
        return "Buy Detail Report" in text and "HYPHEN" in text

    def _is_threeolives(self, text: str) -> bool:
        """
        Check if text matches a 3 Olives Media insertion order.

        Definitive marker: "3olivesmedia" appears in the agency email domain.
        """
        return "3olivesmedia" in text.lower()

    def _is_bvk(self, text: str) -> bool:
        """
        Check if text matches a BVK broadcast order.

        Two definitive markers:
        - "bvk" (agency name in Billing To field or footer)
        - "CPE:" (unique estimate/order reference field used by BVK)
        """
        return "bvk" in text.lower() and "CPE:" in text

    def _is_mediasol(self, text: str) -> bool:
        """
        Check if text matches a Media Solutions / Pulsar Advertising order.

        Definitive markers in "Send Billing To:" field:
        - "Pulsar Advertising c/o Mediasol"
        - or simply "Mediasol" anywhere in the text
        Also uses Strata IO format with "Estimate:" and "Crossings TV-TV".
        """
        text_lower = text.lower()
        return "mediasol" in text_lower or "pulsar advertising" in text_lower

    def _is_galeforce(self, text: str) -> bool:
        """
        Check if text matches generic GaleForceMedia order (not Sagent).

        A single definitive marker: the PDF footer "generated by GaleForceMedia".
        We explicitly exclude Sagent orders (already handled above) to avoid
        double-detection.
        """
        return "generated by GaleForceMedia" in text and not self._is_sagent(text)

    def has_encoding_issues(self, text: str) -> bool:
        """
        Check if PDF text has severe encoding issues.

        PDFs with encoding issues show CID (Character ID) markers
        instead of readable text.

        Returns:
            True if text has more than 20 CID markers
        """
        return "(cid:" in text and text.count("(cid:") > 20

    def extract_client_name(
        self,
        first_page_text: str,
        second_page_text: str | None,
        order_type: OrderType
    ) -> str | None:
        """
        Extract client/advertiser name from PDF text based on order type.

        Each agency has different patterns for client names.

        Args:
            first_page_text: Text from first page
            second_page_text: Optional text from second page
            order_type: Detected order type

        Returns:
            Client name if found, None otherwise
        """
        # Agency-specific extraction patterns
        if order_type == OrderType.TIMEADVERTISING:
            return self._extract_timeadvertising_client(first_page_text)

        if order_type in (OrderType.SAGENT, OrderType.GALEFORCE):
            return self._extract_sagent_client(first_page_text)

        elif order_type == OrderType.WORLDLINK:
            return self._extract_worldlink_client(first_page_text)

        elif order_type == OrderType.TCAA:
            return self._extract_tcaa_client(first_page_text)

        elif order_type == OrderType.OPAD:
            return self._extract_opad_client(first_page_text)

        elif order_type in (OrderType.HL, OrderType.HL_BDR, OrderType.MEDIASOL):
            return self._extract_hl_client(first_page_text)

        elif order_type == OrderType.DAVISELEN:
            return self._extract_daviselen_client(first_page_text, second_page_text)

        elif order_type == OrderType.MISFIT:
            return self._extract_misfit_client(first_page_text)

        elif order_type == OrderType.IGRAPHIX:
            return self._extract_igraphix_client(first_page_text)

        elif order_type == OrderType.SACCOUNTYVOTERS:
            return self._extract_saccountyvoters_client(first_page_text)

        elif order_type == OrderType.RWNY:
            return "Resorts World New York"

        elif order_type == OrderType.SCWA:
            # PDF is two-column; address follows on same line — stop at "Address:"
            m = re.search(r'Advertiser\s+(.*?)\s+Address:', first_page_text)
            return m.group(1).strip() if m else "Sacramento County Water Agency"

        elif order_type == OrderType.BVK:
            m = re.search(r'Client:\s*(.+?)(?:\s{2,}|\n|Demo:)', first_page_text, re.DOTALL)
            return m.group(1).strip() if m else None

        elif order_type == OrderType.ADMERASIA:
            # "Ref: McDonald's" is always present on Admerasia IOs
            m = re.search(r"Ref:\s*(.+)", first_page_text)
            if m:
                return m.group(1).strip()
            return "McDonald's"

        # Fallback: try common patterns
        return self._extract_generic_client(first_page_text)

    def _extract_timeadvertising_client(self, text: str) -> str | None:
        """Extract advertiser from Time Advertising order — "ADVERTISER: {name}" line."""
        match = re.search(r'ADVERTISER:\s*([^\n]+)', text)
        return match.group(1).strip() if match else None

    def _extract_sagent_client(self, text: str) -> str | None:
        """
        Extract client from SAGENT order - look for 'ADVERTISER:' field.

        SAGENT format: "ADVERTISER: CAL FIRE REV: 0"
        Need to strip the "REV: 0" suffix.
        """
        match = re.search(r'ADVERTISER:\s*([^\n]+)', text, re.IGNORECASE)
        if match:
            client = match.group(1).strip()
            # Remove "REV: #" suffix if present
            client = re.sub(r'\s+REV:\s*\d+\s*$', '', client, flags=re.IGNORECASE)
            return client.strip()
        return None

    def _extract_worldlink_client(self, text: str) -> str | None:
        """Extract client from WorldLink order - look for 'Advertiser:' field.

        Stop at the next field label so a single-line layout (e.g. OCR'd
        scanned PDFs, where "Advertiser:Feeding America Product Desc:..." has no
        newline) doesn't swallow the trailing fields into the client name.
        """
        match = re.search(
            r'Advertiser:\s*(.+?)(?:\s+(?:Product\s*Desc|Product|Estimate|Buyer)\b|\n|$)',
            text,
        )
        return match.group(1).strip() if match else None

    def _extract_tcaa_client(self, text: str) -> str | None:
        """
        Extract client from TCAA order.

        TCAA orders typically have the client name on a line,
        sometimes followed by "Estimate:" on the same or next line.
        """
        # Try "Client:" pattern first
        match = re.search(r'Client:\s*([^\n]+)', text)
        if match:
            client = match.group(1).strip()
            # Remove estimate if it's on the same line
            client = re.sub(r'\s*Estimate:.*$', '', client)
            return client

        # TCAA specific: Look for pattern after "CRTV-Cable"
        # Client name usually appears after the header
        match = re.search(r'CRTV-Cable[^\n]*\n\s*Estimate:\s*\d+[^\n]*\n\s*([^\n]+)', text)
        if match:
            client = match.group(1).strip()
            # Clean up - remove any trailing estimate references
            client = re.sub(r'\s*Estimate:.*$', '', client)
            return client

        # Fallback: generic extraction
        return self._extract_generic_client(text)

    def _extract_opad_client(self, text: str) -> str | None:
        """Extract client from opAD order - look for 'Client:' field."""
        match = re.search(r'Client:\s*([^\n]+)', text)
        return match.group(1).strip() if match else None

    def _extract_hl_client(self, text: str) -> str | None:
        """
        Extract client from H&L Partners order.

        H&L has fixed customer: Northern CA Dealers Association
        """
        match = re.search(r'Client:\s*([^\n]+?)(?:\s+Estimate:|\s+Vendor:)', text)
        if match:
            return match.group(1).strip()
        # Default for H&L
        return "Northern California Dealers Association"

    def _extract_daviselen_client(
        self,
        first_page: str,
        second_page: str | None
    ) -> str | None:
        """
        Extract client from Daviselen order.

        Look on page 1 for 'Client', or page 2 for 'CLIENT'
        """
        # Try page 1
        match = re.search(r'Client\s+([^\n]+?)(?:\n|Product)', first_page)
        if match:
            return match.group(1).strip()

        # Try page 2 if available
        if second_page:
            match = re.search(r'CLIENT\s+([A-Z]+)\s+(.+?)\s+Market', second_page)
            if match:
                return match.group(2).strip()

        return None

    def _extract_misfit_client(self, text: str) -> str | None:
        """Extract client from Misfit order - look for 'Contact:' field."""
        match = re.search(r'Contact:\s*([^\n]+)', text)
        return match.group(1).strip() if match else None

    def _extract_igraphix_client(self, text: str) -> str | None:
        """
        Extract client from iGraphix order.

        Pattern: "Advertiser: IGraphix c/o <client>" where client is one line.
        """
        match = re.search(
            r'Advertiser:.*?c/o\s+([^\n]+)',
            text,
            re.DOTALL | re.IGNORECASE
        )
        if match:
            client = match.group(1).strip()
            return client
        return None

    def _extract_saccountyvoters_client(self, text: str) -> str | None:
        """Extract client from Sacramento County Voters order."""
        match = re.search(r'Client:\s*([^\n]+)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return "Sacramento County"

    def _extract_generic_client(self, text: str) -> str | None:
        """
        Fallback client extraction using common patterns.

        Tries: Client:, Advertiser:, Customer:
        """
        patterns = [
            r'Client:\s*([^\n]+)',
            r'Advertiser:\s*([^\n]+)',
            r'Customer:\s*([^\n]+)'
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()

        return None


def detect_from_filename(filename: str) -> OrderType:
    """
    Detect order type from a filename alone (for non-PDF files).

    Used for JPG, XLSX, and other formats where content-based detection
    is not applicable at scan time.

    Args:
        filename: File name (basename, not full path)

    Returns:
        Detected OrderType, or UNKNOWN if not recognised
    """
    name_upper = filename.upper()
    if "RESORTS WORLD" in name_upper:
        return OrderType.RWNY
    if "LEXUS" in name_upper:
        return OrderType.LEXUS
    if "DART" in name_upper:
        return OrderType.DART
    if "POLARIS" in name_upper:
        return OrderType.POLARIS
    if "3OLIVES" in name_upper:
        return OrderType.THREEOLIVES
    if "FIGHT" in name_upper and "BITE" in name_upper:
        return OrderType.FIGHTTHEBITE
    # T&T Public Relations — workbooks may still carry the "Brentan Media"
    # template branding in the filename, so match either token.
    if "BRENTAN" in name_upper or "T&T" in name_upper:
        return OrderType.TT
    if "CRISPIN" in name_upper:
        return OrderType.CRISPIN
    # Emerald Queen Casino via TH Media — filename always carries "EQC".
    if "EQC" in name_upper or "EMERALD QUEEN" in name_upper or "TH MEDIA" in name_upper:
        return OrderType.EQC
    # Imprenta XLSX files don't carry "Imprenta" in the filename —
    # fall through to content-based detection in the scanner.
    return OrderType.UNKNOWN


def create_detection_service():
    """
    Factory function to create a fully configured PDFOrderDetector.

    Returns:
        Configured PDFOrderDetector instance (wraps OrderDetectionService)
    """
    from .pdf_order_detector import PDFOrderDetector
    return PDFOrderDetector(OrderDetectionService())
