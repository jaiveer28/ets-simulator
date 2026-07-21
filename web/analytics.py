"""
analytics.py
============
Read-only derivations the UI needs that the engine doesn't store.

Two jobs:
  1. portfolio_value_series() -- portfolio value at EVERY past interval, for the
     benchmark chart. The engine only persists year-end snapshots and current
     state, so we RECONSTRUCT the series by replaying the transaction log
     against historical prices. This is exact (transactions fully determine
     cash + holdings) and needs no engine changes.
  2. annual_report() -- assembles the year-end report from the persisted
     snapshots plus per-stock performance over that year.

=========================  NO-LOOKAHEAD IN THIS MODULE  =======================
Every loop here is bounded by `engine.clock.current_index` -- we never iterate
past the current interval, and every price lookup uses a date drawn from that
bounded range. Nothing in this file can surface a price the user shouldn't see.
Realized-trade and risk-metric helpers read only the transaction log (which
only ever holds past trades) and the clock-bounded value series.
==============================================================================
"""

from collections import deque

from . import intelligence, metrics


def _average_cost(engine, user_id=None):
    """
    Weighted-average buy price per share for each CURRENTLY-held stock, from the
    trade log (sells reduce shares but leave the average unchanged -- standard
    average-cost accounting). Used to tell whether a held position is up or down.
    Reads only past trades, so no lookahead.
    """
    portfolio = engine.portfolios[user_id or engine.default_user]
    shares, cost = {}, {}
    for t in portfolio.transactions:
        if t.action == "BUY":
            cost[t.ticker] = cost.get(t.ticker, 0.0) + t.total_value
            shares[t.ticker] = shares.get(t.ticker, 0.0) + t.shares
        else:
            held = shares.get(t.ticker, 0.0)
            if held > 0:
                avg = cost[t.ticker] / held
                cost[t.ticker] -= avg * t.shares      # remove sold shares at avg
                shares[t.ticker] = held - t.shares
    return {tk: (cost[tk] / shares[tk]) for tk in shares
            if shares[tk] > 1e-9 and cost.get(tk, 0) > 0}


def build_intelligence_input(engine, snap, year, year_start_date, user_id=None):
    """
    Assemble the PLAIN-DATA dict the intelligence engine consumes. This is the
    only place engine internals meet the (engine-agnostic) intelligence module.
    Everything here is bounded by the current simulated date.
    """
    user_id = user_id or engine.default_user
    pf_end = snap["portfolios"][user_id]

    avg_cost = _average_cost(engine, user_id)
    holdings = []
    for h in pf_end["holdings"]:
        basis = avg_cost.get(h["ticker"])
        unreal = ((h["price_usd"] / basis - 1) * 100.0
                  if basis and h.get("price_usd") else None)
        holdings.append({"ticker": h["ticker"], "name": h["name"],
                         "value_usd": h["value_usd"], "unreal_pct": unreal})

    trades = [t for t in engine.transaction_log(user_id)
              if t["date"][:4] == str(year)]
    realized = realized_trades(engine, user_id, year)
    bw = best_worst_realized(engine, user_id, year)

    portfolio_pct = _pct(pf_end["total_value"],
                         snap.get("_start_value", engine.config.starting_capital))
    return {
        "year": year,
        "current_date": engine.current_date,
        "trades": trades,
        "realized": realized,
        "holdings": holdings,
        "total_value": pf_end["total_value"],
        # PriceView enforces no-lookahead: it refuses any date > current_date.
        "prices": intelligence.PriceView(engine.market.price_asof,
                                         engine.current_date),
        "best": bw["best"],
        "worst": bw["worst"],
        "risk": year_risk_metrics(engine, year, user_id),
    }


def _replay(engine, user_id, upto_interval):
    """
    Rebuild (cash, holdings) as they stood at the END of `upto_interval` by
    replaying trades. Uses the raw Transaction objects (exact floats), never the
    rounded display values from transaction_log().
    """
    portfolio = engine.portfolios[user_id]
    cash = portfolio.starting_capital
    holdings = {}
    for t in portfolio.transactions:
        if t.interval_index > upto_interval:
            break                      # trades are appended in time order
        if t.action == "BUY":
            cash -= t.total_value
            holdings[t.ticker] = holdings.get(t.ticker, 0.0) + t.shares
        else:
            cash += t.total_value
            holdings[t.ticker] = holdings.get(t.ticker, 0.0) - t.shares
    # Drop positions that have been fully sold (guard against float dust).
    holdings = {k: v for k, v in holdings.items() if v > 1e-9}
    return cash, holdings


def portfolio_value_series(engine, user_id=None):
    """
    [{date, value}] for every interval from the start up to and INCLUDING the
    current one. Never goes beyond the clock.
    """
    user_id = user_id or engine.default_user
    out = []
    for idx in range(engine.clock.current_index + 1):     # <- bounded by clock
        date = engine.clock.intervals[idx]
        cash, holdings = _replay(engine, user_id, idx)
        value = cash
        for ticker, shares in holdings.items():
            price = engine.market.price_asof(ticker, date)  # date <= current
            if price:
                value += shares * price
        out.append({"date": date, "value": value})
    return out


def benchmark_series(engine):
    """
    Both benchmarks valued at every past interval, same bounded range.
    Returns (buy_and_hold, index) where index may be None.
    """
    bh, idx_series = [], []
    for i in range(engine.clock.current_index + 1):        # <- bounded by clock
        date = engine.clock.intervals[i]
        bh.append({"date": date,
                   "value": engine.benchmark.value(engine.market, date)})
        if engine.index_benchmark:
            idx_series.append({
                "date": date,
                "value": engine.index_benchmark.value(engine.market, date)})
    return bh, (idx_series or None)


def _pct(new, old):
    return (new / old - 1) * 100.0 if old else 0.0


# ---------------------------------------------------------------------------
# Realized trades (FIFO)
# ---------------------------------------------------------------------------
def realized_trades(engine, user_id=None, year=None):
    """
    Match buys to sells using FIFO (first-in, first-out) and return each closed
    round-trip slice as a realized trade.

    FIFO = a sell closes the OLDEST open buy lots first. This is the standard
    broker/tax default, it is intuitive, and it keeps a clean buy-date/price ->
    sell-date/price pair for every realized trade. A single sell that spans
    several buy lots produces several trade records (one per lot slice), and a
    partial sell consumes only part of the oldest lot, leaving the rest open.

    A trade is dated by its SELL (P&L is realized when you sell). The matching
    buy may be from an earlier year; if `year` is given we keep only trades
    whose sell falls in that year.

    NO-LOOKAHEAD: reads engine transactions, which only ever contain trades at
    or before the current simulated date. Uses each trade's own recorded
    execution price -- no fresh price lookups at all.
    """
    user_id = user_id or engine.default_user
    lots = {}          # ticker -> deque of [shares_remaining, price, date]
    out = []

    for t in engine.portfolios[user_id].transactions:   # time-ordered
        if t.action == "BUY":
            lots.setdefault(t.ticker, deque()).append([t.shares, t.price_usd,
                                                        t.date])
        else:  # SELL -- consume oldest lots first
            remaining = t.shares
            queue = lots.get(t.ticker, deque())
            while remaining > 1e-9 and queue:
                lot = queue[0]
                matched = min(remaining, lot[0])
                pnl = (t.price_usd - lot[1]) * matched
                out.append({
                    "ticker": t.ticker,
                    "name": engine.market.name_of(t.ticker),
                    "shares": matched,
                    "buy_date": lot[2],
                    "buy_price": lot[1],
                    "sell_date": t.date,
                    "sell_price": t.price_usd,
                    "pnl_usd": pnl,
                    "pnl_pct": _pct(t.price_usd, lot[1]),
                })
                lot[0] -= matched
                remaining -= matched
                if lot[0] <= 1e-9:
                    queue.popleft()        # lot fully consumed

    if year is not None:
        out = [r for r in out if r["sell_date"][:4] == str(year)]
    return out


def best_worst_realized(engine, user_id=None, year=None):
    """Best and worst realized trades by absolute P&L (None if there are none)."""
    trades = realized_trades(engine, user_id, year)
    if not trades:
        return {"best": None, "worst": None, "count": 0}
    ranked = sorted(trades, key=lambda r: r["pnl_usd"], reverse=True)
    return {
        "best": ranked[0],
        "worst": ranked[-1] if len(ranked) > 1 else None,
        "count": len(trades),
    }


# ---------------------------------------------------------------------------
# Risk metrics
# ---------------------------------------------------------------------------
def overall_risk_metrics(engine, user_id=None):
    """Volatility / max-drawdown / Sharpe over the WHOLE simulation to date."""
    values = [p["value"] for p in portfolio_value_series(engine, user_id)]
    return metrics.risk_metrics(values, engine.config.risk_free_rate)


def year_risk_metrics(engine, year, user_id=None):
    """
    Same three metrics computed from ONLY that calendar year's monthly values.

    We anchor on the previous December's value so the year's first monthly
    return is measured from the year-start, then include each month of `year`
    up to the current interval. All dates are <= the current interval, so this
    stays within no-lookahead.
    """
    series = portfolio_value_series(engine, user_id)
    idx = [i for i, p in enumerate(series) if p["date"][:4] == str(year)]
    if not idx:
        return metrics.risk_metrics([], engine.config.risk_free_rate)
    first = idx[0]
    anchor = first - 1 if first > 0 else first   # prior Dec, if it exists
    values = [p["value"] for p in series[anchor: idx[-1] + 1]]
    return metrics.risk_metrics(values, engine.config.risk_free_rate)


def build_report(engine, snap, prev, user_id=None):
    """
    Core report builder. Takes the year-end snapshot and the previous year's
    snapshot directly, so the SAME logic serves two callers:

      * annual_report()        -- a historical year, from persisted snapshots
      * live_year_end_report() -- the year-end checkpoint, before the year is
                                  closed, computed from live state

    Both produce identical output for the same clock position, because a
    snapshot is just portfolio_state() + benchmark_state() at that instant.
    """
    user_id = user_id or engine.default_user
    pf_end = snap["portfolios"][user_id]
    year = snap["year"]
    start_capital = engine.config.starting_capital

    # --- portfolio: start vs end of this year ---
    if prev:
        pf_start_value = prev["portfolios"][user_id]["total_value"]
        year_start_date = prev["date"]
    else:
        pf_start_value = start_capital
        year_start_date = engine.clock.intervals[0]
    pf_end_value = pf_end["total_value"]

    # --- benchmarks over the same year ---
    def bench_year(key):
        end_b = snap["benchmark"].get(key)
        if not end_b:
            return None
        start_b = (prev["benchmark"][key]["value_usd"]
                   if prev and prev["benchmark"].get(key) else start_capital)
        return {
            "name": end_b["name"],
            "start_value": start_b,
            "end_value": end_b["value_usd"],
            "pct": _pct(end_b["value_usd"], start_b),
        }

    benchmarks = [b for b in (bench_year("buy_and_hold"), bench_year("index")) if b]

    # --- how each held stock performed over the year ---
    performers = []
    for h in pf_end["holdings"]:
        p0 = engine.market.price_asof(h["ticker"], year_start_date)
        p1 = engine.market.price_asof(h["ticker"], snap["date"])
        if p0 and p1:
            performers.append({
                "ticker": h["ticker"],
                "name": h["name"],
                "pct": _pct(p1, p0),
                "value_usd": h["value_usd"],
            })
    performers.sort(key=lambda x: x["pct"], reverse=True)

    # --- trades made during this year ---
    trades = [t for t in engine.transaction_log(user_id)
              if t["date"][:4] == str(year)]

    # --- realized round-trips closed this year (FIFO) + this year's risk ---
    realized = best_worst_realized(engine, user_id, year)
    risk = year_risk_metrics(engine, year, user_id)

    portfolio_pct = _pct(pf_end_value, pf_start_value)
    beat = [{"name": b["name"], "diff": portfolio_pct - b["pct"]}
            for b in benchmarks]

    # --- ETS Intelligence: decision insights + behavioural biases ---
    intel_input = build_intelligence_input(
        engine, snap, year, year_start_date, user_id)
    intel_input["beat"] = beat
    intelligence_result = intelligence.analyze(intel_input)

    return {
        "year": year,
        "date": snap["date"],
        "start_value": pf_start_value,
        "end_value": pf_end_value,
        "change_usd": pf_end_value - pf_start_value,
        "change_pct": portfolio_pct,
        "cash": pf_end["cash"],
        "holdings": pf_end["holdings"],
        "benchmarks": benchmarks,
        "best": performers[0] if performers else None,
        "worst": performers[-1] if len(performers) > 1 else None,
        "performers": performers,
        "trades": trades,
        "realized": realized,          # best/worst closed trade this year
        "risk": risk,                  # this year's volatility / drawdown / Sharpe
        "beat": beat,
        "intelligence": intelligence_result,   # Part A insights + Part B biases
    }


def annual_report(engine, year, user_id=None):
    """
    Report for a COMPLETED year, read from persisted snapshots.
    Returns None if that year hasn't been closed yet.
    """
    snaps = {s["year"]: s for s in engine.year_end_snapshots}
    snap = snaps.get(year)
    if not snap:
        return None
    return build_report(engine, snap, snaps.get(year - 1), user_id)


def live_year_end_report(engine, user_id=None):
    """
    Report for the year the clock is currently sitting at the END of, computed
    from LIVE state -- used by the year-end checkpoint, which is shown BEFORE
    the year is closed and the snapshot persisted.

    The snapshot dict built here mirrors exactly what TradingEngine._fire_year_end
    persists, so the checkpoint and the later stored report agree.
    Returns None if the clock is not on a year-end interval.
    """
    if not engine.clock.is_year_end():
        return None
    snapshot = {
        "year": engine.clock.current_year,
        "date": engine.current_date,
        "interval_index": engine.clock.current_index,
        "portfolios": {uid: engine.portfolio_state(uid)
                       for uid in engine.portfolios},
        "benchmark": engine.benchmark_state(),
    }
    snaps = {s["year"]: s for s in engine.year_end_snapshots}
    return build_report(engine, snapshot,
                        snaps.get(snapshot["year"] - 1), user_id)


def available_report_years(engine):
    """Years that have completed and therefore have a stored report."""
    return sorted(s["year"] for s in engine.year_end_snapshots)


def needs_year_checkpoint(engine):
    """
    True when the user is standing on a year-end interval whose year has NOT
    yet been closed -- i.e. the mandatory annual-report checkpoint is due.

    Derived, never stored. Because "closed" means "a snapshot exists for this
    year", the gate goes false the moment the year is closed, which makes
    /continue-year naturally idempotent (a double-click cannot advance twice)
    and makes the end of the simulation resolve cleanly.
    """
    if not engine.clock.is_year_end():
        return False
    closed = {s["year"] for s in engine.year_end_snapshots}
    return engine.clock.current_year not in closed
