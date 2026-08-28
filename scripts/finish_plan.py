"""Thin CLI wrapper — see src/business_logic/services/finish_plan.py."""

import sys

sys.path.insert(0, ".")
from src.business_logic.services.finish_plan import main  # noqa: E402

if __name__ == "__main__":
    main()
