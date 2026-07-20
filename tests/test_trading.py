"""
Core trading behaviour: buying, selling, rejections, and valuation.
"""

import pytest

from src.engine import TradeError


class TestBuy:
    def test_buy_by_dollars_computes_correct_shares(self, engine):
        price = engine.current_price("TSLA")
        txn = engine.buy("TSLA", dollars=10_000)

        assert txn.action == "BUY"
        assert txn.shares == pytest.approx(10_000 / price)
        assert txn.total_value == pytest.approx(10_000)
        assert txn.price_usd == pytest.approx(price)

    def test_buy_by_shares_computes_correct_cost(self, engine):
        price = engine.current_price("AAPL")
        txn = engine.buy("AAPL", shares=100)

        assert txn.shares == pytest.approx(100)
        assert txn.total_value == pytest.approx(100 * price)

    def test_buy_deducts_cash_and_creates_holding(self, engine):
        start_cash = engine.portfolio_state()["cash"]
        engine.buy("NVDA", dollars=25_000)
        state = engine.portfolio_state()

        assert state["cash"] == pytest.approx(start_cash - 25_000, abs=0.01)
        assert len(state["holdings"]) == 1
        assert state["holdings"][0]["ticker"] == "NVDA"

    def test_buy_is_logged(self, engine):
        engine.buy("JPM", dollars=5_000)
        log = engine.transaction_log()

        assert len(log) == 1
        entry = log[0]
        assert entry["action"] == "BUY"
        assert entry["ticker"] == "JPM"
        assert entry["date"] == engine.current_date
        assert entry["cash_after"] == pytest.approx(95_000, abs=0.01)

    def test_buy_max_invests_all_cash(self, engine):
        engine.buy_max("AMZN")
        assert engine.portfolio_state()["cash"] == pytest.approx(0, abs=0.01)


class TestSell:
    def test_sell_by_shares_returns_proceeds(self, engine):
        engine.buy("TSLA", dollars=10_000)
        price = engine.current_price("TSLA")
        held = engine._portfolio().shares_of("TSLA")

        txn = engine.sell("TSLA", shares=held / 2)

        assert txn.action == "SELL"
        assert txn.shares == pytest.approx(held / 2)
        assert txn.total_value == pytest.approx(held / 2 * price)

    def test_sell_adds_cash_and_reduces_holding(self, engine):
        engine.buy("TSLA", dollars=10_000)
        held = engine._portfolio().shares_of("TSLA")
        engine.sell("TSLA", shares=held / 2)
        state = engine.portfolio_state()

        assert state["cash"] == pytest.approx(95_000, abs=0.01)
        assert state["holdings"][0]["shares"] == pytest.approx(held / 2, rel=1e-5)

    def test_sell_all_liquidates_position_entirely(self, engine):
        engine.buy("NVDA", dollars=10_000)
        engine.sell_all("NVDA")

        assert engine.portfolio_state()["holdings"] == []
        assert engine.portfolio_state()["cash"] == pytest.approx(100_000, abs=0.01)

    def test_selling_displayed_share_count_succeeds(self, engine):
        """
        REGRESSION: portfolio_state() reports shares at limited precision. A UI
        echoes that displayed number straight back into sell() for "sell all".
        Rounding UP once made this fail with a bogus "insufficient shares".

        NOTE: the share count must come from a DOLLAR division (an untidy float
        like 61.00408307...). Using shares=100 gives an exact float, where
        rounding and flooring agree and the bug cannot reproduce.
        """
        engine.buy("AAPL", dollars=12_345.67)
        engine.sell("AAPL", dollars=5_000)          # leaves an untidy remainder
        displayed = engine.portfolio_state()["holdings"][0]["shares"]

        engine.sell("AAPL", shares=displayed)       # must not raise

        # And it must leave no phantom dust position behind.
        assert engine.portfolio_state()["holdings"] == []

    def test_selling_displayed_shares_works_for_every_stock(self, engine):
        """
        Same echo-back path, swept across the whole universe with untidy
        amounts, so at least one position is guaranteed to expose a
        round-up if the floor-not-round invariant is ever broken.
        """
        for i, ticker in enumerate(engine.universe()):
            engine.buy(ticker, dollars=1_000 + i * 137.77)

        for h in engine.portfolio_state()["holdings"]:
            engine.sell(h["ticker"], shares=h["shares"])   # must not raise
        assert engine.portfolio_state()["holdings"] == []

    def test_displayed_shares_never_exceed_actual_holding(self, engine):
        """The invariant that makes echo-back safe, asserted directly."""
        for i, ticker in enumerate(engine.universe()):
            engine.buy(ticker, dollars=997.31 + i * 311.19)

        for h in engine.portfolio_state()["holdings"]:
            actual = engine._portfolio().shares_of(h["ticker"])
            assert h["shares"] <= actual, (
                f"{h['ticker']}: displayed {h['shares']} > actual {actual}")

    def test_buying_with_displayed_cash_succeeds(self, engine):
        """REGRESSION: same echo-back problem, on the cash side."""
        engine.buy("AAPL", shares=100)      # leaves an untidy cash balance
        displayed_cash = engine.portfolio_state()["cash"]
        assert displayed_cash != round(displayed_cash), "need an untidy balance"

        engine.buy("MC.PA", dollars=displayed_cash)   # must not raise
        assert engine.portfolio_state()["cash"] == pytest.approx(0, abs=0.01)

    def test_displayed_cash_never_exceeds_actual(self, engine):
        engine.buy("NVDA", dollars=33_333.33)
        engine.buy("TSLA", shares=7)
        assert engine.portfolio_state()["cash"] <= engine._portfolio().cash

    def test_partial_sell_keeps_remaining_position(self, engine):
        engine.buy("NVDA", dollars=10_000)
        engine.sell("NVDA", dollars=4_000)
        holdings = engine.portfolio_state()["holdings"]

        assert len(holdings) == 1
        assert holdings[0]["value_usd"] == pytest.approx(6_000, abs=1.0)


class TestRejections:
    def test_insufficient_cash_is_rejected(self, engine):
        with pytest.raises(TradeError, match="Insufficient cash"):
            engine.buy("AAPL", dollars=5_000_000)

    def test_insufficient_cash_leaves_state_untouched(self, engine):
        with pytest.raises(TradeError):
            engine.buy("AAPL", dollars=5_000_000)

        state = engine.portfolio_state()
        assert state["cash"] == pytest.approx(100_000)
        assert state["holdings"] == []
        assert engine.transaction_log() == []

    def test_insufficient_shares_is_rejected(self, engine):
        engine.buy("TSLA", dollars=1_000)
        with pytest.raises(TradeError, match="Insufficient shares"):
            engine.sell("TSLA", shares=9_999)

    def test_selling_unheld_stock_is_rejected(self, engine):
        with pytest.raises(TradeError, match="No holdings"):
            engine.sell("AMZN", shares=1)

    def test_no_short_selling_possible(self, engine):
        """Holdings can never go negative, however hard we try."""
        engine.buy("JPM", dollars=1_000)
        with pytest.raises(TradeError):
            engine.sell("JPM", shares=1e9)
        assert engine._portfolio().shares_of("JPM") > 0

    def test_unknown_ticker_is_rejected(self, engine):
        with pytest.raises(TradeError, match="Unknown or non-tradable"):
            engine.buy("FAKESTOCK", dollars=100)

    def test_index_is_not_tradable(self, engine):
        """Benchmark indices live in the price table but must never be buyable."""
        assert "^SP500TR" not in engine.universe()
        with pytest.raises(TradeError, match="Unknown or non-tradable"):
            engine.buy("^SP500TR", dollars=100)

    @pytest.mark.parametrize("kwargs", [
        {"dollars": 100, "shares": 1},   # both given
        {},                              # neither given
        {"dollars": -100},               # negative
        {"shares": 0},                   # zero
    ])
    def test_invalid_quantity_arguments_rejected(self, engine, kwargs):
        with pytest.raises(TradeError):
            engine.buy("AAPL", **kwargs)


class TestPortfolioValuation:
    def test_total_value_equals_cash_plus_holdings(self, engine):
        engine.buy("AAPL", dollars=30_000)
        engine.buy("NVDA", dollars=20_000)
        s = engine.portfolio_state()

        assert s["total_value"] == pytest.approx(
            s["cash"] + s["holdings_value"], abs=0.01)

    def test_value_unchanged_immediately_after_buying(self, engine):
        """Converting cash to stock at the same instant creates no P&L."""
        engine.buy("AAPL", dollars=40_000)
        s = engine.portfolio_state()

        assert s["total_value"] == pytest.approx(100_000, abs=0.01)
        assert s["pnl_dollars"] == pytest.approx(0, abs=0.01)

    def test_pnl_tracks_price_movement(self, engine):
        entry = engine.current_price("AAPL")
        engine.buy_max("AAPL")
        shares = engine._portfolio().shares_of("AAPL")

        for _ in range(12):
            engine.advance()

        later = engine.current_price("AAPL")
        s = engine.portfolio_state()

        assert s["total_value"] == pytest.approx(shares * later, abs=0.01)
        expected_pct = (later / entry - 1) * 100
        assert s["pnl_pct"] == pytest.approx(expected_pct, abs=0.01)

    def test_holdings_value_matches_shares_times_price(self, engine):
        engine.buy("MC.PA", dollars=15_000)
        h = engine.portfolio_state()["holdings"][0]
        assert h["value_usd"] == pytest.approx(
            h["shares"] * h["price_usd"], rel=1e-4)


class TestMultiUser:
    def test_users_have_independent_portfolios(self, engine):
        engine.add_user("user-2")
        engine.buy("AAPL", dollars=10_000, user_id="user-1")
        engine.buy("NVDA", dollars=50_000, user_id="user-2")

        u1 = engine.portfolio_state("user-1")
        u2 = engine.portfolio_state("user-2")

        assert u1["cash"] == pytest.approx(90_000, abs=0.01)
        assert u2["cash"] == pytest.approx(50_000, abs=0.01)
        assert u1["holdings"][0]["ticker"] == "AAPL"
        assert u2["holdings"][0]["ticker"] == "NVDA"

    def test_transaction_logs_are_separate(self, engine):
        engine.add_user("user-2")
        engine.buy("AAPL", dollars=1_000, user_id="user-1")
        engine.buy("NVDA", dollars=1_000, user_id="user-2")
        engine.buy("JPM", dollars=1_000, user_id="user-2")

        assert len(engine.transaction_log("user-1")) == 1
        assert len(engine.transaction_log("user-2")) == 2
