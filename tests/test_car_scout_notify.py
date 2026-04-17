"""Unit tests for workflows.car_scout.notify."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from workflows.car_scout.notify import (
    PennyworthNotifyError,
    format_unicorn_sms,
    notify_unicorn,
)


class TestFormatUnicornSms:
    def test_with_trim(self):
        title, body = format_unicorn_sms(
            year=2020,
            make="Subaru",
            model="Crosstrek",
            trim="Premium",
            mileage=42000,
            price=19900,
            delta_pct=14.0,
            dealer_or_city="Roger Jobs",
            short_url="https://x.example/a",
        )
        assert title == "🏆 Unicorn: 2020 Subaru Crosstrek Premium"
        assert "42,000 mi" in body
        assert "$19,900" in body
        assert "Roger Jobs" in body
        assert "14% below market" in body

    def test_without_trim(self):
        title, body = format_unicorn_sms(
            year=2019,
            make="Subaru",
            model="Outback",
            trim=None,
            mileage=78000,
            price=17500,
            delta_pct=12.0,
            dealer_or_city="Bellingham",
            short_url="https://x.example/b",
        )
        assert title == "🏆 Unicorn: 2019 Subaru Outback"

    def test_combined_length_under_160(self):
        # Worst case: long trim name, big mileage, specific dealer
        title, body = format_unicorn_sms(
            year=2022,
            make="Subaru",
            model="Crosstrek",
            trim="Sport Limited EyeSight",
            mileage=115000,
            price=22000,
            delta_pct=22.5,
            dealer_or_city="Roger Jobs Subaru",
            short_url="https://www.cargurus.com/Cars/link/abc",
        )
        # Template renders "{{title}}\n{{body}}\n{{url}}" in Pennyworth — URL
        # is a third line (~50 chars for a shortened CarGurus link). Title+body
        # under ~130 keeps the full SMS to ≤2 Twilio segments in the worst case.
        combined = title + body
        assert len(combined) < 130, f"combined too long: {len(combined)} chars"


class TestNotifyUnicorn:
    def test_success_returns_result(self, monkeypatch):
        monkeypatch.setenv("AOL_API_TOKEN", "test-token")
        monkeypatch.setenv("PENNYWORTH_BASE_URL", "https://pw.example")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "pushed": True,
            "sms": True,
            "notificationId": "notif-abc",
        }

        with patch("workflows.car_scout.notify.httpx.post", return_value=mock_resp) as mock_post:
            result = notify_unicorn(
                title="🏆 Unicorn",
                body="test",
                url="https://x.example/a",
                data={"workflow": "car_scout"},
            )

        assert result["notificationId"] == "notif-abc"
        assert result["sms"] is True

        # Verify the call shape
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"] == {
            "title": "🏆 Unicorn",
            "body": "test",
            "url": "https://x.example/a",
            "data": {"workflow": "car_scout"},
        }
        assert call_kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert mock_post.call_args.args[0] == "https://pw.example/api/events/external"

    def test_trailing_slash_in_base_url_trimmed(self, monkeypatch):
        monkeypatch.setenv("AOL_API_TOKEN", "test-token")
        monkeypatch.setenv("PENNYWORTH_BASE_URL", "https://pw.example/")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"pushed": False, "sms": True, "notificationId": "n1"}

        with patch("workflows.car_scout.notify.httpx.post", return_value=mock_resp) as mock_post:
            notify_unicorn(title="T", body="B", url="https://x/y")

        assert mock_post.call_args.args[0] == "https://pw.example/api/events/external"

    def test_omits_data_when_none(self, monkeypatch):
        monkeypatch.setenv("AOL_API_TOKEN", "test-token")
        monkeypatch.setenv("PENNYWORTH_BASE_URL", "https://pw.example")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"pushed": False, "sms": True, "notificationId": "n1"}

        with patch("workflows.car_scout.notify.httpx.post", return_value=mock_resp) as mock_post:
            notify_unicorn(title="T", body="B", url="https://x/y")

        assert "data" not in mock_post.call_args.kwargs["json"]

    def test_raises_when_token_missing(self, monkeypatch):
        monkeypatch.delenv("AOL_API_TOKEN", raising=False)
        with pytest.raises(PennyworthNotifyError, match="AOL_API_TOKEN"):
            notify_unicorn(title="T", body="B", url="https://x/y")

    def test_raises_on_4xx(self, monkeypatch):
        monkeypatch.setenv("AOL_API_TOKEN", "test-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = '{"error":"Unauthorized"}'

        with patch("workflows.car_scout.notify.httpx.post", return_value=mock_resp):
            with pytest.raises(PennyworthNotifyError, match="401"):
                notify_unicorn(title="T", body="B", url="https://x/y")

    def test_raises_on_5xx(self, monkeypatch):
        monkeypatch.setenv("AOL_API_TOKEN", "test-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("workflows.car_scout.notify.httpx.post", return_value=mock_resp):
            with pytest.raises(PennyworthNotifyError, match="500"):
                notify_unicorn(title="T", body="B", url="https://x/y")

    def test_raises_on_network_failure(self, monkeypatch):
        monkeypatch.setenv("AOL_API_TOKEN", "test-token")
        with patch(
            "workflows.car_scout.notify.httpx.post",
            side_effect=httpx.ConnectError("network down"),
        ):
            with pytest.raises(PennyworthNotifyError, match="network down"):
                notify_unicorn(title="T", body="B", url="https://x/y")

    def test_explicit_params_override_env(self, monkeypatch):
        monkeypatch.setenv("AOL_API_TOKEN", "env-token")
        monkeypatch.setenv("PENNYWORTH_BASE_URL", "https://env.example")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"pushed": True, "sms": True, "notificationId": "n1"}

        with patch("workflows.car_scout.notify.httpx.post", return_value=mock_resp) as mock_post:
            notify_unicorn(
                title="T",
                body="B",
                url="https://x/y",
                base_url="https://override.example",
                token="override-token",
            )

        assert mock_post.call_args.args[0] == "https://override.example/api/events/external"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer override-token"
