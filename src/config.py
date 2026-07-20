"""
config.py
=========
Single source of truth for WHAT we fetch and WHERE it goes.

Keeping all of this in one place (instead of sprinkling tickers and dates
through the code) means that if we ever want to add a stock, change the date
range, or move the output folder, we edit exactly one file.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Folder layout
# ---------------------------------------------------------------------------
# PROJECT_ROOT is the folder that CONTAINS this `src` package, resolved
# relative to this file so the code works no matter where it's launched from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"            # CSVs exactly as downloaded (audit trail)
PROCESSED_DIR = DATA_DIR / "processed"  # USD-converted, validated CSVs
DB_PATH = DATA_DIR / "market.db"      # SQLite source of truth for the simulator

# ---------------------------------------------------------------------------
# Date range for the simulation
# ---------------------------------------------------------------------------
# NOTE: yfinance treats `end` as EXCLUSIVE, so END_DATE = 2026-01-01 means the
# last row we can get is the final trading day of 2025 (2026-01-01 is a market
# holiday everywhere anyway). That matches the "Jan 2021 -> Jan 2026" window.
START_DATE = "2021-01-01"
END_DATE = "2026-01-01"

# We fetch FX a couple of weeks earlier than the stocks so that the very first
# stock trading day always has a prior exchange rate to fall back on.
FX_START_DATE = "2020-12-15"

# ---------------------------------------------------------------------------
# The 10 stocks
# ---------------------------------------------------------------------------
# Each entry records the Yahoo Finance ticker, a human-readable name, the
# NATIVE trading currency, and the exchange (for documentation / holiday
# context). `currency` drives how we convert each price to USD later.
STOCKS = [
    {"ticker": "RELIANCE.NS",  "name": "Reliance Industries", "currency": "INR", "exchange": "NSE (India)"},
    {"ticker": "HDFCBANK.NS",  "name": "HDFC Bank",           "currency": "INR", "exchange": "NSE (India)"},
    # Maruti Suzuki replaced Tata Motors here (2026-07-18).
    # WHY: Tata Motors demerged on 2025-10-14. Yahoo retired TATAMOTORS.NS and
    # carries the history under TMPV.NS, which shows a ~-40% single-day step at
    # the demerger. Adjusted close corrects for splits/dividends but NOT
    # demergers, so that drop is real in the data and would have been booked as
    # a fake 40% loss by any portfolio holding it -- teaching a false lesson.
    # Maruti is a like-for-like swap: same auto sector, same NSE calendar,
    # 1236 rows over the full range, and zero >20% days (no corporate actions).
    {"ticker": "MARUTI.NS",    "name": "Maruti Suzuki",      "currency": "INR", "exchange": "NSE (India)"},
    {"ticker": "AAPL",         "name": "Apple",               "currency": "USD", "exchange": "NASDAQ (US)"},
    {"ticker": "TSLA",         "name": "Tesla",               "currency": "USD", "exchange": "NASDAQ (US)"},
    {"ticker": "NVDA",         "name": "Nvidia",              "currency": "USD", "exchange": "NASDAQ (US)"},
    {"ticker": "AMZN",         "name": "Amazon",              "currency": "USD", "exchange": "NASDAQ (US)"},
    {"ticker": "JPM",          "name": "JPMorgan Chase",      "currency": "USD", "exchange": "NYSE (US)"},
    {"ticker": "ASML",         "name": "ASML Holding",        "currency": "USD", "exchange": "NASDAQ ADR (US)"},
    {"ticker": "MC.PA",        "name": "LVMH",                "currency": "EUR", "exchange": "Euronext Paris (FR)"},
]

# ---------------------------------------------------------------------------
# Market indices (benchmarks -- NOT tradable)
# ---------------------------------------------------------------------------
# These are stored in the same `prices` table but tagged asset_type='index', so
# the trading engine's tradable universe excludes them. They exist purely as
# benchmark comparisons.
#
# WHY BOTH: ^GSPC is the famous "S&P 500" number but it is PRICE-ONLY -- it
# excludes dividends. Our 10 stocks are valued on dividend-ADJUSTED prices, so
# comparing them to ^GSPC would unfairly flatter the stocks (over 2021-2025 the
# gap is ~14 percentage points). ^SP500TR is the Total Return version and is the
# correct like-for-like benchmark; ^GSPC is kept for display/recognition.
INDICES = [
    {"ticker": "^SP500TR", "name": "S&P 500 (Total Return)", "currency": "USD", "exchange": "Index (US)"},
    {"ticker": "^GSPC",    "name": "S&P 500 (price only)",   "currency": "USD", "exchange": "Index (US)"},
]

# The index the engine uses as its default benchmark.
DEFAULT_INDEX_BENCHMARK = "^SP500TR"

# ---------------------------------------------------------------------------
# Foreign exchange
# ---------------------------------------------------------------------------
# Base currency for the whole simulator is USD. We need to turn INR and EUR
# prices into USD using the rate FROM THE SAME HISTORICAL DATE.
#
# Yahoo FX conventions (confirmed empirically):
#   USDINR=X  ~ 83    -> 1 USD = 83 INR   (to get USD: divide the INR price)
#   EURUSD=X  ~ 1.10  -> 1 EUR = 1.10 USD (to get USD: multiply the EUR price)
#
# To make conversion uniform everywhere, convert.py turns these into a single
# "usd_per_unit" factor per currency, so USD = native_price * usd_per_unit.
FX_TICKERS = {
    "USDINR": "USDINR=X",
    "EURUSD": "EURUSD=X",
}

# ---------------------------------------------------------------------------
# Data-quality thresholds
# ---------------------------------------------------------------------------
# Any single-day move larger than this (on ADJUSTED close) gets flagged for a
# human to eyeball. Real crashes and mergers can exceed it, so this is a
# "please verify", not an auto-delete.
SPIKE_THRESHOLD = 0.20  # 20%

# How many times to retry a failed download, and the base seconds to wait
# between attempts (exponential backoff: 2s, 4s, 8s, ...).
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2
