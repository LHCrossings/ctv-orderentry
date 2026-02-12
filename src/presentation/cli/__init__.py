"""
CLI Input Collection - User interaction layer.

This module provides classes for collecting user input from the command line,
keeping user interaction separate from business logic.
"""

from .input_collectors import (
    InputCollector,
    BatchInputCollector,
    input_collector,
    batch_input_collector,
)

__all__ = [
    "InputCollector",
    "BatchInputCollector",
    "input_collector",
    "batch_input_collector",
]
