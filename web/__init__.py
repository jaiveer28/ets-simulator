"""
web/__init__.py -- the Flask application.
=========================================
This layer ONLY drives the engine. It contains no trading logic, no pricing, and
no clock of its own.

REQUEST LIFECYCLE (load -> mutate -> save -> discard)
-----------------------------------------------------
Every request opens a fresh SimulationStore + TradingEngine via get_engine(),
acts on it, and drops both in teardown_appcontext(). No engine object survives
a request, so two browser tabs can never diverge from a stale in-memory copy.
The engine write-throughs on every mutation, so "save" is implicit; the store's
optimistic-locking version check rejects a stale writer with
ConcurrentModificationError (e.g. a double-clicked "Advance").

=============================  NO-LOOKAHEAD AT THE UI  ========================
1. Prices shown come from engine.current_price(), which takes NO date argument
   -- the template cannot ask for another date.
2. Chart/report series are bounded by engine.clock.current_index in analytics.py.
3. The trade dropdown uses engine.universe(): ticker + name only, no prices.
4. Nothing serialises the full interval list or any future price into the page.
A pytest test asserts no rendered page contains a date after the current one.
==============================================================================
"""

import math
import os

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)

from src.engine import (MarketData, SimConfig, SimulationStore, TradeError,
                        TradingEngine)
from src.engine.persistence import ConcurrentModificationError

from . import analytics, charts, stock_info

# Product name. Kept in ONE place so renaming later is a single-line change.
APP_NAME = "ETS"

# Chart series for the navy & white theme. To honour "navy + white only" the
# three lines are white and two navy-grey tints rather than distinct hues, so
# they are ALSO distinguished by dash pattern (solid / dashed / dotted) -- which
# keeps them readable even though they share a colour family.
COLOR_PORTFOLIO = "#ffffff"   # white, solid  (the hero line)
COLOR_BUYHOLD = "#93a8dc"     # light navy-grey, dashed
COLOR_INDEX = "#7d8ec2"       # navy-grey, dotted (5:1 on panel — readable)
DASH_PORTFOLIO = None
DASH_BUYHOLD = "7 4"
DASH_INDEX = "2 4"


MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def month_label(iso_date):
    """'2022-01-31' -> 'January 2022'. Used for the month-change transition."""
    year, month, _ = iso_date.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


def floor_to(value, decimals):
    """
    Truncate for display. NEVER round up: a displayed figure must not exceed the
    real one. (The engine already floors its own output; this is the same rule
    applied to anything the UI formats itself.)
    """
    factor = 10 ** decimals
    return math.floor(value * factor) / factor


def create_app(sim_db_path=None, market_db_path=None):
    app = Flask(__name__)
    # SECRET_KEY signs the session cookie (which holds the sim_id). In production
    # set the SECRET_KEY environment variable to a long random value. For local
    # dev we fall back to a fixed key so sessions survive server restarts.
    # `using_dev_secret` lets the launcher (run_ui.py) warn the user; create_app
    # itself stays side-effect free so tests import cleanly.
    secret = os.environ.get("SECRET_KEY")
    app.config["USING_DEV_SECRET"] = not secret
    app.config["SECRET_KEY"] = secret or "dev-only-key-change-me"
    app.config["SIM_DB"] = sim_db_path or SimConfig().sim_db_path
    app.config["MARKET_DB"] = market_db_path

    # ---------------- per-request engine lifecycle ----------------
    def get_engine():
        """Load (or create) this session's simulation. One per request."""
        if "engine" in g:
            return g.engine

        store = SimulationStore(app.config["SIM_DB"])
        g.store = store

        cfg_kwargs = {}
        if app.config["MARKET_DB"]:
            cfg_kwargs["db_path"] = app.config["MARKET_DB"]

        sim_id = session.get("sim_id")
        if sim_id and store.exists(sim_id):
            # BUGFIX: TradingEngine.load() rebuilds SimConfig from the saved row,
            # which (correctly) does not persist db_path -- that is environment
            # specific. So a configured market DB was silently dropped on reload
            # and the engine fell back to the default file. If one is configured,
            # build MarketData ourselves and inject it, using the price basis the
            # simulation was actually created with.
            market = None
            if app.config["MARKET_DB"]:
                row = store.load_simulation(sim_id)
                market = MarketData(app.config["MARKET_DB"],
                                    price_field=row["price_field"])
            engine = TradingEngine.load(sim_id, store, market=market)
        else:
            sim_id = f"sim-{os.urandom(4).hex()}"
            engine = TradingEngine(SimConfig(**cfg_kwargs),
                                   sim_id=sim_id, store=store)
            session["sim_id"] = sim_id
        g.engine = engine
        return engine

    @app.teardown_appcontext
    def _discard_engine(exc):
        """Discard the engine + connections. Nothing persists in memory."""
        engine = g.pop("engine", None)
        if engine is not None:
            try:
                engine.market.close()
            except Exception:
                pass
        store = g.pop("store", None)
        if store is not None:
            try:
                store.close()
            except Exception:
                pass

    @app.context_processor
    def _inject_globals():
        """APP_NAME available to every template, so rebranding is one constant."""
        return {"app_name": APP_NAME}

    # ---------------- shared view data ----------------
    def dashboard_context(engine):
        state = engine.portfolio_state()
        prices = engine.current_prices()
        marks = engine.benchmark_state()

        # Per-holding gain/loss needs an average cost basis, derived from the
        # user's own trade history (all past data).
        cost_basis = _average_cost_basis(engine)
        holdings = []
        for h in state["holdings"]:
            basis = cost_basis.get(h["ticker"])
            gain = gain_pct = None
            if basis:
                invested = basis * h["shares"]
                gain = h["value_usd"] - invested
                gain_pct = (gain / invested * 100) if invested else 0.0
            holdings.append({**h, "avg_cost": basis,
                             "gain_usd": gain, "gain_pct": gain_pct})

        needs_checkpoint = analytics.needs_year_checkpoint(engine)
        return {
            "state": state,
            "holdings": holdings,
            "prices": prices,
            "marks": marks,
            "universe": [{"ticker": t, "name": engine.market.name_of(t),
                          "price": prices.get(t)} for t in engine.universe()],
            "interval_no": engine.clock.current_index + 1,
            "total_intervals": engine.clock.total_intervals,
            "current_date": engine.current_date,
            "month_label": month_label(engine.current_date),
            "year": engine.clock.current_year,
            # A finished simulation is one with no next interval AND no
            # outstanding year to close.
            "is_finished": not engine.clock.has_next() and not needs_checkpoint,
            "needs_checkpoint": needs_checkpoint,
            # Set by the redirect after a successful advance -> shows the
            # month-change transition so time visibly moved.
            "moved": request.args.get("moved") == "1",
            "report_years": analytics.available_report_years(engine),
        }

    def _average_cost_basis(engine, user_id=None):
        """
        Weighted-average cost per share for each currently-held stock, computed
        from the trade log. Sells reduce the position but leave the average
        unchanged (standard average-cost accounting).
        """
        portfolio = engine.portfolios[user_id or engine.default_user]
        shares, cost = {}, {}
        for t in portfolio.transactions:
            if t.action == "BUY":
                shares[t.ticker] = shares.get(t.ticker, 0.0) + t.shares
                cost[t.ticker] = cost.get(t.ticker, 0.0) + t.total_value
            else:
                held = shares.get(t.ticker, 0.0)
                if held > 0:
                    avg = cost.get(t.ticker, 0.0) / held
                    shares[t.ticker] = held - t.shares
                    cost[t.ticker] = cost.get(t.ticker, 0.0) - avg * t.shares
        return {k: cost[k] / v for k, v in shares.items()
                if v > 1e-9 and cost.get(k)}

    # ---------------- routes ----------------
    @app.route("/")
    def dashboard():
        engine = get_engine()
        return render_template("dashboard.html", **dashboard_context(engine))

    @app.route("/trade", methods=["POST"])
    def trade():
        engine = get_engine()
        action = request.form.get("action")
        ticker = request.form.get("ticker", "")
        mode = request.form.get("mode")          # dollars | shares | percent
        raw = request.form.get("amount", "").strip()

        try:
            if not raw:
                raise TradeError("Enter an amount.")
            amount = float(raw)

            # PERCENTAGE ACTIONS USE fraction= -- the engine computes the
            # quantity from its own exact internal balance, so no displayed or
            # rounded figure is ever sent back to it.
            if mode == "percent":
                kwargs = {"fraction": amount / 100.0}
            elif mode == "shares":
                kwargs = {"shares": amount}
            else:
                kwargs = {"dollars": amount}

            if action == "buy":
                txn = engine.buy(ticker, **kwargs)
            elif action == "sell":
                txn = engine.sell(ticker, **kwargs)
            else:
                raise TradeError("Unknown action.")

            flash(f"{txn.action} {txn.shares:,.4f} {txn.ticker} @ "
                  f"${txn.price_usd:,.2f} = ${txn.total_value:,.2f}", "success")
        except ValueError:
            flash("Amount must be a number.", "error")
        except TradeError as e:
            flash(str(e), "error")
        except ConcurrentModificationError:
            flash("This simulation was changed in another tab. Reloaded.",
                  "error")
        return redirect(url_for("dashboard"))

    @app.route("/advance", methods=["POST"])
    def advance():
        """
        Move one month forward -- UNLESS the year must be closed first.

        Standing on December, we deliberately do NOT advance. The year-end
        report is a mandatory checkpoint: the only route into January is the
        "Continue" button on that screen (/continue-year).
        """
        engine = get_engine()
        if analytics.needs_year_checkpoint(engine):
            return redirect(url_for("checkpoint"))
        try:
            new_date = engine.advance()
        except ConcurrentModificationError:
            flash("This simulation was changed in another tab. Reloaded.",
                  "error")
            return redirect(url_for("dashboard"))

        if new_date is None:
            flash("Simulation complete -- you've reached the end of the data.",
                  "success")
            return redirect(url_for("dashboard"))
        # moved=1 triggers the month-change transition on the dashboard.
        return redirect(url_for("dashboard", moved=1))

    @app.route("/checkpoint")
    def checkpoint():
        """
        The mandatory end-of-year review. Renders the annual report from LIVE
        state (the clock is parked on 31 December, so that IS the year-end
        state) -- the snapshot itself is persisted when the user continues.
        """
        engine = get_engine()
        if not analytics.needs_year_checkpoint(engine):
            return redirect(url_for("dashboard"))
        data = analytics.live_year_end_report(engine)
        next_year = (data["year"] + 1) if engine.clock.has_next() else None
        return render_template(
            "report.html", r=data, checkpoint=True, next_year=next_year,
            current_date=engine.current_date,
            report_years=analytics.available_report_years(engine))

    @app.route("/continue-year", methods=["POST"])
    def continue_year():
        """
        Acknowledge the annual report and roll into January.

        engine.advance() here is what fires and persists the year-end snapshot.
        Guarded by needs_year_checkpoint(), so a double-click cannot advance
        twice: once the year is closed the gate is false and this is a no-op.
        """
        engine = get_engine()
        if analytics.needs_year_checkpoint(engine):
            try:
                if engine.advance() is None:
                    flash("Simulation complete -- that was the final year.",
                          "success")
                    return redirect(url_for("dashboard"))
            except ConcurrentModificationError:
                flash("This simulation was changed in another tab. Reloaded.",
                      "error")
                return redirect(url_for("dashboard"))
        return redirect(url_for("dashboard", moved=1))

    @app.route("/report/<int:year>")
    def report(year):
        engine = get_engine()
        data = analytics.annual_report(engine, year)
        if not data:
            flash(f"No report available for {year} yet.", "error")
            return redirect(url_for("dashboard"))
        return render_template(
            "report.html", r=data, checkpoint=False,
            current_date=engine.current_date,
            report_years=analytics.available_report_years(engine))

    @app.route("/stocks")
    def stocks():
        """
        Read-only company background.

        NO-LOOKAHEAD: the only market number on this page is the price AS OF the
        current simulated date, via engine.current_price() (which takes no date
        argument). There is no history, no chart, no return figure -- and the
        descriptions in stock_info.py are restricted to what each business does,
        with no performance narrative that could reveal later events.
        """
        engine = get_engine()
        rows = []
        for ticker in engine.universe():          # data-driven, from the DB
            info = stock_info.profile(ticker)
            rows.append({
                "ticker": ticker,
                "name": engine.market.name_of(ticker),
                "price": engine.current_price(ticker),   # as-of today only
                **info,
            })
        return render_template(
            "stocks.html", stocks=rows, current_date=engine.current_date,
            report_years=analytics.available_report_years(engine))

    @app.route("/performance")
    def performance():
        engine = get_engine()
        pf = analytics.portfolio_value_series(engine)
        bh, idx = analytics.benchmark_series(engine)

        series = [{"label": "Your portfolio", "color": COLOR_PORTFOLIO,
                   "dash": DASH_PORTFOLIO, "points": pf},
                  {"label": "Buy & hold (10 stocks)", "color": COLOR_BUYHOLD,
                   "dash": DASH_BUYHOLD, "points": bh}]
        if idx:
            series.append({"label": "S&P 500 (Total Return)",
                           "color": COLOR_INDEX, "dash": DASH_INDEX,
                           "points": idx})

        return render_template(
            "performance.html",
            chart=charts.line_chart_svg(series),
            marks=engine.benchmark_state(),
            state=engine.portfolio_state(),
            risk=analytics.overall_risk_metrics(engine),
            current_date=engine.current_date,
            report_years=analytics.available_report_years(engine))

    @app.route("/history")
    def history():
        engine = get_engine()
        return render_template(
            "history.html", log=list(reversed(engine.transaction_log())),
            current_date=engine.current_date,
            report_years=analytics.available_report_years(engine))

    @app.route("/reset", methods=["POST"])
    def reset():
        """Start a brand-new simulation (the old one stays saved on disk)."""
        session.pop("sim_id", None)
        flash("Started a new simulation with $100,000.", "success")
        return redirect(url_for("dashboard"))

    # ---------------- template helpers ----------------
    @app.template_filter("money")
    def _money(v):
        if v is None:
            return "--"
        return f"${floor_to(v, 2):,.2f}"

    @app.template_filter("shares")
    def _shares(v):
        return f"{floor_to(v, 6):,.4f}"

    @app.template_filter("pct")
    def _pct(v):
        return "--" if v is None else f"{v:+.2f}%"

    @app.template_filter("metricpct")
    def _metricpct(v):
        """Risk-metric fraction (0.182) -> '18.2%'. Non-signed. '--' if None."""
        return "--" if v is None else f"{v * 100:.1f}%"

    @app.template_filter("ratio")
    def _ratio(v):
        """Sharpe ratio to 2dp, or '--' when undefined (flat/all-cash)."""
        return "--" if v is None else f"{v:.2f}"

    return app
