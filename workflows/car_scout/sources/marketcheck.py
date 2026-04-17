"""MarketCheck adapter — converts MCListing objects to canonical Listing.

Used by the daily digest path. Different from the Bright Data scrape path
(which powers the every-2h unicorn scout).
"""

from __future__ import annotations

import time

from lib.logger import get_logger
from lib.marketcheck import MarketCheckClient, MCListing

from ..models import Listing, PriceObservation, Transmission, TitleStatus
from .base import ALL_TARGET_MAKES_MODELS, SourceResult, tier_for

logger = get_logger("car-scout.mc-source")


def _transmission_from(heading: str) -> Transmission:
    h = heading.lower()
    if "manual" in h:
        return "manual"
    if any(k in h for k in ("automatic", "auto", "cvt")):
        return "auto"
    return "unknown"


def _title_status_from(mc: MCListing) -> TitleStatus:
    if mc.carfax_clean_title is True:
        return "clean"
    # MarketCheck doesn't expose salvage/rebuilt flags directly, so anything
    # not-explicitly-clean stays "unknown" and the digest badges it as such
    return "unknown"


def _to_canonical(mc: MCListing) -> Listing | None:
    tier = tier_for(mc.make, mc.model)
    if tier is None:
        return None

    transmission = _transmission_from(mc.heading)
    title_status = _title_status_from(mc)

    # Seed price_history from MarketCheck's ref_price when there's been a change
    price_history: list[PriceObservation] = []
    if mc.ref_price and mc.last_seen_at and mc.ref_price != mc.price:
        price_history.append(
            PriceObservation(timestamp=mc.last_seen_at, price=mc.ref_price)
        )
        price_history.append(
            PriceObservation(timestamp=mc.last_seen_at, price=mc.price)
        )

    # owner_count via carfax_1_owner
    owner_count: int | None = None
    if mc.carfax_1_owner is True:
        owner_count = 1

    first_seen = mc.first_seen_at_source or mc.last_seen_at
    last_seen = mc.last_seen_at or first_seen

    # Graceful: MarketCheck usually has a valid URL, but if it's malformed Pydantic
    # will refuse — drop the listing then
    try:
        return Listing(
            url=mc.vdp_url,
            vin=mc.vin,
            source="marketcheck",
            year=mc.year,
            make=mc.make,
            model=mc.model,
            trim=mc.trim,
            transmission=transmission,
            mileage=mc.miles,
            price=mc.price,
            price_history=price_history,
            title_status=title_status,
            photos=[p for p in mc.photo_links[:5]],  # cap at 5 — email size
            dealer_name=mc.dealer_name,
            seller_type="dealer" if mc.seller_type == "dealer" else "unknown",
            city=mc.city,
            state=mc.state,
            owner_count=owner_count,
            tier=tier,
            first_seen=first_seen or __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
            last_seen=last_seen or __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("mc_listing_validation_failed", extra={"error": str(exc), "url": mc.vdp_url})
        return None


def fetch_all_targets(
    client: MarketCheckClient,
    *,
    zip_code: str,
    radius_mi: int,
    year_floor: int,
    budget_ceiling: int,
    rows_per_bucket: int = 50,
) -> SourceResult:
    """Fetch MarketCheck listings for every target (make, model) tier.

    Returns a SourceResult with canonical Listing objects ready for state merge.
    """
    result = SourceResult(source_name="marketcheck")

    # MarketCheck free tier caps at 5 req/sec. We pace at 250ms between buckets
    # (4 req/sec) with a small safety margin. Over 7 buckets: ~1.75s total —
    # negligible compared to network latency.
    for i, (make, model) in enumerate(sorted(ALL_TARGET_MAKES_MODELS)):
        if i > 0:
            time.sleep(0.25)
        try:
            mc_items = client.search_active(
                make=make,
                model=model,
                zip=zip_code,
                radius=radius_mi,
                year_min=year_floor,
                price_max=budget_ceiling,
                max_rows=rows_per_bucket,
            )
            result.pages_fetched += 1
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{make} {model}: {exc}")
            continue

        for mc in mc_items:
            listing = _to_canonical(mc)
            if listing is not None:
                result.listings.append(listing)

    logger.info(
        "marketcheck_fetch_complete",
        extra={
            "listings": len(result.listings),
            "buckets": result.pages_fetched,
            "errors": len(result.errors),
        },
    )
    return result
