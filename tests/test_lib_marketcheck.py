"""Tests for lib.marketcheck — sort defaults + typed subscription errors."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.marketcheck import (
    MarketCheckClient,
    MarketCheckConfigError,
    MarketCheckFetchError,
    MarketCheckSubscriptionError,
)


def _stub_client():
    client = MarketCheckClient(api_key="test-key")
    return client


def _ok_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"listings": [], "num_found": 0}
    return resp


class TestSearchActiveQueryParams:
    """sort_by=last_seen desc avoids the cheapest-N bias of price asc on a 50-row cap."""

    def test_sort_by_is_last_seen(self):
        client = _stub_client()
        with patch.object(client._client, "get", return_value=_ok_response()) as mock_get:
            client.search_active(make="Subaru", model="Crosstrek", zip="98225", radius=100)
        assert mock_get.call_args.kwargs["params"]["sort_by"] == "last_seen"

    def test_sort_order_is_desc(self):
        client = _stub_client()
        with patch.object(client._client, "get", return_value=_ok_response()) as mock_get:
            client.search_active(make="Subaru", model="Crosstrek", zip="98225", radius=100)
        assert mock_get.call_args.kwargs["params"]["sort_order"] == "desc"


class TestSubscriptionErrorTyped:
    """422 must raise MarketCheckSubscriptionError so callers can catch it
    distinctly from generic fetch errors and surface the silent-zero case.
    """

    def test_422_raises_subscription_error(self):
        client = _stub_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.text = '{"code":422,"message":"Subscribed package radius limit of 100 miles exceeded"}'
        with patch.object(client._client, "get", return_value=mock_resp):
            with pytest.raises(MarketCheckSubscriptionError):
                client.search_active(make="Subaru", model="Crosstrek", zip="98225", radius=300)

    def test_subscription_error_is_subclass_of_fetch_error(self):
        # Existing call sites that catch MarketCheckFetchError still work.
        assert issubclass(MarketCheckSubscriptionError, MarketCheckFetchError)

    def test_500_raises_generic_fetch_error_not_subscription(self):
        client = _stub_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "internal error"
        with patch.object(client._client, "get", return_value=mock_resp):
            with pytest.raises(MarketCheckFetchError) as exc_info:
                client.search_active(make="Subaru", model="Crosstrek", zip="98225", radius=100)
        assert not isinstance(exc_info.value, MarketCheckSubscriptionError)


class TestConfig:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("MARKETCHECK_API_KEY", raising=False)
        with pytest.raises(MarketCheckConfigError):
            MarketCheckClient()
