"""
portfolio.py
============
Plain state containers -- WHAT a user owns and WHAT they've done. No pricing or
validation logic lives here (that needs market data and belongs in the engine);
these are deliberately simple, serialisable records.

Every record carries a `user_id`. Right now there's one user, but because state
is keyed by user from day one, supporting many independent users later is an
extension (add more Portfolio objects to the engine's registry), not a rewrite.
"""

from dataclasses import dataclass, field
from typing import Dict, List

# Floating-point tolerance: fractional shares and dollar math accumulate tiny
# errors, so we treat anything within EPS as equal (e.g. "sell everything").
EPS = 1e-9


@dataclass
class Transaction:
    """One immutable record of a buy or sell. This is the audit log entry."""
    user_id: str
    interval_index: int   # which decision point (0-based)
    date: str             # simulated date, ISO 'YYYY-MM-DD'
    ticker: str
    action: str           # "BUY" or "SELL"
    shares: float         # shares transacted (always positive)
    price_usd: float      # execution price per share, in USD
    total_value: float    # shares * price_usd
    cash_after: float     # user's cash balance immediately after the trade

    def as_dict(self):
        return {
            "user_id": self.user_id,
            "interval": self.interval_index,
            "date": self.date,
            "ticker": self.ticker,
            "action": self.action,
            "shares": round(self.shares, 6),
            "price_usd": round(self.price_usd, 4),
            "total_value": round(self.total_value, 2),
            "cash_after": round(self.cash_after, 2),
        }


@dataclass
class Portfolio:
    """A single user's cash, holdings, and transaction history."""
    user_id: str
    starting_capital: float
    cash: float = 0.0
    holdings: Dict[str, float] = field(default_factory=dict)  # ticker -> shares
    transactions: List[Transaction] = field(default_factory=list)

    def __post_init__(self):
        # New portfolios start fully in cash.
        if self.cash == 0.0 and not self.holdings:
            self.cash = self.starting_capital

    def shares_of(self, ticker):
        return self.holdings.get(ticker, 0.0)

    def set_shares(self, ticker, shares):
        """Set a holding, removing it entirely if it rounds to zero."""
        if shares <= EPS:
            self.holdings.pop(ticker, None)
        else:
            self.holdings[ticker] = shares
