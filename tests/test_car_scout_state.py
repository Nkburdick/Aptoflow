"""Unit tests for workflows.car_scout.state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workflows.car_scout.models import Listing, PriceObservation, WorkflowState
from workflows.car_scout.state import (
    comp_key,
    load_state,
    merge_listing,
    prune_old,
    record_sms,
    save_state,
    sms_count_last_24h,
)


def _listing(**overrides) -> Listing:
    base = dict(
        url="https://www.cargurus.com/Cars/link/abc",
        vin="VIN-001",
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
    )
    base.update(overrides)
    return Listing(**base)


class TestCompKey:
    def test_formatting(self):
        assert comp_key(2020, "Subaru", "Crosstrek") == "2020_subaru_crosstrek"

    def test_lowercase_and_space_handling(self):
        assert comp_key(2019, "Toyota", "RAV4") == "2019_toyota_rav4"
        assert comp_key(2021, "Mazda", "CX-5") == "2021_mazda_cx-5"


class TestMergeListing:
    def test_new_listing_insertion(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
        listing = _listing()

        merged = merge_listing(state, listing, now=now)

        assert merged.dedup_key() == "VIN-001"
        assert "VIN-001" in state.listings
        assert state.listings["VIN-001"].first_seen == now
        assert state.listings["VIN-001"].last_seen == now
        assert len(state.listings["VIN-001"].price_history) == 1
        assert state.listings["VIN-001"].price_history[0].price == 19900

    def test_repeat_same_price_updates_last_seen_only(self):
        state = WorkflowState()
        t1 = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
        t2 = t1 + timedelta(hours=2)

        merge_listing(state, _listing(), now=t1)
        merge_listing(state, _listing(), now=t2)

        tracked = state.listings["VIN-001"]
        assert tracked.first_seen == t1
        assert tracked.last_seen == t2
        assert len(tracked.price_history) == 1  # no price change -> no append

    def test_price_drop_appends_history(self):
        state = WorkflowState()
        t1 = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
        t2 = t1 + timedelta(days=2)

        merge_listing(state, _listing(price=19900), now=t1)
        merge_listing(state, _listing(price=18400), now=t2)

        tracked = state.listings["VIN-001"]
        assert tracked.price == 18400
        assert [o.price for o in tracked.price_history] == [19900, 18400]

    def test_vin_dedup_wins_over_url(self):
        state = WorkflowState()
        t1 = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)

        merge_listing(state, _listing(url="https://a.example/1"), now=t1)
        # Same VIN, different URL (common when a dealer relists) — should merge
        merge_listing(state, _listing(url="https://b.example/2", price=18000), now=t1)

        assert len(state.listings) == 1
        assert state.listings["VIN-001"].price == 18000

    def test_url_dedup_when_vin_missing(self):
        state = WorkflowState()
        t1 = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)

        url = "https://a.example/1"
        merge_listing(state, _listing(vin=None, url=url), now=t1)
        merge_listing(state, _listing(vin=None, url=url, price=17000), now=t1 + timedelta(days=1))

        assert len(state.listings) == 1
        only = next(iter(state.listings.values()))
        assert only.price == 17000

    def test_comp_observation_recorded(self):
        state = WorkflowState()
        t1 = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)

        merge_listing(state, _listing(), now=t1)
        merge_listing(state, _listing(vin="VIN-002", url="https://a.example/2", price=20500), now=t1)

        bucket = comp_key(2020, "Subaru", "Crosstrek")
        assert bucket in state.comps
        assert [o.price for o in state.comps[bucket]] == [19900, 20500]


class TestPruneOld:
    def test_drops_stale_listing(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        stale = _listing(vin="STALE-001")
        stale.last_seen = now - timedelta(days=8)  # >7 days
        state.listings[stale.dedup_key()] = stale

        counts = prune_old(state, now=now)
        assert "STALE-001" not in state.listings
        assert counts["listings"] == 1

    def test_keeps_fresh_listing(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        fresh = _listing(vin="FRESH-001")
        fresh.last_seen = now - timedelta(days=2)
        state.listings[fresh.dedup_key()] = fresh

        prune_old(state, now=now)
        assert "FRESH-001" in state.listings

    def test_drops_old_comps(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        bucket = comp_key(2020, "Subaru", "Crosstrek")
        state.comps[bucket] = [
            PriceObservation(timestamp=now - timedelta(days=31), price=20000),
            PriceObservation(timestamp=now - timedelta(days=5), price=19500),
        ]

        counts = prune_old(state, now=now)
        assert len(state.comps[bucket]) == 1
        assert state.comps[bucket][0].price == 19500
        assert counts["comps"] == 1

    def test_drops_empty_comp_bucket(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        bucket = comp_key(2020, "Subaru", "Crosstrek")
        state.comps[bucket] = [
            PriceObservation(timestamp=now - timedelta(days=40), price=20000),
        ]

        prune_old(state, now=now)
        assert bucket not in state.comps

    def test_drops_old_sms_timestamps(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
        state.sms_timestamps = [
            now - timedelta(hours=25),  # expired
            now - timedelta(hours=2),   # still valid
        ]

        prune_old(state, now=now)
        assert len(state.sms_timestamps) == 1

    def test_drops_old_top_picks(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)
        state.top_picks_last_7_days = {
            "https://old.example": now - timedelta(days=8),
            "https://new.example": now - timedelta(days=1),
        }

        prune_old(state, now=now)
        assert "https://old.example" not in state.top_picks_last_7_days
        assert "https://new.example" in state.top_picks_last_7_days

    def test_releases_unicorn_dedupe_for_dropped_listings(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, tzinfo=timezone.utc)

        # Listing exists + already-notified
        fresh = _listing(vin="FRESH-001")
        fresh.last_seen = now - timedelta(days=1)
        state.listings[fresh.dedup_key()] = fresh
        state.unicorn_notified.add("FRESH-001")

        # Orphaned dedupe slot (listing was pruned earlier)
        state.unicorn_notified.add("ORPHAN-VIN")

        counts = prune_old(state, now=now)
        assert "FRESH-001" in state.unicorn_notified
        assert "ORPHAN-VIN" not in state.unicorn_notified
        assert counts["unicorn_dedupes"] == 1


class TestSmsRateLimit:
    def test_record_and_count(self):
        state = WorkflowState()
        now = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
        record_sms(state, now=now)
        record_sms(state, now=now - timedelta(hours=2))
        record_sms(state, now=now - timedelta(hours=30))  # out of window

        assert sms_count_last_24h(state, now=now) == 2


class TestSaveLoad:
    def test_roundtrip(self, tmp_path: Path):
        state = WorkflowState()
        now = datetime(2026, 4, 17, 12, tzinfo=timezone.utc)
        merge_listing(state, _listing(), now=now)
        record_sms(state, now=now)
        state.runs_total = 3

        p = tmp_path / "state.json"
        save_state(state, path=p)
        assert p.exists()

        restored = load_state(path=p)
        assert "VIN-001" in restored.listings
        assert restored.listings["VIN-001"].price == 19900
        assert len(restored.sms_timestamps) == 1
        assert restored.runs_total == 3

    def test_load_missing_file_returns_empty(self, tmp_path: Path):
        p = tmp_path / "nonexistent.json"
        state = load_state(path=p)
        assert state.listings == {}
        assert state.runs_total == 0

    def test_load_empty_file_returns_empty(self, tmp_path: Path):
        p = tmp_path / "empty.json"
        p.write_text("")
        state = load_state(path=p)
        assert state.listings == {}

    def test_atomic_write_via_tempfile(self, tmp_path: Path):
        state = WorkflowState()
        state.runs_total = 42
        p = tmp_path / "state.json"
        save_state(state, path=p)
        # tempfile should be gone after replace
        assert not (tmp_path / "state.json.tmp").exists()
        assert p.exists()
