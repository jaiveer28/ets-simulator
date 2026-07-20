"""
Shared pytest fixtures.

Tests run against the REAL market.db (historical data is static and read-only,
so tests are deterministic), but simulation state always goes to a throwaway
store so nothing touches the user's real saved simulations.
"""

import pytest

from src.engine import SimConfig, SimulationStore, TradingEngine


@pytest.fixture
def config():
    """Standard config: $100k, monthly, adjusted-USD prices."""
    return SimConfig()


@pytest.fixture
def engine(config):
    """A fresh in-memory engine (no persistence)."""
    e = TradingEngine(config)
    yield e
    e.close()


@pytest.fixture
def store(tmp_path):
    """A SimulationStore backed by a temp file (survives reconnects)."""
    s = SimulationStore(tmp_path / "test_sims.db")
    yield s
    s.close()


@pytest.fixture
def persisted_engine(config, store):
    """An engine wired to a throwaway persistent store."""
    e = TradingEngine(config, sim_id="test-sim", store=store)
    yield e
    e.market.close()   # leave the store open for reload assertions
