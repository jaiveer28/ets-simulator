"""
metrics.py
==========
Pure, self-contained risk-metric maths. Every function takes plain numbers and
returns plain numbers -- no engine, no database, no I/O -- so each one can be
unit-tested against hand-computed values.

The three metrics, in plain English:

  * Volatility   -- how bumpy the ride was. The standard deviation of the
                    portfolio's periodic returns. Higher = more up-and-down.
  * Max drawdown -- the worst peak-to-trough fall. If the portfolio hit $130k
                    then sank to $110k before recovering, that's a ~15% drawdown.
                    It answers "what's the biggest loss I'd have sat through?"
  * Sharpe ratio -- return earned per unit of risk. (Return above the risk-free
                    rate) / volatility. Higher = better reward for the risk taken.
                    Roughly: <1 modest, 1-2 good, >2 excellent.

NO-LOOKAHEAD: these functions only see the value/return series they are handed.
The caller (analytics.py) builds that series bounded by the current simulated
interval, so future data can never reach here.
"""

import math

# Monthly simulation -> 12 periods per year, used to annualise.
PERIODS_PER_YEAR = 12


def simple_returns(values):
    """
    Period-over-period simple returns from a value series.
    [100, 110, 99] -> [0.10, -0.10]. Skips non-positive prior values.
    """
    out = []
    for prev, cur in zip(values, values[1:]):
        out.append((cur / prev - 1.0) if prev > 0 else 0.0)
    return out


def _stdev(xs):
    """Sample standard deviation (ddof=1). None if fewer than 2 points."""
    n = len(xs)
    if n < 2:
        return None
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)


def volatility(returns, periods_per_year=PERIODS_PER_YEAR):
    """
    Annualised volatility = stdev(periodic returns) * sqrt(periods per year).
    Returned as a FRACTION (0.18 = 18%). None if not enough data.
    """
    sd = _stdev(returns)
    if sd is None:
        return None
    return sd * math.sqrt(periods_per_year)


def max_drawdown(values):
    """
    Largest peak-to-trough decline in the value series, as a POSITIVE fraction
    (0.15 = a 15% drawdown). 0.0 if the series only ever rose. None if empty.
    """
    if not values:
        return None
    peak = values[0]
    worst = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            drop = (v - peak) / peak      # <= 0
            if drop < worst:
                worst = drop
    return -worst                          # report as a positive magnitude


def sharpe_ratio(returns, risk_free_annual, periods_per_year=PERIODS_PER_YEAR):
    """
    Annualised Sharpe ratio.

    Per period: excess = mean(return) - risk_free_per_period, divided by the
    stdev of returns, then scaled by sqrt(periods per year) to annualise.

    Returns None when it isn't defined: fewer than 2 returns, or zero
    volatility (a flat/all-cash portfolio has no risk to adjust for, so the
    ratio would divide by zero -- we surface "n/a", never infinity).
    """
    sd = _stdev(returns)
    if sd is None or sd == 0:
        return None
    rf_per_period = risk_free_annual / periods_per_year
    mean_excess = (sum(returns) / len(returns)) - rf_per_period
    return (mean_excess / sd) * math.sqrt(periods_per_year)


def risk_metrics(values, risk_free_annual, periods_per_year=PERIODS_PER_YEAR):
    """
    Bundle all three metrics for a value series into one dict, with each value a
    fraction (or None when undefined). `n_periods` is how many returns fed the
    calculation, so the UI can warn when the sample is very small.
    """
    rets = simple_returns(values)
    return {
        "n_periods": len(rets),
        "volatility": volatility(rets, periods_per_year),
        "max_drawdown": max_drawdown(values),
        "sharpe": sharpe_ratio(rets, risk_free_annual, periods_per_year),
        "risk_free_rate": risk_free_annual,
    }
