"""MarketCheck API client — aggregates CarGurus, Autotrader, Cars.com, dealer feeds.

Lightweight reusable primitive for any AptoFlow workflow that needs structured
used-car listing data. The free tier supports 500 calls/month, sufficient for
twice-daily digest pulls across 3-4 (make, model) buckets.

Docs: https://apidocs.marketcheck.com/

Usage:

    from lib.marketcheck import MarketCheckClient

    client = MarketCheckClient()  # reads MARKETCHECK_API_KEY from env
    for listing in client.search_active(
        make="subaru",
        model="crosstrek",
        zip="98225",
        radius=100,
        year_min=2015,
        price_max=22000,
    ):
        print(listing.vin, listing.price)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from lib.logger import get_logger

logger = get_logger("lib.marketcheck")

DEFAULT_BASE_URL = "https://mc-api.marketcheck.com/v2"
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_ROWS = 50  # per (make, model) query — free-tier-friendly


class MarketCheckConfigError(Exception):
    """Raised when required API config is missing."""


class MarketCheckFetchError(Exception):
    """Raised when an API call fails."""


_YEAR_RE = re.compile(r"^\s*(\d{4})\s+")


@dataclass(frozen=True)
class MCListing:
    """A normalized subset of MarketCheck's listing payload.

    Kept in MarketCheck-space (not converted to Listing yet) so callers can
    inspect raw MC fields before mapping to their own schema. Pairs with
    `to_canonical_listing` in workflows/car_scout/sources/marketcheck.py.
    """

    # Identity
    id: str
    vin: str | None
    vdp_url: str
    source_aggregator: str  # "cargurus", "autotrader.com", dealer domain, etc.

    # Core vehicle
    year: int
    make: str
    model: str
    trim: str | None
    heading: str
    exterior_color: str | None
    base_ext_color: str | None

    # Listing metadata
    price: int
    miles: int
    msrp: int | None
    ref_price: int | None
    price_change_percent: float | None
    dom: int | None   # days-on-market (MarketCheck-observed)
    dom_active: int | None

    # History flags
    carfax_1_owner: bool | None
    carfax_clean_title: bool | None

    # Media
    photo_links: list[str]

    # Seller
    seller_type: str  # "dealer" | "private" | etc.
    dealer_name: str | None
    city: str | None
    state: str | None

    # Bookkeeping
    first_seen_at_source: datetime | None
    last_seen_at: datetime | None


def _parse_ts_unix(raw: Any) -> datetime | None:
    if not isinstance(raw, (int, float)) or raw <= 0:
        return None
    try:
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    except (ValueError, OSError):
        return None


def _parse_heading(heading: str) -> tuple[int | None, str | None]:
    """MarketCheck's `heading` field looks like '2023 Subaru Crosstrek Sport SUV'.

    Returns (year, trim_guess). Trim is best-effort — words after model_name.
    """
    m = _YEAR_RE.match(heading)
    year = int(m.group(1)) if m else None
    return year, None  # trim resolved from dedicated field


def _normalize_make(raw: str | None) -> str | None:
    if not raw:
        return None
    # Canonicalize: "subaru" -> "Subaru", "cr-v" handled via model mapping
    return raw.strip().title() if len(raw) > 2 else raw.upper()


def _normalize_model(raw: str | None) -> str | None:
    if not raw:
        return None
    # Special cases: MarketCheck returns "crosstrek" lowercase; we want "Crosstrek"
    special = {"cr-v": "CR-V", "cx-5": "CX-5", "rav4": "RAV4"}
    low = raw.lower().strip()
    return special.get(low, raw.strip().title())


class MarketCheckClient:
    """Paginated client for MarketCheck's /v2/search/car/active endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.api_key = (api_key or os.environ.get("MARKETCHECK_API_KEY", "")).strip()
        if not self.api_key:
            raise MarketCheckConfigError(
                "MARKETCHECK_API_KEY env var is required"
            )
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_s)

    def __enter__(self) -> MarketCheckClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def search_active(
        self,
        *,
        make: str,
        model: str,
        zip: str,
        radius: int,
        year_min: int | None = None,
        price_max: int | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> list[MCListing]:
        """Fetch active listings matching the filters. Returns up to max_rows listings.

        Free tier is capped at 500 calls/month so we keep this to a single API
        call (no auto-pagination). Set max_rows to the amount you need per
        (make, model) bucket; 50 is the per-page default and typically covers
        the top matches for a local search.
        """
        params = {
            "api_key": self.api_key,
            "make": make.lower(),
            "model": model.lower(),
            "zip": zip,
            "radius": str(radius),
            "car_type": "used",
            "rows": str(max_rows),
            "sort_by": "price",
            "sort_order": "asc",
        }
        if year_min is not None:
            params["year_min"] = str(year_min)
        if price_max is not None:
            params["price_max"] = str(price_max)

        url = f"{self.base_url}/search/car/active"
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise MarketCheckFetchError(f"Network error: {exc}") from exc

        if resp.status_code != 200:
            raise MarketCheckFetchError(
                f"MarketCheck returned {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        raw_listings = data.get("listings") or []
        total = data.get("num_found", 0)

        logger.info(
            "marketcheck_search_complete",
            extra={
                "make": make,
                "model": model,
                "total_in_bucket": total,
                "returned": len(raw_listings),
            },
        )

        results: list[MCListing] = []
        for raw in raw_listings:
            parsed = self._raw_to_mclisting(raw)
            if parsed is not None:
                results.append(parsed)
        return results

    def _raw_to_mclisting(self, raw: dict[str, Any]) -> MCListing | None:
        """Convert one MarketCheck listing dict to an MCListing."""
        vin = raw.get("vin")
        vdp_url = raw.get("vdp_url")
        heading = raw.get("heading", "")

        if not vdp_url:
            return None

        year, _ = _parse_heading(heading)
        # MarketCheck's top-level `build` object often has year/make/model/trim;
        # prefer that when present, fall back to heading-parse.
        build = raw.get("build") or {}
        year = build.get("year") or year
        make = _normalize_make(build.get("make") or _extract_word(heading, 1))
        model = _normalize_model(build.get("model") or _extract_word(heading, 2))
        trim = build.get("trim") or _extract_word(heading, 3, upto=5)

        if not all([year, make, model]):
            return None

        price = raw.get("price")
        miles = raw.get("miles")
        if not isinstance(price, (int, float)) or not isinstance(miles, (int, float)):
            return None

        media = raw.get("media") or {}
        photo_links = media.get("photo_links") or []
        if not isinstance(photo_links, list):
            photo_links = []
        photo_links = [p for p in photo_links if isinstance(p, str)]

        dealer = raw.get("dealer") or {}
        dealer_name = dealer.get("name") if isinstance(dealer, dict) else None
        city = dealer.get("city") if isinstance(dealer, dict) else None
        state = dealer.get("state") if isinstance(dealer, dict) else None

        return MCListing(
            id=str(raw.get("id", "")),
            vin=vin if isinstance(vin, str) else None,
            vdp_url=vdp_url,
            source_aggregator=raw.get("source") or raw.get("data_source", "mc"),
            year=int(year),
            make=make,
            model=model,
            trim=trim if trim else None,
            heading=heading,
            exterior_color=raw.get("exterior_color") if isinstance(raw.get("exterior_color"), str) else None,
            base_ext_color=raw.get("base_ext_color") if isinstance(raw.get("base_ext_color"), str) else None,
            price=int(price),
            miles=int(miles),
            msrp=_as_int(raw.get("msrp")),
            ref_price=_as_int(raw.get("ref_price")),
            price_change_percent=_as_float(raw.get("price_change_percent")),
            dom=_as_int(raw.get("dom")),
            dom_active=_as_int(raw.get("dom_active")),
            carfax_1_owner=raw.get("carfax_1_owner") if isinstance(raw.get("carfax_1_owner"), bool) else None,
            carfax_clean_title=raw.get("carfax_clean_title") if isinstance(raw.get("carfax_clean_title"), bool) else None,
            photo_links=photo_links,
            seller_type=raw.get("seller_type", "dealer"),
            dealer_name=dealer_name,
            city=city,
            state=state,
            first_seen_at_source=_parse_ts_unix(raw.get("first_seen_at_source")),
            last_seen_at=_parse_ts_unix(raw.get("last_seen_at")),
        )


def _as_int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


def _as_float(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _extract_word(heading: str, index: int, *, upto: int | None = None) -> str | None:
    """Return the 0-indexed word at `index` (or words index..upto-1 joined) from heading."""
    parts = heading.split() if heading else []
    if index >= len(parts):
        return None
    if upto is None:
        return parts[index]
    slice_ = parts[index:upto]
    return " ".join(slice_) if slice_ else None
