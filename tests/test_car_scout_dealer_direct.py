"""Unit tests for workflows.car_scout.sources.dealer_direct.

The scraper hits three Bellingham dealers via Bright Data to close
MarketCheck's coverage gap on Subaru trade-ins at non-Subaru dealers.
Tests use fixture HTML to avoid any network traffic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from workflows.car_scout.sources.dealer_direct import (
    DEALERS,
    DealerDirectScraper,
    _parse_dealercom,
    _parse_dealerinspire,
    _parse_jazel,
)


# ─── Fixture HTML — shaped to match real-world output ────────────────────────

JAZEL_FIXTURE = """
<html><body>
<div class="vehicle-card">
  <a href="/vehicle/JF2GTACC3L1234567/Used-2020-Subaru-Crosstrek-Premium-AWD">
    <h3>2020 Subaru Crosstrek Premium AWD</h3>
    <span class="price">$23,995</span>
    <span class="mileage">52,123 miles</span>
  </a>
</div>
<div class="vehicle-card">
  <a href="/vehicle/JF1GPAR60F1234567/Used-2015-Subaru-Impreza-Limited">
    <h3>2015 Subaru Impreza Limited</h3>
    <span class="price">$14,500</span>
    <span class="mileage">105,200 mi</span>
  </a>
</div>
<div class="vehicle-card">
  <a href="/vehicle/1FMCU0GD5JU123456/Used-2018-Ford-Escape-SE">
    <h3>2018 Ford Escape SE</h3>
    <span class="price">$18,995</span>
    <span class="mileage">78,450 miles</span>
  </a>
</div>
</body></html>
"""

DEALERINSPIRE_FIXTURE = """
<html><body>
<a href="/used/Subaru/2019-Subaru-Forester-abc123/">
  <div class="vehicle-card">
    <span>2019 Subaru Forester Limited</span>
    <span>Used Subaru</span>
    <span>VIN: JF2SKAJC7K1234567</span>
    <span>$22,450</span>
    <span>65,200 miles</span>
  </div>
</a>
</body></html>
"""

DEALERCOM_FIXTURE = """
<html><body>
<div data-vin="JF1GPAR60F1234567" class="inventory-listing">
  <h2>2017 Subaru Outback 2.5i Limited</h2>
  <a href="/used-inventory/2017-subaru-outback/">View Details</a>
  <span class="price">$19,900</span>
  <span class="mileage">82,000 miles</span>
</div>
<div data-vin="JF2GTACC3L7654321" class="inventory-listing">
  <h2>2020 Subaru Crosstrek Sport</h2>
  <a href="/used-inventory/2020-subaru-crosstrek/">View Details</a>
  <span class="price">$26,500</span>
  <span class="mileage">42,000 miles</span>
</div>
</body></html>
"""

SKELETON_FIXTURE = """
<html><body>
<div class="vehicle-card-skeleton-grid">Loading...</div>
</body></html>
"""


# ─── Parser tests ────────────────────────────────────────────────────────────


def _dealer_by_name(name: str):
    for d in DEALERS:
        if d.name == name:
            return d
    raise KeyError(name)


class TestParseJazel:
    def test_extracts_subaru_crosstrek(self):
        dealer = _dealer_by_name("bellingham-ford")
        listings = _parse_jazel(JAZEL_FIXTURE, dealer, "Crosstrek")
        assert len(listings) == 1
        l = listings[0]
        assert l.make == "Subaru"
        assert l.model == "Crosstrek"
        assert l.year == 2020
        assert l.price == 23995
        assert l.mileage == 52123
        assert l.vin == "JF2GTACC3L1234567"
        assert l.source == "dealer_direct"
        assert l.tier == "primary"
        assert l.dealer_name == "Bellingham Ford"

    def test_filters_out_non_subaru_cards(self):
        """Ford Escape in the fixture must NOT come through when we asked for Crosstrek."""
        dealer = _dealer_by_name("bellingham-ford")
        listings = _parse_jazel(JAZEL_FIXTURE, dealer, "Crosstrek")
        assert all(l.model == "Crosstrek" for l in listings)

    def test_extracts_subaru_impreza(self):
        dealer = _dealer_by_name("bellingham-ford")
        listings = _parse_jazel(JAZEL_FIXTURE, dealer, "Impreza")
        assert len(listings) == 1
        assert listings[0].model == "Impreza"
        assert listings[0].year == 2015
        assert listings[0].mileage == 105200

    def test_empty_html_returns_empty(self):
        dealer = _dealer_by_name("bellingham-ford")
        assert _parse_jazel("", dealer, "Crosstrek") == []


class TestParseDealerInspire:
    def test_extracts_forester(self):
        dealer = _dealer_by_name("toyota-of-bellingham")
        listings = _parse_dealerinspire(DEALERINSPIRE_FIXTURE, dealer, "Forester")
        assert len(listings) == 1
        l = listings[0]
        assert l.make == "Subaru"
        assert l.model == "Forester"
        assert l.year == 2019
        assert l.price == 22450
        assert l.vin == "JF2SKAJC7K1234567"
        assert l.source == "dealer_direct"
        assert l.dealer_name == "Toyota of Bellingham"

    def test_skeleton_html_returns_empty(self):
        """If Bright Data doesn't render JS, we get a skeleton — 0 listings, no crash."""
        dealer = _dealer_by_name("toyota-of-bellingham")
        assert _parse_dealerinspire(SKELETON_FIXTURE, dealer, "Forester") == []


class TestParseDealerCom:
    def test_extracts_outback(self):
        dealer = _dealer_by_name("audi-bellingham")
        listings = _parse_dealercom(DEALERCOM_FIXTURE, dealer, "Outback")
        assert len(listings) == 1
        l = listings[0]
        assert l.model == "Outback"
        assert l.year == 2017
        assert l.vin == "JF1GPAR60F1234567"
        assert l.source == "dealer_direct"
        assert l.dealer_name == "Audi Bellingham"

    def test_extracts_crosstrek_too(self):
        dealer = _dealer_by_name("audi-bellingham")
        listings = _parse_dealercom(DEALERCOM_FIXTURE, dealer, "Crosstrek")
        assert len(listings) == 1
        assert listings[0].model == "Crosstrek"


# ─── Scraper behavior ───────────────────────────────────────────────────────


class TestDealerDirectScraper:
    def test_fetch_failures_do_not_crash_cycle(self):
        """If Bright Data throws on one URL, the scraper continues to the next."""
        from lib.scraping import BrightDataFetchError

        client = MagicMock()
        client.fetch.side_effect = BrightDataFetchError("502 bad gateway")

        scraper = DealerDirectScraper(
            client,
            zip_code="98225",
            radius_mi=0,
            budget_ceiling=0,
            year_floor=0,
        )
        result = scraper.scrape()
        # 3 dealers × 4 primary Subaru models = 12 attempted fetches; all fail
        assert len(result.errors) == 12
        assert result.listings == []
        # No exception bubbled up — that's the point

    def test_mixed_success_and_failure(self):
        """If one dealer's URL works and another fails, we keep the working one's output."""
        client = MagicMock()

        def fake_fetch(url: str) -> str:
            if "bellinghamford.com" in url and "Crosstrek" in url:
                return JAZEL_FIXTURE
            if "bellinghamford.com" in url and "Impreza" in url:
                return JAZEL_FIXTURE
            return ""

        client.fetch.side_effect = fake_fetch
        scraper = DealerDirectScraper(
            client,
            zip_code="98225",
            radius_mi=0,
            budget_ceiling=0,
            year_floor=0,
        )
        result = scraper.scrape()
        assert len(result.listings) >= 1
        assert all(l.make == "Subaru" for l in result.listings)

    def test_source_name_is_dealer_direct(self):
        """For Modal logging + summary reporting."""
        scraper = DealerDirectScraper(
            MagicMock(),
            zip_code="98225",
            radius_mi=0,
            budget_ceiling=0,
            year_floor=0,
        )
        assert scraper.name == "dealer_direct"

    def test_zero_listings_emits_warning_log(self, caplog):
        """v1.2: a successful fetch that yields 0 listings must log at WARNING.

        This makes the "Dealer.com JS shell returns no inventory" silent-zero
        case visible in Modal logs. Previously logged at INFO and got lost.
        """
        import logging

        client = MagicMock()
        # Return a valid HTML body that has no Subaru cards at all
        client.fetch.return_value = (
            "<html><body><h1>Inventory</h1><p>No vehicles match your search.</p></body></html>"
        )
        scraper = DealerDirectScraper(
            client,
            zip_code="98225",
            radius_mi=0,
            budget_ceiling=0,
            year_floor=0,
        )

        with caplog.at_level(logging.WARNING, logger="car-scout.dealer-direct"):
            result = scraper.scrape()

        # All 12 dealer×model combos should warn (every one returned 0 listings)
        zero_listing_warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "dealer_direct_zero_listings" in r.message
        ]
        assert len(zero_listing_warnings) >= 1
        assert result.listings == []
