"""Unit tests for workflows.car_scout.models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from workflows.car_scout.models import (
    Listing,
    PriceObservation,
    Score,
    WorkflowState,
)


def _sample_listing(**overrides):
    base = dict(
        url="https://www.cargurus.com/Cars/link/abc",
        vin="JF2GTAPC9LH232411",
        source="cargurus",
        year=2020,
        make="Subaru",
        model="Crosstrek",
        trim="Premium",
        transmission="auto",
        mileage=42000,
        price=19900,
        title_status="clean",
        tier="primary",
    )
    base.update(overrides)
    return Listing(**base)


class TestListing:
    def test_dedup_key_prefers_vin(self):
        listing = _sample_listing()
        assert listing.dedup_key() == "JF2GTAPC9LH232411"

    def test_dedup_key_falls_back_to_url(self):
        listing = _sample_listing(vin=None)
        assert listing.dedup_key() == "https://www.cargurus.com/Cars/link/abc"

    def test_defaults_for_unknown_fields(self):
        listing = _sample_listing(transmission="unknown", title_status="unknown")
        assert listing.transmission == "unknown"
        assert listing.title_status == "unknown"
        assert listing.seller_type == "dealer"
        assert listing.photos == []
        assert listing.description == ""

    def test_first_seen_and_last_seen_default_to_now(self):
        before = datetime.now(timezone.utc)
        listing = _sample_listing()
        after = datetime.now(timezone.utc)
        assert before <= listing.first_seen <= after
        assert before <= listing.last_seen <= after

    def test_rejects_invalid_tier(self):
        with pytest.raises(ValidationError):
            _sample_listing(tier="tertiary")

    def test_rejects_invalid_source(self):
        with pytest.raises(ValidationError):
            _sample_listing(source="craigslist")

    def test_rejects_invalid_title_status(self):
        with pytest.raises(ValidationError):
            _sample_listing(title_status="sketchy")

    def test_cargurus_rating_enum(self):
        listing = _sample_listing()
        listing.cargurus_rating = "Great"
        # Forward-compatible — values restricted to known tier strings
        with pytest.raises(ValidationError):
            _sample_listing(cargurus_rating="AwesomeDeal")


class TestPriceObservation:
    def test_roundtrip(self):
        ts = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
        obs = PriceObservation(timestamp=ts, price=19900)
        raw = obs.model_dump_json()
        restored = PriceObservation.model_validate_json(raw)
        assert restored.price == 19900
        assert restored.timestamp == ts


class TestScore:
    def test_defaults(self):
        score = Score(
            listing_url="https://x.example/a",
            cargurus_component=100.0,
            market_delta_component=80.0,
            mileage_component=90.0,
            redflag_penalty=0.0,
            total=91.0,
            band="great",
        )
        assert score.reasoning == ""
        assert score.passes_unicorn is False
        assert score.low_comp_confidence is False


class TestWorkflowState:
    def test_empty_state_roundtrip(self):
        state = WorkflowState()
        raw = state.model_dump_json()
        restored = WorkflowState.model_validate_json(raw)
        assert restored.listings == {}
        assert restored.comps == {}
        assert restored.unicorn_notified == set()
        assert restored.runs_total == 0

    def test_populated_state_roundtrip(self):
        state = WorkflowState()
        listing = _sample_listing()
        state.listings[listing.dedup_key()] = listing
        state.unicorn_notified.add("JF2GTAPC9LH232411")
        state.runs_total = 7

        raw = state.model_dump_json()
        restored = WorkflowState.model_validate_json(raw)

        assert "JF2GTAPC9LH232411" in restored.listings
        assert restored.listings["JF2GTAPC9LH232411"].price == 19900
        assert "JF2GTAPC9LH232411" in restored.unicorn_notified
        assert restored.runs_total == 7
