"""
Tests for Application Configuration.
"""

import sys
from pathlib import Path

import pytest

# Add src to path
_src_path = Path(__file__).parent.parent.parent / "src"
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from orchestration.config import ApplicationConfig


class TestApplicationConfig:
    """Tests for ApplicationConfig."""

    def test_create_config_with_all_fields(self):
        """Should create config with all fields."""
        config = ApplicationConfig(
            incoming_dir=Path("/test/incoming"),
            processed_dir=Path("/test/processed"),
            error_dir=Path("/test/error"),
            customer_db_path=Path("/test/customers.db"),
            batch_size=5,
            auto_process=True,
            require_confirmation=False,
            headless=True,
            browser_timeout=60
        )

        assert config.incoming_dir == Path("/test/incoming")
        assert config.processed_dir == Path("/test/processed")
        assert config.error_dir == Path("/test/error")
        assert config.customer_db_path == Path("/test/customers.db")
        assert config.batch_size == 5
        assert config.auto_process is True
        assert config.require_confirmation is False
        assert config.headless is True
        assert config.browser_timeout == 60

    def test_create_config_with_defaults(self):
        """Should use default values for optional fields."""
        config = ApplicationConfig(
            incoming_dir=Path("/test/incoming"),
            processed_dir=Path("/test/processed"),
            error_dir=Path("/test/error"),
            customer_db_path=Path("/test/customers.db")
        )

        # Check defaults
        assert config.batch_size == 10
        assert config.auto_process is False
        assert config.require_confirmation is True
        assert config.headless is False
        assert config.browser_timeout == 30

    def test_from_defaults_factory(self):
        """Should create config with standard defaults."""
        config = ApplicationConfig.from_defaults()

        # Should use current directory as base
        base_path = Path.cwd()
        assert config.incoming_dir == base_path / "incoming"
        assert config.processed_dir == base_path / "processed"
        assert config.error_dir == base_path / "errors"
        assert config.customer_db_path == base_path / "data" / "customers.db"

    def test_for_testing_factory(self):
        """Should create config suitable for testing."""
        config = ApplicationConfig.for_testing()

        # Should use temp directory
        assert "/tmp/order_processing_test" in str(config.incoming_dir)
        assert config.auto_process is True
        assert config.require_confirmation is False
        assert config.headless is True
        assert config.batch_size == 5

    def test_config_is_immutable(self):
        """Should be immutable (frozen dataclass)."""
        config = ApplicationConfig.from_defaults()

        with pytest.raises(AttributeError):
            config.batch_size = 20

    def test_ensure_directories(self, tmp_path):
        """Should create directories if they don't exist."""
        config = ApplicationConfig(
            incoming_dir=tmp_path / "incoming",
            processed_dir=tmp_path / "processed",
            error_dir=tmp_path / "error",
            customer_db_path=tmp_path / "data" / "customers.db"
        )

        # Directories shouldn't exist yet
        assert not config.incoming_dir.exists()
        assert not config.processed_dir.exists()
        assert not config.error_dir.exists()

        # Create them
        config.ensure_directories()

        # Now they should exist
        assert config.incoming_dir.exists()
        assert config.processed_dir.exists()
        assert config.error_dir.exists()
        assert config.customer_db_path.parent.exists()

    def test_ensure_directories_idempotent(self, tmp_path):
        """Should be safe to call multiple times."""
        config = ApplicationConfig(
            incoming_dir=tmp_path / "incoming",
            processed_dir=tmp_path / "processed",
            error_dir=tmp_path / "error",
            customer_db_path=tmp_path / "data" / "customers.db"
        )

        # Create once
        config.ensure_directories()

        # Create again - should not error
        config.ensure_directories()

        # Still exist
        assert config.incoming_dir.exists()


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
