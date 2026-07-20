"""
Deterministic unit tests for the two helpers that keep "sell everything" /
"invest all my cash" working.

These are pinned with hand-picked values rather than live market data, so they
fail the instant someone "tidies" _floor_to into round() or shrinks _tolerance
back to a raw epsilon -- regardless of what prices happen to be in the DB.

Background: portfolio_state() shows shares to 6dp and cash to 2dp. A UI echoes
those displayed numbers back into sell()/buy(). If a displayed value is ever
LARGER than the true holding, the trade is rejected with a nonsense
"insufficient shares/cash" error. Two things prevent that:
    1. displayed values are FLOORED, never rounded  -> never exceed reality
    2. the comparison tolerance is wider than display rounding error
"""

import pytest

from src.engine.trading import _floor_to, _tolerance


class TestFloorTo:
    def test_never_rounds_up(self):
        """round() would return 1.0 here, which exceeds the input."""
        assert _floor_to(0.9999999, 6) == 0.999999
        assert round(0.9999999, 6) == 1.0        # documents the bug we avoid

    def test_result_never_exceeds_input(self):
        values = [61.00408307, 0.9999999, 123.4567891, 1e-7,
                  99999.999999, 38.995901234, 0.0]
        for v in values:
            assert _floor_to(v, 6) <= v, f"{v} floored upward"

    def test_cash_flooring_never_exceeds_input(self):
        for v in [87178.140159, 0.999, 100000.005, 12345.678]:
            assert _floor_to(v, 2) <= v

    def test_truncates_to_requested_precision(self):
        assert _floor_to(61.00408307, 6) == 61.004083
        assert _floor_to(87178.14999, 2) == 87178.14

    def test_exact_values_are_unchanged(self):
        assert _floor_to(100.0, 6) == 100.0
        assert _floor_to(100000.0, 2) == 100000.0


class TestTolerance:
    def test_absorbs_worst_case_6dp_display_rounding(self):
        """
        The worst error from rounding to 6dp is 5e-7. The tolerance must exceed
        it, or echoing a displayed share count back into sell() can be rejected.
        """
        assert _tolerance(100.0) > 5e-7

    def test_absorbs_worst_case_2dp_cash_rounding_via_floor(self):
        """
        Cash is floored to 2dp, so the displayed value is at most 0.01 BELOW
        actual -- never above. The tolerance only needs to be positive here, but
        it must never be zero or negative.
        """
        assert _tolerance(100_000.0) > 0

    def test_scales_with_magnitude(self):
        """Large positions need proportionally more slack for float error."""
        assert _tolerance(1e12) > _tolerance(1.0)

    def test_has_a_sane_floor_for_small_values(self):
        assert _tolerance(0.0) >= 1e-6
        assert _tolerance(1e-9) >= 1e-6

    @pytest.mark.parametrize("value", [0.0, 1.0, 100.0, 1e6, 1e12])
    def test_always_positive(self, value):
        assert _tolerance(value) > 0
