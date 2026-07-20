"""
Known-input sanity checks for the risk-metric maths in web/metrics.py.

These use hand-computable values so a wrong formula is caught immediately,
independent of any market data.
"""

import math

import pytest

from web import metrics


class TestSimpleReturns:
    def test_basic(self):
        assert metrics.simple_returns([100, 110, 99]) == pytest.approx([0.10, -0.10])

    def test_single_value_has_no_returns(self):
        assert metrics.simple_returns([100]) == []

    def test_flat_series_is_all_zero(self):
        assert metrics.simple_returns([100, 100, 100]) == [0.0, 0.0]


class TestVolatility:
    def test_zero_when_returns_constant(self):
        # Constant value -> zero returns -> zero volatility.
        assert metrics.volatility([0.0, 0.0, 0.0]) == 0.0

    def test_matches_hand_computed_sample_stdev(self):
        # returns [0.1, -0.1]: mean 0, sample var = (0.01+0.01)/1 = 0.02,
        # stdev = sqrt(0.02); annualised = *sqrt(12).
        rets = [0.10, -0.10]
        expected = math.sqrt(0.02) * math.sqrt(12)
        assert metrics.volatility(rets) == pytest.approx(expected)

    def test_none_with_fewer_than_two_returns(self):
        assert metrics.volatility([0.05]) is None

    def test_annualisation_scales_by_sqrt_periods(self):
        rets = [0.02, -0.01, 0.03, -0.02]
        monthly = metrics.volatility(rets, periods_per_year=1)
        annual = metrics.volatility(rets, periods_per_year=12)
        assert annual == pytest.approx(monthly * math.sqrt(12))


class TestMaxDrawdown:
    def test_simple_peak_to_trough(self):
        # Peak 130, trough 110 -> (110-130)/130 = -15.38%.
        assert metrics.max_drawdown([100, 130, 110, 120]) == pytest.approx(20/130)

    def test_monotonic_rise_has_no_drawdown(self):
        assert metrics.max_drawdown([100, 110, 120, 130]) == 0.0

    def test_takes_the_deepest_of_multiple_dips(self):
        # dip1: 120->108 (10%); dip2: 150->105 (30%). Deepest = 30%.
        assert metrics.max_drawdown([100, 120, 108, 150, 105]) == pytest.approx(0.30)

    def test_full_wipeout(self):
        assert metrics.max_drawdown([100, 0]) == pytest.approx(1.0)

    def test_empty_series_is_none(self):
        assert metrics.max_drawdown([]) is None


class TestSharpe:
    def test_none_when_volatility_zero(self):
        # Flat portfolio: no risk -> ratio undefined, must be None not infinity.
        assert metrics.sharpe_ratio([0.0, 0.0, 0.0], 0.03) is None

    def test_none_with_too_few_returns(self):
        assert metrics.sharpe_ratio([0.05], 0.03) is None

    def test_matches_hand_computed(self):
        rets = [0.02, 0.01, 0.03, 0.00]  # mean 0.015
        rf = 0.12                          # -> 0.01 per month
        mean_excess = 0.015 - 0.01
        sd = metrics._stdev(rets)
        expected = (mean_excess / sd) * math.sqrt(12)
        assert metrics.sharpe_ratio(rets, rf) == pytest.approx(expected)

    def test_higher_risk_free_lowers_sharpe(self):
        rets = [0.02, 0.01, 0.03, 0.015]
        assert metrics.sharpe_ratio(rets, 0.00) > metrics.sharpe_ratio(rets, 0.06)

    def test_positive_returns_above_rf_give_positive_sharpe(self):
        rets = [0.02, 0.025, 0.018, 0.022]
        assert metrics.sharpe_ratio(rets, 0.03) > 0


class TestBundle:
    def test_risk_metrics_reports_all_and_counts_periods(self):
        m = metrics.risk_metrics([100, 110, 121], 0.03)
        assert m["n_periods"] == 2
        assert m["volatility"] is not None
        assert m["max_drawdown"] == 0.0        # only rose
        assert m["risk_free_rate"] == 0.03

    def test_all_cash_portfolio_has_defined_but_riskless_metrics(self):
        m = metrics.risk_metrics([100_000, 100_000, 100_000], 0.03)
        assert m["volatility"] == 0.0
        assert m["max_drawdown"] == 0.0
        assert m["sharpe"] is None             # undefined, not infinity

    def test_insufficient_history_is_all_none(self):
        m = metrics.risk_metrics([100_000], 0.03)
        assert m["n_periods"] == 0
        assert m["volatility"] is None and m["sharpe"] is None
