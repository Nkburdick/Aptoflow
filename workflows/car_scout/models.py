"""Pydantic models for car_scout — Listing, Score, WorkflowState.

Spec: .agent/plans/car_scout.md
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

Transmission = Literal["auto", "manual", "unknown"]
TitleStatus = Literal["clean", "salvage", "rebuilt", "unknown"]
SellerType = Literal["dealer", "private", "unknown"]
Source = Literal["marketcheck", "cargurus", "autotrader", "cars_com", "dealer_direct"]
Tier = Literal["primary", "secondary"]
ScoreBand = Literal["unicorn", "great", "good", "fair", "pass"]
CarGurusRating = Literal["Great", "Good", "Fair", "High", "Overpriced"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PriceObservation(BaseModel):
    """One snapshot of a listing's price at a point in time."""

    timestamp: datetime
    price: int  # USD


class Listing(BaseModel):
    """Canonical shape of a single vehicle listing, normalized across sources."""

    # Identity
    url: HttpUrl
    vin: str | None = None
    source: Source

    # Core vehicle
    year: int
    make: str
    model: str
    trim: str | None = None
    transmission: Transmission = "unknown"
    mileage: int
    exterior_color: str | None = None

    # Listing metadata
    price: int
    price_history: list[PriceObservation] = Field(default_factory=list)
    title_status: TitleStatus = "unknown"
    photos: list[HttpUrl] = Field(default_factory=list)
    description: str = ""

    # Seller
    dealer_name: str | None = None
    seller_type: SellerType = "dealer"
    city: str | None = None
    state: str | None = None
    distance_mi: int | None = None

    # History badges
    accident_count: int | None = None
    owner_count: int | None = None
    use_type: str | None = None

    # CarGurus-supplied deal rating
    cargurus_rating: CarGurusRating | None = None

    # Bookkeeping
    first_seen: datetime = Field(default_factory=_utc_now)
    last_seen: datetime = Field(default_factory=_utc_now)
    tier: Tier

    def dedup_key(self) -> str:
        """Prefer VIN when present; fall back to URL string."""
        return self.vin if self.vin else str(self.url)


class Score(BaseModel):
    """Computed score for a listing against current market comps."""

    listing_url: HttpUrl
    computed_at: datetime = Field(default_factory=_utc_now)

    # Components (0-100 each)
    cargurus_component: float
    market_delta_component: float
    mileage_component: float
    redflag_component: float  # 0 (deal-breaker flags) to 100 (clean description)

    total: float
    band: ScoreBand
    reasoning: str = ""

    # Unicorn gate inputs
    is_new_listing: bool = False
    had_recent_price_drop: bool = False
    mileage_percentile_for_model_year: float = 50.0
    passes_unicorn: bool = False

    # Diagnostic flags
    low_comp_confidence: bool = False


class WorkflowState(BaseModel):
    """Serializable state persisted to Modal volume as JSON."""

    # Listings keyed by dedup_key (VIN when present, URL otherwise)
    listings: dict[str, Listing] = Field(default_factory=dict)

    # Bucket comps for market-median computation, rolling 30-day window
    # Key format: "year_make_model" (lower-case, underscore-joined)
    comps: dict[str, list[PriceObservation]] = Field(default_factory=dict)

    # Unicorn SMS dedupe — VINs (or URL hashes when no VIN) already pinged
    unicorn_notified: set[str] = Field(default_factory=set)

    # SMS rate-limiting window (last 24h)
    sms_timestamps: list[datetime] = Field(default_factory=list)

    # Digest dedupe — URLs featured in Top Picks in the last 7 days
    top_picks_last_7_days: dict[str, datetime] = Field(default_factory=dict)

    # Per-listing VDP title verification cache (dedup_key -> "clean"|"branded"|"unknown").
    # Once verified, a listing never re-fetches. Cleared only when a listing is
    # pruned from state via prune_old.
    title_verifications: dict[str, str] = Field(default_factory=dict)

    # Bookkeeping
    last_scout_run: datetime | None = None
    last_digest_sent: datetime | None = None
    runs_total: int = 0
