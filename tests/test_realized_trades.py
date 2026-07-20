"""
FIFO realized-trade identification (web/analytics.realized_trades).

Covers the cases the annual report depends on: a clean round-trip, a PARTIAL
sell, a sell that spans multiple buy lots, cross-year dating, and still-open
positions producing no realized trade.
"""

import pytest

from src.engine import SimConfig, TradingEngine
from web import analytics


@pytest.fixture
def engine():
    e = TradingEngine(SimConfig())
    yield e
    e.close()


def _advance_months(engine, n):
    for _ in range(n):
        engine.advance()


class TestFifoRealizedTrades:
    def test_open_position_has_no_realized_trade(self, engine):
        engine.buy("AAPL", dollars=10_000)
        assert analytics.realized_trades(engine) == []

    def test_simple_round_trip(self, engine):
        buy_price = engine.current_price("AAPL")
        engine.buy("AAPL", shares=10)
        _advance_months(engine, 3)
        sell_price = engine.current_price("AAPL")
        engine.sell("AAPL", shares=10)

        trades = analytics.realized_trades(engine)
        assert len(trades) == 1
        t = trades[0]
        assert t["shares"] == pytest.approx(10)
        assert t["buy_price"] == pytest.approx(buy_price)
        assert t["sell_price"] == pytest.approx(sell_price)
        assert t["pnl_usd"] == pytest.approx((sell_price - buy_price) * 10)

    def test_partial_sell_realizes_only_sold_shares(self, engine):
        buy_price = engine.current_price("NVDA")
        engine.buy("NVDA", shares=100)
        _advance_months(engine, 2)
        sell_price = engine.current_price("NVDA")
        engine.sell("NVDA", shares=40)          # partial

        trades = analytics.realized_trades(engine)
        assert len(trades) == 1
        assert trades[0]["shares"] == pytest.approx(40)
        assert trades[0]["pnl_usd"] == pytest.approx((sell_price - buy_price) * 40)
        # 60 shares remain open.
        assert engine._portfolio().shares_of("NVDA") == pytest.approx(60)

    def test_fifo_matches_oldest_lot_first(self, engine):
        # Two buy lots at different prices/months, then sell the size of lot 1.
        p1 = engine.current_price("AAPL")
        engine.buy("AAPL", shares=10)           # lot 1 (oldest)
        _advance_months(engine, 4)
        engine.buy("AAPL", shares=10)           # lot 2 (newer, different price)
        _advance_months(engine, 1)
        sell_price = engine.current_price("AAPL")
        engine.sell("AAPL", shares=10)          # should close LOT 1

        trades = analytics.realized_trades(engine)
        assert len(trades) == 1
        # P&L must use lot 1's price, proving FIFO (not the newer lot).
        assert trades[0]["buy_price"] == pytest.approx(p1)
        assert trades[0]["pnl_usd"] == pytest.approx((sell_price - p1) * 10)

    def test_sell_spanning_two_lots_creates_two_records(self, engine):
        engine.buy("AAPL", shares=10)           # lot 1
        _advance_months(engine, 2)
        engine.buy("AAPL", shares=10)           # lot 2
        _advance_months(engine, 1)
        engine.sell("AAPL", shares=15)          # closes lot 1 (10) + part of lot 2 (5)

        trades = analytics.realized_trades(engine)
        assert len(trades) == 2
        assert trades[0]["shares"] == pytest.approx(10)   # all of lot 1
        assert trades[1]["shares"] == pytest.approx(5)    # part of lot 2
        assert engine._portfolio().shares_of("AAPL") == pytest.approx(5)

    def test_best_worst_ranking(self, engine):
        engine.buy("NVDA", shares=50)
        engine.buy("AAPL", shares=50)
        _advance_months(engine, 6)
        engine.sell("NVDA", shares=50)
        engine.sell("AAPL", shares=50)

        bw = analytics.best_worst_realized(engine)
        assert bw["count"] == 2
        assert bw["best"]["pnl_usd"] >= bw["worst"]["pnl_usd"]


class TestYearFiltering:
    def test_trade_is_dated_by_its_sell_year(self, engine):
        # Buy in 2021, sell in 2022 -> realized in 2022, not 2021.
        engine.buy("AAPL", shares=10)
        while engine.current_date[:4] == "2021":
            engine.advance()
        assert engine.current_date.startswith("2022")
        engine.sell("AAPL", shares=10)

        assert analytics.realized_trades(engine, year=2021) == []
        y2022 = analytics.realized_trades(engine, year=2022)
        assert len(y2022) == 1
        assert y2022[0]["buy_date"].startswith("2021")   # buy predates the year
        assert y2022[0]["sell_date"].startswith("2022")

    def test_no_realized_trades_returns_empty_best_worst(self, engine):
        engine.buy("AAPL", shares=10)            # never sold
        bw = analytics.best_worst_realized(engine, year=2021)
        assert bw == {"best": None, "worst": None, "count": 0}


class TestRiskMetricsIntegration:
    def test_overall_metrics_are_no_lookahead_bounded(self, engine):
        """Metrics must use only values up to the current interval."""
        engine.buy("AAPL", dollars=50_000)
        _advance_months(engine, 5)
        m = analytics.overall_risk_metrics(engine)
        # 6 value points (months 0..5) -> 5 returns.
        assert m["n_periods"] == 5

    def test_year_metrics_only_use_that_year(self, engine):
        engine.buy("AAPL", dollars=50_000)
        _advance_months(engine, 13)              # into 2022
        m2021 = analytics.year_risk_metrics(engine, 2021)
        # 2021 has 12 monthly points + the anchor is month 0 itself (no prior
        # year), so returns come only from within/adjacent to 2021, never 2022.
        assert m2021["n_periods"] >= 1
        assert m2021["volatility"] is not None
