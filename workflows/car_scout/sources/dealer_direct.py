"""Dealer-direct inventory scraper — closes MarketCheck's coverage gap.

MarketCheck's aggregator sometimes misses Subaru trade-ins sitting on
non-Subaru dealer lots (Wilson Toyota trade-in, Ford dealership lot, etc.).
This scraper hits three Bellingham dealers' used-inventory pages directly
and filters to the four primary Subaru models.

Platforms:
- **Bellingham Ford** (Jazel) — server-rendered HTML, URL path filter by model
- **Toyota of Bellingham** (DealerInspire) — JS-rendered, Algolia-style filters
- **Audi Bellingham** (Dealer.com) — JS-rendered (see CAVEAT below)

CAVEAT — Dealer.com sites return a JS shell to plain Bright Data fetches.
Audi Bellingham (and nearby Subaru-trade-in candidates: Sound Ford, Walker's
Renton Subaru, Carter Subaru Shoreline, Michael's Subaru of Bellevue) all
hydrate inventory client-side via the Akamai-protected
`/api/widget/ws-inv-data/getInventory` XHR. The dealercom parser will return
0 listings for these — closing this gap requires Bright Data JS-rendering
(paid) or reverse-engineering the DDC API.

The scrape() method emits a WARNING when a fetch succeeds but yields 0
listings, so the silent-zero case is visible in Modal logs.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pydantic import ValidationError

from lib.logger import get_logger
from lib.scraping import BrightDataFetchError

from ..models import Listing
from .base import AbstractSourceScraper, SourceResult, tier_for

logger = get_logger("car-scout.dealer-direct")


# Primary Subaru models we hunt for — secondary makes/models aren't expected
# as trade-ins at these specific dealers, so we scope the scrape tight.
PRIMARY_SUBARU_MODELS: tuple[str, ...] = (
    "Crosstrek",
    "Forester",
    "Outback",
    "Impreza",
)


@dataclass(frozen=True)
class DealerConfig:
    """One configured dealer page to scrape."""

    name: str                     # slug, used in logs + DealerConfig lookup
    display_name: str             # human-readable, becomes Listing.dealer_name
    base_url: str                 # origin, for resolving relative links
    subaru_url_template: str      # full URL to Subaru-filtered inventory, {model} placeholder
    parser_key: str               # 'jazel' | 'dealerinspire' | 'dealercom'
    city: str
    state: str


DEALERS: tuple[DealerConfig, ...] = (
    DealerConfig(
        name="bellingham-ford",
        display_name="Bellingham Ford",
        base_url="https://www.bellinghamford.com",
        subaru_url_template=(
            "https://www.bellinghamford.com/inventory/used-vehicles/"
            "models-Subaru-{model}/"
        ),
        parser_key="jazel",
        city="Bellingham",
        state="WA",
    ),
    DealerConfig(
        name="toyota-of-bellingham",
        display_name="Toyota of Bellingham",
        base_url="https://www.toyotaofbellingham.com",
        subaru_url_template=(
            "https://www.toyotaofbellingham.com/used-vehicles/"
            "?_dFR%5Bmake%5D%5B0%5D=Subaru&_dFR%5Bmodel%5D%5B0%5D={model}"
        ),
        parser_key="dealerinspire",
        city="Bellingham",
        state="WA",
    ),
    DealerConfig(
        name="audi-bellingham",
        display_name="Audi Bellingham",
        base_url="https://www.audibellingham.com",
        subaru_url_template=(
            "https://www.audibellingham.com/used-inventory/"
            "index.htm?make=Subaru&model={model}"
        ),
        parser_key="dealercom",
        city="Bellingham",
        state="WA",
    ),
)


# ─── Generic helpers ────────────────────────────────────────────────────────

_VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
_PRICE_RE = re.compile(r"\$\s?([\d,]+)")
_MILEAGE_RE = re.compile(r"([\d,]+)\s*(?:mi|miles?)\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
    m = pattern.search(text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except (ValueError, IndexError):
        return None


def _extract_vin(text: str, link: str | None = None) -> str | None:
    """VINs are 17 chars, no I/O/Q. Search text, fall back to URL if present."""
    for source in (text, link or ""):
        m = _VIN_RE.search(source)
        if m:
            return m.group(1)
    return None


def _build_listing(
    *,
    dealer: DealerConfig,
    model: str,
    url: str,
    year: int,
    vin: str | None,
    mileage: int,
    price: int,
    trim: str | None = None,
) -> Listing | None:
    """Shared Listing construction with tier resolution + validation."""
    tier = tier_for("Subaru", model)
    if tier is None:
        logger.debug("dealer_direct_tier_none", extra={"model": model})
        return None

    now = datetime.now(timezone.utc)
    try:
        return Listing(
            url=url,
            vin=vin,
            source="dealer_direct",
            year=year,
            make="Subaru",
            model=model,
            trim=trim,
            # Dealer cards rarely expose transmission explicitly; digest handles "unknown"
            transmission="unknown",
            mileage=mileage,
            price=price,
            title_status="unknown",
            photos=[],
            dealer_name=dealer.display_name,
            seller_type="dealer",
            city=dealer.city,
            state=dealer.state,
            tier=tier,
            first_seen=now,
            last_seen=now,
        )
    except ValidationError as exc:
        logger.warning(
            "dealer_direct_listing_invalid",
            extra={"dealer": dealer.name, "url": url, "error": str(exc)},
        )
        return None


# ─── Parser: Jazel (Bellingham Ford) ────────────────────────────────────────

def _parse_jazel(html: str, dealer: DealerConfig, model: str) -> list[Listing]:
    """Parse Jazel platform HTML (server-rendered, links to /vehicle/[VIN]/).

    Anchor format observed: `<a href="/vehicle/[VIN]/Used-[Year]-[Make]-[Model]-[...]">`.
    Each card contains price and mileage text nearby.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    seen_vins: set[str] = set()

    # Each vehicle detail link is the anchor — find all vehicle cards
    for anchor in soup.find_all("a", href=re.compile(r"^/vehicle/[A-HJ-NPR-Z0-9]{17}/")):
        href = anchor.get("href", "")
        absolute_url = urljoin(dealer.base_url, href)

        vin = _extract_vin("", link=href)
        if not vin or vin in seen_vins:
            continue

        # Climb up to the enclosing card div to find price/mileage text
        card = anchor.find_parent(["div", "article", "li"])
        card_text = card.get_text(" ", strip=True) if card else anchor.get_text(" ", strip=True)

        # Filter: must match our expected model in the URL slug or text
        url_slug = href.lower()
        if model.lower() not in url_slug and model.lower() not in card_text.lower():
            continue

        year = _first_int(_YEAR_RE, card_text)
        mileage = _first_int(_MILEAGE_RE, card_text)
        price = _first_int(_PRICE_RE, card_text)
        if year is None or mileage is None or price is None:
            logger.debug(
                "jazel_card_missing_fields",
                extra={"dealer": dealer.name, "vin": vin, "year": year, "mileage": mileage, "price": price},
            )
            continue

        listing = _build_listing(
            dealer=dealer,
            model=model,
            url=absolute_url,
            year=year,
            vin=vin,
            mileage=mileage,
            price=price,
        )
        if listing is not None:
            listings.append(listing)
            seen_vins.add(vin)

    return listings


# ─── Parser: DealerInspire (Toyota of Bellingham) ───────────────────────────

def _parse_dealerinspire(html: str, dealer: DealerConfig, model: str) -> list[Listing]:
    """Parse DealerInspire platform HTML.

    DealerInspire typically uses Algolia's InstantSearch widgets and returns
    a JS-rendered grid. If Bright Data fetches without JS rendering, we'll
    see skeleton placeholders. Fall through to any vehicle-card anchors
    present, which sometimes appear in SSR fallback content.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    seen_vins: set[str] = set()

    # DealerInspire inventory VDP URLs follow `/vehicle/[VIN]/` or `/used/Subaru/[Year]-[...]/`.
    candidate_anchors = soup.find_all(
        "a",
        href=re.compile(r"(/vehicle/|/used/Subaru|/inventory/)[^?#]+", re.IGNORECASE),
    )
    for anchor in candidate_anchors:
        href = anchor.get("href", "")
        absolute_url = urljoin(dealer.base_url, href)

        card = anchor.find_parent(["div", "article", "li"])
        card_text = (card.get_text(" ", strip=True) if card else anchor.get_text(" ", strip=True))
        if model.lower() not in card_text.lower():
            continue
        if "subaru" not in card_text.lower():
            continue

        vin = _extract_vin(card_text, link=href)
        if vin is None or vin in seen_vins:
            continue

        year = _first_int(_YEAR_RE, card_text)
        mileage = _first_int(_MILEAGE_RE, card_text)
        price = _first_int(_PRICE_RE, card_text)
        if year is None or mileage is None or price is None:
            continue

        listing = _build_listing(
            dealer=dealer,
            model=model,
            url=absolute_url,
            year=year,
            vin=vin,
            mileage=mileage,
            price=price,
        )
        if listing is not None:
            listings.append(listing)
            seen_vins.add(vin)

    return listings


# ─── Parser: Dealer.com (Audi Bellingham) ───────────────────────────────────

def _parse_dealercom(html: str, dealer: DealerConfig, model: str) -> list[Listing]:
    """Parse Dealer.com platform HTML.

    Dealer.com inventory pages render cards with `data-uuid` or `data-vin`
    attributes on container divs, with text children for year/mileage/price.
    Also supports legacy structure with vehicle-card class.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    seen_vins: set[str] = set()

    # Primary: containers with data-vin (Dealer.com convention)
    for node in soup.find_all(attrs={"data-vin": True}):
        vin = node.get("data-vin", "").strip().upper()
        if len(vin) != 17 or vin in seen_vins:
            continue

        card_text = node.get_text(" ", strip=True)
        if "subaru" not in card_text.lower() or model.lower() not in card_text.lower():
            continue

        # Find the first VDP anchor inside this card
        anchor = node.find("a", href=True)
        href = anchor.get("href") if anchor else f"#{vin}"
        absolute_url = urljoin(dealer.base_url, href)

        year = _first_int(_YEAR_RE, card_text)
        mileage = _first_int(_MILEAGE_RE, card_text)
        price = _first_int(_PRICE_RE, card_text)
        if year is None or mileage is None or price is None:
            continue

        listing = _build_listing(
            dealer=dealer,
            model=model,
            url=absolute_url,
            year=year,
            vin=vin,
            mileage=mileage,
            price=price,
        )
        if listing is not None:
            listings.append(listing)
            seen_vins.add(vin)

    return listings


# ─── Parser dispatch ─────────────────────────────────────────────────────────

ParserFn = Callable[[str, DealerConfig, str], list[Listing]]

PARSERS: dict[str, ParserFn] = {
    "jazel": _parse_jazel,
    "dealerinspire": _parse_dealerinspire,
    "dealercom": _parse_dealercom,
}


# ─── Scraper class ───────────────────────────────────────────────────────────


MAX_DEALER_FETCH_WORKERS = 6


class DealerDirectScraper(AbstractSourceScraper):
    """Fetches Subaru used inventory from each configured Bellingham dealer.

    Fan-out: 3 dealers × 4 primary Subaru models = 12 page fetches. Runs via
    ThreadPoolExecutor (6 workers) — same pattern as `title_vdp.verify_titles_parallel`.
    At 5-15s per page, parallelizing cuts total cycle time from ~2 min to ~30s.
    """

    name = "dealer_direct"

    def scrape(self) -> SourceResult:
        result = SourceResult(source_name=self.name)
        work: list[tuple[DealerConfig, str, str]] = [
            (dealer, model, dealer.subaru_url_template.format(model=model))
            for dealer in DEALERS
            if PARSERS.get(dealer.parser_key) is not None
            for model in PRIMARY_SUBARU_MODELS
        ]
        for dealer in DEALERS:
            if PARSERS.get(dealer.parser_key) is None:
                result.errors.append(f"{dealer.name}: no parser for {dealer.parser_key}")

        with ThreadPoolExecutor(max_workers=MAX_DEALER_FETCH_WORKERS) as pool:
            futures = {
                pool.submit(self._fetch_and_parse, dealer, model, url): (dealer, model)
                for (dealer, model, url) in work
            }
            for fut in as_completed(futures):
                dealer, model = futures[fut]
                try:
                    listings, error = fut.result()
                except Exception as exc:  # noqa: BLE001 — never let one bucket kill the cycle
                    result.errors.append(f"{dealer.name} {model}: {exc}")
                    logger.warning(
                        "dealer_direct_future_crashed",
                        extra={"dealer": dealer.name, "model": model, "error": str(exc)},
                    )
                    continue
                if error:
                    result.errors.append(f"{dealer.name} {model}: {error}")
                else:
                    result.pages_fetched += 1
                result.listings.extend(listings)

        logger.info(
            "dealer_direct_scrape_complete",
            extra={
                "total_listings": len(result.listings),
                "pages_fetched": result.pages_fetched,
                "errors": len(result.errors),
            },
        )
        return result

    def _fetch_and_parse(
        self, dealer: DealerConfig, model: str, url: str
    ) -> tuple[list[Listing], str | None]:
        """Fetch one (dealer, model) page and parse it. Returns (listings, error_message)."""
        try:
            html = self.client.fetch(url)
        except BrightDataFetchError as exc:
            return [], str(exc)
        except Exception as exc:  # noqa: BLE001
            return [], f"fetch failed: {exc}"

        parser = PARSERS[dealer.parser_key]
        try:
            listings = parser(html, dealer, model)
        except Exception as exc:  # noqa: BLE001
            return [], f"parse failed: {exc}"

        # Distinguish "no Subaru trade-ins right now" from "parser broken / JS
        # shell only" — known-gap dealercom is expected, others warrant investigation.
        if not listings:
            logger.warning(
                "dealer_direct_zero_listings",
                extra={
                    "dealer": dealer.name,
                    "platform": dealer.parser_key,
                    "model": model,
                    "html_size": len(html),
                },
            )
        else:
            logger.info(
                "dealer_direct_bucket_done",
                extra={
                    "dealer": dealer.name,
                    "model": model,
                    "listings": len(listings),
                    "html_size": len(html),
                },
            )
        return listings, None
