"""
Presentation Layer - CLI and output formatting.

This layer handles all user interaction and output formatting,
keeping it separate from business logic.
"""

from .cli.input_collectors import (
    BatchInputCollector,
    InputCollector,
    batch_input_collector,
    input_collector,
)
from .formatters.output_formatters import (
    ConsoleFormatter,
    OrderFormatter,
    ProcessingResultFormatter,
    ProgressFormatter,
    console_formatter,
    order_formatter,
    progress_formatter,
    result_formatter,
)

__all__ = [
    # Input collection
    "InputCollector",
    "BatchInputCollector",
    "input_collector",
    "batch_input_collector",
    # Output formatting
    "ConsoleFormatter",
    "OrderFormatter",
    "ProcessingResultFormatter",
    "ProgressFormatter",
    "console_formatter",
    "order_formatter",
    "result_formatter",
    "progress_formatter",
]
