"""
benchmark.py
============
Passive benchmarks the user's active trading is measured against.

Built now:
  * BuyAndHoldEqualWeight -- $100k split equally across all stocks on day 1,
    never traded again. This is the "did you beat just buying everything?" bar.

Seam for later (see IndexBenchmark):
  * A market-index benchmark (e.g. S&P 500 via ^GSPC). The interface is here so
    it slots in without touching the engine; it just needs ^GSPC data added to
    market.db first. Left as a TODO for the next session per scope.

Both benchmarks read prices through the SAME date-gated MarketData the user's
portfolio uses, so they also cannot see the future.
"""

from .portfolio import EPS


class Benchmark:
    """Interface: a benchmark can report its total USD value at a given date."""

    def value(self, market, as_of):
        raise NotImplementedError


class BuyAndHoldEqualWeight(Benchmark):
    """
    Invest `capital` split equally across every stock that has a price on the
    entry date, buy fractional shares once, and never trade again.
    """

    def __init__(self, market, capital, entry_date):
        self.capital = capital
        self.entry_date = entry_date
        self.shares = {}          # ticker -> shares bought on day 1
        self.skipped = []         # tickers with no price on the entry date

        universe = market.universe()
        # Only spread across stocks that actually have a price on entry day.
        priced = {t: market.price_asof(t, entry_date) for t in universe}
        investable = [t for t, p in priced.items() if p and p > 0]
        self.skipped = [t for t in universe if t not in investable]

        per_stock = capital / len(investable) if investable else 0.0
        for t in investable:
            self.shares[t] = per_stock / priced[t]

    def value(self, market, as_of):
        """Total USD value of the frozen basket at `as_of` (date-gated)."""
        total = 0.0
        for t, sh in self.shares.items():
            price = market.price_asof(t, as_of)
            if price:
                total += sh * price
        return total


class IndexBenchmark(Benchmark):
    """
    "What if I'd just bought the index?" -- `capital` into a single index on the
    entry date, held untouched.

    Defaults to ^SP500TR (S&P 500 TOTAL RETURN) rather than ^GSPC on purpose:
    our stock prices are dividend-adjusted, so benchmarking them against a
    price-only index that omits dividends would unfairly flatter the stocks
    (~14 percentage points over 2021-2025). ^GSPC is still in market.db if you
    want to display the familiar headline number.

    Raises ValueError if the index isn't in market.db, so the engine can degrade
    gracefully rather than crash.
    """

    def __init__(self, market, capital, entry_date, ticker="^SP500TR"):
        self.ticker = ticker
        self.capital = capital
        self.entry_date = entry_date
        entry_price = market.price_asof(ticker, entry_date)
        if not entry_price:
            raise ValueError(
                f"No data for index {ticker}; fetch it into market.db first.")
        self.shares = capital / entry_price

    def value(self, market, as_of):
        price = market.price_asof(self.ticker, as_of)
        return self.shares * price if price else 0.0
