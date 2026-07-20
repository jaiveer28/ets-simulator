"""
trading.py
==========
The TradingEngine -- the one object a UI (or a test script) drives. It wires
together the clock, the read-only market data, the per-user portfolios, the
benchmarks, and (optionally) persistence.

    buy / sell / hold        -- act at the current interval
    buy_max / sell_all       -- exact "invest everything" / "liquidate" helpers
    advance                  -- reveal the next interval's prices (moves time)
    current_price(s)         -- prices AS OF now (never the future)
    portfolio_state          -- cash, holdings, total value, P&L
    transaction_log          -- full audit history
    benchmark_state          -- buy-and-hold + S&P 500 comparison
    register_year_end_hook   -- callback fired at each calendar year-end
    TradingEngine.load(...)  -- restore a saved simulation

NO-LOOKAHEAD: the engine only ever asks MarketData for prices at
`self.clock.current_date`. The public price methods take NO date argument, so a
caller cannot request the future even by mistake.

-----------------------------------------------------------------------------
CORPORATE ACTIONS -- A GENERAL LIMITATION TO KNOW ABOUT
-----------------------------------------------------------------------------
Prices come from adjusted close, which corrects for SPLITS and DIVIDENDS but
NOT for demergers/spin-offs. If a holding demerges, its price drops by the value
of the spun-off entity and this engine books that as a real loss, because it has
no concept of receiving shares in the new company.

This bit us with Tata Motors (demerged 2025-10-14, a ~-40% step), which is why
that stock was replaced with Maruti Suzuki in config.py. The current 10-stock
universe has NO demergers over 2021-2025, so the simulation is clean today --
but ANY new stock added later must be checked for corporate actions first.
The data layer's >20% single-day flag is what surfaces them.
-----------------------------------------------------------------------------
"""

import math
import uuid

from .benchmark import BuyAndHoldEqualWeight, IndexBenchmark
from .clock import SimulationClock
from .market_data import MarketData
from .portfolio import EPS, Portfolio, Transaction
from .sim_config import SimConfig


class TradeError(Exception):
    """Raised when a trade is rejected (bad input, insufficient cash/shares)."""


def _tolerance(value):
    """
    Comparison tolerance for "do you have enough?" checks.

    Why this isn't just EPS: portfolio_state() reports shares rounded to 6dp and
    cash to 2dp. A UI naturally echoes those displayed numbers straight back
    into sell()/buy() for "sell everything" / "invest all my cash". A raw 1e-9
    epsilon is far tighter than that display rounding, so such a trade would be
    spuriously rejected. We therefore allow an absolute slack of 1e-6 (larger
    than any 6dp rounding error) and scale it for very large positions.
    """
    return max(1e-6, abs(value) * 1e-9)


def _floor_to(value, decimals):
    """
    Round DOWN to `decimals` places. Used for reported shares/cash so the number
    a user sees is never GREATER than what they actually hold -- echoing a
    displayed figure back into a trade can then never overshoot.
    """
    factor = 10 ** decimals
    return math.floor(value * factor) / factor


class TradingEngine:
    """Core simulation engine. UI-agnostic and user-agnostic."""

    def __init__(self, config=None, market=None, default_user="user-1",
                 sim_id=None, store=None):
        self.config = config or SimConfig()
        # Dependency-injectable market data (real DB by default) -- makes testing
        # and swapping data sources easy.
        self.market = market or MarketData(
            self.config.db_path, price_field=self.config.price_field)
        self.clock = SimulationClock(
            self.config.start_date, self.config.end_date, self.config.interval)

        # --- persistence (optional; None = pure in-memory) ---
        self.sim_id = sim_id or f"sim-{uuid.uuid4().hex[:8]}"
        self.store = store
        is_new = True
        if self.store:
            is_new = not self.store.exists(self.sim_id)
            if is_new:
                self.store.create_simulation(
                    self.sim_id, self.config, self.clock.current_index)

        # Multi-user ready: portfolios are keyed by user_id. We create one now.
        # persist=is_new so restoring an existing sim never clobbers saved state.
        self.default_user = default_user
        self.portfolios = {}
        self.add_user(default_user, persist=is_new)

        # --- benchmarks, both fixed at the first interval date ---
        entry = self.clock.intervals[0]
        self.benchmark = BuyAndHoldEqualWeight(
            self.market, self.config.starting_capital, entry)
        # S&P 500 benchmark. Uses the TOTAL RETURN index by default so it is
        # like-for-like with our dividend-adjusted stock prices. If the index
        # data isn't in market.db, we degrade gracefully to None.
        try:
            self.index_benchmark = IndexBenchmark(
                self.market, self.config.starting_capital, entry,
                ticker=self.config.index_benchmark_ticker)
        except ValueError:
            self.index_benchmark = None

        # Year-end hooks: callbacks(year:int, snapshot:dict).
        self._year_end_hooks = []
        self.year_end_snapshots = []
        self.register_year_end_hook(self._default_year_end_handler)

    # ================= restore ============================================
    @classmethod
    def load(cls, sim_id, store, market=None):
        """
        Restore a saved simulation: rebuild the clock at its saved position and
        repopulate cash/holdings/transactions. Benchmarks are RECOMPUTED (they
        are deterministic functions of entry date + capital + market data), so
        they can never drift out of sync with what was stored.
        """
        row = store.load_simulation(sim_id)
        if row is None:
            raise ValueError(f"No saved simulation with sim_id={sim_id!r}")

        # Run under the config the simulation STARTED with, not current defaults.
        config = SimConfig(
            starting_capital=row["starting_capital"],
            interval=row["interval"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            price_field=row["price_field"],
        )
        engine = cls(config=config, market=market, sim_id=sim_id, store=store)

        # Restore the clock position, bounds-checked.
        idx = int(row["current_index"])
        if not 0 <= idx < engine.clock.total_intervals:
            raise ValueError(
                f"Saved current_index {idx} is outside the interval range "
                f"0..{engine.clock.total_intervals - 1}")
        engine.clock.current_index = idx

        # Restore portfolios. We set fields explicitly rather than via the
        # constructor, so Portfolio.__post_init__ can't "helpfully" reset a
        # legitimately-zero cash balance back to the starting capital.
        saved = store.load_portfolios(sim_id)
        if saved:
            engine.portfolios = {}
            for uid, data in saved.items():
                p = Portfolio(user_id=uid,
                              starting_capital=data["starting_capital"])
                p.cash = data["cash"]
                p.holdings = dict(data["holdings"])
                p.transactions = [
                    Transaction(
                        user_id=t["user_id"],
                        interval_index=t["interval_index"],
                        date=t["date"],
                        ticker=t["ticker"],
                        action=t["action"],
                        shares=t["shares"],
                        price_usd=t["price_usd"],
                        total_value=t["total_value"],
                        cash_after=t["cash_after"],
                    ) for t in data["transactions"]
                ]
                engine.portfolios[uid] = p
            if engine.default_user not in engine.portfolios:
                engine.default_user = next(iter(engine.portfolios))

        engine.year_end_snapshots = store.load_year_ends(sim_id)
        return engine

    # ================= user / account management ==========================
    def add_user(self, user_id, persist=True):
        """Register an independent portfolio. Multi-user is just more of these."""
        if user_id not in self.portfolios:
            self.portfolios[user_id] = Portfolio(
                user_id=user_id, starting_capital=self.config.starting_capital)
            if self.store and persist:
                self.store.save_portfolio(self.sim_id, self.portfolios[user_id])
        return self.portfolios[user_id]

    def _portfolio(self, user_id=None):
        return self.portfolios[user_id or self.default_user]

    # ================= time / prices ======================================
    @property
    def current_date(self):
        return self.clock.current_date

    @property
    def interval_index(self):
        return self.clock.current_index

    def current_price(self, ticker):
        """Price of `ticker` AS OF the current simulated date (never future)."""
        return self.market.price_asof(ticker, self.clock.current_date)

    def current_prices(self):
        """{ticker: price} for the whole tradable universe, as of now."""
        return self.market.prices_asof(self.clock.current_date)

    def universe(self):
        """Tradable tickers, data-driven from the database (excludes indices)."""
        return self.market.universe()

    # ================= trading ============================================
    def _require_price(self, ticker):
        if ticker not in self.market.universe():
            raise TradeError(f"Unknown or non-tradable ticker {ticker!r}. "
                             f"Available: {', '.join(self.market.universe())}")
        price = self.current_price(ticker)
        if price is None or price <= 0:
            raise TradeError(
                f"No price for {ticker} as of {self.current_date} "
                f"(it may not trade yet).")
        return price

    @staticmethod
    def _resolve_quantity(price, dollars, shares, kind):
        """Turn a dollars-or-shares request into (shares, dollar_value)."""
        if (dollars is None) == (shares is None):
            raise TradeError(
                f"To {kind}, specify exactly one of `dollars`, `shares`, "
                f"or `fraction`.")
        if dollars is not None:
            if dollars <= 0:
                raise TradeError("Dollar amount must be positive.")
            return dollars / price, dollars
        if shares <= 0:
            raise TradeError("Share count must be positive.")
        return shares, shares * price

    @staticmethod
    def _check_one_of(dollars, shares, fraction, kind):
        """Exactly one of the three ways to size a trade must be given."""
        given = sum(x is not None for x in (dollars, shares, fraction))
        if given != 1:
            raise TradeError(
                f"To {kind}, specify exactly one of `dollars`, `shares`, "
                f"or `fraction` (got {given}).")
        if fraction is not None and not 0 < fraction <= 1:
            raise TradeError("`fraction` must be greater than 0 and at most 1.")

    def buy(self, ticker, dollars=None, shares=None, fraction=None,
            user_id=None):
        """
        Buy a position in `ticker` at the current price, sized one of three ways:

            dollars=5000     spend a dollar amount
            shares=10        buy a share count
            fraction=0.25    spend 25% of AVAILABLE CASH  <- intent-based

        Prefer `fraction` (or buy_max) in a UI: it is computed from the exact
        internal cash balance, so no displayed/rounded number is ever sent back
        to the engine and the "insufficient cash" rounding trap cannot occur.

        Rejects (TradeError) if cash is insufficient.
        """
        self._check_one_of(dollars, shares, fraction, "buy")
        p = self._portfolio(user_id)

        # Resolve intent against the EXACT internal balance, never a display value.
        if fraction is not None:
            dollars = p.cash * fraction

        price = self._require_price(ticker)
        qty, cost = self._resolve_quantity(price, dollars, shares, "buy")

        if cost > p.cash + _tolerance(p.cash):
            raise TradeError(
                f"Insufficient cash to buy {ticker}: need ${cost:,.2f}, "
                f"have ${p.cash:,.2f}.")
        cost = min(cost, p.cash)      # clamp trivial overshoot from rounding
        qty = cost / price            # keep shares consistent with actual spend

        p.cash = max(0.0, p.cash - cost)
        p.set_shares(ticker, p.shares_of(ticker) + qty)
        return self._log(p, ticker, "BUY", qty, price, cost)

    def buy_max(self, ticker, user_id=None):
        """Invest ALL available cash in `ticker` (exact internal balance)."""
        p = self._portfolio(user_id)
        if p.cash <= 0:
            raise TradeError("No cash available to invest.")
        return self.buy(ticker, fraction=1.0, user_id=user_id)

    def sell(self, ticker, dollars=None, shares=None, fraction=None,
             user_id=None):
        """
        Sell part or all of a position in `ticker`, sized one of three ways:

            dollars=5000     sell a dollar amount's worth
            shares=10        sell a share count
            fraction=0.5     sell 50% of the HOLDING  <- intent-based

        Prefer `fraction` (or sell_all) in a UI: it is computed from the exact
        internal share count, so no displayed/rounded number round-trips and the
        "insufficient shares" rounding trap cannot occur.

        Rejects (TradeError) if the user doesn't hold enough shares.
        No short-selling: you can never end up below zero shares.
        """
        self._check_one_of(dollars, shares, fraction, "sell")
        p = self._portfolio(user_id)
        held = p.shares_of(ticker)
        if held <= EPS:
            raise TradeError(f"No holdings in {ticker} to sell.")

        price = self._require_price(ticker)

        # Resolve intent against the EXACT internal holding, never a display value.
        if fraction is not None:
            shares = held * fraction

        qty, _ = self._resolve_quantity(price, dollars, shares, "sell")
        if qty > held + _tolerance(held):
            raise TradeError(
                f"Insufficient shares of {ticker}: trying to sell {qty:.6f}, "
                f"only hold {held:.6f}.")
        qty = min(qty, held)  # clamp tiny float overshoot when selling "all"

        # Dust sweep: if this sale would leave a sliver too small to display
        # (e.g. the user sold the floored, displayed share count), liquidate the
        # whole position instead. Otherwise the UI shows a phantom "0.000000
        # shares / $0.00" row forever, which is confusing and useless.
        if 0 < (held - qty) <= _tolerance(held):
            qty = held

        proceeds = qty * price
        p.cash += proceeds
        p.set_shares(ticker, held - qty)
        return self._log(p, ticker, "SELL", qty, price, proceeds)

    def sell_all(self, ticker, user_id=None):
        """Liquidate the ENTIRE position in `ticker` (exact internal holding)."""
        p = self._portfolio(user_id)
        if p.shares_of(ticker) <= EPS:
            raise TradeError(f"No holdings in {ticker} to sell.")
        return self.sell(ticker, fraction=1.0, user_id=user_id)

    def hold(self, user_id=None):
        """Explicit 'do nothing this interval'. Provided for API clarity."""
        return None

    def _log(self, portfolio, ticker, action, qty, price, value):
        txn = Transaction(
            user_id=portfolio.user_id,
            interval_index=self.clock.current_index,
            date=self.current_date,
            ticker=ticker,
            action=action,
            shares=qty,
            price_usd=price,
            total_value=value,
            cash_after=portfolio.cash,
        )
        portfolio.transactions.append(txn)
        # Write-through persistence: state hits disk immediately, so a crash or
        # a closed browser tab loses nothing. record_trade() writes the log
        # entry and the resulting cash/holdings in ONE transaction -- writing
        # them separately would let a crash in between leave the audit log
        # recording a trade that never affected the portfolio.
        if self.store:
            self.store.record_trade(self.sim_id, txn, portfolio)
        return txn

    # ================= advancing time =====================================
    def advance(self):
        """
        Finish the current interval and reveal the next one's prices.

        If the interval we're leaving closes out a calendar year, fire the
        year-end hooks FIRST (using that year-end's fully-revealed prices --
        still not the future). Returns the new date, or None if the simulation
        is over.
        """
        if self.clock.is_year_end():
            self._fire_year_end()

        moved = self.clock.advance()
        if self.store:
            self.store.save_clock(self.sim_id, self.clock.current_index)
        return self.current_date if moved else None

    # ================= year-end hook ======================================
    def register_year_end_hook(self, callback):
        """Register callback(year:int, snapshot:dict), called at each year-end."""
        self._year_end_hooks.append(callback)

    def _default_year_end_handler(self, year, snapshot):
        """Records the snapshot in memory and (if persisting) on disk."""
        self.year_end_snapshots.append(snapshot)
        if self.store:
            self.store.save_year_end(
                self.sim_id, year, snapshot["date"], snapshot)

    def _fire_year_end(self):
        year = self.clock.current_year

        # IDEMPOTENT: never fire the same year twice.
        # advance() writes the year-end snapshot and the new clock position as
        # two separate writes. A crash in between leaves the snapshot saved but
        # the clock still parked on December, so resuming would re-fire the hook
        # and record the year again. Guarding here also makes the engine safe
        # against a UI that double-submits or retries an advance request.
        if any(s.get("year") == year for s in self.year_end_snapshots):
            return

        snapshot = {
            "year": year,
            "date": self.current_date,
            "interval_index": self.clock.current_index,
            "portfolios": {uid: self.portfolio_state(uid)
                           for uid in self.portfolios},
            "benchmark": self.benchmark_state(),
        }
        for cb in self._year_end_hooks:
            cb(year, snapshot)

    # ================= reporting ==========================================
    def portfolio_state(self, user_id=None):
        """
        Snapshot of a user's portfolio valued at the current (revealed) prices:
        cash, holdings (with per-stock value), total value, and P&L vs start.
        """
        p = self._portfolio(user_id)
        holdings = []
        holdings_value = 0.0
        for ticker in sorted(p.holdings):
            shares = p.holdings[ticker]
            price = self.current_price(ticker)
            value = shares * price if price else 0.0
            holdings_value += value
            holdings.append({
                "ticker": ticker,
                # Floored, not rounded: a displayed share count must never
                # exceed the true holding, or echoing it into sell() overshoots.
                "shares": _floor_to(shares, 6),
                "name": self.market.name_of(ticker),
                "price_usd": round(price, 4) if price else None,
                "value_usd": round(value, 2),
            })

        total = p.cash + holdings_value
        pnl = total - p.starting_capital
        pnl_pct = pnl / p.starting_capital * 100.0
        return {
            "user_id": p.user_id,
            "date": self.current_date,
            "interval_index": self.clock.current_index,
            # Floored for the same reason as shares above (see _floor_to).
            "cash": _floor_to(p.cash, 2),
            "holdings": holdings,
            "holdings_value": round(holdings_value, 2),
            "total_value": round(total, 2),
            "starting_capital": p.starting_capital,
            "pnl_dollars": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        }

    def transaction_log(self, user_id=None):
        """Full transaction history for a user, as a list of dicts."""
        return [t.as_dict() for t in self._portfolio(user_id).transactions]

    def _benchmark_entry(self, name, value):
        start = self.config.starting_capital
        return {
            "name": name,
            "value_usd": round(value, 2),
            "pnl_dollars": round(value - start, 2),
            "pnl_pct": round((value - start) / start * 100.0, 2),
        }

    def benchmark_state(self):
        """
        Both benchmarks valued at the current date:
          buy_and_hold -- $100k equally split across the 10 stocks on day 1
          index        -- the same $100k in the S&P 500 (total return)
        `index` is None if the index data isn't present in market.db.
        """
        bh = self._benchmark_entry(
            "Buy & Hold (equal-weight stocks)",
            self.benchmark.value(self.market, self.current_date))

        idx = None
        if self.index_benchmark:
            idx = self._benchmark_entry(
                self.market.name_of(self.index_benchmark.ticker),
                self.index_benchmark.value(self.market, self.current_date))
            idx["ticker"] = self.index_benchmark.ticker

        return {"buy_and_hold": bh, "index": idx}

    def close(self):
        self.market.close()
        if self.store:
            self.store.close()
