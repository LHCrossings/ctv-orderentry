"""
Domain Enums - Type-safe constants for the order processing system.

These enums replace magic strings throughout the codebase and provide
compile-time type checking and IDE autocompletion.
"""

from enum import Enum


class OrderType(Enum):
    """
    Types of advertising orders supported by the system.

    Each agency has unique PDF formats and processing requirements.
    CHARMAINE is a generic catch-all for Charmaine's direct client orders.
    """
    WORLDLINK = "worldlink"
    TCAA = "tcaa"
    OPAD = "opad"
    RPM = "rpm"
    HL = "hl"
    DAVISELEN = "daviselen"
    MISFIT = "misfit"
    IMPACT = "impact"
    IGRAPHIX = "igraphix"
    ADMERASIA = "admerasia"
    LEXUS = "lexus"
    SAGENT = "sagent"
    GALEFORCE = "galeforce"
    CHARMAINE = "charmaine"
    SACCOUNTYVOTERS = "saccountyvoters"
    XML = "xml"
    UNKNOWN = "unknown"

    def requires_block_refresh(self) -> bool:
        """Determine if this order type needs manual block refresh after processing."""
        # WorldLink block refresh is now automated — no manual step needed
        return False

    def supports_multiple_markets(self) -> bool:
        """Check if this order type can span multiple markets."""
        return self in {OrderType.WORLDLINK, OrderType.MISFIT, OrderType.RPM, OrderType.SAGENT}

    def is_always_agency(self) -> bool:
        """
        Check if this order type is always an agency order.

        All known agency OrderTypes are always billed as agency.
        CHARMAINE and UNKNOWN may be either agency or client.
        """
        return self not in {OrderType.CHARMAINE, OrderType.UNKNOWN}


class OrderBillingType(Enum):
    """
    Whether an order comes through an agency or direct from a client.

    This determines billing configuration universally across ALL order types.

    AGENCY orders:
        - Charge To: "Customer share indicating agency %"
        - Invoice Header: "Agency"

    CLIENT orders:
        - Charge To: "Customer"
        - Invoice Header: "Customer"

    Detection logic:
        - Known agency OrderTypes (WORLDLINK, TCAA, MISFIT, etc.) → AGENCY
        - No agency detected in PDF → likely CLIENT → prompt user to confirm
    """
    AGENCY = "agency"
    CLIENT = "client"

    def get_billing_type(self) -> "BillingType":
        """Map order billing type to standard billing configuration."""
        if self == OrderBillingType.AGENCY:
            return BillingType.CUSTOMER_SHARE_AGENCY
        else:
            return BillingType.CUSTOMER_DIRECT

    def get_charge_to(self) -> str:
        """Convenience: get charge_to string directly."""
        return self.get_billing_type().get_charge_to()

    def get_invoice_header(self) -> str:
        """Convenience: get invoice_header string directly."""
        return self.get_billing_type().get_invoice_header()


# ═══════════════════════════════════════════════════════════════════════════════
# KNOWN AGENCY KEYWORDS - For auto-detection of agency vs client orders
# ═══════════════════════════════════════════════════════════════════════════════

KNOWN_AGENCY_KEYWORDS: list[str] = [
    "worldlink", "tatari", "tcaa", "daviselen", "misfit",
    "igraphix", "admerasia", "opad", "rpm", "h&l partners",
    "impact marketing", "sagent", "galeforce", "galeforcemedia",
    "ntooitive",
]
"""
If ANY of these keywords appear in the PDF text (case-insensitive),
the order is automatically flagged as an AGENCY order.
If NONE are found, the system prompts the user to confirm CLIENT.
"""


def detect_order_billing_type(pdf_text: str) -> tuple[OrderBillingType, str | None]:
    """
    Detect whether an order is agency or client based on PDF content.

    Scans PDF text for known agency keywords. If found, returns AGENCY
    with the matched keyword. If not found, returns CLIENT with None.

    Args:
        pdf_text: Full text extracted from the PDF

    Returns:
        Tuple of (OrderBillingType, matched_keyword_or_None)

    Examples:
        >>> detect_order_billing_type("Agency: TCAA\\nClient: Toyota")
        (OrderBillingType.AGENCY, "tcaa")

        >>> detect_order_billing_type("Sacramento Region Community Foundation")
        (OrderBillingType.CLIENT, None)
    """
    text_lower = pdf_text.lower()

    for keyword in KNOWN_AGENCY_KEYWORDS:
        if keyword in text_lower:
            return OrderBillingType.AGENCY, keyword

    return OrderBillingType.CLIENT, None


class OrderStatus(Enum):
    """Status of an order in the processing pipeline."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Market(Enum):
    """
    Broadcast markets for Crossings TV and The Asian Channel.

    Each member carries both the string code (.value) and the Etere integer
    station ID (.etere_id).  Integer values are authoritative — taken from
    etere_client.py which is confirmed correct.
    """
    # Crossings TV markets          code   etere_id
    NYC = ("NYC", 1)   # New York City/New Jersey
    CMP = ("CMP", 2)   # Chicago/Minneapolis
    HOU = ("HOU", 3)   # Houston
    SFO = ("SFO", 4)   # San Francisco
    SEA = ("SEA", 5)   # Seattle
    LAX = ("LAX", 6)   # Los Angeles
    CVC = ("CVC", 7)   # Central Valley (Sacramento)
    WDC = ("WDC", 8)   # Washington DC
    MMT = ("MMT", 9)   # Multimarket National

    # The Asian Channel markets
    DAL = ("DAL", 10)  # Dallas

    def __new__(cls, code: str, etere_id: int):
        obj = object.__new__(cls)
        obj._value_ = code
        obj.etere_id = etere_id  # type: ignore[attr-defined]
        return obj

    def is_crossings_tv_market(self) -> bool:
        """Check if this is a Crossings TV market (vs Asian Channel)."""
        return self != Market.DAL

    def is_asian_channel_market(self) -> bool:
        """Check if this is an Asian Channel market."""
        return self == Market.DAL


class Language(Enum):
    """
    Programming languages for ethnic broadcasting.

    Each language has specific ROS (Run of Schedule) time blocks.
    Maps to abbreviations used in Etere block codes.
    """
    MANDARIN = "M"
    CANTONESE = "C"
    PUNJABI = "P"
    SOUTH_ASIAN = "SA"
    FILIPINO = "T"
    VIETNAMESE = "V"
    HMONG = "Hm"
    JAPANESE = "J"
    KOREAN = "K"

    def get_ros_schedule(self) -> tuple[str, str]:
        """
        Get the standard ROS (Run of Schedule) time range for this language.

        Returns:
            Tuple of (days, time_range) for standard ROS programming

        Examples:
            Chinese: ("M-Su", "6a-11:59p")
            Filipino: ("M-Su", "4p-7p")
        """
        ros_schedules = {
            Language.MANDARIN: ("M-Su", "6a-11:59p"),
            Language.CANTONESE: ("M-Su", "6a-11:59p"),
            Language.FILIPINO: ("M-Su", "4p-7p"),
            Language.KOREAN: ("M-Su", "8a-10a"),
            Language.VIETNAMESE: ("M-Su", "11a-1p"),
            Language.HMONG: ("Sa-Su", "6p-8p"),
            Language.SOUTH_ASIAN: ("M-Su", "1p-4p"),
            Language.PUNJABI: ("M-Su", "1p-4p"),
            Language.JAPANESE: ("M-F", "10a-11a"),
        }
        return ros_schedules.get(self, ("M-Su", "6a-11:59p"))

    def get_block_abbreviation(self) -> str:
        """
        Get the Etere block code abbreviation for this language.

        Returns:
            Block code used in Etere system (e.g., "M/C" for Chinese)
        """
        block_codes = {
            Language.MANDARIN: "M/C",
            Language.CANTONESE: "M/C",
            Language.FILIPINO: "T",
            Language.KOREAN: "K",
            Language.VIETNAMESE: "V",
            Language.HMONG: "Hm",
            Language.SOUTH_ASIAN: "SA/P",
            Language.PUNJABI: "SA/P",
            Language.JAPANESE: "J",
        }
        return block_codes.get(self, "M/C")


class BillingType(Enum):
    """
    Standard billing configurations for contracts.

    Format: (charge_to, invoice_header)
    These control how billing is handled in Etere contracts.
    """
    CUSTOMER_SHARE_AGENCY = ("Customer share indicating agency %", "Agency")
    AGENCY_WITH_CREDIT = ("Agency with Credit Note", "Customer")
    CUSTOMER_DIRECT = ("Customer", "Customer")
    AGENCY_DIRECT = ("Agency", "Agency")

    def get_charge_to(self) -> str:
        """Get the charge_to value."""
        return self.value[0]

    def get_invoice_header(self) -> str:
        """Get the invoice_header value."""
        return self.value[1]


class SeparationInterval(Enum):
    """
    Standard separation interval configurations by agency.

    Format: (customer_separation, event_separation, order_separation)
    These control how far apart ads must be scheduled in Etere.
    """
    WORLDLINK = (5, 0, 15)
    OPAD = (15, 0, 15)
    RPM = (25, 0, 15)
    HL_PARTNERS = (25, 0, 0)
    DAVISELEN_DEFAULT = (15, 0, 0)
    MISFIT = (15, 0, 0)
    SAGENT = (10, 0, 0)
    GALEFORCE = (25, 0, 0)
    CHARMAINE = (15, 0, 0)
    SACCOUNTYVOTERS = (15, 0, 0)
    DEFAULT = (15, 0, 0)

    @classmethod
    def for_order_type(cls, order_type: OrderType) -> tuple[int, int, int]:
        """
        Get separation intervals for a specific order type.

        Args:
            order_type: The type of order being processed

        Returns:
            Tuple of (customer, event, order) separation values
        """
        mapping = {
            OrderType.WORLDLINK: cls.WORLDLINK.value,
            OrderType.OPAD: cls.OPAD.value,
            OrderType.RPM: cls.RPM.value,
            OrderType.HL: cls.HL_PARTNERS.value,
            OrderType.DAVISELEN: cls.DAVISELEN_DEFAULT.value,
            OrderType.MISFIT: cls.MISFIT.value,
            OrderType.SAGENT: cls.SAGENT.value,
            OrderType.GALEFORCE: cls.GALEFORCE.value,
            OrderType.CHARMAINE: cls.CHARMAINE.value,
            OrderType.SACCOUNTYVOTERS: cls.SACCOUNTYVOTERS.value,
        }
        return mapping.get(order_type, cls.DEFAULT.value)
