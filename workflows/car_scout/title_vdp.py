"""VDP (Vehicle Detail Page) title verification via Bright Data.

Per-listing HTML scan for title-brand keywords. Cached forever per VIN —
title status doesn't change on a listing, so one verification survives
all subsequent scout runs.

Return values:
- "branded"  — VDP explicitly advertises rebuilt/salvage/reconstructed/etc.
- "clean"    — VDP explicitly advertises clean title OR no owner/accident flags
- "unknown"  — fetch succeeded but couldn't find any title language

Integration: called from main._run_digest after MarketCheck merge, before
scoring. Listings marked "branded" get added to the hard-reject set for this
run and all future runs.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Iterable, Literal

from bs4 import BeautifulSoup

from lib.logger import get_logger
from lib.scraping import BrightDataClient, BrightDataConfig, BrightDataFetchError

logger = get_logger("car-scout.title-vdp")

TitleVerdict = Literal["branded", "clean", "unknown"]

# Hard-reject phrases. Word-boundary safe (avoid matching "rebuilt into a
# Subaru" or other false positives). All case-insensitive.
BRANDED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brebuilt\s+title\b", re.IGNORECASE),
    re.compile(r"\bsalvage\s+title\b", re.IGNORECASE),
    re.compile(r"\bbranded\s+title\b", re.IGNORECASE),
    re.compile(r"\btitle\s+brand(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\breconstructed\s+title\b", re.IGNORECASE),
    re.compile(r"\bprior\s+salvage\b", re.IGNORECASE),
    re.compile(r"\bflood\s+title\b", re.IGNORECASE),
    re.compile(r"\bfire\s+damage\s+title\b", re.IGNORECASE),
    re.compile(r"\blemon\s+title\b", re.IGNORECASE),
    re.compile(r"\bbuyback\s+title\b", re.IGNORECASE),
    # Standalone word forms — looser but still bounded
    re.compile(r"\brebuilt\b", re.IGNORECASE),
    re.compile(r"\bsalvage\b", re.IGNORECASE),
    re.compile(r"\breconstructed\b", re.IGNORECASE),
)

CLEAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bclean\s+title\b", re.IGNORECASE),
    re.compile(r"\bno\s+accidents\s+reported\b", re.IGNORECASE),
    re.compile(r"\bcarfax\s+one.?owner\b", re.IGNORECASE),
    re.compile(r"\baccident\s*free\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class VdpScanResult:
    """Outcome of a single VDP verification attempt."""

    verdict: TitleVerdict
    matched_branded_phrase: str | None
    matched_clean_phrase: str | None
    fetch_error: str | None
    html_size: int


def _extract_text(html: str) -> str:
    """Pull visible text from HTML. Fallback to raw HTML if BS parsing fails."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        # Drop script and style tags — they sometimes contain the word "rebuilt"
        # in JS code (e.g., rebuild functions) that would false-positive
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)
    except Exception:  # noqa: BLE001 — any parser failure falls back to raw
        return html


def scan_html_for_title(html: str) -> VdpScanResult:
    """Pure function: inspect HTML → verdict. No network calls."""
    text = _extract_text(html)

    # Brand check wins: if we see ANY branded phrase, listing is rejected.
    for pattern in BRANDED_PATTERNS:
        m = pattern.search(text)
        if m:
            return VdpScanResult(
                verdict="branded",
                matched_branded_phrase=m.group(0),
                matched_clean_phrase=None,
                fetch_error=None,
                html_size=len(html),
            )

    # Positive confirmation
    for pattern in CLEAN_PATTERNS:
        m = pattern.search(text)
        if m:
            return VdpScanResult(
                verdict="clean",
                matched_branded_phrase=None,
                matched_clean_phrase=m.group(0),
                fetch_error=None,
                html_size=len(html),
            )

    # Fell through — neither branded nor clean language found
    return VdpScanResult(
        verdict="unknown",
        matched_branded_phrase=None,
        matched_clean_phrase=None,
        fetch_error=None,
        html_size=len(html),
    )


def verify_title_via_vdp(
    url: str,
    client: BrightDataClient,
) -> VdpScanResult:
    """Fetch the VDP and scan for title language. Network + parse in one call.

    Never raises — fetch errors are captured in the result.fetch_error field
    with verdict="unknown" so the caller can decide whether to admit/reject
    unknown-status listings.
    """
    try:
        html = client.fetch(url)
    except BrightDataFetchError as exc:
        logger.warning(
            "vdp_fetch_failed",
            extra={"url": url, "error": str(exc)[:200]},
        )
        return VdpScanResult(
            verdict="unknown",
            matched_branded_phrase=None,
            matched_clean_phrase=None,
            fetch_error=str(exc)[:200],
            html_size=0,
        )

    result = scan_html_for_title(html)
    logger.info(
        "vdp_scan_complete",
        extra={
            "url": url,
            "verdict": result.verdict,
            "branded_match": result.matched_branded_phrase,
            "clean_match": result.matched_clean_phrase,
            "html_size": result.html_size,
        },
    )
    return result


def verify_titles_parallel(
    urls: Iterable[str],
    *,
    config: BrightDataConfig | None = None,
    max_workers: int = 10,
    per_request_timeout_s: float = 12.0,
) -> dict[str, VdpScanResult]:
    """Run VDP verifications in parallel. Short timeouts + low retry count —
    slow dealer sites are expected; we don't block the digest on one bad URL.

    Each worker gets its own BrightDataClient (httpx isn't thread-safe for
    sharing a client). Total wall time with 5 workers and 20 URLs: ~5-15s.
    """
    cfg = config or BrightDataConfig.from_env()
    # Tighten timeouts + retries for parallel mode — the outer digest runs
    # on a 5-min cron budget, we'd rather mark slow URLs "unknown" than wait.
    tuned = BrightDataConfig(
        zone=cfg.zone,
        username=cfg.username,
        password=cfg.password,
        proxy_host=cfg.proxy_host,
        timeout_s=per_request_timeout_s,
        max_retries=1,  # skip exponential backoff for VDP verify
    )

    def _one(url: str) -> tuple[str, VdpScanResult]:
        client = BrightDataClient(config=tuned)
        try:
            return url, verify_title_via_vdp(url, client)
        finally:
            client.close()

    results: dict[str, VdpScanResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_one, url): url for url in urls}
        for fut in as_completed(futures):
            url, result = fut.result()
            results[url] = result

    return results
