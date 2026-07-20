"""
test_engine.py
==============
A terminal walkthrough of the trading engine -- no UI required. Run:

    python test_engine.py

It demonstrates, with real data:
  1. Setup + the data-driven universe.
  2. The requested scenario: buy $10k Tesla in month 1, hold 6 months, sell half.
  3. Validation: rejected trades (insufficient cash / shares).
  4. A no-lookahead PROOF.
  5. A full 60-month run showing year-end hooks + the buy-and-hold benchmark.
"""

from src.engine import SimConfig, TradingEngine, TradeError


def money(x):
    return f"${x:,.2f}"


def print_state(engine, user="user-1"):
    s = engine.portfolio_state(user)
    print(f"    Date {s['date']} (interval {s['interval_index']}) | "
          f"Cash {money(s['cash'])} | Holdings {money(s['holdings_value'])} | "
          f"TOTAL {money(s['total_value'])} | "
          f"P&L {money(s['pnl_dollars'])} ({s['pnl_pct']:+.2f}%)")
    for h in s["holdings"]:
        print(f"        {h['ticker']:<12} {h['shares']:>10.4f} sh @ "
              f"{money(h['price_usd']):>12} = {money(h['value_usd'])}")


def main():
    print("=" * 74)
    print("TRADING ENGINE -- TERMINAL TEST")
    print("=" * 74)

    # --- 1. Setup ---------------------------------------------------------
    engine = TradingEngine(SimConfig())  # $100k, monthly, adj_close_usd
    print(f"\n[1] Setup")
    print(f"    Starting capital : {money(engine.config.starting_capital)}")
    print(f"    Interval         : {engine.config.interval} "
          f"({engine.clock.total_intervals} decision points)")
    print(f"    Price basis      : {engine.config.price_field}")
    print(f"    Universe (from DB): {', '.join(engine.universe())}")
    print(f"    First date       : {engine.current_date}")

    # --- 2. Requested scenario -------------------------------------------
    print(f"\n[2] Scenario: buy $10,000 TSLA in month 1, hold 6 months, sell half")
    txn = engine.buy("TSLA", dollars=10_000)
    print(f"    BUY  {txn.shares:.4f} TSLA @ {money(txn.price_usd)} "
          f"= {money(txn.total_value)}  (cash now {money(txn.cash_after)})")
    print_state(engine)

    print(f"    ...holding for 6 months (advancing time)...")
    for _ in range(6):
        engine.advance()   # reveals next month's price; user does nothing (hold)
    print(f"    Now at {engine.current_date}. TSLA revealed price: "
          f"{money(engine.current_price('TSLA'))}")

    held = engine.portfolio_state()["holdings"][0]["shares"]
    txn = engine.sell("TSLA", shares=held / 2)
    print(f"    SELL {txn.shares:.4f} TSLA @ {money(txn.price_usd)} "
          f"= {money(txn.total_value)}  (cash now {money(txn.cash_after)})")
    print_state(engine)

    # --- 3. Validation: rejected trades ----------------------------------
    print(f"\n[3] Validation (these SHOULD be rejected):")
    for label, fn in [
        ("Buy $5,000,000 of AAPL (not enough cash)",
         lambda: engine.buy("AAPL", dollars=5_000_000)),
        ("Sell 9999 TSLA (more than held)",
         lambda: engine.sell("TSLA", shares=9999)),
        ("Sell AMZN (none held)",
         lambda: engine.sell("AMZN", shares=1)),
        ("Buy with both dollars AND shares",
         lambda: engine.buy("AAPL", dollars=100, shares=1)),
    ]:
        try:
            fn()
            print(f"    !! NOT REJECTED: {label}  <-- BUG")
        except TradeError as e:
            print(f"    rejected: {label}\n        -> {e}")

    # --- 4. No-lookahead proof -------------------------------------------
    print(f"\n[4] No-lookahead proof:")
    today = engine.current_date
    price_now = engine.current_price("NVDA")
    print(f"    current_price('NVDA') takes NO date argument; it is pinned to "
          f"the clock ({today}) = {money(price_now)}")
    print(f"    There is no engine/market method to fetch a price after {today}.")
    engine.advance()
    price_next = engine.current_price("NVDA")
    print(f"    Only after advance() to {engine.current_date} does NVDA reveal "
          f"{money(price_next)} -- the future became visible only by "
          f"moving the clock forward.")

    # --- 5. Full run: year-end hooks + benchmark -------------------------
    print(f"\n[5] Running to the end (holding), showing year-end hooks:")
    fired = []
    engine.register_year_end_hook(
        lambda year, snap: fired.append((year, snap)))
    while engine.advance() is not None:
        pass
    for year, snap in fired:
        pf = snap["portfolios"]["user-1"]
        bh = snap["benchmark"]["buy_and_hold"]
        idx = snap["benchmark"]["index"]
        idx_txt = f" | S&P500 {idx['pnl_pct']:+.2f}%" if idx else ""
        print(f"    YEAR-END {year} ({snap['date']}): "
              f"portfolio {money(pf['total_value'])} "
              f"({pf['pnl_pct']:+.2f}%) | "
              f"buy&hold {money(bh['value_usd'])} ({bh['pnl_pct']:+.2f}%)"
              f"{idx_txt}")

    # --- Final summary ----------------------------------------------------
    print(f"\n[FINAL] at {engine.current_date}")
    print_state(engine)
    marks = engine.benchmark_state()
    me = engine.portfolio_state()
    print(f"\n    {'STRATEGY':<38}{'VALUE':>14}{'RETURN':>12}")
    print(f"    {'-' * 62}")
    print(f"    {'Your portfolio':<38}{money(me['total_value']):>14}"
          f"{me['pnl_pct']:>11.2f}%")
    for key in ("buy_and_hold", "index"):
        b = marks[key]
        if not b:
            continue
        print(f"    {b['name']:<38}{money(b['value_usd']):>14}"
              f"{b['pnl_pct']:>11.2f}%")
        diff = me["pnl_pct"] - b["pnl_pct"]
        verdict = "BEAT" if diff > 0 else "TRAILED"
        print(f"    {'  -> you ' + verdict + ' this by':<38}"
              f"{'':>14}{diff:>11.2f} pts")

    print(f"\n    Transaction log ({len(engine.transaction_log())} trades):")
    for t in engine.transaction_log():
        print(f"        {t['date']} {t['action']:<4} {t['shares']:>9.4f} "
              f"{t['ticker']:<10} @ ${t['price_usd']:>10.4f}  "
              f"= ${t['total_value']:>12,.2f}  cash->${t['cash_after']:>12,.2f}")

    engine.close()

    # --- 6. Persistence: state survives between sessions -------------------
    print(f"\n[6] Persistence demo (state surviving a 'restart'):")
    import tempfile, os
    from src.engine import SimulationStore
    tmp = os.path.join(tempfile.gettempdir(), "demo_sims.db")
    if os.path.exists(tmp):
        os.remove(tmp)

    store = SimulationStore(tmp)
    e1 = TradingEngine(SimConfig(), sim_id="demo", store=store)
    e1.buy("AAPL", dollars=25_000)
    e1.buy("NVDA", dollars=15_000)
    for _ in range(4):
        e1.advance()
    print(f"    Session 1: traded, advanced to {e1.current_date}, "
          f"cash {money(e1.portfolio_state()['cash'])}")
    e1.market.close()
    del e1                                  # simulate closing the app

    e2 = TradingEngine.load("demo", store)   # <- reopen from disk
    s = e2.portfolio_state()
    print(f"    Session 2: RESTORED at {e2.current_date}, "
          f"cash {money(s['cash'])}, "
          f"{len(s['holdings'])} holdings, "
          f"{len(e2.transaction_log())} trades in history")
    for h in s["holdings"]:
        print(f"        {h['ticker']:<12} {h['shares']:>10.4f} sh = "
              f"{money(h['value_usd'])}")
    e2.close()
    os.remove(tmp)

    print("\n" + "=" * 74)
    print("TEST COMPLETE.")
    print("=" * 74)


if __name__ == "__main__":
    main()
