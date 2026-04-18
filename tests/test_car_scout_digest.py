"""Unit tests for workflows.car_scout.digest."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from workflows.car_scout.digest import (
    DigestPayload,
    DigestSendError,
    _recent_price_drop,
    _to_card,
    assemble_digest,
    compose_subject,
    render_digest_html,
    render_digest_plaintext,
    send_digest,
)
from workflows.car_scout.models import Listing, PriceObservation, Score, WorkflowState


NOW = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)


def _listing(**overrides) -> Listing:
    base = dict(
        url="https://www.cargurus.com/Cars/link/a",
        vin="VIN-A",
        source="cargurus",
        year=2020,
        make="Subaru",
        model="Crosstrek",
        trim="Premium",
        transmission="auto",
        mileage=42000,
        price=19900,
        title_status="clean",
        tier="primary",
        first_seen=NOW - timedelta(hours=3),
        last_seen=NOW,
        price_history=[PriceObservation(timestamp=NOW, price=19900)],
    )
    base.update(overrides)
    return Listing(**base)


def _score(**overrides) -> Score:
    base = dict(
        listing_url="https://www.cargurus.com/Cars/link/a",
        computed_at=NOW,
        cargurus_component=100.0,
        market_delta_component=85.0,
        mileage_component=100.0,
        redflag_component=99.0,
        total=90.0,
        band="great",
        reasoning="Great deal — 12% below market median",
        mileage_percentile_for_model_year=20.0,
    )
    base.update(overrides)
    return Score(**base)


class TestAssembleDigest:
    def test_top_picks_sorted_by_score(self):
        state = WorkflowState()
        listings = [
            (_listing(url=f"https://x.example/{i}", vin=f"V{i}"), _score(total=t, listing_url=f"https://x.example/{i}"))
            for i, t in enumerate([72.0, 88.0, 95.0, 80.0])
        ]
        payload = assemble_digest(listings, state, now=NOW)
        assert len(payload.top_picks) == 3
        assert [c.deal_score for c in payload.top_picks] == [95, 88, 80]

    def test_pass_band_dropped(self):
        state = WorkflowState()
        listings = [
            (_listing(vin="A", url="https://x.example/A"), _score(total=40.0, band="pass", listing_url="https://x.example/A")),
            (_listing(vin="B", url="https://x.example/B"), _score(total=75.0, band="good", listing_url="https://x.example/B")),
        ]
        payload = assemble_digest(listings, state, now=NOW)
        urls = {c.url for c in payload.top_picks + payload.new_today}
        assert "https://x.example/A" not in urls
        assert "https://x.example/B" in urls

    def test_secondary_fair_band_dropped(self):
        state = WorkflowState()
        # Secondary tier (RAV4) scoring just "fair" should not surface
        secondary = _listing(
            vin="RAV4",
            url="https://x.example/rav4",
            make="Toyota",
            model="RAV4",
            tier="secondary",
        )
        payload = assemble_digest(
            [(secondary, _score(total=60.0, band="fair", listing_url="https://x.example/rav4"))],
            state,
            now=NOW,
        )
        assert payload.new_today == []

    def test_top_pick_dedupe_against_last_7_days(self):
        state = WorkflowState()
        state.top_picks_last_7_days["https://x.example/old"] = NOW - timedelta(days=2)

        repeat = _listing(vin="R", url="https://x.example/old")
        fresh = _listing(vin="F", url="https://x.example/fresh")
        payload = assemble_digest(
            [
                (repeat, _score(total=96.0, band="unicorn", listing_url="https://x.example/old")),
                (fresh, _score(total=85.0, band="great", listing_url="https://x.example/fresh")),
            ],
            state,
            now=NOW,
        )
        top_urls = [c.url for c in payload.top_picks]
        assert "https://x.example/old" not in top_urls
        assert "https://x.example/fresh" in top_urls

    def test_new_today_excludes_top_picks(self):
        state = WorkflowState()
        high = _listing(vin="H", url="https://x.example/high")
        low = _listing(vin="L", url="https://x.example/low")
        payload = assemble_digest(
            [
                (high, _score(total=95.0, listing_url="https://x.example/high")),
                (low, _score(total=72.0, listing_url="https://x.example/low")),
            ],
            state,
            now=NOW,
        )
        top_urls = {c.url for c in payload.top_picks}
        new_urls = {c.url for c in payload.new_today}
        assert not (top_urls & new_urls)

    def test_old_listings_not_in_new_today(self):
        state = WorkflowState()
        old = _listing(vin="OLD", url="https://x.example/old", first_seen=NOW - timedelta(days=3))
        payload = assemble_digest(
            [(old, _score(total=72.0, band="good", listing_url="https://x.example/old"))],
            state,
            now=NOW,
        )
        assert payload.new_today == []

    def test_price_drops_surfaced_as_own_section_when_score_below_top(self):
        state = WorkflowState()
        state.last_digest_sent = NOW - timedelta(hours=24)

        dropped = _listing(
            vin="D",
            url="https://x.example/d",
            first_seen=NOW - timedelta(days=3),
            price=18000,
            price_history=[
                PriceObservation(timestamp=NOW - timedelta(days=3), price=20000),
                PriceObservation(timestamp=NOW - timedelta(hours=2), price=18000),
            ],
        )
        # Score below top-pick threshold (70) — goes to Price Drops section
        payload = assemble_digest(
            [(dropped, _score(total=65.0, band="fair", listing_url="https://x.example/d"))],
            state,
            now=NOW,
        )
        assert len(payload.price_drops) == 1
        assert payload.price_drops[0].old_price == 20000

    def test_price_drop_on_top_pick_enriches_card_not_separate_section(self):
        state = WorkflowState()
        state.last_digest_sent = NOW - timedelta(hours=24)

        dropped_high = _listing(
            vin="D",
            url="https://x.example/d",
            first_seen=NOW - timedelta(days=3),
            price=18000,
            price_history=[
                PriceObservation(timestamp=NOW - timedelta(days=3), price=20000),
                PriceObservation(timestamp=NOW - timedelta(hours=2), price=18000),
            ],
        )
        payload = assemble_digest(
            [(dropped_high, _score(total=92.0, band="great", listing_url="https://x.example/d"))],
            state,
            now=NOW,
        )
        # High-scoring drops surface in Top Picks (not repeated in Price Drops)
        # with old_price baked in.
        assert len(payload.top_picks) == 1
        assert payload.top_picks[0].old_price == 20000
        assert payload.price_drops == []

    def test_dedupe_same_listing_multiple_scores(self):
        state = WorkflowState()
        listing = _listing()
        # Same URL scored twice — only latest wins
        payload = assemble_digest(
            [
                (listing, _score(total=75.0)),
                (listing, _score(total=85.0)),
            ],
            state,
            now=NOW,
        )
        total_cards = payload.top_picks + payload.new_today
        assert len(total_cards) == 1

    def test_empty_state(self):
        payload = assemble_digest([], WorkflowState(), now=NOW)
        assert payload.empty is True


class TestRecentPriceDrop:
    def test_returns_old_price_on_qualifying_drop(self):
        listing = _listing(
            price=18000,
            price_history=[
                PriceObservation(timestamp=NOW - timedelta(days=2), price=20000),
                PriceObservation(timestamp=NOW - timedelta(hours=2), price=18000),
            ],
        )
        cutoff = NOW - timedelta(hours=24)
        assert _recent_price_drop(listing, cutoff) == 20000

    def test_ignores_small_drop(self):
        listing = _listing(
            price=19500,
            price_history=[
                PriceObservation(timestamp=NOW - timedelta(days=2), price=19900),
                PriceObservation(timestamp=NOW - timedelta(hours=2), price=19500),
            ],
        )
        cutoff = NOW - timedelta(hours=24)
        # 2% drop — below threshold
        assert _recent_price_drop(listing, cutoff) is None


class TestRenderHtml:
    def test_includes_top_picks_section(self):
        payload = DigestPayload(
            top_picks=[
                _to_card(_listing(), _score(total=95.0)),
            ],
            sources_checked=1,
            listings_in_state=1,
            last_scout_local="2026-04-17 12:25 UTC",
        )
        html = render_digest_html(payload, now=NOW)
        assert "Top Picks" in html
        assert "Subaru Crosstrek" in html
        assert "$19,900" in html
        assert "42,000 miles" in html
        assert "Great deal" in html

    def test_empty_state_rendered(self):
        payload = DigestPayload(
            sources_checked=1,
            listings_in_state=5,
            last_scout_local="2026-04-17 12:25 UTC",
        )
        html = render_digest_html(payload, now=NOW)
        assert "No new matches" in html

    def test_price_drop_shows_old_price(self):
        payload = DigestPayload(
            price_drops=[
                _to_card(_listing(price=18000), _score(), old_price=20000),
            ],
            sources_checked=1,
            listings_in_state=1,
        )
        html = render_digest_html(payload, now=NOW)
        assert "$18,000" in html
        assert "$20,000" in html  # struck-through


class TestRenderPlaintext:
    def test_structure(self):
        payload = DigestPayload(
            top_picks=[_to_card(_listing(), _score(total=95.0))],
            new_today=[_to_card(_listing(vin="N1", url="https://x.example/n1"), _score(total=75.0))],
        )
        text = render_digest_plaintext(payload)
        assert "TOP PICKS" in text
        assert "NEW TODAY" in text
        assert "Subaru Crosstrek" in text

    def test_empty_plaintext(self):
        text = render_digest_plaintext(DigestPayload())
        assert "No new matches" in text


class TestComposeSubject:
    def test_empty_subject_mentions_still_watching(self):
        payload = DigestPayload()
        assert "still watching" in compose_subject(payload, now=NOW)

    def test_populated_subject_has_counts(self):
        payload = DigestPayload(
            top_picks=[_to_card(_listing(), _score())],
            new_today=[_to_card(_listing(vin="N", url="https://x.example/n"), _score())],
        )
        subject = compose_subject(payload, now=NOW)
        assert "2 listings" in subject or "2 listing" in subject
        assert "1 top pick" in subject


class TestSendDigest:
    def test_raises_on_missing_recipient(self, monkeypatch):
        monkeypatch.delenv("CAR_SCOUT_DIGEST_TO", raising=False)
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        with pytest.raises(DigestSendError, match="recipient"):
            send_digest(html="<h1></h1>", plaintext="", subject="S")

    def test_raises_when_resend_key_missing(self, monkeypatch):
        monkeypatch.setenv("CAR_SCOUT_DIGEST_TO", "to@y")
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        with pytest.raises(DigestSendError, match="RESEND_API_KEY"):
            send_digest(html="<h1></h1>", plaintext="", subject="S")

    def test_sends_via_resend(self, monkeypatch):
        monkeypatch.setenv("CAR_SCOUT_DIGEST_FROM", "alfred@aptoworks.com")
        monkeypatch.setenv("CAR_SCOUT_DIGEST_TO", "nick@aptoworks.com")
        monkeypatch.setenv("RESEND_API_KEY", "re_test")

        from lib.email import ResendClient, SendResult

        mock_client = MagicMock(spec=ResendClient)
        mock_client.send.return_value = SendResult(id="email_123")

        send_digest(
            html="<h1>ok</h1>",
            plaintext="ok",
            subject="Test",
            client=mock_client,
        )

        mock_client.send.assert_called_once()
        kwargs = mock_client.send.call_args.kwargs
        assert kwargs["from_address"] == "alfred@aptoworks.com"
        assert kwargs["to"] == "nick@aptoworks.com"
        assert kwargs["subject"] == "Test"
        assert kwargs["html"] == "<h1>ok</h1>"
        assert kwargs["text"] == "ok"
        assert kwargs["reply_to"] == "nick@aptoworks.com"

    def test_wraps_send_errors(self, monkeypatch):
        monkeypatch.setenv("CAR_SCOUT_DIGEST_FROM", "alfred@aptoworks.com")
        monkeypatch.setenv("CAR_SCOUT_DIGEST_TO", "nick@aptoworks.com")

        from lib.email import ResendClient, ResendSendError

        mock_client = MagicMock(spec=ResendClient)
        mock_client.send.side_effect = ResendSendError("Resend returned 422: bad from domain")

        with pytest.raises(DigestSendError, match="422"):
            send_digest(
                html="<h1></h1>",
                plaintext="",
                subject="Test",
                client=mock_client,
            )
