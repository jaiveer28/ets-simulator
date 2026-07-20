"""
sim_config.py
=============
Every knob of the simulation lives here so NOTHING is hardcoded in the trading
logic. Want $50k starting capital instead of $100k? Weekly intervals instead of
monthly? A different price column? Change it here (or pass overrides) -- the
engine reads these values, it never assumes them.
"""

from dataclasses import dataclass
from pathlib import Path

from .. import config as data_config  # the data-layer config (DB path, etc.)

# Price columns the engine is allowed to trade/value on. Whitelisted so a
# config value can never be used to inject arbitrary SQL into the price query.
ALLOWED_PRICE_FIELDS = {"adj_close_usd", "close_usd", "close", "adj_close"}


@dataclass
class SimConfig:
    """Tunable parameters for one simulation run."""

    # --- Money ---
    starting_capital: float = 100_000.0
    # A *suggested* default the UI can offer; the engine does not force it.
    default_allocation_per_stock: float = 10_000.0

    # --- Time ---
    # "monthly" -> one decision point at each month-end (60 over 5 years).
    # "weekly"  -> one at each Friday. Anything else raises in the clock.
    interval: str = "monthly"
    start_date: str = "2021-01-01"
    end_date: str = "2025-12-31"

    # --- Pricing ---
    # adj_close_usd = adjusted close in USD: handles splits/dividends cleanly and
    # is already currency-converted with same-date FX. Used for BOTH trade
    # execution and portfolio valuation so everything is internally consistent.
    price_field: str = "adj_close_usd"

    # --- Benchmarks ---
    # Total-return S&P 500 by default: our stocks are valued on dividend-adjusted
    # prices, so benchmarking against the price-only ^GSPC would unfairly flatter
    # them (a ~14 percentage-point gap over 2021-2025).
    index_benchmark_ticker: str = data_config.DEFAULT_INDEX_BENCHMARK

    # --- Risk metrics ---
    # Annual risk-free rate used in the Sharpe ratio. Default 3% ~= the average
    # 3-month US Treasury-bill yield over the Jan-2021 -> Dec-2025 window (near 0%
    # in 2021, ~5% through 2023-24). It is the "return you could earn risk-free",
    # subtracted from portfolio return before dividing by volatility. Configurable
    # so a different era or assumption can be modelled without code changes.
    risk_free_rate: float = 0.03

    # --- Data source ---
    # Defaults to the data layer's market.db but can point anywhere.
    db_path: Path = data_config.DB_PATH
    # Where user/simulation state is saved. Deliberately a DIFFERENT file from
    # market.db, which is read-only reference data.
    sim_db_path: Path = data_config.DATA_DIR / "simulations.db"

    def __post_init__(self):
        if self.price_field not in ALLOWED_PRICE_FIELDS:
            raise ValueError(
                f"price_field must be one of {sorted(ALLOWED_PRICE_FIELDS)}, "
                f"got {self.price_field!r}"
            )
        if self.starting_capital <= 0:
            raise ValueError("starting_capital must be positive")
