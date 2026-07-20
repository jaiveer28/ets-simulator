"""
store.py
========
Persists the finished data locally so the simulator can run OFFLINE.

We write three things:
  * data/raw/<TICKER>.csv        -- native data exactly as downloaded (audit).
  * data/processed/<TICKER>.csv  -- native + USD columns, human-readable.
  * data/market.db (SQLite)      -- the SOURCE OF TRUTH the sim will query.

Why SQLite as the primary store? The simulator's main operation is a
point-in-time lookup: "what was the USD price of X on date D?" A SQLite table
indexed by (ticker, date) answers that instantly and joins cleanly to the FX
table -- something flat CSVs can't do without reloading everything each time.
The CSVs are kept alongside purely for transparency and easy eyeballing.
"""

import sqlite3

import pandas as pd

from . import config


def _flatten_stock(ticker: str, name: str, currency: str,
                   df: pd.DataFrame, asset_type: str = "stock") -> pd.DataFrame:
    """
    Turn one asset's date-indexed frame into tidy rows (one row per date) with
    stable, lowercase column names, ready to stack with the other assets.

    `asset_type` is 'stock' (tradable) or 'index' (benchmark only). The trading
    engine builds its tradable universe from asset_type='stock', so indices can
    live in the same table without ever becoming buyable.
    """
    out = df.copy()
    out = out.reset_index()  # 'date' becomes a normal column
    out = out.rename(columns={
        "date": "date",
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
        "Close_USD": "close_usd", "AdjClose_USD": "adj_close_usd",
    })
    out.insert(1, "ticker", ticker)
    out.insert(2, "name", name)
    out.insert(3, "currency", currency)
    out.insert(4, "asset_type", asset_type)
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")

    column_order = [
        "date", "ticker", "name", "currency", "asset_type",
        "open", "high", "low", "close", "adj_close", "volume",
        "usd_per_unit", "fx_filled", "close_usd", "adj_close_usd",
    ]
    return out[column_order]


def save_raw_csv(ticker: str, df: pd.DataFrame) -> None:
    """Write the untouched downloaded data for one stock to data/raw/."""
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.RAW_DIR / f"{ticker}.csv")


def save_processed_csv(ticker: str, tidy: pd.DataFrame) -> None:
    """Write the native + USD converted data for one stock to data/processed/."""
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    tidy.to_csv(config.PROCESSED_DIR / f"{ticker}.csv", index=False)


def write_database(all_prices: pd.DataFrame, fx: pd.DataFrame) -> None:
    """
    Write the combined price table and the FX table into SQLite, replacing any
    previous run. Adds an index on (ticker, date) for fast lookups.
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    try:
        # --- prices: one row per (ticker, date) ---
        all_prices.to_sql("prices", conn, if_exists="replace", index=False)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prices_ticker_date "
            "ON prices (ticker, date)"
        )
        # Speeds up "give me the tradable universe" lookups.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prices_asset_type "
            "ON prices (asset_type)"
        )

        # --- fx_rates: the daily rates + the usd_per_unit factors ---
        fx_out = fx.copy().reset_index()
        fx_out["date"] = pd.to_datetime(fx_out["date"]).dt.strftime("%Y-%m-%d")
        fx_out["usd_per_inr"] = 1.0 / fx_out["USDINR"]
        fx_out["usd_per_eur"] = fx_out["EURUSD"]
        fx_out = fx_out.rename(columns={"USDINR": "usdinr", "EURUSD": "eurusd"})
        fx_out.to_sql("fx_rates", conn, if_exists="replace", index=False)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fx_date ON fx_rates (date)")

        conn.commit()
    finally:
        conn.close()
