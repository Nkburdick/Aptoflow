"""Authentication and rate limiting for webhook endpoints."""

import os
import time
from collections import defaultdict

from dotenv import load_dotenv
from fastapi import HTTPException, Request

load_dotenv()


def verify_bearer_token(authorization: str | None = None) -> str:
    """Verify a Bearer token against MODAL_BEARER_TOKEN.

    Designed for use with FastAPI Depends().

    Args:
        authorization: The Authorization header value.

    Returns:
        The validated token.

    Raises:
        HTTPException: 401 if token is missing/invalid, 500 if server token not configured.
    """
    expected = os.environ.get("MODAL_BEARER_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="Server bearer token not configured")

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")

    if parts[1] != expected:
        raise HTTPException(status_code=401, detail="Invalid token")

    return parts[1]


class RateLimiter:
    """In-memory per-IP rate limiter.

    Args:
        max_requests: Maximum requests allowed per window.
        window_seconds: Time window in seconds.
    """

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, request: Request) -> None:
        """Check if the request is within rate limits.

        Args:
            request: FastAPI request object.

        Raises:
            HTTPException: 429 if rate limit exceeded.
        """
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean old entries
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if t > cutoff
        ]

        if len(self._requests[client_ip]) >= self.max_requests:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        self._requests[client_ip].append(now)
