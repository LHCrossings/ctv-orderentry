"""
Application Configuration.

Centralized configuration for the order processing application.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ApplicationConfig:
    """
    Central configuration for the application.
    
    All paths and settings are configurable through this object.
    """
    # Directory paths
    incoming_dir: Path
    processed_dir: Path
    error_dir: Path
    
    # Database paths
    customer_db_path: Path
    
    # Processing settings
    batch_size: int = 10
    auto_process: bool = False
    require_confirmation: bool = True
    
    # Browser settings
    headless: bool = False
    browser_timeout: int = 30
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        # Ensure directories exist or can be created
        for dir_path in [self.incoming_dir, self.processed_dir, self.error_dir]:
            if not dir_path.exists():
                # In production, you might want to create these
                # For now, just validate they're set
                pass
    
    @classmethod
    def from_defaults(cls) -> "ApplicationConfig":
        """
        Create configuration with default values.
        
        Returns:
            ApplicationConfig with standard defaults
        """
        base_path = Path.cwd()
        
        return cls(
            incoming_dir=base_path / "incoming",
            processed_dir=base_path / "processed",
            error_dir=base_path / "errors",
            customer_db_path=base_path / "data" / "customers.db",
            batch_size=10,
            auto_process=False,
            require_confirmation=True,
            headless=False,
            browser_timeout=30
        )
    
    @classmethod
    def for_testing(cls) -> "ApplicationConfig":
        """
        Create configuration for testing environment.
        
        Returns:
            ApplicationConfig with testing defaults
        """
        base_path = Path("/tmp/order_processing_test")
        
        return cls(
            incoming_dir=base_path / "incoming",
            processed_dir=base_path / "processed",
            error_dir=base_path / "errors",
            customer_db_path=base_path / "customers_test.db",
            batch_size=5,
            auto_process=True,
            require_confirmation=False,
            headless=True,
            browser_timeout=10
        )
    
    def ensure_directories(self) -> None:
        """Create all required directories if they don't exist."""
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.error_dir.mkdir(parents=True, exist_ok=True)
        self.customer_db_path.parent.mkdir(parents=True, exist_ok=True)
