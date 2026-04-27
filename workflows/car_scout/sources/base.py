"""Abstract source scraper interface + built-in source registry."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

from lib.scraping import BrightDataClient

from ..models import Listing, Tier

PRIMARY_MAKES_MODELS: set[tuple[str, str]] = {
    ("Subaru", "Crosstrek"),
    ("Subaru", "Forester"),
}

SECONDARY_MAKES_MODELS: set[tuple[str, str]] = set()

ALL_TARGET_MAKES_MODELS = PRIMARY_MAKES_MODELS | SECONDARY_MAKES_MODELS


def tier_for(make: str, model: str) -> Tier | None:
    """Return 'primary', 'secondary', or None if the make/model isn't in scope."""
    pair = (make, model)
    if pair in PRIMARY_MAKES_MODELS:
        return "primary"
    if pair in SECONDARY_MAKES_MODELS:
        return "secondary"
    return None


@dataclass
class SourceResult:
    """Per-source return payload for one scout cycle."""

    source_name: str
    listings: list[Listing] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pages_fetched: int = 0
    # Subscription / quota / radius-cap errors — separated from generic errors
    # so the operator notices the silent zero-ingestion case in digest summaries.
    subscription_errors: list[str] = field(default_factory=list)


class AbstractSourceScraper(abc.ABC):
    """Base class every source adapter implements."""

    # Canonical source-name identifier (used in logs + Listing.source)
    name: str = "abstract"

    def __init__(
        self,
        client: BrightDataClient,
        *,
        zip_code: str,
        radius_mi: int,
        budget_ceiling: int,
        year_floor: int,
        max_pages: int = 3,
    ) -> None:
        self.client = client
        self.zip_code = zip_code
        self.radius_mi = radius_mi
        self.budget_ceiling = budget_ceiling
        self.year_floor = year_floor
        self.max_pages = max_pages

    @abc.abstractmethod
    def scrape(self) -> SourceResult:
        """Fetch all pages for every (make, model) we care about and return listings."""
        raise NotImplementedError


def build_default_scrapers(
    client: BrightDataClient,
    *,
    zip_code: str,
    radius_mi: int,
    budget_ceiling: int,
    year_floor: int,
) -> list[AbstractSourceScraper]:
    """Instantiate the default set of enabled source scrapers for V1."""
    # Imports deferred to avoid circular imports during module load.
    from .cargurus import CarGurusScraper
    from .dealer_direct import DealerDirectScraper

    return [
        CarGurusScraper(
            client,
            zip_code=zip_code,
            radius_mi=radius_mi,
            budget_ceiling=budget_ceiling,
            year_floor=year_floor,
        ),
        # Dealer-direct closes MarketCheck's coverage gap on Subaru trade-ins
        # sitting at non-Subaru dealers (Bellingham Ford, Toyota of Bellingham,
        # Audi Bellingham). Primary Subarus only.
        DealerDirectScraper(
            client,
            zip_code=zip_code,
            radius_mi=radius_mi,
            budget_ceiling=budget_ceiling,
            year_floor=year_floor,
        ),
        # Autotrader, Cars.com queued for V1.5 once current set is battle-tested.
    ]


def build_dealer_direct_scraper(client: BrightDataClient) -> AbstractSourceScraper:
    """Construct just the dealer-direct scraper, bypassing CarGurus.

    The digest path uses this because it pulls primary inventory from
    MarketCheck already — CarGurus scout path is separate. Centralizing here
    keeps the dealer-direct config in one module.
    """
    from .dealer_direct import DealerDirectScraper

    # zip/radius/budget/year are irrelevant to dealer-direct (URLs are hardcoded
    # to the 3 Bellingham dealers), but the AbstractSourceScraper signature
    # requires them. Pass defaults.
    return DealerDirectScraper(
        client,
        zip_code="98225",
        radius_mi=0,
        budget_ceiling=0,
        year_floor=0,
    )
