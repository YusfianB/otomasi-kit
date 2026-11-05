"""Scraper web berbasis config YAML (otomasi-kit).

Pemakaian: python -m scraper --help
"""

from .config import ConfigError, load_config, parse_config
from .runner import RunSummary, scrape

__all__ = ["ConfigError", "RunSummary", "load_config", "parse_config", "scrape"]
__version__ = "0.1.0"
