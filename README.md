# ETS — Educational Trading Simulator

ETS is a historical stock-portfolio trading simulator. You start with **$100,000
of virtual cash** and trade **10 real companies** using **real daily price data
from January 2021 to December 2025**, moving forward through time one month at a
time. At the end of each simulated year you get an **Annual Performance Report**
comparing your decisions against passive benchmarks. Its purpose is to teach
portfolio decision-making — and crucially, it is built so you can never cheat by
seeing the future.

---

## What it does

- **Trade 10 real stocks across 3 regions** — Reliance Industries, HDFC Bank and
  Maruti Suzuki (India), Apple, Tesla, Nvidia, Amazon, JPMorgan Chase and ASML
  (USA), and LVMH (France).
- **One currency to reason in** — every price, whether it traded in rupees, euros
  or dollars, is converted to **US dollars using the exchange rate from that same
  historical date**, so your whole portfolio is directly comparable.
- **Year-based flow** — trade through twelve months, then a mandatory year-end
  review before the next year begins. Five simulated years, ~60 decisions.
- **Buy / sell by dollars, shares, or a percentage** — including "sell everything"
  and "invest all my cash", computed exactly.
- **Annual Performance Report** — year-start vs year-end value, your best and
  worst *decisions* (closed trades), and how you compared to benchmarks.
- **Risk metrics** — volatility, maximum drawdown, and the Sharpe ratio, each
  with a plain-English explanation.
- **Benchmarks** — your portfolio vs an equal-weight buy-and-hold basket and the
  S&P 500 Total Return index, plotted over time.
- **Runs fully offline** — once the data is downloaded, no internet is needed.
- **No look-ahead** — the single most important property (see below).

---

## Architecture

Three layers, each independent and testable, connected in one direction:

```
  DATA LAYER              TRADING ENGINE            WEB UI (Flask)
  ----------              --------------            --------------
  Yahoo Finance   --->    reads prices     <---     renders pages,
  download +              (read-only),               drives the engine
  USD conversion          runs the clock,            per request
       |                  buy/sell/advance                |
       v                       |                          v
   market.db  <---------- (read-only) ----------   simulations.db
  (prices, FX)                                     (portfolios, trades,
   immutable                                        clock position)
```

- **Data layer** (`main.py`, `src/config.py`, `src/fetch.py`, `src/convert.py`,
  `src/validate.py`, `src/store.py`) — downloads the history once, converts every
  price to USD, validates it, and writes it to `data/market.db`. Run once.
- **Trading engine** (`src/engine/`) — the pure simulation core. It knows nothing
  about the web. It reads prices (read-only, date-gated), holds the forward-only
  clock, executes trades, tracks portfolios, and fires the year-end hook.
- **Web UI** (`web/`) — a thin Flask app. Each request loads a simulation from
  `simulations.db`, drives the engine, saves, and discards it. It never contains
  business logic of its own.
- **Access layer** (`src/access.py`) — deliberately separate from the engine, so
  a future accounts/subscription layer can be added without touching the core.

**Why two databases?** `market.db` is immutable reference data and is opened
*read-only*, so the simulation can never corrupt historical prices. Your own
state — cash, holdings, trade history, where you are in time — lives separately
in `simulations.db`.

---

## Key design decisions (and why)

These are the choices a reviewer should understand — each one is a deliberate
trade-off, not an accident.

**1. Same-date currency conversion.**
An Indian stock bought in 2021 is converted to USD using the 2021 exchange rate,
not today's. Using today's rate would silently mix up *stock* performance with
*currency* movements and rewrite history. Same-date conversion keeps the
simulation honest.

**2. Adjusted closing prices.**
Prices are adjusted for stock splits and dividends. Without this, a stock split
(e.g. Nvidia's 10-for-1) would look like a 90% crash, and dividends would vanish.
Adjusted prices make returns comparable and correct over five years. (The one
thing adjustment does *not* cover is a demerger — which is why Tata Motors, which
demerged in 2025, was replaced with Maruti Suzuki.)

**3. Strict no-look-ahead enforcement.**
This is the heart of the project. The simulator must never reveal a price that
hadn't happened yet at the simulated point in time — otherwise a user could
"predict" the future and the exercise would be meaningless. It is enforced
*structurally*, not by convention:
- The only way to read a price is one method that asks for the price *on or
  before* a given date (`WHERE date <= as_of`). There is **no** method that can
  return a future price — the code path simply does not exist.
- The clock moves **forward only**; there is no rewind or jump.
- The public price methods take **no date argument at all** — they are pinned to
  the current simulated date, so a page or chart *cannot* request the future even
  by mistake.
- The performance chart plots only up to the current month.
- Automated tests assert that no rendered page ever contains a date later than
  the current simulated date.

**4. Total-return S&P 500 benchmark.**
The benchmark is the S&P 500 *Total Return* index (dividends reinvested), not the
price-only index everyone quotes. Because our stocks are valued on
dividend-adjusted prices, comparing them to a dividend-free index would unfairly
flatter them — over 2021–2025 the gap is about 14 percentage points.

**5. Floor, never round up.**
Any share count or cash figure shown on screen is truncated downward, never
rounded up. If a displayed number were even a hair larger than reality, a
"sell everything" action that echoed it back could be rejected as "insufficient
shares". Truncation guarantees the displayed number is always safe to act on.

**6. Engine separated from the access layer.**
The simulation core has no concept of users, accounts, or billing. That lives in
a separate access layer. This means the project could grow into a multi-user or
subscription product later by extending one layer — not rewriting the core.

---

## Running it locally

**Requirements:** Python 3.12. On this machine Python is installed but not on the
system PATH, so the commands below call it by its full path. (If your `python`
command works directly, you can use `python` instead.)

```powershell
# 1. Move into the project folder
cd "C:\Users\mp_ma\OneDrive\Desktop\STOCK SIMULATOR"

# 2. Install the dependencies (one time)
& "C:\Users\mp_ma\AppData\Local\Programs\Python\Python312\python.exe" -m pip install -r requirements.txt

# 3. (One time only) download the historical data into data/market.db
& "C:\Users\mp_ma\AppData\Local\Programs\Python\Python312\python.exe" main.py

# 4. Start the web app
& "C:\Users\mp_ma\AppData\Local\Programs\Python\Python312\python.exe" run_ui.py
```

Then open **http://127.0.0.1:5000** in a browser. Press `Ctrl+C` in the terminal
to stop the server. Step 3 needs internet; everything after it runs offline.

**SECRET_KEY (optional).** The app signs its session cookie with a secret key.
For local use it falls back to a built-in development key automatically — nothing
to do. For a public deployment, set your own before starting:

```powershell
$env:SECRET_KEY = "some-long-random-string"
```

---

## Testing

The project has an automated test suite (**188 tests**) covering the trading
engine, persistence, the risk-metric maths, FIFO trade matching, and the web
layer — including dedicated tests that the no-look-ahead and offline guarantees
hold.

```powershell
& "C:\Users\mp_ma\AppData\Local\Programs\Python\Python312\python.exe" -m pytest tests/ -q
```

The tests were also validated by *mutation testing* — deliberately breaking a
calculation and confirming a test catches it — so a passing suite is meaningful,
not just green by luck.

---

## Known limitations & future work

- **Single user.** State is keyed by user from the start, but the UI runs one
  simulation per browser session. Multi-user support is an extension of the
  existing structure, not a rewrite.
- **Corporate actions.** Prices adjust for splits and dividends but not
  demergers/spin-offs; the current 10-stock universe was chosen to have none.
- **Risk-free rate.** The Sharpe ratio assumes a fixed 3% annual risk-free rate
  (≈ the average US T-bill yield over the period), configurable in one place. A
  refinement would use the actual month-by-month rate.
- **Development server.** It runs on Flask's built-in server, which is ideal for
  a local demo but would need a production web server (e.g. gunicorn/waitress)
  for real deployment.
- **Monetisation-ready, not monetised.** The engine/access-layer split leaves a
  clean seam for subscriptions or premium features, but none are built.

---

## Project layout

```
STOCK SIMULATOR/
├── main.py                 # one-command data pipeline
├── run_ui.py               # starts the web app
├── requirements.txt
├── data/
│   ├── market.db           # historical prices + FX (read-only reference)
│   ├── simulations.db      # your portfolios, trades, progress
│   ├── raw/  processed/    # CSV copies of the data (audit / transparency)
├── src/
│   ├── config.py           # tickers, dates, currencies (single source of truth)
│   ├── fetch/convert/validate/store.py   # the data pipeline
│   ├── access.py           # account/access layer (separate from the engine)
│   └── engine/             # the trading engine (UI-agnostic core)
├── web/                    # the Flask application
│   ├── __init__.py         # routes + per-request engine lifecycle
│   ├── analytics.py        # reports, chart series, realized trades (FIFO)
│   ├── metrics.py          # volatility / drawdown / Sharpe maths
│   ├── charts.py           # server-rendered SVG chart (no JS libraries)
│   ├── templates/  static/ # dark-theme UI
└── tests/                  # 188 automated tests
```
