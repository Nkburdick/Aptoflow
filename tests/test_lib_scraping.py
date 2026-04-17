"""Unit tests for lib.scraping BrightDataClient + BrightDataConfig."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from lib.scraping import (
    BrightDataClient,
    BrightDataConfig,
    BrightDataConfigError,
    BrightDataFetchError,
)


class TestBrightDataConfig:
    def test_from_env_happy_path(self, monkeypatch):
        monkeypatch.setenv("BRIGHTDATA_ZONE", "aptoflow_unblocker")
        monkeypatch.setenv("BRIGHTDATA_USERNAME", "brd-customer-X-zone-Y")
        monkeypatch.setenv("BRIGHTDATA_PASSWORD", "secret")

        cfg = BrightDataConfig.from_env()
        assert cfg.zone == "aptoflow_unblocker"
        assert cfg.username == "brd-customer-X-zone-Y"
        assert cfg.password == "secret"

    def test_from_env_raises_on_missing(self, monkeypatch):
        monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)
        monkeypatch.setenv("BRIGHTDATA_USERNAME", "u")
        monkeypatch.setenv("BRIGHTDATA_PASSWORD", "p")

        with pytest.raises(BrightDataConfigError, match="BRIGHTDATA_ZONE"):
            BrightDataConfig.from_env()

    def test_from_env_lists_all_missing(self, monkeypatch):
        monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)
        monkeypatch.delenv("BRIGHTDATA_USERNAME", raising=False)
        monkeypatch.delenv("BRIGHTDATA_PASSWORD", raising=False)

        with pytest.raises(BrightDataConfigError) as exc_info:
            BrightDataConfig.from_env()
        msg = str(exc_info.value)
        assert "BRIGHTDATA_ZONE" in msg
        assert "BRIGHTDATA_USERNAME" in msg
        assert "BRIGHTDATA_PASSWORD" in msg

    def test_proxy_url_includes_auth(self):
        cfg = BrightDataConfig(
            zone="z", username="u", password="p", proxy_host="host:1234"
        )
        assert cfg.proxy_url() == "http://u:p@host:1234"

    def test_empty_strings_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("BRIGHTDATA_ZONE", "")
        monkeypatch.setenv("BRIGHTDATA_USERNAME", "u")
        monkeypatch.setenv("BRIGHTDATA_PASSWORD", "p")
        with pytest.raises(BrightDataConfigError, match="BRIGHTDATA_ZONE"):
            BrightDataConfig.from_env()


def _cfg(**overrides) -> BrightDataConfig:
    base = dict(
        zone="z",
        username="u",
        password="p",
        proxy_host="h:1",
        timeout_s=1.0,
        max_retries=3,
    )
    base.update(overrides)
    return BrightDataConfig(**base)


class TestBrightDataClientFetch:
    def test_success_returns_body(self):
        client = BrightDataClient(config=_cfg())
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>ok</html>"

        with patch.object(client._client, "get", return_value=mock_resp):
            html = client.fetch("https://example.com")
        assert html == "<html>ok</html>"
        client.close()

    def test_includes_default_headers(self):
        client = BrightDataClient(config=_cfg())
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"

        with patch.object(client._client, "get", return_value=mock_resp) as mock_get:
            client.fetch("https://x")

        headers = mock_get.call_args.kwargs["headers"]
        assert "Mozilla" in headers["User-Agent"]
        assert headers["Accept-Language"].startswith("en-US")
        client.close()

    def test_country_header_applied(self):
        client = BrightDataClient(config=_cfg())
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"

        with patch.object(client._client, "get", return_value=mock_resp) as mock_get:
            client.fetch("https://x", country="us")

        assert mock_get.call_args.kwargs["headers"]["x-brd-country"] == "us"
        client.close()

    def test_extra_headers_merged(self):
        client = BrightDataClient(config=_cfg())
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"

        with patch.object(client._client, "get", return_value=mock_resp) as mock_get:
            client.fetch("https://x", extra_headers={"Referer": "https://y"})

        assert mock_get.call_args.kwargs["headers"]["Referer"] == "https://y"
        client.close()

    def test_retries_on_5xx_then_succeeds(self):
        client = BrightDataClient(config=_cfg(max_retries=3))
        fail_resp = MagicMock(status_code=503, text="")
        ok_resp = MagicMock(status_code=200, text="ok")

        with patch.object(client._client, "get", side_effect=[fail_resp, ok_resp]):
            with patch("lib.scraping.time.sleep"):
                html = client.fetch("https://x")
        assert html == "ok"
        client.close()

    def test_does_not_retry_on_most_4xx(self):
        client = BrightDataClient(config=_cfg(max_retries=3))
        resp = MagicMock(status_code=403, text="")

        with patch.object(client._client, "get", return_value=resp) as mock_get:
            with patch("lib.scraping.time.sleep"):
                with pytest.raises(BrightDataFetchError, match="403"):
                    client.fetch("https://x")
        # Only one attempt — 403 isn't retryable
        assert mock_get.call_count == 1
        client.close()

    def test_retries_on_429(self):
        client = BrightDataClient(config=_cfg(max_retries=3))
        resp = MagicMock(status_code=429, text="")

        with patch.object(client._client, "get", return_value=resp) as mock_get:
            with patch("lib.scraping.time.sleep"):
                with pytest.raises(BrightDataFetchError):
                    client.fetch("https://x")
        assert mock_get.call_count == 3
        client.close()

    def test_raises_after_max_retries(self):
        client = BrightDataClient(config=_cfg(max_retries=2))
        with patch.object(client._client, "get", side_effect=httpx.ConnectError("boom")):
            with patch("lib.scraping.time.sleep"):
                with pytest.raises(BrightDataFetchError, match="2 attempts"):
                    client.fetch("https://x")
        client.close()

    def test_context_manager_closes(self):
        with BrightDataClient(config=_cfg()) as client:
            assert client._client is not None
        # After exit, the underlying httpx client should be closed
        # httpx.Client exposes .is_closed
        assert client._client.is_closed
