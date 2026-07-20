"""
fetch.py
========
Everything that talks to the internet (Yahoo Finance) lives here.

Two public functions:
  * fetch_stock(ticker)  -> daily OHLCV + Adj Close for one stock
  * fetch_fx()           -> daily USD/INR and EUR/USD rates

Both download with retry + exponential backoff so a transient Yahoo hiccup or
rate-limit doesn't crash the whole pipeline -- it retries, then reports a clear
failure if it still can't get the data.
"""

import time

import pandas as pd
import yfinance as yf

from . import config


def _normalise_index_to_date(df: pd.DataFrame) -> pd.DataFrame:
    """
    Yahoo returns a timezone-aware index in each market's LOCAL time
    (US = America/New_York, India = Asia/Kolkata, Paris = Europe/Paris).

    We only care about the calendar date, and we need every stock + FX series
    to share the SAME date keys so we can join them. This collapses the index
    to a plain, timezone-free date (midnight) using the local calendar day.
    """
    df = df.copy()
    df.index = pd.to_datetime(df.index.date)   # local date -> naive datetime
    df.index.name = "date"
    return df


def _download_with_retry(download_fn, label: str) -> pd.DataFrame:
    """
    Run a download function, retrying on failure with exponential backoff.

    `download_fn` is a zero-argument callable that returns a DataFrame; wrapping
    it this way lets us reuse the same retry logic for stocks and for FX.
    Raises RuntimeError if every attempt fails, so the caller can report which
    ticker died rather than getting a silent empty frame.
    """
    last_error = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            df = download_fn()
            if df is None or df.empty:
                raise ValueError("Yahoo returned no rows (empty DataFrame)")
            return df
        except Exception as exc:  # noqa: BLE001 - we want to catch anything network-y
            last_error = exc
            wait = config.RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
            print(f"    ! {label}: attempt {attempt}/{config.MAX_RETRIES} failed "
                  f"({exc}). Retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"{label}: all {config.MAX_RETRIES} attempts failed. "
                       f"Last error: {last_error}")


def fetch_stock(ticker: str) -> pd.DataFrame:
    """
    Download one stock's daily history over the configured date range.

    Uses auto_adjust=False on purpose so we keep BOTH the raw Close AND a
    separate 'Adj Close' (split/dividend-adjusted). Returns a DataFrame indexed
    by date with columns: Open, High, Low, Close, Adj Close, Volume.
    """
    def _dl():
        t = yf.Ticker(ticker)
        return t.history(
            start=config.START_DATE,
            end=config.END_DATE,
            auto_adjust=False,   # keep raw OHLC + a real 'Adj Close' column
            actions=True,        # also pull dividends/splits (context, not stored)
        )

    raw = _download_with_retry(_dl, label=ticker)
    raw = _normalise_index_to_date(raw)

    # Keep only the six columns we care about; drop Dividends / Stock Splits.
    keep = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    raw = raw[keep]
    return raw


def fetch_fx() -> pd.DataFrame:
    """
    Download the two FX pairs we need and return a tidy DataFrame indexed by
    date with two columns: 'USDINR' (1 USD in INR) and 'EURUSD' (1 EUR in USD).

    We only keep each pair's Close, which is the daily rate.
    """
    def _dl():
        return yf.download(
            list(config.FX_TICKERS.values()),
            start=config.FX_START_DATE,
            end=config.END_DATE,
            auto_adjust=False,
            progress=False,
            group_by="ticker",
        )

    raw = _download_with_retry(_dl, label="FX (USDINR=X, EURUSD=X)")

    # `raw` has MultiIndex columns like ('USDINR=X', 'Close'). Pull each pair's
    # Close into a simple, well-named column.
    out = pd.DataFrame(index=raw.index)
    for nice_name, yf_symbol in config.FX_TICKERS.items():
        out[nice_name] = raw[(yf_symbol, "Close")]

    out = _normalise_index_to_date(out)
    # FX can have the odd missing day; forward-fill so every weekday has a rate,
    # then back-fill only the very first rows if they start on a gap.
    out = out.sort_index().ffill().bfill()
    return out
