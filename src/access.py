"""
access.py  --  ACCOUNT / ACCESS LAYER (deliberately separate from the engine)
=============================================================================
This layer answers "WHO is using the simulator and WHAT are they allowed to do".
It is kept completely apart from `src/engine/` (the core simulation) on purpose:

  * The engine knows nothing about accounts, tiers, subscriptions, or limits.
  * A future monetisation layer (paid tiers, premium stock universe, usage
    caps, trial limits) lives HERE and gates the engine -- so it can be
    added without touching a single line of core trading logic.

Right now there is one free user with no restrictions. Every method below is the
natural place a paywall or quota would later slot in; today they all say "yes".
"""

from dataclasses import dataclass


@dataclass
class AccountContext:
    """Identity + entitlements for one user session."""
    user_id: str = "user-1"
    tier: str = "free"          # future: "free" | "premium" | ...

    # --- Entitlement checks (future monetisation hooks) -------------------
    def can_access_stock(self, ticker: str) -> bool:
        # Future: premium tiers might unlock a larger stock universe.
        return True

    def can_advance_time(self) -> bool:
        # Future: free tier might cap how far a simulation can run.
        return True

    def can_create_simulation(self) -> bool:
        # Future: free tier might allow only one active simulation.
        return True
