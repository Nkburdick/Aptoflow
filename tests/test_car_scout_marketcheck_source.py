"""Tests for workflows.car_scout.sources.marketcheck — typed subscription errors."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from lib.marketcheck import MarketCheckFetchError, MarketCheckSubscriptionError
from workflows.car_scout.sources.marketcheck import fetch_all_targets


class TestSubscriptionErrorClassification:
    """MarketCheckSubscriptionError must land in result.subscription_errors and
    emit a WARNING log so the silent-zero case is visible."""

    def test_subscription_error_populates_subscription_errors(self, caplog):
        client = MagicMock()
        client.search_active.side_effect = MarketCheckSubscriptionError(
            'MarketCheck returned 422: {"code":422,"message":"Subscribed package radius limit of 100 miles exceeded"}'
        )

        with caplog.at_level(logging.WARNING, logger="car-scout.mc-source"):
            result = fetch_all_targets(
                client, zip_code="98225", radius_mi=300,
                year_floor=2015, budget_ceiling=30000,
            )

        assert len(result.subscription_errors) > 0
        assert result.errors == []  # Subscription errors don't double-count
        sub_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "marketcheck_subscription_error" in r.message
        ]
        assert len(sub_warnings) > 0

    def test_generic_fetch_error_does_not_classify_as_subscription(self):
        client = MagicMock()
        client.search_active.side_effect = MarketCheckFetchError("Network error: connection timed out")
        result = fetch_all_targets(
            client, zip_code="98225", radius_mi=100,
            year_floor=2015, budget_ceiling=30000,
        )
        assert len(result.errors) > 0
        assert result.subscription_errors == []

    def test_successful_fetch_no_subscription_errors(self):
        client = MagicMock()
        client.search_active.return_value = []
        result = fetch_all_targets(
            client, zip_code="98225", radius_mi=100,
            year_floor=2015, budget_ceiling=30000,
        )
        assert result.subscription_errors == []
