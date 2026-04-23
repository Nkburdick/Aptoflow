"""Market-median + mileage + CarGurus-rating scoring for car_scout listings.

Produces a `Score` 0-100 per listing with a `ScoreBand` label. The band is
what the digest groups by and what the unicorn matcher checks.

Math reference: .agent/plans/car_scout.md — Scoring Algorithm section.

Weights:
  A = CarGurus rating component  (weight 0.30)
  B = Market-median delta        (weight 0.40)
  C = Mileage percentile         (weight 0.20)
  D = Red-flag LLM scan          (weight 0.10)
  total = 0.30*A + 0.40*B + 0.20*C + 0.10*D

Band thresholds:
  unicorn  ≥ 95
  great    ≥ 85
  good     ≥ 70
  fair     ≥ 50
  pass     < 50
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal

from .models import Listing, PriceObservation, Score, ScoreBand, WorkflowState
from .state import comp_key

# Preference bonuses — applied after weighted components, capped at 100
CROSSTREK_BONUS = 3.0
PNW_ARB_BONUS = 2.0
PNW_STATES: frozenset[str] = frozenset({"WA", "OR", "ID"})

CARGURUS_COMPONENT_TABLE: dict[str | None, float] = {
    "Great": 100.0,
    "Good": 80.0,
    "Fair": 60.0,
    "High": 30.0,
    "Overpriced": 10.0,
    None: 50.0,  # neutral when CarGurus didn't supply a rating
}

MIN_COMPS_FOR_CONFIDENCE = 3
COMP_WINDOW_DAYS = 30

RedFlagSeverity = Literal[0, 1, 2, 3]


def _market_delta_component(delta_pct: float) -> float:
    """Map a price-vs-median delta percentage to a 0-100 component.

    delta_pct is (median - price) / median, so positive = listing is below market.
    """
    if delta_pct >= 0.20:
        return 100.0
    if delta_pct >= 0.10:
        return 85.0
    if delta_pct >= 0.05:
        return 65.0
    if delta_pct >= -0.05:
        return 50.0
    if delta_pct >= -0.15:
        return 25.0
    return 5.0


def _mileage_component(percentile_rank: float) -> float:
    """Map a 0-100 mileage percentile (lower miles = lower percentile) to a component.

    0 = lowest-mile listing we've seen for this (year, make, model). 100 = highest.
    """
    if percentile_rank <= 25:
        return 100.0
    if percentile_rank <= 50:
        return 75.0
    if percentile_rank <= 75:
        return 50.0
    if percentile_rank <= 90:
        return 25.0
    return 10.0


def _redflag_component(severity: RedFlagSeverity) -> float:
    """severity 0 (clean) -> 99, severity 3 (deal-breaker) -> 0, capped 0-100."""
    return max(0.0, min(100.0, (3 - severity) * 33.0))


def _band_for(total: float) -> ScoreBand:
    if total >= 95:
        return "unicorn"
    if total >= 85:
        return "great"
    if total >= 70:
        return "good"
    if total >= 50:
        return "fair"
    return "pass"


def _recent_prices(observations: Iterable[PriceObservation], now: datetime) -> list[int]:
    """Return prices from observations that are within COMP_WINDOW_DAYS of now."""
    cutoff = now - timedelta(days=COMP_WINDOW_DAYS)
    return [o.price for o in observations if o.timestamp >= cutoff]


def compute_market_median(
    state: WorkflowState,
    year: int,
    make: str,
    model: str,
    now: datetime | None = None,
) -> tuple[float | None, int]:
    """Return (median, n_comps) for a given bucket within the rolling 30-day window.

    Returns (None, n) if there aren't enough comps for a reliable median.
    """
    ts = now or datetime.now(timezone.utc)
    bucket = comp_key(year, make, model)
    obs = state.comps.get(bucket, [])
    recent = _recent_prices(obs, ts)

    if len(recent) < MIN_COMPS_FOR_CONFIDENCE:
        return (None, len(recent))
    return (statistics.median(recent), len(recent))


def compute_mileage_percentile(
    listing: Listing,
    state: WorkflowState,
) -> float:
    """Percentile rank of this listing's mileage among state listings in the same bucket.

    Returns 50.0 (neutral) if fewer than 3 comps exist.
    """
    bucket_listings = [
        l
        for l in state.listings.values()
        if l.year == listing.year and l.make == listing.make and l.model == listing.model
    ]
    if len(bucket_listings) < MIN_COMPS_FOR_CONFIDENCE:
        return 50.0

    mileages = sorted(l.mileage for l in bucket_listings)
    n = len(mileages)
    # Count how many have mileage strictly less than this listing
    lower = sum(1 for m in mileages if m < listing.mileage)
    return (lower / n) * 100.0


def score_listing(
    listing: Listing,
    state: WorkflowState,
    *,
    redflag_severity: RedFlagSeverity = 0,
    redflag_flags: list[str] | None = None,
    now: datetime | None = None,
) -> Score:
    """Compute a full Score for a listing against current state comps.

    `redflag_severity` is produced upstream by an LLM scan of the description.
    Pass 0 for tests when you don't care about red-flag contribution.
    """
    ts = now or datetime.now(timezone.utc)

    # Component A: CarGurus rating
    component_a = CARGURUS_COMPONENT_TABLE.get(listing.cargurus_rating, 50.0)

    # Component B: market delta
    median, n_comps = compute_market_median(state, listing.year, listing.make, listing.model, now=ts)
    low_comp_confidence = median is None
    if median is None:
        # Neutral when we lack comps; upstream may choose to wire in KBB reference
        component_b = 50.0
        delta_pct = 0.0
    else:
        delta_pct = (median - listing.price) / median
        component_b = _market_delta_component(delta_pct)

    # Component C: mileage percentile
    percentile = compute_mileage_percentile(listing, state)
    component_c = _mileage_component(percentile)

    # Component D: red-flag component (0-100)
    component_d = _redflag_component(redflag_severity)

    weighted = 0.30 * component_a + 0.40 * component_b + 0.20 * component_c + 0.10 * component_d

    crosstrek_bonus = CROSSTREK_BONUS if (listing.make == "Subaru" and listing.model == "Crosstrek") else 0.0

    state_code = (listing.state or "").strip().upper()
    pnw_arb_bonus = (
        PNW_ARB_BONUS
        if listing.make == "Subaru" and state_code and state_code not in PNW_STATES
        else 0.0
    )

    total = min(100.0, weighted + crosstrek_bonus + pnw_arb_bonus)
    band = _band_for(total)

    # Reasoning blurb — single-line deterministic summary; LLM-enhanced version
    # happens in the digest builder, not here (avoids unnecessary API calls on
    # listings we'll never show).
    flag_hint = f" • flags: {', '.join(redflag_flags)}" if redflag_flags else ""
    bonus_hint = ""
    if crosstrek_bonus:
        bonus_hint += f" • Crosstrek +{int(CROSSTREK_BONUS)}"
    if pnw_arb_bonus:
        bonus_hint += f" • out-of-PNW ({state_code}) +{int(PNW_ARB_BONUS)}"
    if median is None:
        reasoning = f"{band.title()} ({total:.0f}) — low comps, mileage percentile {percentile:.0f}{bonus_hint}{flag_hint}"
    else:
        reasoning = (
            f"{band.title()} ({total:.0f}) — {delta_pct * 100:+.0f}% vs "
            f"${int(median):,} median, mileage pct {percentile:.0f}{bonus_hint}{flag_hint}"
        )

    return Score(
        listing_url=listing.url,
        computed_at=ts,
        cargurus_component=component_a,
        market_delta_component=component_b,
        mileage_component=component_c,
        redflag_component=component_d,
        total=total,
        band=band,
        reasoning=reasoning,
        mileage_percentile_for_model_year=percentile,
        low_comp_confidence=low_comp_confidence,
    )
