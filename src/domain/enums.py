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
    """
    WORLDLINK = "worldlink"
    TCAA = "tcaa"
    OPAD = "opad"
    RPM = "rpm"
    HL_PARTNERS = "hl"
    DAVISELEN = "daviselen"
    MISFIT = "misfit"
    IMPACT = "impact"
    IGRAPHIX = "igraphix"
    ADMERASIA = "admerasia"
    SAGENT = "sagent"
    UNKNOWN = "unknown"
    
    def requires_block_refresh(self) -> bool:
        """Determine if this order type needs block refresh after processing."""
        # Only WorldLink orders with multiple markets need block refresh
        return self == OrderType.WORLDLINK
    
    def supports_multiple_markets(self) -> bool:
        """Check if this order type can span multiple markets."""
        return self in {OrderType.WORLDLINK, OrderType.MISFIT, OrderType.RPM, OrderType.SAGENT}


class OrderStatus(Enum):
    """Status of an order in the processing pipeline."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Market(Enum):
    """
    Broadcast markets for Crossings TV and The Asian Channel.
    
    Maps market codes to full names per project documentation.
    """
    # Crossings TV markets
    CVC = "CVC"  # Central Valley (Sacramento)
    SFO = "SFO"  # San Francisco
    LAX = "LAX"  # Los Angeles
    SEA = "SEA"  # Seattle
    HOU = "HOU"  # Houston
    CMP = "CMP"  # Chicago/Minneapolis
    WDC = "WDC"  # Washington DC
    MMT = "MMT"  # Multimarket National
    NYC = "NYC"  # New York City/New Jersey
    
    # The Asian Channel markets
    DAL = "DAL"  # Dallas
    
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
            Block code used in Etere system (e.g., "C/M" for Chinese)
        """
        block_codes = {
            Language.MANDARIN: "C/M",
            Language.CANTONESE: "C/M",
            Language.FILIPINO: "T",
            Language.KOREAN: "K",
            Language.VIETNAMESE: "V",
            Language.HMONG: "Hm",
            Language.SOUTH_ASIAN: "SA/P",
            Language.PUNJABI: "SA/P",
            Language.JAPANESE: "J",
        }
        return block_codes.get(self, "C/M")


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
            OrderType.HL_PARTNERS: cls.HL_PARTNERS.value,
            OrderType.DAVISELEN: cls.DAVISELEN_DEFAULT.value,
            OrderType.MISFIT: cls.MISFIT.value,
            OrderType.SAGENT: cls.SAGENT.value,
        }
        return mapping.get(order_type, cls.DEFAULT.value)
