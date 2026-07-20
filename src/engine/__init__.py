"""
engine
======
The core, UI-agnostic simulation engine. Nothing in here knows about a
frontend, a web framework, or billing -- it is pure trading-simulation logic
that can be driven from a terminal script, a test, or (later) a web UI.

Public surface most callers need:
    from src.engine import TradingEngine, SimConfig, MarketData
"""

from .sim_config import SimConfig
from .market_data import MarketData
from .persistence import SimulationStore
from .trading import TradingEngine, TradeError

__all__ = ["SimConfig", "MarketData", "SimulationStore",
           "TradingEngine", "TradeError"]
