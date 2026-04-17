"""Unicorn matcher — strict criteria for SMS ping trigger.

All 5 must be true:
1. Primary tier vehicle (Crosstrek / Forester / Outback / Impreza)
2. CarGurus "Great Deal" OR market_delta_component >= 85 (~10% below median)
3. Mileage in the bottom 25th percentile for its (year, make, model)
4. Clean title CONFIRMED (title_status == "clean", not "unknown")
5. Either new to us this run OR had a >=5% price drop

Plus safety gates:
- unicorn_sms_fired_in_last_24h < UNICORN_SMS_DAILY_CAP (default 3)
- dedup_key not in state.unicorn_notified (never re-ping same vehicle)

Tuning: if unicorns fire more than ~2/week, tighten condition #2 to >=90.
If fewer than ~1/month, loosen to >=75. Start at 85.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import Listing, Score, WorkflowState
from .state import sms_count_last_24h

MARKET_DELTA_THRESHOLD = 85.0
MILEAGE_PERCENTILE_THRESHOLD = 25.0
PRICE_DROP_THRESHOLD = 0.05
PRICE_DROP_WINDOW_HOURS = 24


@dataclass(frozen=True)
class UnicornDecision:
    """Outcome of running the unicorn matcher against one listing."""

    is_unicorn: bool
    reasons: list[str]  # reasons the check passed or failed — for log visibility
    rate_limited: bool = False
    already_notified: bool = False


def _sms_daily_cap() -> int:
    try:
        return int(os.environ.get("UNICORN_SMS_DAILY_CAP", "3"))
    except ValueError:
        return 3


def _had_recent_price_drop(listing: Listing, now: datetime) -> tuple[bool, float]:
    """Check if the listing had a >= PRICE_DROP_THRESHOLD drop within the last 24h.

    Returns (had_drop, drop_pct). drop_pct is the most recent single-step change
    expressed as a positive fraction (0.05 = 5% drop).
    """
    if len(listing.price_history) < 2:
        return (False, 0.0)

    cutoff = now - timedelta(hours=PRICE_DROP_WINDOW_HOURS)
    # Find the most recent price change within the window
    history = sorted(listing.price_history, key=lambda o: o.timestamp)
    for i in range(len(history) - 1, 0, -1):
        cur = history[i]
        if cur.timestamp < cutoff:
            break
        prev = history[i - 1]
        if cur.price < prev.price and prev.price > 0:
            drop_pct = (prev.price - cur.price) / prev.price
            if drop_pct >= PRICE_DROP_THRESHOLD:
                return (True, drop_pct)
    return (False, 0.0)


def _is_new_listing_this_run(listing: Listing, now: datetime) -> bool:
    """A listing is 'new' if its first_seen is within the last 2 hours (one scout cycle)."""
    return listing.first_seen >= now - timedelta(hours=2)


def evaluate_unicorn(
    listing: Listing,
    score: Score,
    state: WorkflowState,
    *,
    now: datetime | None = None,
) -> UnicornDecision:
    """Return UnicornDecision — whether this listing triggers an SMS ping."""
    ts = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    # Gate 1: tier
    if listing.tier != "primary":
        reasons.append(f"tier={listing.tier} (needs primary)")
        return UnicornDecision(is_unicorn=False, reasons=reasons)

    # Gate 2: CarGurus Great OR market-delta component >= 85
    great_deal = listing.cargurus_rating == "Great"
    market_good = score.market_delta_component >= MARKET_DELTA_THRESHOLD
    if not (great_deal or market_good):
        reasons.append(
            f"not-a-deal: cargurus={listing.cargurus_rating}, "
            f"market_delta={score.market_delta_component:.0f}"
        )
        return UnicornDecision(is_unicorn=False, reasons=reasons)
    reasons.append(
        f"deal-ok: cargurus={listing.cargurus_rating or 'none'}, "
        f"market_delta={score.market_delta_component:.0f}"
    )

    # Gate 3: bottom-25th mileage percentile
    if score.mileage_percentile_for_model_year > MILEAGE_PERCENTILE_THRESHOLD:
        reasons.append(
            f"miles-too-high: percentile={score.mileage_percentile_for_model_year:.0f}"
        )
        return UnicornDecision(is_unicorn=False, reasons=reasons)
    reasons.append(
        f"miles-ok: percentile={score.mileage_percentile_for_model_year:.0f}"
    )

    # Gate 4: clean title CONFIRMED
    if listing.title_status != "clean":
        reasons.append(f"title={listing.title_status} (unicorn requires 'clean')")
        return UnicornDecision(is_unicorn=False, reasons=reasons)
    reasons.append("title-clean")

    # Gate 5: new to us OR price dropped >=5% in last 24h
    is_new = _is_new_listing_this_run(listing, ts)
    had_drop, drop_pct = _had_recent_price_drop(listing, ts)
    if not (is_new or had_drop):
        reasons.append("not-new-and-no-recent-drop")
        return UnicornDecision(is_unicorn=False, reasons=reasons)
    if is_new:
        reasons.append("new-this-run")
    if had_drop:
        reasons.append(f"price-drop={drop_pct * 100:.0f}%")

    # Safety gate A: rate-limit
    sms_count = sms_count_last_24h(state, now=ts)
    cap = _sms_daily_cap()
    if sms_count >= cap:
        reasons.append(f"rate-limited: {sms_count}/{cap} in 24h")
        return UnicornDecision(is_unicorn=False, reasons=reasons, rate_limited=True)

    # Safety gate B: already notified on this VIN/URL
    key = listing.dedup_key()
    if key in state.unicorn_notified:
        reasons.append("already-notified")
        return UnicornDecision(is_unicorn=False, reasons=reasons, already_notified=True)

    reasons.append("UNICORN")
    return UnicornDecision(is_unicorn=True, reasons=reasons)
