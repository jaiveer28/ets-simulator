"""
Intent-based sizing (`fraction`) -- the permanent fix for the echo-back class
of bugs.

The rounding bug happened because a UI expressed "sell everything" by sending
back a DISPLAYED number. No tolerance value fixes that in general: a UI showing
shares to 2dp and rounding up overshoots by ~0.005, thousands of times more than
any safe tolerance. `fraction` removes the round-trip entirely -- the engine
computes the quantity from its own exact internal state.

These tests prove fraction-based trades are exact regardless of display format.
"""

import pytest

from src.engine import TradeError


class TestSellFraction:
    def test_fraction_one_liquidates_exactly(self, engine):
        engine.buy("AAPL", dollars=12_345.67)
        engine.sell("AAPL", fraction=1.0)
        assert engine.portfolio_state()["holdings"] == []

    def test_fraction_half_leaves_exactly_half(self, engine):
        engine.buy("NVDA", dollars=9_876.54)
        held = engine._portfolio().shares_of("NVDA")
        engine.sell("NVDA", fraction=0.5)
        assert engine._portfolio().shares_of("NVDA") == pytest.approx(held / 2)

    def test_fraction_ignores_display_rounding_entirely(self, engine):
        """
        The key property: even a UI that rounds displayed shares UP (the exact
        scenario that broke sell()) cannot corrupt a fraction-based trade,
        because no displayed value is used.
        """
        engine.buy("MC.PA", dollars=7_777.77)
        actual = engine._portfolio().shares_of("MC.PA")
        # A hostile UI rounds UP to 2dp and would post this back:
        hostile = round(actual + 0.005, 2)
        assert hostile > actual

        engine.sell("MC.PA", fraction=1.0)   # unaffected by the above
        assert engine.portfolio_state()["holdings"] == []

    def test_fraction_works_across_whole_universe(self, engine):
        for i, ticker in enumerate(engine.universe()):
            engine.buy(ticker, dollars=901.37 + i * 213.11)
        for ticker in list(engine._portfolio().holdings):
            engine.sell(ticker, fraction=1.0)
        assert engine.portfolio_state()["holdings"] == []


class TestBuyFraction:
    def test_fraction_one_spends_all_cash(self, engine):
        engine.buy("AAPL", fraction=1.0)
        assert engine.portfolio_state()["cash"] == pytest.approx(0, abs=0.01)

    def test_fraction_quarter_spends_a_quarter(self, engine):
        engine.buy("TSLA", fraction=0.25)
        assert engine._portfolio().cash == pytest.approx(75_000)

    def test_fraction_uses_exact_cash_not_displayed(self, engine):
        engine.buy("AAPL", shares=100)          # leaves an untidy balance
        exact = engine._portfolio().cash
        engine.buy("NVDA", fraction=1.0)
        assert engine._portfolio().cash == pytest.approx(0, abs=1e-9)
        assert exact != round(exact, 2)          # balance really was untidy


class TestFractionValidation:
    @pytest.mark.parametrize("bad", [0, -0.5, 1.5, 2])
    def test_out_of_range_fraction_rejected(self, engine, bad):
        with pytest.raises(TradeError, match="fraction"):
            engine.buy("AAPL", fraction=bad)

    def test_cannot_combine_fraction_with_dollars(self, engine):
        with pytest.raises(TradeError, match="exactly one"):
            engine.buy("AAPL", dollars=100, fraction=0.5)

    def test_cannot_combine_fraction_with_shares(self, engine):
        engine.buy("AAPL", dollars=1_000)
        with pytest.raises(TradeError, match="exactly one"):
            engine.sell("AAPL", shares=1, fraction=0.5)

    def test_selling_fraction_of_nothing_rejected(self, engine):
        with pytest.raises(TradeError, match="No holdings"):
            engine.sell("AMZN", fraction=1.0)

    def test_helpers_still_work(self, engine):
        """sell_all/buy_max are now thin wrappers over fraction=1.0."""
        engine.buy_max("JPM")
        assert engine.portfolio_state()["cash"] == pytest.approx(0, abs=0.01)
        engine.sell_all("JPM")
        assert engine.portfolio_state()["holdings"] == []
        assert engine.portfolio_state()["cash"] == pytest.approx(100_000, abs=1.0)
