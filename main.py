"""
main.py
=======
Orchestrates the whole data layer end to end:

    1. Fetch USD/INR and EUR/USD exchange rates (once).
    2. Build the per-date usd_per_unit conversion factors.
    3. For each of the 10 stocks:
         fetch -> save raw -> convert to USD -> validate -> save processed.
    4. Write everything into SQLite (data/market.db) + CSVs.
    5. Print a coverage summary, data-quality flags, and sample rows.

Run from the project root:
    python main.py

After it finishes once, the simulator can run fully offline from data/market.db.
"""

import sys

import pandas as pd

from src import config, convert, fetch, store, validate


def run():
    print("=" * 72)
    print("STOCK SIMULATOR -- DATA LAYER BUILD")
    print(f"Date range: {config.START_DATE} -> {config.END_DATE} "
          f"(end exclusive; last row is the final 2025 trading day)")
    print("=" * 72)

    # --- Step 1: exchange rates -------------------------------------------
    print("\n[1/4] Fetching exchange rates (USD/INR, EUR/USD)...")
    fx = fetch.fetch_fx()
    factors = convert.build_usd_factors(fx)
    print(f"      OK: {len(fx)} daily FX rows "
          f"({fx.index.min().date()} -> {fx.index.max().date()}).")

    # --- Step 2: each stock ------------------------------------------------
    print(f"\n[2/4] Fetching, converting, and validating "
          f"{len(config.STOCKS)} stocks + {len(config.INDICES)} indices...")
    tidy_frames = []      # tidy rows to stack into the combined DB table
    reports = []          # one validation report per stock
    failures = []         # (ticker, error) for anything that couldn't be fetched

    # Stocks are tradable; indices are benchmark-only. Same pipeline, different
    # asset_type tag, so the engine can exclude indices from the tradable list.
    to_fetch = ([(s, "stock") for s in config.STOCKS]
                + [(i, "index") for i in config.INDICES])

    for spec, asset_type in to_fetch:
        ticker, name, currency = spec["ticker"], spec["name"], spec["currency"]
        label = "" if asset_type == "stock" else "  [benchmark index]"
        print(f"  - {ticker:<14} {name}{label}")
        try:
            raw = fetch.fetch_stock(ticker)          # download
            store.save_raw_csv(ticker, raw)          # audit trail

            converted = convert.convert_stock_to_usd(raw, currency, factors)
            report = validate.validate_stock(ticker, name, converted)
            reports.append(report)

            tidy = store._flatten_stock(ticker, name, currency, converted,
                                        asset_type=asset_type)
            store.save_processed_csv(ticker, tidy)
            tidy_frames.append(tidy)
        except Exception as exc:  # noqa: BLE001
            print(f"    !! FAILED: {exc}")
            failures.append((ticker, str(exc)))

    if not tidy_frames:
        print("\nFATAL: no stock data could be fetched. Aborting.")
        sys.exit(1)

    # --- Step 3: persist ---------------------------------------------------
    print("\n[3/4] Writing SQLite database + CSVs...")
    all_prices = pd.concat(tidy_frames, ignore_index=True)
    store.write_database(all_prices, fx)
    print(f"      OK: {len(all_prices):,} total price rows across "
          f"{all_prices['ticker'].nunique()} stocks -> {config.DB_PATH}")

    # --- Step 4: human-readable summary -----------------------------------
    print("\n[4/4] SUMMARY")
    _print_summary(reports, failures, all_prices, fx)


def _print_summary(reports, failures, all_prices, fx):
    """Print coverage per stock, data-quality flags, and sample rows."""
    # Coverage table --------------------------------------------------------
    print("\n  Coverage per stock:")
    header = f"    {'TICKER':<14}{'DAYS':>6}  {'FIRST':<12}{'LAST':<12}{'MAXGAP':>7}{'SPIKES':>8}"
    print(header)
    print("    " + "-" * (len(header) - 4))
    for r in reports:
        print(f"    {r['ticker']:<14}{r['rows']:>6}  "
              f"{str(r['first_date']):<12}{str(r['last_date']):<12}"
              f"{r['largest_gap_days']:>7}{len(r['spikes']):>8}")

    # Data-quality flags ----------------------------------------------------
    print("\n  Data-quality flags (for manual review):")
    any_flag = False
    for r in reports:
        problems = []
        if r["duplicate_dates"]:
            problems.append(f"{r['duplicate_dates']} duplicate dates")
        if r["nan_or_nonpositive"]:
            problems.append(f"{r['nan_or_nonpositive']} broken rows")
        if r["largest_gap_days"] > 7:
            problems.append(f"gap of {r['largest_gap_days']} days")
        if r["spikes"]:
            shown = ", ".join(f"{d} ({p:+.0f}%)" for d, p in r["spikes"][:5])
            more = "" if len(r["spikes"]) <= 5 else f" ...+{len(r['spikes']) - 5} more"
            problems.append(f">20% moves: {shown}{more}")
        for note in r["notes"]:
            problems.append(note)
        if problems:
            any_flag = True
            print(f"    * {r['ticker']}: " + "; ".join(problems))
    if not any_flag:
        print("    (none)")

    if failures:
        print("\n  Tickers that FAILED to download:")
        for t, e in failures:
            print(f"    * {t}: {e}")
    else:
        print(f"\n  All {len(config.STOCKS) + len(config.INDICES)} tickers "
              f"downloaded successfully.")

    # Sample rows: one INR, one EUR, one USD stock to prove FX conversion ---
    print("\n  Sample rows (proving same-date USD conversion):")
    for ticker in ["RELIANCE.NS", "MC.PA", "AAPL"]:
        sub = all_prices[all_prices["ticker"] == ticker].head(3)
        if sub.empty:
            continue
        cur = sub.iloc[0]["currency"]
        print(f"\n    {ticker} (native {cur}):")
        cols = ["date", "close", "usd_per_unit", "close_usd", "adj_close", "adj_close_usd"]
        print(sub[cols].to_string(index=False).replace("\n", "\n    "))

    print("\n" + "=" * 72)
    print("DATA LAYER COMPLETE. The simulator can now run offline from "
          f"{config.DB_PATH.name}.")
    print("=" * 72)


if __name__ == "__main__":
    run()
