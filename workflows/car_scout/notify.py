"""Pennyworth external-event notifier for car_scout.

POSTs to Pennyworth's /api/events/external endpoint using the existing
AOL_API_TOKEN bearer. Pennyworth routes the event through SMS + push + feed.

Car_scout calls this ONLY for unicorn matches. Normal digest email goes
through SMTP, not through Pennyworth.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from lib.logger import get_logger

logger = get_logger("car-scout.notify")


class PennyworthNotifyError(Exception):
    """Raised when the Pennyworth external-event POST fails."""


def notify_unicorn(
    title: str,
    body: str,
    url: str,
    data: dict[str, Any] | None = None,
    *,
    base_url: str | None = None,
    token: str | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """Fire a unicorn alert to Pennyworth. Returns the NotifyResult dict.

    Raises PennyworthNotifyError on any non-2xx response or network failure.
    The caller is expected to handle retry / rate-limit decisions.
    """
    base = base_url or os.environ.get("PENNYWORTH_BASE_URL", "https://pw.aptoworks.cloud").rstrip("/")
    bearer = token or os.environ.get("AOL_API_TOKEN")
    if not bearer:
        raise PennyworthNotifyError(
            "AOL_API_TOKEN not set — cannot authenticate to Pennyworth"
        )

    payload: dict[str, Any] = {"title": title, "body": body, "url": url}
    if data is not None:
        payload["data"] = data

    endpoint = f"{base}/api/events/external"
    try:
        resp = httpx.post(
            endpoint,
            json=payload,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        logger.error("pennyworth_request_failed", extra={"error": str(exc)})
        raise PennyworthNotifyError(f"Pennyworth request failed: {exc}") from exc

    if resp.status_code >= 300:
        logger.error(
            "pennyworth_bad_status",
            extra={"status": resp.status_code, "body": resp.text[:200]},
        )
        raise PennyworthNotifyError(
            f"Pennyworth returned {resp.status_code}: {resp.text[:200]}"
        )

    result = resp.json()
    logger.info(
        "unicorn_sms_dispatched",
        extra={
            "notification_id": result.get("notificationId"),
            "sms_sent": result.get("sms"),
            "pushed": result.get("pushed"),
        },
    )
    return result


def format_unicorn_sms(
    year: int,
    make: str,
    model: str,
    trim: str | None,
    mileage: int,
    price: int,
    delta_pct: float,
    dealer_or_city: str,
    short_url: str,
) -> tuple[str, str]:
    """Format the SMS title+body pair. Keeps total under ~160 chars when rendered."""
    trim_part = f" {trim}" if trim else ""
    title = f"🏆 Unicorn: {year} {make} {model}{trim_part}"
    body = (
        f"{mileage:,} mi, ${price:,} @ {dealer_or_city}"
        f" — {delta_pct:.0f}% below market"
    )
    return title, body
