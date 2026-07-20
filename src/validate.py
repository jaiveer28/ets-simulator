"""
validate.py
===========
Sanity checks on each stock's data. This does NOT throw data away -- it returns
a report of anything suspicious so a human can decide. Silent bad data is far
more dangerous in a trading sim than a loud warning.

Checks per stock:
  1. Coverage       -- first date, last date, number of trading days.
  2. Duplicate dates -- there should be exactly one row per date.
  3. Internal gaps  -- long stretches with no trading days (beyond normal
                       weekends/holidays) that might signal missing data.
  4. Price spikes   -- single-day ADJUSTED-close moves beyond the threshold,
                       which are either real events (crashes/mergers) or data
                       errors; either way, worth a human look.
  5. Non-positive / NaN prices -- obviously broken rows.
"""

import pandas as pd

from . import config


def validate_stock(ticker: str, name: str, df: pd.DataFrame) -> dict:
    """
    Run all checks on one stock and return a dictionary report.

    We run spike detection on 'Adj Close' specifically: raw Close would show
    stock splits (e.g. a 10:1 split) as fake ~90% crashes, whereas Adj Close is
    already split-adjusted, so a flagged move there is a genuine price event.
    """
    report = {
        "ticker": ticker,
        "name": name,
        "rows": len(df),
        "first_date": None,
        "last_date": None,
        "duplicate_dates": 0,
        "nan_or_nonpositive": 0,
        "largest_gap_days": 0,
        "spikes": [],       # list of (date, pct_change) beyond threshold
        "notes": [],
    }

    if df.empty:
        report["notes"].append("NO DATA RETURNED")
        return report

    df = df.sort_index()
    report["first_date"] = df.index.min().date().isoformat()
    report["last_date"] = df.index.max().date().isoformat()

    # 2. Duplicate dates -----------------------------------------------------
    dupes = df.index.duplicated().sum()
    report["duplicate_dates"] = int(dupes)
    if dupes:
        report["notes"].append(f"{dupes} duplicate date(s) found")

    # 3. Largest gap between consecutive trading days ------------------------
    # Normal is 1 (next day) or 3 (over a weekend). A big number can mean a
    # long holiday OR missing data -- we surface the max for a human to judge.
    if len(df) > 1:
        gaps = df.index.to_series().diff().dt.days.dropna()
        report["largest_gap_days"] = int(gaps.max())

    # 4. Price spikes on Adjusted Close --------------------------------------
    adj = df["Adj Close"].astype(float)
    pct = adj.pct_change()
    flagged = pct[pct.abs() > config.SPIKE_THRESHOLD]
    report["spikes"] = [
        (idx.date().isoformat(), round(float(val) * 100, 1))
        for idx, val in flagged.items()
    ]

    # 5. Broken rows ---------------------------------------------------------
    price_cols = ["Open", "High", "Low", "Close", "Adj Close"]
    broken = ((df[price_cols] <= 0) | df[price_cols].isna()).any(axis=1).sum()
    report["nan_or_nonpositive"] = int(broken)
    if broken:
        report["notes"].append(f"{broken} row(s) with NaN or non-positive prices")

    return report
