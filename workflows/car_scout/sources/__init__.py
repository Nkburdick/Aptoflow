"""Source adapters for car_scout.

Each adapter fetches inventory pages via BrightDataClient, parses them into
canonical `Listing` objects, and yields them back to the scout orchestrator.

Adapters must be stateless — the orchestrator owns the scraping client.
"""

from __future__ import annotations

from .base import AbstractSourceScraper, SourceResult, build_default_scrapers
from .cargurus import CarGurusScraper

__all__ = [
    "AbstractSourceScraper",
    "SourceResult",
    "build_default_scrapers",
    "CarGurusScraper",
]
