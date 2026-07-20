"""
NO-LOOKAHEAD INTEGRITY -- the most important guarantee in the simulator.

If any of these fail, the simulation is cheating: a user (or the engine) could
see prices that hadn't happened yet at the simulated point in time.

These tests verify the guarantee three different ways:
  1. Behaviourally  -- returned prices never come from after the cursor.
  2. Structurally   -- the public API has no way to ASK for a future price.
  3. Independently  -- cross-checked against a direct query on market.db.
"""

import inspect
import sqlite3

import pytest

from src import config as data_config
from src.engine import SimConfig, TradingEngine


@pytest.fixture(scope="module")
def raw_db():
    """Direct read-only DB handle, to independently verify engine answers."""
    uri = f"file:{str(data_config.DB_PATH).replace(chr(92), '/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    yield conn
    conn.close()


class TestPricesNeverComeFromTheFuture:
    def test_price_asof_matches_independent_query(self, engine, raw_db):
        """The engine's price equals the last real close on/before the date."""
        as_of = "2023-06-15"
        for ticker in engine.universe():
            expected = raw_db.execute(
                "SELECT adj_close_usd FROM prices "
                "WHERE ticker = ? AND date <= ? ORDER BY date DESC LIMIT 1",
                (ticker, as_of)).fetchone()[0]
            assert engine.market.price_asof(ticker, as_of) == pytest.approx(expected)

    def test_price_asof_never_uses_a_later_row(self, engine, raw_db):
        """
        The returned price must belong to a date <= as_of. We prove it by
        finding which date the returned value came from.
        """
        as_of = "2022-03-15"
        for ticker in engine.universe():
            price = engine.market.price_asof(ticker, as_of)
            source_date = raw_db.execute(
                "SELECT MAX(date) FROM prices "
                "WHERE ticker = ? AND adj_close_usd = ?",
                (ticker, price)).fetchone()[0]
            assert source_date <= as_of, (
                f"{ticker}: price came from {source_date}, after {as_of}")

    def test_current_price_is_pinned_to_the_clock(self, engine):
        for _ in range(5):
            for ticker in engine.universe():
                assert engine.current_price(ticker) == \
                    engine.market.price_asof(ticker, engine.current_date)
            engine.advance()

    def test_future_price_is_invisible_until_time_advances(self, engine):
        """
        The canonical check: a price that WILL differ later must not be
        visible now, and must only appear after advance().
        """
        before_date = engine.current_date
        before_price = engine.current_price("NVDA")

        engine.advance()

        after_price = engine.current_price("NVDA")
        assert engine.current_date > before_date
        assert after_price != before_price, "test needs a month where price moved"

        # Re-querying the OLD date still yields the OLD price: the gate is the
        # date, not some mutable engine state.
        assert engine.market.price_asof("NVDA", before_date) == \
            pytest.approx(before_price)


class TestTradesExecuteAtTodaysPrice:
    def test_trade_uses_current_not_future_price(self, engine):
        today_price = engine.current_price("TSLA")
        txn = engine.buy("TSLA", dollars=10_000)
        assert txn.price_usd == pytest.approx(today_price)

        engine.advance()
        future_price = engine.current_price("TSLA")
        # The recorded trade must NOT have been repriced by time moving on.
        assert engine.transaction_log()[0]["price_usd"] == \
            pytest.approx(round(today_price, 4))
        assert future_price != today_price


class TestApiOffersNoWayToAskForTheFuture:
    """
    Structural proof: even a caller who WANTED to cheat has no method to call.
    """

    def test_public_price_methods_take_no_date_argument(self, engine):
        for name in ("current_price", "current_prices"):
            params = list(inspect.signature(
                getattr(engine, name)).parameters)
            assert "as_of" not in params and "date" not in params, (
                f"{name} exposes a date parameter -- lookahead becomes possible")

    def test_engine_exposes_no_full_series_accessor(self, engine):
        """
        MarketData must not offer a 'give me the whole price history' method,
        which would hand a caller the future in one call.
        """
        forbidden = {"history", "series", "all_prices", "future_price",
                     "price_at", "get_series"}
        assert not (forbidden & set(dir(engine.market)))

    def test_clock_cannot_move_backwards(self, engine):
        for _ in range(6):
            engine.advance()
        idx = engine.clock.current_index

        # No rewind/seek API exists...
        assert not ({"rewind", "seek", "go_back", "set_index", "jump"}
                    & set(dir(engine.clock)))
        # ...and advancing only ever increases the cursor.
        engine.advance()
        assert engine.clock.current_index == idx + 1

    def test_advance_stops_at_the_end(self, config):
        """Time cannot run past the end of available data."""
        engine = TradingEngine(config)
        while engine.advance() is not None:
            pass
        last = engine.clock.current_index
        assert engine.advance() is None
        assert engine.clock.current_index == last
        engine.close()


class TestBenchmarksAlsoRespectTheGate:
    def test_benchmark_value_changes_only_as_time_advances(self, engine):
        v0 = engine.benchmark_state()["buy_and_hold"]["value_usd"]
        engine.advance()
        v1 = engine.benchmark_state()["buy_and_hold"]["value_usd"]
        assert v0 != v1

    def test_benchmark_starts_at_exactly_starting_capital(self, engine):
        """On day 1 the benchmark has just been bought: no gain yet."""
        bh = engine.benchmark_state()["buy_and_hold"]
        assert bh["value_usd"] == pytest.approx(
            engine.config.starting_capital, abs=0.01)
