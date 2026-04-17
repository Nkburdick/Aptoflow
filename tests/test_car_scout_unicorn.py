"""Unit tests for workflows.car_scout.unicorn."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from workflows.car_scout.models import Listing, PriceObservation, Score, WorkflowState
from workflows.car_scout.unicorn import (
    MARKET_DELTA_THRESHOLD,
    MILEAGE_PERCENTILE_THRESHOLD,
    PRICE_DROP_THRESHOLD,
    _had_recent_price_drop,
    _is_new_listing_this_run,
    evaluate_unicorn,
)


NOW = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)


def _listing(
    *,
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
    tier: str = "primary",
    title_status: str = "clean",
    cargurus_rating: str | None = "Great",
    price_history: list[PriceObservation] | None = None,
    **overrides,
) -> Listing:
    base = dict(
        url="https://www.cargurus.com/Cars/link/a",
        vin="VIN-A",
        source="cargurus",
        year=2020,
        make="Subaru",
        model="Crosstrek",
        trim="Premium",
        transmission="auto",
        mileage=42000,
        price=19900,
        title_status=title_status,
        tier=tier,
        cargurus_rating=cargurus_rating,
        first_seen=first_seen or (NOW - timedelta(minutes=30)),
        last_seen=last_seen or NOW,
        price_history=price_history or [PriceObservation(timestamp=NOW, price=19900)],
    )
    base.update(overrides)
    return Listing(**base)


def _score(
    *,
    market_delta_component: float = 90.0,
    mileage_percentile: float = 15.0,
    total: float = 95.0,
    band: str = "unicorn",
    **overrides,
) -> Score:
    base = dict(
        listing_url="https://www.cargurus.com/Cars/link/a",
        computed_at=NOW,
        cargurus_component=100.0,
        market_delta_component=market_delta_component,
        mileage_component=100.0,
        redflag_component=99.0,
        total=total,
        band=band,
        mileage_percentile_for_model_year=mileage_percentile,
    )
    base.update(overrides)
    return Score(**base)


class TestHasRecentPriceDrop:
    def test_fresh_drop_caught(self):
        history = [
            PriceObservation(timestamp=NOW - timedelta(days=2), price=21500),
            PriceObservation(timestamp=NOW - timedelta(hours=1), price=19900),
        ]
        listing = _listing(price_history=history)
        had_drop, pct = _had_recent_price_drop(listing, NOW)
        assert had_drop is True
        assert pct > PRICE_DROP_THRESHOLD

    def test_small_drop_ignored(self):
        history = [
            PriceObservation(timestamp=NOW - timedelta(days=2), price=19950),
            PriceObservation(timestamp=NOW - timedelta(hours=1), price=19900),
        ]
        listing = _listing(price_history=history)
        had_drop, _ = _had_recent_price_drop(listing, NOW)
        assert had_drop is False

    def test_old_drop_outside_window(self):
        history = [
            PriceObservation(timestamp=NOW - timedelta(days=5), price=22000),
            PriceObservation(timestamp=NOW - timedelta(days=4), price=19900),
        ]
        listing = _listing(price_history=history)
        had_drop, _ = _had_recent_price_drop(listing, NOW)
        assert had_drop is False

    def test_only_one_observation_no_drop(self):
        listing = _listing(price_history=[PriceObservation(timestamp=NOW, price=19900)])
        had_drop, _ = _had_recent_price_drop(listing, NOW)
        assert had_drop is False


class TestIsNewListingThisRun:
    def test_recent_first_seen_is_new(self):
        listing = _listing(first_seen=NOW - timedelta(minutes=30))
        assert _is_new_listing_this_run(listing, NOW) is True

    def test_old_first_seen_not_new(self):
        listing = _listing(first_seen=NOW - timedelta(hours=5))
        assert _is_new_listing_this_run(listing, NOW) is False


class TestEvaluateUnicorn:
    def test_unicorn_happy_path(self):
        listing = _listing()
        score = _score()
        state = WorkflowState()
        decision = evaluate_unicorn(listing, score, state, now=NOW)
        assert decision.is_unicorn is True
        assert "UNICORN" in decision.reasons

    def test_secondary_tier_never_unicorn(self):
        listing = _listing(tier="secondary", make="Toyota", model="RAV4")
        score = _score()
        state = WorkflowState()
        decision = evaluate_unicorn(listing, score, state, now=NOW)
        assert decision.is_unicorn is False
        assert any("tier=" in r for r in decision.reasons)

    def test_not_a_deal_if_cargurus_mediocre_and_delta_below_85(self):
        listing = _listing(cargurus_rating="Fair")
        score = _score(market_delta_component=80.0)  # just below threshold
        state = WorkflowState()
        decision = evaluate_unicorn(listing, score, state, now=NOW)
        assert decision.is_unicorn is False
        assert any("not-a-deal" in r for r in decision.reasons)

    def test_deal_passes_via_cargurus_great_alone(self):
        # Mediocre market delta but CarGurus flagged as Great — still passes gate 2
        listing = _listing(cargurus_rating="Great")
        score = _score(market_delta_component=60.0)
        state = WorkflowState()
        decision = evaluate_unicorn(listing, score, state, now=NOW)
        # With miles-percentile 15 + new listing + clean title + no ratelimit, should be unicorn
        assert decision.is_unicorn is True

    def test_high_miles_rejected(self):
        listing = _listing()
        score = _score(mileage_percentile=50.0)  # above 25 threshold
        state = WorkflowState()
        decision = evaluate_unicorn(listing, score, state, now=NOW)
        assert decision.is_unicorn is False
        assert any("miles-too-high" in r for r in decision.reasons)

    def test_unknown_title_rejected_for_unicorn(self):
        # Unicorn requires CONFIRMED clean — unknown not enough
        listing = _listing(title_status="unknown")
        score = _score()
        state = WorkflowState()
        decision = evaluate_unicorn(listing, score, state, now=NOW)
        assert decision.is_unicorn is False
        assert any("title=" in r for r in decision.reasons)

    def test_salvage_rejected(self):
        listing = _listing(title_status="salvage")
        score = _score()
        state = WorkflowState()
        decision = evaluate_unicorn(listing, score, state, now=NOW)
        assert decision.is_unicorn is False

    def test_old_listing_without_drop_rejected(self):
        listing = _listing(first_seen=NOW - timedelta(hours=20))  # not new
        score = _score()
        state = WorkflowState()
        decision = evaluate_unicorn(listing, score, state, now=NOW)
        assert decision.is_unicorn is False
        assert any("not-new-and-no-recent-drop" in r for r in decision.reasons)

    def test_old_listing_with_price_drop_passes(self):
        listing = _listing(
            first_seen=NOW - timedelta(days=3),
            price_history=[
                PriceObservation(timestamp=NOW - timedelta(days=2), price=22000),
                PriceObservation(timestamp=NOW - timedelta(hours=2), price=19900),
            ],
        )
        score = _score()
        state = WorkflowState()
        decision = evaluate_unicorn(listing, score, state, now=NOW)
        assert decision.is_unicorn is True
        assert any("price-drop=" in r for r in decision.reasons)

    def test_rate_limited_after_daily_cap(self, monkeypatch):
        monkeypatch.setenv("UNICORN_SMS_DAILY_CAP", "1")
        listing = _listing()
        score = _score()
        state = WorkflowState()
        state.sms_timestamps.append(NOW - timedelta(hours=2))  # 1 SMS already

        decision = evaluate_unicorn(listing, score, state, now=NOW)
        assert decision.is_unicorn is False
        assert decision.rate_limited is True

    def test_already_notified_blocks_repeat(self):
        listing = _listing()
        score = _score()
        state = WorkflowState()
        state.unicorn_notified.add(listing.dedup_key())

        decision = evaluate_unicorn(listing, score, state, now=NOW)
        assert decision.is_unicorn is False
        assert decision.already_notified is True

    def test_reasons_always_ordered_for_log_readability(self):
        """Decision reasons should narrate the gate traversal for easy log reading."""
        listing = _listing()
        score = _score()
        state = WorkflowState()
        decision = evaluate_unicorn(listing, score, state, now=NOW)
        # Order: tier-ok implicit → deal-ok → miles-ok → title-clean → new/drop → UNICORN
        expected_markers = ["deal-ok", "miles-ok", "title-clean", "new-this-run", "UNICORN"]
        for i in range(len(expected_markers) - 1):
            a_idx = next(j for j, r in enumerate(decision.reasons) if expected_markers[i] in r)
            b_idx = next(j for j, r in enumerate(decision.reasons) if expected_markers[i + 1] in r)
            assert a_idx < b_idx, f"{expected_markers[i]} should come before {expected_markers[i+1]}"
