"""
The time mechanic (interval schedule, year-end hook) and the benchmarks.
"""

import pytest

from src.engine import SimConfig, TradingEngine
from src.engine.clock import SimulationClock


class TestIntervalSchedule:
    def test_five_years_monthly_is_sixty_intervals(self, engine):
        assert engine.clock.total_intervals == 60

    def test_intervals_are_month_ends_in_order(self, engine):
        intervals = engine.clock.intervals
        assert intervals[0] == "2021-01-31"
        assert intervals[-1] == "2025-12-31"
        assert intervals == sorted(intervals), "intervals must be chronological"

    def test_weekly_interval_is_configurable(self):
        clock = SimulationClock("2021-01-01", "2021-12-31", interval="weekly")
        assert clock.total_intervals > 50
        assert clock.interval == "weekly"

    def test_invalid_interval_rejected(self):
        with pytest.raises(ValueError):
            SimulationClock("2021-01-01", "2021-12-31", interval="hourly")


class TestYearEndHook:
    def test_hook_fires_once_per_year(self, engine):
        fired = []
        engine.register_year_end_hook(lambda y, s: fired.append(y))
        while engine.advance() is not None:
            pass
        assert fired == [2021, 2022, 2023, 2024, 2025]

    def test_hook_fires_on_december_intervals_only(self, engine):
        fired_dates = []
        engine.register_year_end_hook(
            lambda y, s: fired_dates.append(s["date"]))
        while engine.advance() is not None:
            pass
        assert all(d.startswith(f"{y}-12-31")
                   for d, y in zip(fired_dates, [2021, 2022, 2023, 2024, 2025]))

    def test_snapshot_carries_the_data_the_report_will_need(self, engine):
        engine.buy("AAPL", dollars=50_000)
        snapshots = []
        engine.register_year_end_hook(lambda y, s: snapshots.append(s))
        for _ in range(12):
            engine.advance()

        snap = snapshots[0]
        assert snap["year"] == 2021
        assert "portfolios" in snap and "user-1" in snap["portfolios"]
        assert "benchmark" in snap
        pf = snap["portfolios"]["user-1"]
        assert {"cash", "holdings", "total_value", "pnl_pct"} <= set(pf)


class TestBuyAndHoldBenchmark:
    def test_starts_at_starting_capital(self, engine):
        assert engine.benchmark_state()["buy_and_hold"]["value_usd"] == \
            pytest.approx(100_000, abs=0.01)

    def test_spreads_equally_across_all_ten_stocks(self, engine):
        assert len(engine.benchmark.shares) == len(engine.universe()) == 10

    def test_never_trades(self, engine):
        """Share counts are frozen at entry regardless of time passing."""
        before = dict(engine.benchmark.shares)
        for _ in range(24):
            engine.advance()
        assert engine.benchmark.shares == before


class TestIndexBenchmark:
    def test_index_benchmark_is_available(self, engine):
        idx = engine.benchmark_state()["index"]
        assert idx is not None, "S&P 500 data missing from market.db"
        assert idx["ticker"] == "^SP500TR"

    def test_index_starts_at_starting_capital(self, engine):
        assert engine.benchmark_state()["index"]["value_usd"] == \
            pytest.approx(100_000, abs=0.01)

    def test_index_grows_over_the_full_period(self, engine):
        while engine.advance() is not None:
            pass
        idx = engine.benchmark_state()["index"]
        # S&P 500 total return over 2021-2025 was strongly positive.
        assert idx["pnl_pct"] > 50

    def test_total_return_index_beats_price_only_index(self, engine):
        """
        Sanity check on WHY we use ^SP500TR: it must outperform ^GSPC, because
        the difference is precisely reinvested dividends.
        """
        while engine.advance() is not None:
            pass
        end = engine.current_date
        start = engine.clock.intervals[0]

        def total_return(ticker):
            p0 = engine.market.price_asof(ticker, start)
            p1 = engine.market.price_asof(ticker, end)
            return p1 / p0 - 1

        assert total_return("^SP500TR") > total_return("^GSPC")

    def test_index_absent_degrades_gracefully(self, config):
        """If the index isn't in the DB, the engine must not crash."""
        cfg = SimConfig(index_benchmark_ticker="^NOT_A_REAL_INDEX")
        e = TradingEngine(cfg)
        assert e.index_benchmark is None
        assert e.benchmark_state()["index"] is None
        e.close()
