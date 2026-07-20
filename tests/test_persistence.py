"""
Persistence: state must survive between sessions, exactly.
"""

import pytest

from src.engine import SimConfig, SimulationStore, TradingEngine


class TestRoundTrip:
    def test_cash_and_holdings_survive_reload(self, config, store):
        e1 = TradingEngine(config, sim_id="s1", store=store)
        e1.buy("AAPL", dollars=30_000)
        e1.buy("NVDA", dollars=20_000)
        before = e1.portfolio_state()
        e1.market.close()

        e2 = TradingEngine.load("s1", store)
        after = e2.portfolio_state()

        assert after["cash"] == pytest.approx(before["cash"])
        assert after["total_value"] == pytest.approx(before["total_value"])
        assert [h["ticker"] for h in after["holdings"]] == \
               [h["ticker"] for h in before["holdings"]]
        for a, b in zip(after["holdings"], before["holdings"]):
            assert a["shares"] == pytest.approx(b["shares"])
        e2.market.close()

    def test_transaction_history_survives_reload(self, config, store):
        e1 = TradingEngine(config, sim_id="s2", store=store)
        e1.buy("TSLA", dollars=10_000)
        e1.advance()
        e1.sell("TSLA", dollars=4_000)
        before = e1.transaction_log()
        e1.market.close()

        e2 = TradingEngine.load("s2", store)
        after = e2.transaction_log()

        assert len(after) == len(before) == 2
        assert after == before
        e2.market.close()

    def test_clock_position_survives_reload(self, config, store):
        e1 = TradingEngine(config, sim_id="s3", store=store)
        for _ in range(7):
            e1.advance()
        date_before, idx_before = e1.current_date, e1.interval_index
        e1.market.close()

        e2 = TradingEngine.load("s3", store)

        assert e2.interval_index == idx_before
        assert e2.current_date == date_before
        e2.market.close()

    def test_reload_resumes_trading_correctly(self, config, store):
        """After restoring, the engine must behave as if never interrupted."""
        e1 = TradingEngine(config, sim_id="s4", store=store)
        e1.buy("AAPL", dollars=50_000)
        for _ in range(3):
            e1.advance()
        e1.market.close()

        e2 = TradingEngine.load("s4", store)
        e2.sell_all("AAPL")

        assert e2.portfolio_state()["holdings"] == []
        assert len(e2.transaction_log()) == 2
        e2.market.close()

    def test_zero_cash_is_restored_as_zero(self, config, store):
        """
        REGRESSION: Portfolio.__post_init__ resets cash to starting_capital when
        cash is 0. On restore that would silently hand back $100k of free money,
        so load() must set cash explicitly.
        """
        e1 = TradingEngine(config, sim_id="s5", store=store)
        e1.buy_max("AAPL")
        assert e1.portfolio_state()["cash"] == pytest.approx(0, abs=0.01)
        e1.market.close()

        e2 = TradingEngine.load("s5", store)
        assert e2.portfolio_state()["cash"] == pytest.approx(0, abs=0.01)
        e2.market.close()

    def test_config_is_snapshotted_not_reread(self, store):
        """A restored sim runs under the config it STARTED with."""
        e1 = TradingEngine(SimConfig(starting_capital=42_000),
                           sim_id="s6", store=store)
        e1.market.close()

        e2 = TradingEngine.load("s6", store)
        assert e2.config.starting_capital == 42_000
        assert e2.portfolio_state()["starting_capital"] == 42_000
        e2.market.close()


class TestMultiUserPersistence:
    def test_multiple_users_persist_independently(self, config, store):
        e1 = TradingEngine(config, sim_id="m1", store=store)
        e1.add_user("user-2")
        e1.buy("AAPL", dollars=10_000, user_id="user-1")
        e1.buy("NVDA", dollars=60_000, user_id="user-2")
        e1.market.close()

        e2 = TradingEngine.load("m1", store)

        assert e2.portfolio_state("user-1")["cash"] == pytest.approx(90_000, abs=.01)
        assert e2.portfolio_state("user-2")["cash"] == pytest.approx(40_000, abs=.01)
        assert e2.portfolio_state("user-1")["holdings"][0]["ticker"] == "AAPL"
        assert e2.portfolio_state("user-2")["holdings"][0]["ticker"] == "NVDA"
        e2.market.close()


class TestStoreIsolation:
    def test_simulations_are_independent(self, config, store):
        a = TradingEngine(config, sim_id="alpha", store=store)
        b = TradingEngine(config, sim_id="beta", store=store)
        a.buy("AAPL", dollars=10_000)

        assert a.portfolio_state()["cash"] == pytest.approx(90_000, abs=0.01)
        assert b.portfolio_state()["cash"] == pytest.approx(100_000, abs=0.01)
        a.market.close(); b.market.close()

    def test_loading_unknown_sim_raises(self, store):
        with pytest.raises(ValueError, match="No saved simulation"):
            TradingEngine.load("does-not-exist", store)

    def test_year_end_snapshots_persist(self, config, store):
        e1 = TradingEngine(config, sim_id="ye", store=store)
        e1.buy("AAPL", dollars=50_000)
        for _ in range(13):        # past the first December
            e1.advance()
        assert len(e1.year_end_snapshots) >= 1
        e1.market.close()

        e2 = TradingEngine.load("ye", store)
        assert len(e2.year_end_snapshots) >= 1
        assert e2.year_end_snapshots[0]["year"] == 2021
        e2.market.close()

    def test_trade_write_is_atomic(self, config, store):
        """
        REGRESSION: the audit log and the resulting cash/holdings must commit
        together. Written as two separate commits, a crash in between left the
        log recording a BUY that never affected the portfolio -- the books
        didn't balance on reload.
        """
        e1 = TradingEngine(config, sim_id="atomic", store=store)

        def boom(*args, **kwargs):
            raise RuntimeError("simulated crash mid-write")

        original = store._write_portfolio
        store._write_portfolio = boom
        try:
            with pytest.raises(RuntimeError):
                e1.buy("AAPL", dollars=10_000)
        finally:
            store._write_portfolio = original
        e1.market.close()

        e2 = TradingEngine.load("atomic", store)
        # Neither half may have landed.
        assert e2.transaction_log() == []
        assert e2.portfolio_state()["holdings"] == []
        assert e2.portfolio_state()["cash"] == pytest.approx(100_000, abs=0.01)
        e2.market.close()

    def test_year_end_not_duplicated_across_reload(self, config, store):
        """Parking on a December interval and reloading must not double-record."""
        e1 = TradingEngine(config, sim_id="yedup", store=store)
        e1.buy("AAPL", dollars=10_000)
        for _ in range(11):          # land exactly ON 2021-12-31
            e1.advance()
        assert e1.current_date == "2021-12-31"
        e1.market.close()

        e2 = TradingEngine.load("yedup", store)
        e2.advance()                 # leaving December fires the hook
        years = [s["year"] for s in e2.year_end_snapshots]

        assert years.count(2021) == 1
        assert [s["year"] for s in store.load_year_ends("yedup")] == [2021]
        e2.market.close()

    def test_year_end_not_refired_after_crash_mid_advance(self, config, store):
        """
        REGRESSION: advance() saves the year-end snapshot and the new clock
        position as two writes. A crash in between left the snapshot saved but
        the clock parked on December, so resuming re-fired the hook and recorded
        the year twice -- which would make the Annual Report render it twice.
        """
        e1 = TradingEngine(config, sim_id="crash-adv", store=store)
        e1.buy("AAPL", dollars=10_000)
        for _ in range(11):
            e1.advance()                     # park on 2021-12-31

        original = store.save_clock
        store.save_clock = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("crash after snapshot, before clock save"))
        try:
            with pytest.raises(RuntimeError):
                e1.advance()
        finally:
            store.save_clock = original
        e1.market.close()

        e2 = TradingEngine.load("crash-adv", store)
        assert e2.current_date == "2021-12-31"      # clock did not advance
        e2.advance()                                 # resume

        years = [s["year"] for s in e2.year_end_snapshots]
        assert years.count(2021) == 1, f"year-end fired twice: {years}"
        e2.market.close()

    def test_year_end_hook_is_idempotent(self, engine):
        """Firing is guarded by year, so a double-advance can't double-report."""
        fired = []
        engine.register_year_end_hook(lambda y, s: fired.append(y))
        for _ in range(11):
            engine.advance()
        engine._fire_year_end()      # simulate a duplicate/retried trigger
        engine._fire_year_end()
        assert fired.count(2021) <= 1

    def test_market_db_is_read_only(self, engine):
        """The engine must be structurally unable to corrupt historical data."""
        import sqlite3
        with pytest.raises(sqlite3.OperationalError):
            engine.market._conn.execute(
                "UPDATE prices SET adj_close_usd = 0 WHERE ticker = 'AAPL'")
