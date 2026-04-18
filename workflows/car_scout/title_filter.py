"""Title-quality filtering for car_scout listings.

MarketCheck's `carfax_clean_title` flag alone isn't trustworthy — it's False
on ~96% of Crosstrek listings in our region even when the title is clean.
Rebuilt-title specialty dealers exploit this gap: their cars are cheap
enough to pass our scoring but have branded titles.

This module layers deterministic heuristics on top of the weak Carfax flag:

1. Dealer-name blocklist — reject dealers whose name contains rebuilt-specialty
   keywords (e.g., "Premium Spec Auto", "XYZ Salvage", "Wholesale Direct")
2. Heading / listing-text blocklist — reject if the listing advertises
   a rebuilt/salvage title in its own heading
3. Optional VDP fetch — for Top Pick candidates, pull the vehicle detail page
   via Bright Data and confirm "Clean Title" text appears (future V1.1)

The blocklist keywords are intentionally conservative — false positives here
are cheap (one missed legitimate dealer), false negatives are expensive
(a rebuilt car hits Nick's Top Pick email).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Listing

# Case-insensitive substring match against dealer_name or listing heading.
# Ordered by confidence: top entries are near-zero-false-positive.
DEALER_BLOCKLIST_PATTERNS: tuple[str, ...] = (
    # Nick-confirmed rebuilt-title specialty dealers (individual calls)
    "premium spec auto",   # Nick 2026-04-17
    "exelon auto sales",   # Nick 2026-04-18 — three of their listings all rebuilt
    # Self-declaring keywords — no legit dealer puts these in their own name
    "spec auto",           # catches "Top Spec Auto" etc. as well
    "salvage",
    "rebuilt",
    "reconstructed",
    "repairables",
    "branded title",
    "title branded",
    # 2026-04-18: Removed ambiguous patterns ("wholesale", "auction direct",
    # "discount auto", "r&d auto") — VDP scan handles dealers whose names
    # aren't self-declaring. Blocklist grows case-by-case as Nick flags.
)

# Words that, when found in listing heading or description, indicate
# non-clean title or problematic provenance.
LISTING_TEXT_BLOCKLIST: tuple[str, ...] = (
    "rebuilt title",
    "salvage title",
    "salvage",
    "rebuilt",
    "reconstructed",
    "title branded",
    "branded title",
    "prior salvage",
    "title washed",
    "as-is",
    "as is no warranty",
    "mechanic special",
    "needs work",
    "flood damage",
    "hail damage",
    "structural damage",
)

# Known trustworthy dealers — no extra VDP check required, these rarely
# sell branded-title cars. Add to this list as we see legitimate dealers
# in digests that we want to prioritize.
DEALER_ALLOWLIST_SUBSTRINGS: tuple[str, ...] = (
    "roger jobs",        # Bellingham Subaru
    "dewey griffin",     # Bellingham Subaru
    "wilson toyota",     # Bellingham
    "honda of bellingham",
    "northwest honda",
    "carvana",
    "carmax",
    "enterprise car sales",
    "hertz car sales",
    "subaru of",         # e.g., "Subaru of Spokane", "Subaru of Seattle"
    "toyota of",
    "honda of",
    "mazda of",
)


@dataclass(frozen=True)
class TitleFilterDecision:
    """Outcome of running the title-filter heuristics against a listing."""

    passes: bool
    reasons: list[str]
    dealer_trust_tier: str  # "allowlist" | "default" | "blocked"


def _matches_any(haystack: str | None, patterns: tuple[str, ...]) -> str | None:
    """Return the first pattern that matches (case-insensitive substring), else None."""
    if not haystack:
        return None
    low = haystack.lower()
    for pat in patterns:
        if pat in low:
            return pat
    return None


def evaluate_title(listing: Listing) -> TitleFilterDecision:
    """Apply deterministic title-quality filters.

    Order of checks:
      1. Explicit branded title_status from the source (salvage/rebuilt)
      2. Dealer-name blocklist
      3. Listing-heading / description blocklist
      4. Allowlist boost (sets dealer_trust_tier)
      5. Default pass
    """
    reasons: list[str] = []

    # (1) Source already told us — trust it
    if listing.title_status in ("salvage", "rebuilt"):
        reasons.append(f"source_title_status={listing.title_status}")
        return TitleFilterDecision(passes=False, reasons=reasons, dealer_trust_tier="blocked")

    # (2) Dealer-name blocklist
    dealer_hit = _matches_any(listing.dealer_name, DEALER_BLOCKLIST_PATTERNS)
    if dealer_hit:
        reasons.append(f"dealer_blocklist={dealer_hit!r}")
        return TitleFilterDecision(passes=False, reasons=reasons, dealer_trust_tier="blocked")

    # (3) Listing-text blocklist
    heading_hit = _matches_any(listing.description, LISTING_TEXT_BLOCKLIST)
    if heading_hit:
        reasons.append(f"description_blocklist={heading_hit!r}")
        return TitleFilterDecision(passes=False, reasons=reasons, dealer_trust_tier="blocked")

    # (4) Allowlist boost
    tier = "default"
    allow_hit = _matches_any(listing.dealer_name, DEALER_ALLOWLIST_SUBSTRINGS)
    if allow_hit:
        tier = "allowlist"
        reasons.append(f"dealer_allowlist={allow_hit!r}")
    else:
        reasons.append("default_dealer_tier")

    return TitleFilterDecision(passes=True, reasons=reasons, dealer_trust_tier=tier)


def is_trusted_dealer(dealer_name: str | None) -> bool:
    """Convenience wrapper — True if dealer_name matches the allowlist."""
    return _matches_any(dealer_name, DEALER_ALLOWLIST_SUBSTRINGS) is not None
