"""CarGurus inventory-listing scraper.

Per car_scout plan, we fetch the search-results page for each target
(make, model) and extract the embedded JSON listings payload. CarGurus renders
the initial listing set inside a large JSON blob — typically either a Next.js
`__NEXT_DATA__` script or a `window.__PRELOADED_STATE__` / similar global.

This scraper is defensive: it tries several extraction strategies and returns
whatever listings it can parse. Unparseable cards are logged and skipped, not
fatal. Expect to revise selectors after first real run — log any 0-listing
runs loudly so regressions surface fast.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from pydantic import ValidationError

from lib.logger import get_logger
from lib.scraping import BrightDataFetchError

from ..models import Listing
from .base import ALL_TARGET_MAKES_MODELS, AbstractSourceScraper, SourceResult, tier_for

logger = get_logger("car-scout.cargurus")

# CarGurus entity IDs for each make (these are stable per-make identifiers
# in CarGurus's search params, not per-model). Populate on first real fetch.
# These values are placeholders — verify with one real fetch before shipping.
# If the make has no entityId known, we'll fall back to the name-based slug search.
CARGURUS_ENTITY_IDS: dict[str, str] = {
    "Subaru": "d270",
    "Toyota": "d296",
    "Honda": "d200",
    "Mazda": "d238",
}


def _build_query_url(
    *,
    zip_code: str,
    radius_mi: int,
    make: str,
    model: str,
    budget_ceiling: int,
    year_floor: int,
    page: int = 1,
) -> str:
    """Construct a CarGurus inventory-listing search URL for one (make, model)."""
    entity = CARGURUS_ENTITY_IDS.get(make, "")
    # CarGurus's inventory search supports filters by maxPrice + minYear via query params.
    # Model filter is trickier (requires a separate entity lookup); we rely on
    # CarGurus's post-filter on the results page plus our own strict make/model match.
    params = {
        "zip": zip_code,
        "distance": str(radius_mi),
        "entitySelectingHelper.selectedEntity": entity,
        "sortDir": "ASC",
        "sortType": "DEAL_SCORE",
        "maxPrice": str(budget_ceiling),
        "minYear": str(year_floor),
        "modelFilter": model,
        "page": str(page),
    }
    qs = "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())
    return (
        "https://www.cargurus.com/Cars/inventorylisting/"
        "viewDetailsFilterViewInventoryListing.action?" + qs
    )


def _extract_next_data_json(html: str) -> dict[str, Any] | None:
    """Try to pull the Next.js __NEXT_DATA__ blob. Return parsed JSON or None."""
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _extract_initial_state_json(html: str) -> dict[str, Any] | None:
    """Fall back to window.__INITIAL_STATE__ or window.__PRELOADED_STATE__ globals."""
    for pattern in (
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;\s*</script>",
        r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;\s*</script>",
    ):
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None


def _walk_for_listings(node: Any, found: list[dict[str, Any]]) -> None:
    """Depth-first walk of a JSON tree, collecting dicts that look like listings."""
    if isinstance(node, dict):
        # Heuristic: a listing dict has at least vin-or-listingId, price, and mileage
        keys = set(node.keys())
        has_id = bool(keys & {"vin", "listingId", "listing_id", "id"})
        has_price = bool(keys & {"price", "priceInDollars", "listingPrice", "askingPrice"})
        has_mileage = bool(keys & {"mileage", "mileageInMiles", "odometer"})
        if has_id and has_price and has_mileage:
            found.append(node)
        else:
            for value in node.values():
                _walk_for_listings(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_for_listings(item, found)


def _parse_listing_dict(raw: dict[str, Any], expected_make: str, expected_model: str) -> Listing | None:
    """Convert a raw listing dict (from embedded JSON) into a canonical Listing."""
    tier = tier_for(expected_make, expected_model)
    if tier is None:
        return None

    url = (
        raw.get("vdpUrl")
        or raw.get("detailUrl")
        or raw.get("url")
        or raw.get("listingUrl")
    )
    if not url:
        return None
    if url.startswith("/"):
        url = "https://www.cargurus.com" + url

    make = raw.get("makeName") or raw.get("make") or expected_make
    model = raw.get("modelName") or raw.get("model") or expected_model
    # Strict guard against cross-model bleed-through from CarGurus's default result set
    if make != expected_make or model != expected_model:
        return None

    price = raw.get("priceInDollars") or raw.get("price") or raw.get("listingPrice")
    if not isinstance(price, (int, float)):
        return None

    mileage = raw.get("mileageInMiles") or raw.get("mileage") or raw.get("odometer")
    if not isinstance(mileage, (int, float)):
        return None

    year = raw.get("year") or raw.get("modelYear")
    if not isinstance(year, int):
        return None

    vin = raw.get("vin")
    if vin and not isinstance(vin, str):
        vin = str(vin)

    trim = raw.get("trim") or raw.get("trimName")
    transmission_raw = (raw.get("transmission") or "").lower()
    if "auto" in transmission_raw or "cvt" in transmission_raw:
        transmission = "auto"
    elif "manual" in transmission_raw:
        transmission = "manual"
    else:
        transmission = "unknown"

    title_status_raw = (raw.get("titleStatus") or raw.get("titleCondition") or "").lower()
    if title_status_raw in ("clean", "cleanbranded"):
        title_status = "clean"
    elif "salvage" in title_status_raw:
        title_status = "salvage"
    elif "rebuilt" in title_status_raw:
        title_status = "rebuilt"
    else:
        title_status = "unknown"

    dealer_name = raw.get("sellerName") or raw.get("dealerName")
    seller_type_raw = (raw.get("sellerType") or "dealer").lower()
    seller_type = "private" if "private" in seller_type_raw else "dealer"

    city = raw.get("city") or raw.get("sellerCity")
    state = raw.get("state") or raw.get("sellerState")

    accident_count = raw.get("accidentCount")
    if accident_count is not None and not isinstance(accident_count, int):
        accident_count = None
    owner_count = raw.get("ownerCount")
    if owner_count is not None and not isinstance(owner_count, int):
        owner_count = None

    cargurus_rating = raw.get("dealRating") or raw.get("dealScore")
    allowed_ratings = {"Great", "Good", "Fair", "High", "Overpriced"}
    if cargurus_rating not in allowed_ratings:
        cargurus_rating = None

    photo_urls_raw = raw.get("pictureUrls") or raw.get("photos") or []
    photos: list[str] = []
    if isinstance(photo_urls_raw, list):
        for p in photo_urls_raw:
            if isinstance(p, str):
                photos.append(p)
            elif isinstance(p, dict) and "url" in p and isinstance(p["url"], str):
                photos.append(p["url"])

    description = raw.get("description") or raw.get("sellerNotes") or ""
    if not isinstance(description, str):
        description = ""

    try:
        return Listing(
            url=url,
            vin=vin,
            source="cargurus",
            year=int(year),
            make=make,
            model=model,
            trim=trim,
            transmission=transmission,
            mileage=int(mileage),
            price=int(price),
            title_status=title_status,
            photos=photos,
            description=description,
            dealer_name=dealer_name,
            seller_type=seller_type,
            city=city,
            state=state,
            accident_count=accident_count,
            owner_count=owner_count,
            cargurus_rating=cargurus_rating,
            tier=tier,
        )
    except ValidationError as exc:
        logger.warning("cargurus_listing_validation_failed", extra={"error": str(exc), "url": url})
        return None


class CarGurusScraper(AbstractSourceScraper):
    """Fetches CarGurus inventory listings for every target (make, model)."""

    name = "cargurus"

    def scrape(self) -> SourceResult:
        result = SourceResult(source_name=self.name)

        for make, model in sorted(ALL_TARGET_MAKES_MODELS):
            for page in range(1, self.max_pages + 1):
                url = _build_query_url(
                    zip_code=self.zip_code,
                    radius_mi=self.radius_mi,
                    make=make,
                    model=model,
                    budget_ceiling=self.budget_ceiling,
                    year_floor=self.year_floor,
                    page=page,
                )

                try:
                    html = self.client.fetch(url)
                    result.pages_fetched += 1
                except BrightDataFetchError as exc:
                    result.errors.append(f"{make} {model} p{page}: {exc}")
                    break  # Stop paginating this model if page 1 itself failed

                listings = self._parse_page(html, make, model)
                result.listings.extend(listings)

                # Stop paginating once a page comes back empty (natural end)
                if not listings:
                    break

        logger.info(
            "cargurus_scrape_complete",
            extra={
                "listings": len(result.listings),
                "pages": result.pages_fetched,
                "errors": len(result.errors),
            },
        )
        return result

    def _parse_page(self, html: str, expected_make: str, expected_model: str) -> list[Listing]:
        # Strategy 1: __NEXT_DATA__
        blob = _extract_next_data_json(html)

        # Strategy 2: window.__INITIAL_STATE__ / __PRELOADED_STATE__
        if blob is None:
            blob = _extract_initial_state_json(html)

        # Strategy 3: let BeautifulSoup find inline script blocks with a JSON-object
        # literal that look like listings. Cheap fallback; rarely needed.
        if blob is None:
            soup = BeautifulSoup(html, "html.parser")
            for script in soup.find_all("script"):
                text = script.string or ""
                if '"listingId"' in text or '"vdpUrl"' in text:
                    m = re.search(r"(\{.*\"listings?\".*\})", text, re.DOTALL)
                    if m:
                        try:
                            blob = json.loads(m.group(1))
                            break
                        except json.JSONDecodeError:
                            continue

        if blob is None:
            logger.warning(
                "cargurus_no_data_blob_found",
                extra={"html_length": len(html)},
            )
            return []

        raw_listings: list[dict[str, Any]] = []
        _walk_for_listings(blob, raw_listings)

        parsed: list[Listing] = []
        for raw in raw_listings:
            listing = _parse_listing_dict(raw, expected_make, expected_model)
            if listing is not None:
                parsed.append(listing)

        return parsed
