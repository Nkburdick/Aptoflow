"""Unit tests for workflows.car_scout.scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from workflows.car_scout.models import (
    Listing,
    PriceObservation,
    WorkflowState,
)
from workflows.car_scout.scoring import (
    COMP_WINDOW_DAYS,
    MIN_COMPS_FOR_CONFIDENCE,
    _band_for,
    _market_delta_component,
    _mileage_component,
    _redflag_component,
    compute_market_median,
    compute_mileage_percentile,
    score_listing,
)
from workflows.car_scout.state import comp_key, merge_listing


def _listing(**overrides) -> Listing:
    base = dict(
        url="https://www.cargurus.com/Cars/link/a",
        vin="VIN-A",
        source="cargurus",
        year=2020,
        make="Subaru",
        model="Crosstrek",
        trim="Premium",
        transmission="auto",
        mileage=45000,
        price=19900,
        title_status="clean",
        tier="primary",
    )
    base.update(overrides)
    return Listing(**base)


class TestMarketDeltaComponent:
    @pytest.mark.parametrize(
        "delta_pct, expected",
        [
            (0.25, 100.0),   # 25% below median
            (0.12, 85.0),    # 12% below
            (0.07, 65.0),    # 7% below
            (0.0, 50.0),     # on-median
            (-0.10, 25.0),   # 10% above
            (-0.25, 5.0),    # 25% above (overpriced)
        ],
    )
    def test_piecewise_mapping(self, delta_pct, expected):
        assert _market_delta_component(delta_pct) == expected


class TestMileageComponent:
    @pytest.mark.parametrize(
        "percentile, expected",
        [
            (10.0, 100.0),
            (25.0, 100.0),
            (30.0, 75.0),
            (60.0, 50.0),
            (80.0, 25.0),
            (95.0, 10.0),
        ],
    )
    def test_piecewise_mapping(self, percentile, expected):
        assert _mileage_component(percentile) == expected


class TestRedflagComponent:
    def test_clean_is_99(self):
        assert _redflag_component(0) == 99.0

    def test_deal_breaker_is_zero(self):
        assert _redflag_component(3) == 0.0

    def test_intermediate(self):
        assert _redflag_component(1) == 66.0
        assert _redflag_component(2) == 33.0


class TestBandFor:
    @pytest.mark.parametrize(
        "total, band",
        [
            (98.0, "unicorn"),
            (95.0, "unicorn"),
            (94.9, "great"),
            (85.0, "great"),
            (84.9, "good"),
            (70.0, "good"),
            (69.9, "fair"),
            (50.0, "fair"),
            (49.9, "pass"),
            (0.0, "pass"),
        ],
    )
    def test_thresholds(self, total, band):
        assert _band_for(total) == band


class TestComputeMarketMedian:
    def test_enough_comps_within_window(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        bucket = comp_key(2020, "Subaru", "Crosstrek")
        state.comps[bucket] = [
            PriceObservation(timestamp=now - timedelta(days=5), price=19000),
            PriceObservation(timestamp=now - timedelta(days=10), price=20000),
            PriceObservation(timestamp=now - timedelta(days=15), price=21000),
        ]
        median, n = compute_market_median(state, 2020, "Subaru", "Crosstrek", now=now)
        assert median == 20000
        assert n == 3

    def test_insufficient_comps_returns_none(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        bucket = comp_key(2020, "Subaru", "Crosstrek")
        state.comps[bucket] = [
            PriceObservation(timestamp=now, price=19000),
        ]
        median, n = compute_market_median(state, 2020, "Subaru", "Crosstrek", now=now)
        assert median is None
        assert n == 1

    def test_out_of_window_comps_excluded(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        bucket = comp_key(2020, "Subaru", "Crosstrek")
        state.comps[bucket] = [
            PriceObservation(timestamp=now - timedelta(days=40), price=15000),  # expired
            PriceObservation(timestamp=now - timedelta(days=40), price=16000),  # expired
            PriceObservation(timestamp=now - timedelta(days=5), price=20000),
            PriceObservation(timestamp=now - timedelta(days=10), price=21000),
            PriceObservation(timestamp=now - timedelta(days=15), price=22000),
        ]
        median, _ = compute_market_median(state, 2020, "Subaru", "Crosstrek", now=now)
        assert median == 21000


class TestComputeMileagePercentile:
    def test_bottom_percentile(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        for miles, vin in [(30000, "A"), (60000, "B"), (90000, "C"), (110000, "D")]:
            merge_listing(
                state,
                _listing(vin=vin, mileage=miles, url=f"https://x.example/{vin}"),
                now=now,
            )

        # Our test listing with 25000 miles — below ALL comps = 0 percentile
        target = _listing(vin="TARGET", mileage=25000, url="https://target.example")
        # Don't merge TARGET (we're scoring it, not adding it to state)
        pct = compute_mileage_percentile(target, state)
        assert pct == 0.0

    def test_top_percentile(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        for miles, vin in [(30000, "A"), (60000, "B"), (90000, "C")]:
            merge_listing(
                state,
                _listing(vin=vin, mileage=miles, url=f"https://x.example/{vin}"),
                now=now,
            )
        target = _listing(vin="TARGET", mileage=150000, url="https://target.example")
        pct = compute_mileage_percentile(target, state)
        assert pct == 100.0

    def test_insufficient_comps_returns_neutral(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        merge_listing(state, _listing(vin="A", mileage=30000), now=now)
        target = _listing(vin="TARGET", mileage=60000)
        pct = compute_mileage_percentile(target, state)
        assert pct == 50.0


class TestScoreListing:
    def _populate_bucket(self, state: WorkflowState, now: datetime, prices: list[int]) -> None:
        bucket = comp_key(2020, "Subaru", "Crosstrek")
        state.comps[bucket] = [
            PriceObservation(timestamp=now - timedelta(days=i + 1), price=p)
            for i, p in enumerate(prices)
        ]

    def test_great_deal_produces_unicorn_or_great_band(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        self._populate_bucket(state, now, [22000, 21500, 22500])

        # Populate mileage comps too so percentile calculates
        for miles, vin in [(90000, "A"), (95000, "B"), (100000, "C")]:
            merge_listing(
                state,
                _listing(vin=vin, mileage=miles, url=f"https://x.example/{vin}", price=22000),
                now=now,
            )

        target = _listing(mileage=42000, price=18500, cargurus_rating="Great")
        score = score_listing(target, state, now=now)
        assert score.band in ("unicorn", "great")
        assert score.cargurus_component == 100.0
        assert score.market_delta_component >= 85.0  # 18500 is ~16% below 22000 median
        assert score.mileage_component == 100.0  # lowest miles in bucket

    def test_overpriced_produces_pass_or_fair(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        self._populate_bucket(state, now, [19000, 19500, 20000])

        target = _listing(price=25000, cargurus_rating="Overpriced")
        score = score_listing(target, state, now=now)
        # Overpriced + above market should fail fair threshold
        assert score.band in ("fair", "pass")
        assert score.cargurus_component == 10.0

    def test_low_comp_confidence_flagged(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        # No comps in bucket
        score = score_listing(_listing(), state, now=now)
        assert score.low_comp_confidence is True
        assert score.market_delta_component == 50.0  # neutral fallback

    def test_redflag_severity_3_drags_score_down(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        self._populate_bucket(state, now, [22000, 21500, 22500])
        for miles, vin in [(90000, "A"), (95000, "B"), (100000, "C")]:
            merge_listing(
                state,
                _listing(vin=vin, mileage=miles, url=f"https://x.example/{vin}", price=22000),
                now=now,
            )

        target = _listing(mileage=42000, price=18500, cargurus_rating="Great")
        clean = score_listing(target, state, now=now, redflag_severity=0)
        flagged = score_listing(
            target, state, now=now, redflag_severity=3, redflag_flags=["salvage auction"],
        )
        assert flagged.total < clean.total
        # Clean severity contributes ~10 points; severity-3 contributes 0
        assert clean.total - flagged.total == pytest.approx(9.9, abs=0.01)
        assert "salvage auction" in flagged.reasoning

    def test_reasoning_includes_delta(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        self._populate_bucket(state, now, [20000, 20500, 21000])
        for miles, vin in [(80000, "A"), (85000, "B"), (90000, "C")]:
            merge_listing(
                state,
                _listing(vin=vin, mileage=miles, url=f"https://x.example/{vin}", price=20000),
                now=now,
            )

        score = score_listing(_listing(mileage=45000, price=18000), state, now=now)
        assert "vs $" in score.reasoning
        assert "mileage pct" in score.reasoning
