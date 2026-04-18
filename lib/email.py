"""Resend API client — reusable transactional-email primitive for AptoFlow workflows.

Uses Resend's HTTPS REST API (not SMTP). Much cleaner than Gmail-SMTP-via-app-
password: no 2FA juggling, no mailbox needed, no seat cost. Sender domain just
needs a one-time SPF + DKIM DNS verification on Resend's dashboard.

Docs: https://resend.com/docs

Usage:

    from lib.email import ResendClient

    client = ResendClient()  # reads RESEND_API_KEY from env
    client.send(
        from_address="alfred@aptoworks.com",
        to="nick@aptoworks.com",
        subject="Hello",
        html="<p>Hi</p>",
        text="Hi",
    )
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from lib.logger import get_logger

logger = get_logger("lib.email")

DEFAULT_BASE_URL = "https://api.resend.com"
DEFAULT_TIMEOUT_S = 20.0


class ResendConfigError(Exception):
    """Raised when RESEND_API_KEY is missing."""


class ResendSendError(Exception):
    """Raised when a send call fails."""


@dataclass(frozen=True)
class SendResult:
    """Successful-send payload — id from Resend for deliverability tracking."""

    id: str


class ResendClient:
    """Thin wrapper around Resend's /emails endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.api_key = (api_key or os.environ.get("RESEND_API_KEY", "")).strip()
        if not self.api_key:
            raise ResendConfigError("RESEND_API_KEY env var is required")
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_s)

    def __enter__(self) -> ResendClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def send(
        self,
        *,
        from_address: str,
        to: str | list[str],
        subject: str,
        html: str,
        text: str | None = None,
        reply_to: str | list[str] | None = None,
    ) -> SendResult:
        """Send one email. Raises ResendSendError on non-2xx / network failure."""
        payload: dict[str, object] = {
            "from": from_address,
            "to": to if isinstance(to, list) else [to],
            "subject": subject,
            "html": html,
        }
        if text is not None:
            payload["text"] = text
        if reply_to is not None:
            payload["reply_to"] = reply_to if isinstance(reply_to, list) else [reply_to]

        url = f"{self.base_url}/emails"
        try:
            resp = self._client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise ResendSendError(f"Resend request failed: {exc}") from exc

        if resp.status_code >= 300:
            logger.error(
                "resend_bad_status",
                extra={"status": resp.status_code, "body_preview": resp.text[:300]},
            )
            raise ResendSendError(
                f"Resend returned {resp.status_code}: {resp.text[:300]}"
            )

        body = resp.json()
        email_id = body.get("id", "")
        logger.info(
            "resend_send_ok",
            extra={"id": email_id, "to": to, "subject": subject},
        )
        return SendResult(id=email_id)
