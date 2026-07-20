# Stock Simulator — Data Layer

Plain-English guide to how the data layer works, why it's built this way, and
what to watch out for. This is the *only* thing built so far — no trading
logic, UI, or reporting yet.

## What this does, in one sentence

It downloads 5 years (Jan 2021 → Jan 2026) of daily prices for 10 real stocks
plus the exchange rates needed to convert every price into US dollars **using
the rate from that same historical date**, validates the data, and saves it
locally so the simulator can run with no internet.

## The 10 stocks

| Ticker | Company | Native currency | Exchange |
|--------|---------|-----------------|----------|
| RELIANCE.NS | Reliance Industries | INR | NSE (India) |
| HDFCBANK.NS | HDFC Bank | INR | NSE (India) |
| TMPV.NS | Tata Motors (PV) | INR | NSE (India) |
| AAPL | Apple | USD | NASDAQ |
| TSLA | Tesla | USD | NASDAQ |
| NVDA | Nvidia | USD | NASDAQ |
| AMZN | Amazon | USD | NASDAQ |
| JPM | JPMorgan Chase | USD | NYSE |
| ASML | ASML Holding | USD | NASDAQ (ADR) |
| MC.PA | LVMH | EUR | Euronext Paris |

**Two ticker decisions worth explaining in an interview:**

- **ASML** — we use the NASDAQ ADR (`ASML`, priced in USD), not the Amsterdam
  listing (`ASML.AS`, in EUR). The ADR needs no currency conversion and follows
  the US trading calendar, giving cleaner data with no accuracy loss.
- **Tata Motors** — the obvious ticker `TATAMOTORS.NS` now returns *"no data /
  possibly delisted"*. Tata Motors **demerged in 2025**, and Yahoo carries the
  continuous historical price series under **`TMPV.NS`** (the Passenger-Vehicle
  successor). See the *Known caveats* section.

## How currency conversion works (the key idea)

All prices must end up in **USD** (the simulator's base currency), converted with
the exchange rate from the *same date* — never today's rate — so history stays
honest.

Yahoo gives us two rates per day:
- `USDINR=X` ≈ 83 → **1 USD = 83 INR** (to get USD, *divide* the rupee price)
- `EURUSD=X` ≈ 1.10 → **1 EUR = 1.10 USD** (to get USD, *multiply* the euro price)

Rather than scatter "divide here, multiply there" logic around the code, we
precompute one number per currency per day called **`usd_per_unit`**:

```
USD:  usd_per_unit = 1.0
INR:  usd_per_unit = 1 / USDINR      (a rupee is a small fraction of a dollar)
EUR:  usd_per_unit = EURUSD          (a euro is ~1.1 dollars)
```

Then **every** conversion is the same one-liner:

```
price_usd = price_native × usd_per_unit
```

For US stocks the factor is exactly 1.0, so USD == native. Worked example
straight from the database: LVMH on 2023-06-15 was €843.70, the factor was
1.0843, so 843.70 × 1.0843 = **$914.83**.

## Folder structure

```
STOCK SIMULATOR/
├── main.py                 # runs the whole pipeline + prints the summary
├── requirements.txt        # yfinance, pandas
├── DATA_LAYER_README.md    # this file
├── data/
│   ├── raw/                # per-stock CSVs exactly as downloaded (audit trail)
│   ├── processed/          # native + USD-converted, validated CSVs (readable)
│   └── market.db           # SQLite — the SOURCE OF TRUTH the simulator queries
└── src/
    ├── config.py           # WHAT to fetch: tickers, dates, currencies, thresholds
    ├── fetch.py            # downloads from Yahoo, with retry + backoff
    ├── convert.py          # native → USD using the usd_per_unit factor
    ├── validate.py         # gaps, duplicates, spikes, broken rows
    └── store.py            # writes SQLite + CSVs
```

Each `src/` file does exactly one job — that separation is what makes the code
easy to explain, test, and extend.

## Why SQLite (not just CSVs)

The simulator's main question is *"what was the USD price of stock X on date
D?"* — a point-in-time lookup. A SQLite table indexed by `(ticker, date)`
answers that instantly and joins cleanly to the exchange-rate table. Flat CSVs
would force us to reload and merge everything on every query. So **SQLite is the
source of truth**, and the CSVs are kept alongside purely for transparency and
easy eyeballing.

Two tables:
- **`prices`** — one row per `(ticker, date)`: native OHLCV + `adj_close`, the
  `usd_per_unit` factor, `fx_filled` flag, `close_usd` / `adj_close_usd`, and an
  **`asset_type`** column (`'stock'` or `'index'`).
- **`fx_rates`** — one row per date: `usdinr`, `eurusd`, and the derived
  `usd_per_inr` / `usd_per_eur` factors.

Once `market.db` exists, the simulator needs **no internet**.

### Benchmark indices

Alongside the 10 tradable stocks, the table also holds two **benchmark indices**
tagged `asset_type='index'`. The trading engine builds its tradable universe
from `asset_type='stock'`, so these can never be bought — they exist only for
comparison:

| Ticker | What it is | Why it's here |
|---|---|---|
| `^SP500TR` | S&P 500 **Total Return** | The **default benchmark**. Includes reinvested dividends. |
| `^GSPC` | S&P 500 (price only) | The familiar headline number, for display. |

**Why total return matters:** our 10 stocks are valued on *dividend-adjusted*
prices, so comparing them against the price-only `^GSPC` would unfairly flatter
them. Over 2021–2025 the gap is large — `^SP500TR` returned **+99.1%** versus
`^GSPC`'s **+85.0%**, and that ~14-point difference is purely dividends.

### Where user data lives (NOT here)

`market.db` is **immutable reference data**, opened read-only by the engine.
Your portfolios, trades, and simulation progress are saved separately in
**`data/simulations.db`**, so user state can never corrupt historical prices.

## Data quality: what we check and why

Validation never deletes data — it *flags* things for a human, because silent
bad data is far more dangerous in a trading sim than a loud warning.

- **Duplicate dates** — must be exactly one row per date (found: 0).
- **Gaps** — largest run of days with no trading (weekends/holidays are normal;
  a huge gap could mean missing data).
- **Broken rows** — NaN or ≤ 0 prices (found: 0).
- **Price spikes** — any single-day move > **20%** on **Adjusted Close**. We use
  *adjusted* close on purpose: raw close would show stock splits (e.g. NVDA's
  10:1) as fake ~90% crashes, whereas adjusted close is already split-corrected,
  so a flagged move is a *genuine* price event.

Every spike flagged in this build is a **real event**, not a data error:

| Stock | Date | Move | What it was |
|-------|------|------|-------------|
| TSLA | 2024-10-24 | +22% | post-earnings surge |
| TSLA | 2025-04-09 | +23% | tariff-pause rally |
| NVDA | 2023-05-25 | +24% | blowout AI-guidance earnings |
| TMPV | 2021-10-13 | +20% | rally |
| TMPV | 2025-10-14 | **−40%** | **demerger (structural, not a crash)** |

## Coverage actually achieved

- **Full window covered:** every stock runs Jan 2021 → 2025-12-31 (2026-01-01 is
  a market holiday everywhere, so that's the true last trading day).
- **Row counts differ by market calendar** — this is expected, not a bug:
  US ~1,255 days, Euronext (LVMH) ~1,281, NSE (India) ~1,236. Different
  countries have different holidays.
- **10/10 tickers fetched successfully.**
- Only **15 rows** total needed a forward-filled exchange rate (a stock traded
  on a day the FX feed skipped) — those rows are marked `fx_filled = 1`.

## Known caveats (carry these into the trading-logic session)

1. **Tata Motors demerger (2025-10-14).** `TMPV.NS` shows a ~−40% step that day.
   This is the commercial-vehicle business splitting off into a separate
   listing — the share value divided between two companies. Adjusted close
   corrects for splits and dividends but **not** demergers, so this
   discontinuity is real in the data. When we build trading logic, treat it as a
   corporate action, not a market loss.
2. **Different trading calendars.** The three markets don't trade on identical
   days. Each stock is stored on its own native calendar. When the simulator
   steps through time, it will need a rule for days when some markets are shut
   (e.g. carry the last known price forward).
3. **ADR vs local listing.** ASML uses the USD ADR. If you later want the
   "authentic" euro-denominated European listing, switch to `ASML.AS` in
   `config.py` (it will then need EUR conversion like LVMH).

## How to re-run

```powershell
python main.py
```

It re-downloads everything and overwrites `data/`. After one successful run you
can demo fully offline — the simulator only reads `data/market.db`.
```
