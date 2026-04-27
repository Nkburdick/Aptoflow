"""Unit tests for workflows.car_scout.sources.cargurus."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from lib.scraping import BrightDataFetchError
from workflows.car_scout.sources.base import ALL_TARGET_MAKES_MODELS, tier_for
from workflows.car_scout.sources.cargurus import (
    CarGurusScraper,
    _build_query_url,
    _extract_initial_state_json,
    _extract_next_data_json,
    _parse_listing_dict,
    _walk_for_listings,
)


def _raw_listing(**overrides) -> dict:
    base = dict(
        vin="JF2GTAPC9LH232411",
        vdpUrl="/Cars/inventorylisting/link/abc",
        year=2020,
        makeName="Subaru",
        modelName="Crosstrek",
        trim="Premium",
        priceInDollars=19900,
        mileageInMiles=42000,
        transmission="Automatic (CVT)",
        titleStatus="Clean",
        sellerName="Roger Jobs Subaru",
        sellerType="dealer",
        city="Bellingham",
        state="WA",
        dealRating="Great",
        pictureUrls=["https://cdn.example/a.jpg", "https://cdn.example/b.jpg"],
        accidentCount=0,
        ownerCount=1,
    )
    base.update(overrides)
    return base


class TestBuildQueryUrl:
    def test_includes_all_expected_params(self):
        url = _build_query_url(
            zip_code="98225",
            radius_mi=100,
            make="Subaru",
            model="Crosstrek",
            budget_ceiling=22000,
            year_floor=2015,
            page=1,
        )
        assert "zip=98225" in url
        assert "distance=100" in url
        assert "maxPrice=22000" in url
        assert "minYear=2015" in url
        assert "modelFilter=Crosstrek" in url
        assert url.startswith("https://www.cargurus.com/Cars/inventorylisting/")

    def test_includes_pagination(self):
        url = _build_query_url(
            zip_code="98225",
            radius_mi=100,
            make="Subaru",
            model="Crosstrek",
            budget_ceiling=22000,
            year_floor=2015,
            page=3,
        )
        assert "page=3" in url


class TestExtractNextData:
    def test_parses_next_data_script(self):
        payload = {"props": {"listings": [{"vin": "X"}]}}
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        assert _extract_next_data_json(html) == payload

    def test_returns_none_when_absent(self):
        assert _extract_next_data_json("<html><body>hello</body></html>") is None

    def test_returns_none_on_invalid_json(self):
        html = '<script id="__NEXT_DATA__">not-json</script>'
        assert _extract_next_data_json(html) is None


class TestExtractInitialState:
    def test_parses_initial_state(self):
        payload = {"a": 1, "b": [1, 2]}
        html = f'<script>window.__INITIAL_STATE__ = {json.dumps(payload)};</script>'
        assert _extract_initial_state_json(html) == payload

    def test_parses_preloaded_state(self):
        payload = {"ok": True}
        html = f'<script>window.__PRELOADED_STATE__ = {json.dumps(payload)};</script>'
        assert _extract_initial_state_json(html) == payload

    def test_returns_none_when_absent(self):
        assert _extract_initial_state_json("<html></html>") is None


class TestWalkForListings:
    def test_finds_listing_at_top_level(self):
        listing = _raw_listing()
        found: list = []
        _walk_for_listings(listing, found)
        assert len(found) == 1
        assert found[0]["vin"] == "JF2GTAPC9LH232411"

    def test_finds_listings_nested_in_array(self):
        tree = {"props": {"pageProps": {"results": [_raw_listing(vin="A"), _raw_listing(vin="B")]}}}
        found: list = []
        _walk_for_listings(tree, found)
        assert {f["vin"] for f in found} == {"A", "B"}

    def test_ignores_non_listing_dicts(self):
        tree = {"metadata": {"totalCount": 50}, "config": {"foo": "bar"}}
        found: list = []
        _walk_for_listings(tree, found)
        assert found == []


class TestParseListingDict:
    def test_happy_path(self):
        listing = _parse_listing_dict(_raw_listing(), "Subaru", "Crosstrek")
        assert listing is not None
        assert listing.vin == "JF2GTAPC9LH232411"
        assert listing.year == 2020
        assert listing.make == "Subaru"
        assert listing.model == "Crosstrek"
        assert listing.price == 19900
        assert listing.mileage == 42000
        assert listing.transmission == "auto"
        assert listing.title_status == "clean"
        assert listing.cargurus_rating == "Great"
        assert listing.tier == "primary"
        assert listing.dealer_name == "Roger Jobs Subaru"
        assert str(listing.url).startswith("https://www.cargurus.com")
        assert len(listing.photos) == 2
        assert listing.accident_count == 0
        assert listing.owner_count == 1

    def test_rejects_cross_model_bleedthrough(self):
        # CarGurus default sort sometimes returns other Subaru models — must reject
        raw = _raw_listing(modelName="Forester")
        assert _parse_listing_dict(raw, "Subaru", "Crosstrek") is None

    def test_rejects_out_of_scope_model(self):
        # A model not in our tier registry should be dropped
        assert _parse_listing_dict(_raw_listing(), "Subaru", "BRZ") is None

    def test_rejects_missing_url(self):
        raw = _raw_listing()
        del raw["vdpUrl"]
        assert _parse_listing_dict(raw, "Subaru", "Crosstrek") is None

    def test_rejects_missing_price(self):
        raw = _raw_listing()
        del raw["priceInDollars"]
        assert _parse_listing_dict(raw, "Subaru", "Crosstrek") is None

    def test_rejects_missing_mileage(self):
        raw = _raw_listing()
        del raw["mileageInMiles"]
        assert _parse_listing_dict(raw, "Subaru", "Crosstrek") is None

    def test_rejects_missing_year(self):
        raw = _raw_listing()
        del raw["year"]
        assert _parse_listing_dict(raw, "Subaru", "Crosstrek") is None

    def test_prepends_domain_to_relative_url(self):
        raw = _raw_listing(vdpUrl="/Cars/link/xyz")
        listing = _parse_listing_dict(raw, "Subaru", "Crosstrek")
        assert listing is not None
        assert str(listing.url) == "https://www.cargurus.com/Cars/link/xyz"

    def test_transmission_parsing(self):
        auto = _parse_listing_dict(_raw_listing(transmission="6-Speed Automatic"), "Subaru", "Crosstrek")
        assert auto and auto.transmission == "auto"

        manual = _parse_listing_dict(_raw_listing(transmission="6-Speed Manual"), "Subaru", "Crosstrek")
        assert manual and manual.transmission == "manual"

        unknown = _parse_listing_dict(_raw_listing(transmission=""), "Subaru", "Crosstrek")
        assert unknown and unknown.transmission == "unknown"

    def test_title_salvage_rejected_via_downstream_but_parseable(self):
        # Parser itself sets title_status; hard filter happens later in scoring pipeline
        listing = _parse_listing_dict(
            _raw_listing(titleStatus="Salvage"), "Subaru", "Crosstrek"
        )
        assert listing is not None
        assert listing.title_status == "salvage"

    def test_invalid_cargurus_rating_becomes_none(self):
        listing = _parse_listing_dict(
            _raw_listing(dealRating="SUPER_MEGA_DEAL"), "Subaru", "Crosstrek"
        )
        assert listing is not None
        assert listing.cargurus_rating is None

    def test_photos_from_dict_list(self):
        raw = _raw_listing(pictureUrls=[{"url": "https://a.example/1.jpg"}])
        listing = _parse_listing_dict(raw, "Subaru", "Crosstrek")
        assert listing is not None
        assert len(listing.photos) == 1
        assert str(listing.photos[0]) == "https://a.example/1.jpg"

    def test_out_of_scope_make_model_returns_none(self):
        raw = _raw_listing(makeName="Toyota", modelName="RAV4")
        listing = _parse_listing_dict(raw, "Toyota", "RAV4")
        assert listing is None  # No tier for non-Subaru make → listing rejected


def _build_html_with_nextdata(listings: list[dict]) -> str:
    blob = {"props": {"pageProps": {"results": listings}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(blob)}</script>'


class TestCarGurusScraper:
    def _scraper(self, client: MagicMock = None) -> CarGurusScraper:
        return CarGurusScraper(
            client or MagicMock(),
            zip_code="98225",
            radius_mi=100,
            budget_ceiling=22000,
            year_floor=2015,
            max_pages=1,
        )

    def test_scrape_uses_client_fetch(self):
        mock_client = MagicMock()
        mock_client.fetch.return_value = _build_html_with_nextdata([_raw_listing()])
        scraper = self._scraper(mock_client)

        result = scraper.scrape()

        # One fetch per (make, model) pair in the tier registry, max_pages=1
        assert mock_client.fetch.call_count == len(ALL_TARGET_MAKES_MODELS)
        # The Subaru/Crosstrek listing passes; other (make,model) buckets parse 0
        assert result.source_name == "cargurus"
        assert len(result.listings) == 1
        assert result.listings[0].make == "Subaru"
        assert result.listings[0].model == "Crosstrek"

    def test_parse_page_returns_empty_when_no_blob(self):
        scraper = self._scraper()
        listings = scraper._parse_page("<html><body>no json here</body></html>", "Subaru", "Crosstrek")
        assert listings == []

    def test_scrape_records_fetch_errors_and_continues(self):
        mock_client = MagicMock()

        def flaky_fetch(url: str) -> str:
            if "Crosstrek" in url:
                raise BrightDataFetchError("cloudflare block")
            return _build_html_with_nextdata([_raw_listing(modelName="Forester", makeName="Subaru")])

        mock_client.fetch.side_effect = flaky_fetch
        scraper = self._scraper(mock_client)

        result = scraper.scrape()

        # Crosstrek fetch failed; error recorded
        assert any("Crosstrek" in e for e in result.errors)
        # Forester fetch succeeded with matching make/model — 1 listing makes it through
        forester = [l for l in result.listings if l.model == "Forester"]
        assert len(forester) == 1

    def test_scrape_stops_paginating_on_empty_page(self):
        mock_client = MagicMock()
        mock_client.fetch.return_value = _build_html_with_nextdata([])
        scraper = CarGurusScraper(
            mock_client,
            zip_code="98225",
            radius_mi=100,
            budget_ceiling=22000,
            year_floor=2015,
            max_pages=5,
        )

        scraper.scrape()

        # One fetch per (make, model) pair; should NOT keep paging past empty
        assert mock_client.fetch.call_count == len(ALL_TARGET_MAKES_MODELS)


class TestTierFor:
    def test_primary_subaru(self):
        assert tier_for("Subaru", "Crosstrek") == "primary"
        assert tier_for("Subaru", "Forester") == "primary"

    def test_out_of_scope_returns_none(self):
        # Dropped from the registry — Owen's family preference is lifted-Subaru only
        assert tier_for("Subaru", "Outback") is None
        assert tier_for("Subaru", "Impreza") is None
        assert tier_for("Toyota", "RAV4") is None
        assert tier_for("Honda", "CR-V") is None
        assert tier_for("Mazda", "CX-5") is None
        assert tier_for("Subaru", "BRZ") is None
        assert tier_for("Ford", "Escape") is None
