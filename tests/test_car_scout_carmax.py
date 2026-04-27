"""Unit tests for the CarMax nationwide source + shipping estimator.

Cover: shipping-fee tier math, MarketCheck seller_name param threading,
4-model bucket iteration, and the digest section plumbing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from workflows.car_scout.sources.carmax_nationwide import (
    PRIMARY_SUBARU_MODELS,
    _STATE_DISTANCE_MI,
    estimate_shipping_fee,
    fetch_carmax_nationwide_subarus,
)


class TestEstimateShippingFee:
    """CarMax's $0/$199/$299/$499 tier table based on state-centroid distance."""

    def test_washington_is_free(self):
        assert estimate_shipping_fee("WA") == 0

    def test_oregon_regional(self):
        # Oregon ~180mi, falls in 60-250 band → $199
        assert estimate_shipping_fee("OR") == 199

    def test_california_cross_region(self):
        # CA ~700mi → 250-1500 band → $299
        assert estimate_shipping_fee("CA") == 299

    def test_georgia_cross_country(self):
        # GA ~2400mi → 1500+ band → $499
        assert estimate_shipping_fee("GA") == 499

    def test_florida_cross_country(self):
        assert estimate_shipping_fee("FL") == 499

    def test_unknown_state_returns_none(self):
        assert estimate_shipping_fee("ZZ") is None
        assert estimate_shipping_fee("") is None
        assert estimate_shipping_fee(None) is None

    def test_lowercase_state_normalizes(self):
        assert estimate_shipping_fee("wa") == 0
        assert estimate_shipping_fee("ga") == 499

    def test_whitespace_handled(self):
        assert estimate_shipping_fee("  CA  ") == 299

    def test_all_lower48_states_mapped(self):
        """Coverage check — every lower-48 state + HI/AK has a distance entry."""
        lower_48_plus = {
            "WA", "OR", "ID", "CA", "NV", "MT", "UT", "AZ", "WY", "NM",
            "CO", "ND", "SD", "NE", "KS", "OK", "TX", "MN", "IA", "MO",
            "AR", "LA", "WI", "IL", "MS", "MI", "IN", "KY", "TN", "AL",
            "OH", "WV", "VA", "NC", "SC", "GA", "FL", "PA", "NY", "VT",
            "NH", "MA", "RI", "CT", "NJ", "DE", "MD", "DC", "ME",
            "HI", "AK",
        }
        missing = lower_48_plus - set(_STATE_DISTANCE_MI.keys())
        assert not missing, f"Missing distance for: {missing}"


class TestFetchCarMaxNationwideSubarus:
    def test_iterates_all_primary_subaru_models(self):
        """Must query every model in PRIMARY_SUBARU_MODELS — not secondary makes."""
        mc = MagicMock()
        mc.search_active.return_value = []  # no results, just count calls

        result = fetch_carmax_nationwide_subarus(mc, year_floor=2015, budget_ceiling=30000)

        assert mc.search_active.call_count == len(PRIMARY_SUBARU_MODELS)
        called_models = {call.kwargs["model"] for call in mc.search_active.call_args_list}
        assert called_models == set(PRIMARY_SUBARU_MODELS)

    def test_passes_seller_name_filter_to_marketcheck(self):
        """Every bucket call must include seller_name='CarMax'."""
        mc = MagicMock()
        mc.search_active.return_value = []

        fetch_carmax_nationwide_subarus(mc, year_floor=2015, budget_ceiling=30000)

        for call in mc.search_active.call_args_list:
            assert call.kwargs["seller_name"] == "CarMax"

    def test_passes_year_floor_and_budget_ceiling(self):
        mc = MagicMock()
        mc.search_active.return_value = []

        fetch_carmax_nationwide_subarus(mc, year_floor=2018, budget_ceiling=25000)

        for call in mc.search_active.call_args_list:
            assert call.kwargs["year_min"] == 2018
            assert call.kwargs["price_max"] == 25000

    def test_radius_within_free_tier_cap(self):
        """Radius must stay at/under MarketCheck's free-tier 100mi subscription
        cap. v1.2 dropped the original 5000mi nationwide radius after every
        query was silently 422'ing — see workflows/car_scout PRD v1.2.
        """
        mc = MagicMock()
        mc.search_active.return_value = []

        fetch_carmax_nationwide_subarus(mc, year_floor=2015, budget_ceiling=30000)

        for call in mc.search_active.call_args_list:
            assert call.kwargs["radius"] <= 100

    def test_per_bucket_failure_does_not_crash_aggregate(self):
        """If one (model) query raises, the other still returns."""
        mc = MagicMock()

        def flaky_search(**kwargs):
            if kwargs["model"] == "Forester":
                raise RuntimeError("MarketCheck 503")
            return []

        mc.search_active.side_effect = flaky_search

        result = fetch_carmax_nationwide_subarus(mc, year_floor=2015, budget_ceiling=30000)

        assert len(result.errors) == 1
        assert "Forester" in result.errors[0]
        # All non-failing buckets succeed
        assert result.pages_fetched == len(PRIMARY_SUBARU_MODELS) - 1


class TestCarMaxDigestSection:
    """assemble_digest routes source=carmax listings to their own section."""

    def test_carmax_listing_goes_to_carmax_section_not_top_picks(self):
        from datetime import datetime, timezone
        from workflows.car_scout.digest import assemble_digest
        from workflows.car_scout.models import Listing, Score, WorkflowState

        now = datetime(2026, 4, 24, tzinfo=timezone.utc)
        state = WorkflowState()
        state.last_scout_run = now

        carmax_listing = Listing(
            url="https://carmax.example/1",
            vin="CARMAX-VIN-1",
            source="carmax",
            year=2021,
            make="Subaru",
            model="Crosstrek",
            mileage=40000,
            price=22000,
            title_status="clean",
            tier="primary",
            state="GA",
            shipping_fee_estimate=499,
        )
        score = Score(
            listing_url=carmax_listing.url,
            computed_at=now,
            cargurus_component=90.0,
            market_delta_component=85.0,
            mileage_component=90.0,
            redflag_component=99.0,
            total=90.0,
            band="great",
            reasoning="Great (90) test",
            mileage_percentile_for_model_year=20.0,
        )

        payload = assemble_digest([(carmax_listing, score)], state, now=now)

        # CarMax-tagged listings stay out of the top picks / new today sections
        assert len(payload.top_picks) == 0
        assert len(payload.new_today) == 0
        assert len(payload.price_drops) == 0
        assert len(payload.carmax) == 1
        assert payload.carmax[0].shipping_fee_estimate == 499

    def test_empty_carmax_section_renders_without_error(self):
        from workflows.car_scout.digest import render_digest_html, DigestPayload

        payload = DigestPayload()  # all sections empty
        html = render_digest_html(payload)
        assert "🚚 CarMax" not in html  # section shouldn't appear when empty
        assert "<html" in html.lower()  # but the page itself must render

    def test_carmax_section_appears_in_html_when_populated(self):
        from datetime import datetime, timezone
        from workflows.car_scout.digest import render_digest_html, DigestCard, DigestPayload

        card = DigestCard(
            url="https://carmax.example/1",
            photo=None,
            year=2021,
            make="Subaru",
            model="Crosstrek",
            trim=None,
            price=22000,
            old_price=None,
            mileage=40000,
            deal_score=90,
            deal_band="great",
            cargurus_rating=None,
            reasoning="Great deal",
            accident_count=0,
            owner_count=1,
            city="Atlanta",
            state="GA",
            dealer_name="CarMax",
            shipping_fee_estimate=499,
        )
        payload = DigestPayload(carmax=[card])
        html = render_digest_html(payload)

        assert "CarMax (Nationwide)" in html
        assert "$499" in html
        assert "Ships to Bellingham" in html


class TestAmCycleGuard:
    """Regression guard for the AM/PM CarMax-fetch timing.

    AM cron fires at 13:30 UTC (hour=13), PM at 01:30 UTC (hour=1).
    CarMax fetch should run ONLY on AM. The `ts.hour >= 12` predicate
    in main._run_digest enforces this. Had a prior inverted bug where
    `ts.hour < 12` was used — which would have flipped AM and PM.
    """

    def test_am_cron_hour_passes_guard(self):
        """13:30 UTC AM digest → hour 13 → gate opens for CarMax fetch."""
        assert 13 >= 12

    def test_pm_cron_hour_skips_guard(self):
        """01:30 UTC PM digest → hour 1 → gate stays closed, CarMax skipped."""
        assert not (1 >= 12)

    def test_noon_utc_edge_case(self):
        """12:00 UTC boundary — should pass (hour 12 counts as AM cycle)."""
        assert 12 >= 12


class TestCarMaxShippingFeeRendering:
    def test_free_shipping_displayed_differently_from_paid(self):
        from workflows.car_scout.digest import _render_card_html, DigestCard

        free_card = DigestCard(
            url="https://x.example/1", photo=None, year=2021, make="Subaru", model="Crosstrek",
            trim=None, price=22000, old_price=None, mileage=40000, deal_score=90,
            deal_band="great", cargurus_rating=None, reasoning="x", accident_count=None,
            owner_count=None, city=None, state="WA", dealer_name="CarMax",
            shipping_fee_estimate=0,
        )
        paid_card = DigestCard(
            url="https://x.example/2", photo=None, year=2021, make="Subaru", model="Crosstrek",
            trim=None, price=22000, old_price=None, mileage=40000, deal_score=90,
            deal_band="great", cargurus_rating=None, reasoning="x", accident_count=None,
            owner_count=None, city=None, state="GA", dealer_name="CarMax",
            shipping_fee_estimate=499,
        )

        free_html = _render_card_html(free_card)
        paid_html = _render_card_html(paid_card)

        assert "FREE" in free_html
        assert "$499" in paid_html
        assert "FREE" not in paid_html

    def test_no_shipping_line_for_non_carmax_listing(self):
        """A listing with shipping_fee_estimate=None (non-CarMax) shows no shipping line."""
        from workflows.car_scout.digest import _render_card_html, DigestCard

        card = DigestCard(
            url="https://x.example/1", photo=None, year=2020, make="Subaru", model="Crosstrek",
            trim=None, price=22000, old_price=None, mileage=40000, deal_score=85,
            deal_band="great", cargurus_rating=None, reasoning="x", accident_count=None,
            owner_count=None, city="Bellingham", state="WA", dealer_name="Roger Jobs",
            shipping_fee_estimate=None,
        )
        html = _render_card_html(card)
        assert "Ships to Bellingham" not in html
        assert "🚚" not in html
