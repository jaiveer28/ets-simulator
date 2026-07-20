"""
convert.py
==========
Turns native-currency prices into USD using the exchange rate FROM THE SAME
HISTORICAL DATE -- never today's rate. This is what makes the simulator
historically honest.

Core idea: instead of remembering "divide for INR, multiply for EUR" all over
the codebase, we precompute one factor per currency per date called
`usd_per_unit`:

    USD:  usd_per_unit = 1.0                (already USD)
    INR:  usd_per_unit = 1 / USDINR         (1 INR is a fraction of a dollar)
    EUR:  usd_per_unit = EURUSD             (1 EUR is ~1.1 dollars)

Then EVERY conversion is simply:  price_usd = price_native * usd_per_unit
"""

import pandas as pd


def build_usd_factors(fx: pd.DataFrame) -> pd.DataFrame:
    """
    From the raw FX table (columns USDINR, EURUSD) build a per-date table of
    'usd_per_unit' factors, one column per currency: USD, INR, EUR.

    Returned DataFrame is indexed by date with columns ['USD', 'INR', 'EUR'].
    """
    factors = pd.DataFrame(index=fx.index)
    factors["USD"] = 1.0                 # a dollar is a dollar
    factors["INR"] = 1.0 / fx["USDINR"]  # USDINR = INR per USD -> invert
    factors["EUR"] = fx["EURUSD"]        # EURUSD = USD per EUR -> use directly
    return factors


def convert_stock_to_usd(stock: pd.DataFrame, currency: str,
                         factors: pd.DataFrame) -> pd.DataFrame:
    """
    Add USD columns to one stock's DataFrame.

    Steps:
      1. Line up the correct currency's daily factor against the stock's OWN
         trading dates (reindex + forward-fill, because a stock might trade on a
         day the FX feed skipped, e.g. differing holidays).
      2. Multiply native prices by that factor to get USD prices.
      3. Record the factor used and whether it had to be forward-filled, so the
         data stays auditable.

    Keeps the native columns untouched and appends: usd_per_unit, fx_filled,
    Close_USD, AdjClose_USD.
    """
    stock = stock.copy()

    # The factor series for THIS stock's currency, aligned to its trading days.
    factor_full = factors[currency]

    # Which of the stock's dates had no exact FX match (so we forward-filled)?
    aligned = factor_full.reindex(stock.index)
    fx_filled = aligned.isna()                     # True where a real rate was missing
    aligned = aligned.ffill().bfill()              # fill from the nearest prior rate

    stock["usd_per_unit"] = aligned
    stock["fx_filled"] = fx_filled.astype(int)     # 1 = rate was carried forward

    # Apply the conversion. For USD stocks the factor is 1.0, so USD == native.
    stock["Close_USD"] = stock["Close"] * aligned
    stock["AdjClose_USD"] = stock["Adj Close"] * aligned

    return stock
