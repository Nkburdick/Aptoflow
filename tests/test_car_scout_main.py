"""Smoke tests for workflows.car_scout.main orchestration."""

from __future__ import annotations

import pytest

from workflows.car_scout.main import _color_ok, _passes_hard_filters
from workflows.car_scout.models import Listing


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
        exterior_color="Crystal Black Silica",  # dark color, allowed
    )
    base.update(overrides)
    return Listing(**base)


class TestHardFilters:
    def test_clean_passes(self):
        assert _passes_hard_filters(_listing()) is True

    def test_unknown_title_passes(self):
        # Unknown is admitted with a warning; only salvage/rebuilt are rejected
        assert _passes_hard_filters(_listing(title_status="unknown")) is True

    def test_salvage_rejected(self):
        assert _passes_hard_filters(_listing(title_status="salvage")) is False

    def test_rebuilt_rejected(self):
        assert _passes_hard_filters(_listing(title_status="rebuilt")) is False

    def test_over_budget_rejected(self, monkeypatch):
        monkeypatch.setenv("BUDGET_CEILING_USD", "20000")
        assert _passes_hard_filters(_listing(price=21500)) is False

    def test_under_budget_passes(self, monkeypatch):
        monkeypatch.setenv("BUDGET_CEILING_USD", "22000")
        assert _passes_hard_filters(_listing(price=19900)) is True

    def test_too_old_rejected(self, monkeypatch):
        monkeypatch.setenv("YEAR_FLOOR", "2015")
        assert _passes_hard_filters(_listing(year=2014)) is False

    def test_primary_tier_mileage_limit(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_MILEAGE_CEILING", "80000")
        assert _passes_hard_filters(_listing(mileage=85000)) is False

    def test_secondary_tier_higher_mileage_allowed(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_MILEAGE_CEILING", "80000")
        monkeypatch.setenv("SECONDARY_MILEAGE_CEILING", "110000")
        # Secondary tier: 95k miles should pass
        assert _passes_hard_filters(
            _listing(make="Toyota", model="RAV4", tier="secondary", mileage=95000)
        ) is True
        # But 115k should still fail even for secondary
        assert _passes_hard_filters(
            _listing(make="Toyota", model="RAV4", tier="secondary", mileage=115000)
        ) is False


class TestColorFilter:
    @pytest.mark.parametrize(
        "color",
        [
            "Crystal Black Silica", "Black", "Magnetite Gray Metallic",
            "Charcoal Gray", "Dark Gray", "Navy Blue", "Cosmic Blue Pearl",
            "Autumn Green Metallic", "Espresso Brown", "Maroon",
        ],
    )
    def test_allowed_dark_colors_admit(self, color):
        assert _color_ok(_listing(exterior_color=color)) is True, color

    @pytest.mark.parametrize(
        "color",
        [
            "White", "Crystal White Pearl", "Silver", "Ice Silver Metallic",
            "Beige", "Ivory", "Gold Metallic", "Pumpkin Orange",
            "Yellow", "Pink", "Tan Metallic",
        ],
    )
    def test_blocked_bright_colors_reject(self, color):
        assert _color_ok(_listing(exterior_color=color)) is False, color

    def test_pearl_alone_does_not_block(self):
        # "Cosmic Blue Pearl" = blue (allowed) with pearl finish — admit
        assert _color_ok(_listing(exterior_color="Cosmic Blue Pearl")) is True

    def test_unknown_color_admits(self):
        # Missing color — can't judge, admit
        assert _color_ok(_listing(exterior_color=None)) is True

    def test_unmatched_color_admits(self):
        # Exotic color name we haven't classified — admit and log
        assert _color_ok(_listing(exterior_color="Matte Teal Chameleon")) is True

    def test_color_filter_integrates_into_hard_filter(self):
        bright = _listing(exterior_color="Crystal White Pearl")
        assert _passes_hard_filters(bright) is False

    def test_burgundy_is_admitted(self):
        # Dark red = burgundy, admit
        assert _color_ok(_listing(exterior_color="Dark Burgundy")) is True
        # But bright red rejected
        assert _color_ok(_listing(exterior_color="Ruby Red")) is False
