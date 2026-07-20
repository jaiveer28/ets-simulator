"""
clock.py
========
The time mechanic. Builds the fixed schedule of decision points and holds the
single forward-only cursor into it.

Key ideas:
  * Interval dates are generated from the CALENDAR (month-ends / Fridays), not
    by peeking at the price data -- so the schedule itself leaks no price info.
    The market_data `date <= as_of` gate then resolves each interval to the last
    actual trading day on or before it.
  * The cursor (`current_index`) only ever moves FORWARD via advance(). There is
    no seek(), no jump-to-date. That is what makes "no free time-travel" true.
"""

import pandas as pd

# pandas frequency aliases for each supported interval type.
_FREQ = {
    "monthly": "ME",     # month-end calendar dates
    "weekly": "W-FRI",   # every Friday
}


class SimulationClock:
    """A forward-only cursor over a fixed list of interval dates."""

    def __init__(self, start_date, end_date, interval="monthly"):
        if interval not in _FREQ:
            raise ValueError(
                f"interval must be one of {list(_FREQ)}, got {interval!r}")
        self.interval = interval

        # Build the schedule once, as ISO 'YYYY-MM-DD' strings (matching the
        # TEXT dates stored in market.db so comparisons line up exactly).
        dates = pd.date_range(start=start_date, end=end_date, freq=_FREQ[interval])
        self.intervals = [d.strftime("%Y-%m-%d") for d in dates]
        if not self.intervals:
            raise ValueError("No interval dates in the given range")

        self.current_index = 0  # start at the first decision point

    # ---- Where are we? ---------------------------------------------------
    @property
    def current_date(self):
        return self.intervals[self.current_index]

    @property
    def total_intervals(self):
        return len(self.intervals)

    def has_next(self):
        return self.current_index < len(self.intervals) - 1

    def is_last(self):
        return self.current_index == len(self.intervals) - 1

    # ---- Year-end detection ----------------------------------------------
    def is_year_end(self, index=None):
        """
        True if the interval at `index` (default: current) closes out its
        calendar year -- i.e. the next interval falls in a different year, or
        this is the final interval. For monthly intervals this fires every
        December; it also generalises correctly to weekly intervals.
        """
        i = self.current_index if index is None else index
        if i >= len(self.intervals) - 1:
            return True  # the final interval closes out the simulation
        return self.intervals[i][:4] != self.intervals[i + 1][:4]

    @property
    def current_year(self):
        return int(self.current_date[:4])

    # ---- The ONLY way time moves -----------------------------------------
    def advance(self):
        """
        Move the cursor forward one interval. Returns True if it moved, False if
        we were already at the final interval (simulation over). Forward only --
        there is intentionally no way to go back or jump.
        """
        if self.has_next():
            self.current_index += 1
            return True
        return False
