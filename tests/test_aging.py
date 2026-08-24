"""Tests for the dynasty age curve."""
import pytest

from src import aging


def test_young_players_are_worth_more_than_old_at_every_position():
    for pos in ("QB", "RB", "WR", "TE", "LB"):
        young = aging.age_multiplier(pos, 23, weight=1.0)
        old = aging.age_multiplier(pos, 32, weight=1.0)
        assert young > old, pos


def test_running_backs_fall_off_faster_than_quarterbacks():
    """A 29-year-old RB should be discounted far harder than a 29yo QB."""
    rb = aging.age_multiplier("RB", 29, weight=1.0)
    qb = aging.age_multiplier("QB", 29, weight=1.0)
    assert rb < qb
    assert rb < 0.7 and qb > 0.95


def test_curve_is_monotonic_after_the_peak():
    prev = None
    for age in range(27, 37):
        m = aging.age_multiplier("WR", age, weight=1.0)
        if prev is not None:
            assert m <= prev, f"WR curve rose at {age}"
        prev = m


def test_interpolates_between_breakpoints():
    lo = aging.age_multiplier("RB", 25, weight=1.0)
    mid = aging.age_multiplier("RB", 26, weight=1.0)
    hi = aging.age_multiplier("RB", 27, weight=1.0)
    assert hi < mid < lo          # strictly between the breakpoints
    assert mid == pytest.approx((lo + hi) / 2, abs=0.01)


def test_weight_blends_toward_neutral():
    full = aging.age_multiplier("RB", 30, weight=1.0)
    half = aging.age_multiplier("RB", 30, weight=0.5)
    off = aging.age_multiplier("RB", 30, weight=0.0)
    assert off == 1.0
    assert full < half < off
    assert half == pytest.approx(1.0 + (full - 1.0) * 0.5, abs=1e-9)


def test_unknown_age_is_neutral():
    """A missing age must never silently penalise a player."""
    assert aging.age_multiplier("RB", None, weight=1.0) == 1.0


def test_unknown_position_uses_a_default_curve():
    assert aging.age_multiplier("FB", 23, weight=1.0) > 1.0
    assert aging.age_multiplier(None, 34, weight=1.0) < 1.0


def test_kickers_are_flat():
    assert aging.age_multiplier("PK", 23, weight=1.0) == 1.0
    assert aging.age_multiplier("PK", 38, weight=1.0) == 1.0


def test_extremes_clamp_to_the_curve_ends():
    assert aging.age_multiplier("WR", 18, weight=1.0) == \
        aging.age_multiplier("WR", 21, weight=1.0)
    assert aging.age_multiplier("WR", 45, weight=1.0) == \
        aging.age_multiplier("WR", 36, weight=1.0)


def test_age_weight_env(monkeypatch):
    monkeypatch.setenv("DYNASTY_AGE_WEIGHT", "0.25")
    assert aging.age_weight() == 0.25
    monkeypatch.setenv("DYNASTY_AGE_WEIGHT", "5")     # clamped
    assert aging.age_weight() == 1.0
    monkeypatch.setenv("DYNASTY_AGE_WEIGHT", "junk")  # falls back
    assert aging.age_weight() == 0.5
