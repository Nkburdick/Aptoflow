"""Unit tests for workflows.car_scout.title_filter."""

from __future__ import annotations

import pytest

from workflows.car_scout.models import Listing
from workflows.car_scout.title_filter import (
    DEALER_ALLOWLIST_SUBSTRINGS,
    DEALER_BLOCKLIST_PATTERNS,
    LISTING_TEXT_BLOCKLIST,
    evaluate_title,
    is_trusted_dealer,
)


def _listing(**overrides) -> Listing:
    base = dict(
        url="https://x.example/a",
        vin="VIN-A",
        source="marketcheck",
        year=2020,
        make="Subaru",
        model="Crosstrek",
        trim="Premium",
        transmission="auto",
        mileage=42000,
        price=19900,
        title_status="unknown",
        tier="primary",
        dealer_name="Roger Jobs Subaru",
    )
    base.update(overrides)
    return Listing(**base)


class TestEvaluateTitle:
    def test_clean_listing_from_trusted_dealer_passes(self):
        decision = evaluate_title(_listing())
        assert decision.passes is True
        assert decision.dealer_trust_tier == "allowlist"
        assert any("roger jobs" in r for r in decision.reasons)

    def test_default_dealer_passes_as_default_tier(self):
        decision = evaluate_title(_listing(dealer_name="Bob's Auto Sales"))
        assert decision.passes is True
        assert decision.dealer_trust_tier == "default"

    def test_source_salvage_rejected(self):
        decision = evaluate_title(_listing(title_status="salvage"))
        assert decision.passes is False
        assert any("salvage" in r for r in decision.reasons)

    def test_source_rebuilt_rejected(self):
        decision = evaluate_title(_listing(title_status="rebuilt"))
        assert decision.passes is False

    def test_premium_spec_auto_dealer_rejected(self):
        """Nick-flagged 2026-04-17 — rebuilt-title specialty dealer."""
        decision = evaluate_title(_listing(dealer_name="Premium Spec Auto"))
        assert decision.passes is False
        assert decision.dealer_trust_tier == "blocked"
        assert any("premium spec auto" in r for r in decision.reasons)

    def test_dealer_blocklist_case_insensitive(self):
        for name in ("PREMIUM SPEC AUTO", "premium spec auto LLC", "My Premium Spec Auto Inc"):
            decision = evaluate_title(_listing(dealer_name=name))
            assert decision.passes is False, f"{name} should be blocked"

    @pytest.mark.parametrize(
        "dealer_name",
        [
            "Seattle Salvage Auto",
            "Rebuilt Rides NW",
            "Reconstructed Cars Co",
            "Wholesale Direct",
            "Auction Direct Motors",
        ],
    )
    def test_other_blocklist_dealers_rejected(self, dealer_name):
        decision = evaluate_title(_listing(dealer_name=dealer_name))
        assert decision.passes is False

    def test_listing_description_blocklist(self):
        l = _listing(description="Beautiful car with rebuilt title, runs great")
        decision = evaluate_title(l)
        assert decision.passes is False
        assert any("rebuilt title" in r for r in decision.reasons)

    def test_mechanic_special_description_rejected(self):
        l = _listing(description="Mechanic special — needs work on transmission")
        decision = evaluate_title(l)
        assert decision.passes is False

    def test_legit_description_admitted(self):
        l = _listing(description="Well-maintained, non-smoker, all service records available")
        decision = evaluate_title(l)
        assert decision.passes is True

    def test_allowlist_overrides_default_only(self):
        """Allowlist is an informational tier — it does NOT overrule the blocklist."""
        # Hypothetical: a dealer name with both "Subaru of" and "salvage"
        decision = evaluate_title(_listing(dealer_name="Subaru of Salvage City"))
        # Blocklist wins — this is intentional. Allowlist only boosts trust,
        # it does NOT override a hard reject.
        assert decision.passes is False

    @pytest.mark.parametrize(
        "dealer",
        [
            "Dewey Griffin Subaru",
            "Wilson Toyota of Bellingham",
            "Honda of Bellingham",
            "Carvana Inc.",
            "CarMax Seattle",
            "Subaru of Spokane",
        ],
    )
    def test_allowlist_dealers_tier(self, dealer):
        decision = evaluate_title(_listing(dealer_name=dealer))
        assert decision.passes is True
        assert decision.dealer_trust_tier == "allowlist"


class TestIsTrustedDealer:
    def test_allowlist_hit(self):
        assert is_trusted_dealer("Roger Jobs Subaru") is True

    def test_not_allowlisted(self):
        assert is_trusted_dealer("Bob's Random Auto") is False

    def test_none_returns_false(self):
        assert is_trusted_dealer(None) is False
