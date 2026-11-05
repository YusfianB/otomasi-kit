"""data_tools: Excel/CSV processing, mass document generation and file utilities.

Run ``python -m data_tools --help`` for the command line interface.
"""

__version__ = "0.1.0"


class DataToolsError(Exception):
    """User-facing error; the message is shown as-is by the CLI (Bahasa Indonesia)."""
