"""
market_data.py
==============
Read-only access to the historical prices in market.db, and THE single most
important safety mechanism in the whole engine: the no-lookahead gate.

============================  NO-LOOKAHEAD GUARANTEE  =========================
Every price query REQUIRES an `as_of` date and returns the most recent price
*on or before* that date:

        SELECT <price_field> FROM prices
        WHERE ticker = ? AND date <= ?          <-- the gate
        ORDER BY date DESC LIMIT 1

There is deliberately NO method on this class that returns a price for a future
date, a full price series, or "tomorrow's" price. The only way to see later
prices is to pass a later `as_of` -- and the engine only ever passes the
simulation clock's current date, which moves forward one interval at a time.

So future data cannot leak into a decision, because the code path to fetch it
does not exist. This class is also opened READ-ONLY, so the engine can never
mutate historical data either.
==============================================================================
"""

import sqlite3

from .sim_config import ALLOWED_PRICE_FIELDS


class MarketData:
    """Read-only, date-gated view over the historical price database."""

    def __init__(self, db_path, price_field="adj_close_usd"):
        if price_field not in ALLOWED_PRICE_FIELDS:
            raise ValueError(f"Illegal price_field: {price_field!r}")
        self.price_field = price_field

        # Open the database in READ-ONLY mode via a file: URI. The engine
        # physically cannot write to historical prices.
        uri = f"file:{str(db_path).replace(chr(92), '/')}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)

        # Cache the tradable universe and names ONCE (this is reference data,
        # not price data -- knowing which tickers exist leaks no future prices).
        #
        # Only asset_type='stock' is tradable. Benchmark indices (^SP500TR,
        # ^GSPC) live in the same table but must never be buyable, so they are
        # excluded here while remaining reachable via price_asof().
        self._universe = [r[0] for r in self._conn.execute(
            "SELECT DISTINCT ticker FROM prices WHERE asset_type = 'stock' "
            "ORDER BY ticker")]
        self._names = {t: n for t, n in self._conn.execute(
            "SELECT DISTINCT ticker, name FROM prices")}

    # ---- Reference data (safe: no prices) --------------------------------
    def universe(self):
        """Tradable tickers only (excludes indices), READ FROM THE DATABASE."""
        return list(self._universe)

    def name_of(self, ticker):
        return self._names.get(ticker, ticker)

    def date_bounds(self):
        """Earliest and latest dates present, for sanity/bounds checks."""
        row = self._conn.execute(
            "SELECT MIN(date), MAX(date) FROM prices").fetchone()
        return row[0], row[1]

    # ---- THE date-gated price accessors ----------------------------------
    def price_asof(self, ticker, as_of):
        """
        Most recent <price_field> for `ticker` on or before `as_of`
        (ISO 'YYYY-MM-DD'). Returns a float, or None if the stock has no price
        yet at that date (e.g. a stock that starts trading later).

        This is the ONLY way to read a price, and it can never see the future.
        """
        row = self._conn.execute(
            f"SELECT {self.price_field} FROM prices "
            f"WHERE ticker = ? AND date <= ? "
            f"ORDER BY date DESC LIMIT 1",
            (ticker, as_of),
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def prices_asof(self, as_of, tickers=None):
        """Dict {ticker: price_or_None} for the whole universe (or a subset)."""
        tickers = tickers or self._universe
        return {t: self.price_asof(t, as_of) for t in tickers}

    def close(self):
        self._conn.close()
