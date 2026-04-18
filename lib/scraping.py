"""Bright Data Web Unlocker client — reusable scraping primitive for AptoFlow workflows.

Routes HTTP requests through Bright Data's residential proxy, which transparently
handles Cloudflare/Akamai JS challenges, CAPTCHAs, and IP rotation. Bring your own
BRIGHTDATA_ZONE / BRIGHTDATA_USERNAME / BRIGHTDATA_PASSWORD from the Bright Data
dashboard (Proxies & Scraping Infra → your Unlocker zone).

Usage:

    from lib.scraping import BrightDataClient

    client = BrightDataClient()  # reads env vars
    html = client.fetch("https://www.cargurus.com/Cars/inventorylisting/...")

Why a class and not just a function:
- Workflows typically do 10s-100s of fetches per run; one client reuses HTTP
  connection pool and amortizes config validation.
- Per-workflow override of zone / proxy / retry policy without global state.

Why httpx:
- Already an AptoFlow dep.
- Built-in HTTP/2 support (faster than requests for many small fetches).
- Async-capable if a workflow later wants parallel scraping.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass

import httpx

from lib.logger import get_logger

logger = get_logger("lib.scraping")

DEFAULT_PROXY_HOST = "brd.superproxy.io:33335"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class BrightDataConfigError(Exception):
    """Raised when required Bright Data env vars are missing."""


class BrightDataFetchError(Exception):
    """Raised when a fetch fails after all retries."""


@dataclass(frozen=True)
class BrightDataConfig:
    """Resolved credentials + connection config for a Bright Data Unlocker zone."""

    zone: str
    username: str
    password: str
    proxy_host: str = DEFAULT_PROXY_HOST
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES

    @classmethod
    def from_env(cls) -> BrightDataConfig:
        """Build config from BRIGHTDATA_* env vars. Raises if anything required missing."""
        zone = os.environ.get("BRIGHTDATA_ZONE", "").strip()
        username = os.environ.get("BRIGHTDATA_USERNAME", "").strip()
        password = os.environ.get("BRIGHTDATA_PASSWORD", "").strip()

        missing = [
            name
            for name, val in (
                ("BRIGHTDATA_ZONE", zone),
                ("BRIGHTDATA_USERNAME", username),
                ("BRIGHTDATA_PASSWORD", password),
            )
            if not val
        ]
        if missing:
            raise BrightDataConfigError(
                f"Missing required Bright Data env vars: {', '.join(missing)}"
            )

        return cls(
            zone=zone,
            username=username,
            password=password,
            proxy_host=os.environ.get("BRIGHTDATA_PROXY_HOST", DEFAULT_PROXY_HOST),
            timeout_s=float(os.environ.get("BRIGHTDATA_TIMEOUT_S", DEFAULT_TIMEOUT_S)),
            max_retries=int(os.environ.get("BRIGHTDATA_MAX_RETRIES", DEFAULT_MAX_RETRIES)),
        )

    def proxy_url(self) -> str:
        """Assemble the HTTPS proxy URL including auth."""
        return f"http://{self.username}:{self.password}@{self.proxy_host}"


class BrightDataClient:
    """Thin wrapper that proxies httpx.get through Bright Data Web Unlocker.

    Each instance owns an httpx.Client with connection pooling configured for
    the proxy. Pass a pre-built BrightDataConfig for tests; otherwise the
    default constructor reads from env vars.
    """

    def __init__(
        self,
        config: BrightDataConfig | None = None,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.config = config or BrightDataConfig.from_env()
        self.user_agent = user_agent
        self._client = httpx.Client(
            proxy=self.config.proxy_url(),
            timeout=self.config.timeout_s,
            follow_redirects=True,
            verify=False,  # Bright Data uses a self-signed cert on the proxy
        )

    def __enter__(self) -> BrightDataClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch(
        self,
        url: str,
        *,
        extra_headers: dict[str, str] | None = None,
        country: str | None = None,
    ) -> str:
        """Fetch a URL via the Unlocker proxy. Returns the body as text.

        `country`: optional two-letter ISO country code to pin the exit IP to
        (e.g. "us"). Bright Data interprets as zone-country routing.

        Raises BrightDataFetchError after all retries exhausted.
        """
        headers: dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if extra_headers:
            headers.update(extra_headers)
        if country:
            # Bright Data Unlocker: country override via header
            headers["x-brd-country"] = country

        last_exc: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                resp = self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "brightdata_network_error",
                    extra={"url": url, "attempt": attempt, "error": str(exc)},
                )
            else:
                if 200 <= resp.status_code < 300:
                    return resp.text

                logger.warning(
                    "brightdata_bad_status",
                    extra={
                        "url": url,
                        "attempt": attempt,
                        "status": resp.status_code,
                        "body_preview": resp.text[:200],
                    },
                )
                last_exc = BrightDataFetchError(
                    f"Bright Data returned {resp.status_code} for {url}"
                )
                # Don't retry 4xx except 408/429 (rate limit / timeout)
                if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                    break

            # Exponential backoff with jitter
            if attempt < self.config.max_retries:
                backoff_s = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(backoff_s)

        assert last_exc is not None  # loop guarantees this
        raise BrightDataFetchError(
            f"Failed to fetch {url} after {self.config.max_retries} attempts: {last_exc}"
        ) from last_exc
