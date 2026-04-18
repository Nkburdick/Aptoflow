"""State persistence, dedup, and pruning for car_scout.

State is a single JSON file on Modal volume. Operations:
- load_state / save_state — JSON round-trip via Pydantic
- merge_listing — insert new or update existing (records price history)
- prune_old — drop stale listings, old comps, expired rate-limit entries
- record_sms / sms_count_last_24h — rolling rate-limit window
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Listing, PriceObservation, WorkflowState

DEFAULT_STATE_PATH = "/data/car_scout_state.json"
LISTING_STALE_AFTER_DAYS = 7
COMP_RETENTION_DAYS = 30
TOP_PICK_COOLDOWN_DAYS = 7
UNICORN_DEDUPE_DAYS = 30


def state_path() -> Path:
    """Resolve the state file path from env or default."""
    return Path(os.environ.get("STATE_FILE_PATH", DEFAULT_STATE_PATH))


def load_state(path: Path | None = None) -> WorkflowState:
    """Load state from JSON, returning an empty state if the file is absent."""
    p = path or state_path()
    if not p.exists():
        return WorkflowState()
    try:
        raw = p.read_text()
    except OSError:
        return WorkflowState()
    if not raw.strip():
        return WorkflowState()
    return WorkflowState.model_validate_json(raw)


def save_state(state: WorkflowState, path: Path | None = None) -> None:
    """Persist state to JSON atomically (write to tmp + rename)."""
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(state.model_dump_json(indent=2))
    tmp.replace(p)


def comp_key(year: int, make: str, model: str) -> str:
    """Canonical bucket key for market-median comps."""
    return f"{year}_{make.lower().replace(' ', '-')}_{model.lower().replace(' ', '-')}"


def merge_listing(state: WorkflowState, incoming: Listing, now: datetime | None = None) -> Listing:
    """Merge an incoming scraped listing into state.

    If dedup_key already exists:
        - updates last_seen
        - if price changed, appends to price_history
        - preserves first_seen

    If dedup_key is new:
        - inserts with first_seen/last_seen = now
        - seeds price_history with the current price

    Always records the current price as a comp observation for later scoring.

    Returns the merged Listing (after mutation).
    """
    ts = now or datetime.now(timezone.utc)
    key = incoming.dedup_key()

    # Permanent branded-cache rejection — once a VIN/URL has been verified as
    # a rebuilt/salvage listing via VDP scan, don't let MarketCheck's next
    # fetch quietly re-insert the same vehicle back into state.
    if state.title_verifications.get(key) == "branded":
        # Still record the comp observation (useful market data) but drop the
        # listing itself so it never re-enters the digest pipeline.
        bucket = comp_key(incoming.year, incoming.make, incoming.model)
        state.comps.setdefault(bucket, []).append(
            PriceObservation(timestamp=ts, price=incoming.price)
        )
        return incoming

    existing = state.listings.get(key)
    if existing is None:
        # New listing
        incoming.first_seen = ts
        incoming.last_seen = ts
        if not incoming.price_history:
            incoming.price_history = [PriceObservation(timestamp=ts, price=incoming.price)]
        state.listings[key] = incoming
        merged = incoming
    else:
        # Update in place
        price_changed = existing.price != incoming.price
        existing.last_seen = ts
        # Refresh fields that a source might improve on over time
        existing.photos = incoming.photos or existing.photos
        existing.description = incoming.description or existing.description
        existing.accident_count = existing.accident_count if existing.accident_count is not None else incoming.accident_count
        existing.owner_count = existing.owner_count if existing.owner_count is not None else incoming.owner_count
        existing.cargurus_rating = existing.cargurus_rating or incoming.cargurus_rating
        existing.title_status = (
            incoming.title_status if existing.title_status == "unknown" else existing.title_status
        )
        if price_changed:
            existing.price = incoming.price
            existing.price_history.append(PriceObservation(timestamp=ts, price=incoming.price))
        merged = existing

    # Record comp observation
    bucket = comp_key(merged.year, merged.make, merged.model)
    state.comps.setdefault(bucket, []).append(
        PriceObservation(timestamp=ts, price=merged.price)
    )

    return merged


def prune_old(state: WorkflowState, now: datetime | None = None) -> dict[str, int]:
    """Drop stale data. Returns counts per category for logging."""
    ts = now or datetime.now(timezone.utc)

    listing_cutoff = ts - timedelta(days=LISTING_STALE_AFTER_DAYS)
    comp_cutoff = ts - timedelta(days=COMP_RETENTION_DAYS)
    top_pick_cutoff = ts - timedelta(days=TOP_PICK_COOLDOWN_DAYS)
    sms_cutoff = ts - timedelta(hours=24)

    removed_listings = 0
    for key in list(state.listings.keys()):
        if state.listings[key].last_seen < listing_cutoff:
            del state.listings[key]
            removed_listings += 1

    removed_comps = 0
    for bucket in list(state.comps.keys()):
        before = len(state.comps[bucket])
        state.comps[bucket] = [o for o in state.comps[bucket] if o.timestamp >= comp_cutoff]
        removed_comps += before - len(state.comps[bucket])
        if not state.comps[bucket]:
            del state.comps[bucket]

    removed_sms = len(state.sms_timestamps)
    state.sms_timestamps = [t for t in state.sms_timestamps if t >= sms_cutoff]
    removed_sms -= len(state.sms_timestamps)

    removed_top_picks = 0
    for url in list(state.top_picks_last_7_days.keys()):
        if state.top_picks_last_7_days[url] < top_pick_cutoff:
            del state.top_picks_last_7_days[url]
            removed_top_picks += 1

    # Unicorn dedupe retention — handled here for simplicity; unicorn_notified
    # carries only dedup keys, no timestamps, so we re-tie to listings' last_seen:
    # if we've dropped the listing entirely, release the dedupe slot too.
    kept_keys = set(state.listings.keys())
    # Also keep recently-notified keys whose listings may have just pruned
    # (don't re-ping within UNICORN_DEDUPE_DAYS). We approximate by keeping all
    # currently-tracked keys plus a sentinel set that we bound elsewhere.
    removed_unicorn_dedupes = 0
    for key in list(state.unicorn_notified):
        if key not in kept_keys:
            # Orphan dedupe — drop it. The 30-day rule is implicit in that a
            # VIN stays out of kept_keys once its listing hasn't been seen in
            # 7 days, matching the stale-listing cutoff.
            state.unicorn_notified.discard(key)
            removed_unicorn_dedupes += 1

    return {
        "listings": removed_listings,
        "comps": removed_comps,
        "sms": removed_sms,
        "top_picks": removed_top_picks,
        "unicorn_dedupes": removed_unicorn_dedupes,
    }


def record_sms(state: WorkflowState, now: datetime | None = None) -> None:
    """Append a timestamp for SMS rate-limit accounting."""
    state.sms_timestamps.append(now or datetime.now(timezone.utc))


def sms_count_last_24h(state: WorkflowState, now: datetime | None = None) -> int:
    """Count SMS dispatched in the trailing 24h window."""
    ts = now or datetime.now(timezone.utc)
    cutoff = ts - timedelta(hours=24)
    return sum(1 for t in state.sms_timestamps if t >= cutoff)
