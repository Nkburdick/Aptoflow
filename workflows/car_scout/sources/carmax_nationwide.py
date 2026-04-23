"""CarMax nationwide adapter — surfaces out-of-PNW Subarus with shipping estimate.

CarMax ships any vehicle to any store, so the standard ``SCOUT_RADIUS_MI``
restriction actively costs us inventory on them. This source queries
MarketCheck with ``seller_name="CarMax"`` at nationwide scope for each of
the four primary Subaru models.

MarketCheck free tier caveat: 4 extra calls/day pushes total to ~540/mo
against the 500 cap. We run this fetch at the AM cycle only — PM cycle
relies on the same state. 8% over-cap exposure, accepted for visibility.

Shipping fees use CarMax's published transfer-fee tiers based on distance
from Bellingham (98225). No external geocoding — state-level heuristic
suffices since the tiers are coarse ($0 / $199 / $299 / $499).
"""

from __future__ import annotations

import time

from lib.logger import get_logger
from lib.marketcheck import MarketCheckClient

from ..models import Listing, Source
from .base import SourceResult, tier_for
from .marketcheck import _to_canonical

logger = get_logger("car-scout.carmax")

CARMAX_SELLER_NAME = "CarMax"
NATIONWIDE_RADIUS_MI = 5000  # effectively US-wide
CARMAX_CUSTOMER_ZIP = "98225"

# Primary Subaru models only — CarMax volume on Imprezas/Crosstreks is the
# usable slice. Secondary makes aren't in scope for the nationwide section.
PRIMARY_SUBARU_MODELS: tuple[str, ...] = (
    "Crosstrek",
    "Forester",
    "Outback",
    "Impreza",
)

# CarMax transfer-fee tiers based on straight-line distance. Source:
# CarMax's shipping policy (fees are flat-rate within a band, not per-mile).
SHIPPING_TIERS: tuple[tuple[int, int], ...] = (
    (60, 0),       # same metro — no transfer fee
    (250, 199),    # regional
    (1500, 299),   # cross-region
    (99999, 499),  # cross-country
)

# State-centroid-to-Bellingham straight-line miles. Centroids are approximate
# — accurate to within ~200mi, fine for a tiered-rate estimator. Populated
# only for the lower-48 + HI/AK; missing states fall back to None.
_STATE_DISTANCE_MI: dict[str, int] = {
    "WA": 0,       "OR": 180,     "ID": 380,     "CA": 700,     "NV": 780,
    "MT": 450,     "UT": 710,     "AZ": 1200,    "WY": 720,     "NM": 1150,
    "CO": 980,     "ND": 1100,    "SD": 1200,    "NE": 1400,    "KS": 1550,
    "OK": 1700,    "TX": 1900,    "MN": 1450,    "IA": 1600,    "MO": 1700,
    "AR": 1850,    "LA": 2100,    "WI": 1600,    "IL": 1750,    "MS": 2100,
    "MI": 1900,    "IN": 1900,    "KY": 2000,    "TN": 2100,    "AL": 2300,
    "OH": 2000,    "WV": 2200,    "VA": 2400,    "NC": 2500,    "SC": 2500,
    "GA": 2400,    "FL": 2700,    "PA": 2400,    "NY": 2500,    "VT": 2600,
    "NH": 2600,    "MA": 2600,    "RI": 2650,    "CT": 2600,    "NJ": 2500,
    "DE": 2500,    "MD": 2400,    "DC": 2400,    "ME": 2700,
    "HI": 2700,    "AK": 2200,
}


def estimate_shipping_fee(listing_state: str | None) -> int | None:
    """Return the CarMax transfer fee (USD) for a listing's state, or None if unknown.

    Tiered per CarMax policy:
      - 0-60mi  → $0   (same metro)
      - 60-250  → $199 (regional)
      - 250-1500 → $299 (cross-region)
      - 1500+   → $499 (cross-country)
    """
    if not listing_state:
        return None
    code = listing_state.strip().upper()
    miles = _STATE_DISTANCE_MI.get(code)
    if miles is None:
        return None
    for upper_bound, fee in SHIPPING_TIERS:
        if miles <= upper_bound:
            return fee
    return SHIPPING_TIERS[-1][1]


def _to_carmax_listing(mc_listing) -> Listing | None:
    """Wrap the MarketCheck→Listing conversion, retagging source and adding fee."""
    listing = _to_canonical(mc_listing)
    if listing is None:
        return None
    shipping = estimate_shipping_fee(listing.state)
    return listing.model_copy(
        update={
            "source": "carmax",
            "shipping_fee_estimate": shipping,
        }
    )


def fetch_carmax_nationwide_subarus(
    client: MarketCheckClient,
    *,
    year_floor: int,
    budget_ceiling: int,
    rows_per_bucket: int = 25,
) -> SourceResult:
    """Fetch CarMax-only, nationwide primary-Subaru inventory.

    Returns a SourceResult with canonical Listings tagged ``source="carmax"``
    and ``shipping_fee_estimate`` populated from the state-tier table.
    """
    result = SourceResult(source_name="carmax")

    # Pace between buckets to respect MarketCheck's 5 req/sec ceiling.
    for i, model in enumerate(PRIMARY_SUBARU_MODELS):
        if i > 0:
            time.sleep(0.25)

        if tier_for("Subaru", model) is None:
            # Shouldn't happen (primary Subarus are in the target set), but
            # belt-and-suspenders — a CarMax listing that can't be tiered would
            # be dropped anyway downstream.
            continue

        try:
            mc_items = client.search_active(
                make="Subaru",
                model=model,
                zip=CARMAX_CUSTOMER_ZIP,
                radius=NATIONWIDE_RADIUS_MI,
                year_min=year_floor,
                price_max=budget_ceiling,
                seller_name=CARMAX_SELLER_NAME,
                max_rows=rows_per_bucket,
            )
            result.pages_fetched += 1
        except Exception as exc:  # noqa: BLE001 — one bucket's failure shouldn't kill the fetch
            result.errors.append(f"carmax {model}: {exc}")
            logger.warning(
                "carmax_fetch_bucket_failed",
                extra={"model": model, "error": str(exc)},
            )
            continue

        for mc in mc_items:
            listing = _to_carmax_listing(mc)
            if listing is not None:
                result.listings.append(listing)

    logger.info(
        "carmax_fetch_complete",
        extra={
            "listings": len(result.listings),
            "buckets": result.pages_fetched,
            "errors": len(result.errors),
        },
    )
    return result
