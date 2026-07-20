"""
UI <-> engine integration tests.

The engine has its own suite; these test the WEB LAYER: that routes drive the
engine correctly, that errors surface as readable messages instead of 500s, and
above all that NO FUTURE DATA reaches a rendered page.
"""

import re
import shutil

import pytest

from src import config as data_config
from web import create_app

DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


@pytest.fixture
def client(tmp_path):
    app = create_app(sim_db_path=tmp_path / "sims.db")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def flashes(response):
    return [m[1].strip() for m in
            re.findall(r'flash (\w+)">([^<]+)', response.get_data(as_text=True))]


def current_date(client):
    body = client.get("/").get_data(as_text=True)
    return re.search(r"Prices are as of ([\d-]+)", body).group(1)


def step(client):
    """
    Move the simulation forward one month, honouring the year-end checkpoint.

    On December, /advance deliberately refuses to advance and redirects to the
    mandatory annual-report checkpoint; the only way onward is /continue-year.
    Returns True if time actually moved.
    """
    r = client.post("/advance")
    if r.status_code == 302 and "/checkpoint" in r.headers["Location"]:
        client.get("/checkpoint")               # user reads the report
        client.post("/continue-year")           # ...and acknowledges it
        return True
    return r.status_code == 302


class TestPagesRender:
    @pytest.mark.parametrize("path", ["/", "/stocks", "/performance", "/history"])
    def test_pages_load(self, client, path):
        assert client.get(path).status_code == 200

    @pytest.mark.parametrize("path", ["/", "/stocks", "/performance", "/history"])
    def test_ets_branding_on_every_page(self, client, path):
        """
        REGRESSION: two templates kept a hardcoded "Portfolio Simulator" title
        after the rebrand, so the browser tab was inconsistent. Titles now come
        from the single APP_NAME constant.
        """
        body = client.get(path).get_data(as_text=True)
        title = re.search(r"<title>(.*?)</title>", body, re.S).group(1)
        assert "ETS" in title, f"{path} title not rebranded: {title!r}"
        assert "Portfolio Simulator" not in body

    def test_no_external_resources_anywhere(self, client):
        """The offline guarantee: nothing may be fetched from another origin."""
        for path in ["/", "/stocks", "/performance", "/history"]:
            body = client.get(path).get_data(as_text=True)
            for url in re.findall(r'(?:src|href)="([^"]+)"', body):
                assert not url.startswith(("http://", "https://", "//")), \
                    f"{path} references external resource {url}"

    def test_javascript_is_inline_not_external(self, client):
        """The single-submit guard must be inline JS, never a fetched script."""
        body = client.get("/").get_data(as_text=True)
        assert "<script>" in body                    # inline script present
        assert not re.search(r"<script[^>]+src=", body)  # no external <script src>


class TestPolish:
    def test_data_tables_are_wrapped_for_mobile_scroll(self, client):
        """Every data table sits in a .table-wrap so it scrolls on narrow screens
        instead of pushing the whole page sideways."""
        client.post("/trade", data={"action": "buy", "ticker": "AAPL",
                                    "mode": "percent", "amount": "50"})
        for path in ["/", "/history"]:
            body = client.get(path).get_data(as_text=True)
            main = body.split("<main")[1].split("</main>")[0]
            # Each rendered <table> must be preceded by a table-wrap opener.
            assert main.count("<table") == main.count('class="table-wrap"'), \
                f"{path} has a table not wrapped for mobile scrolling"

    def test_single_submit_guard_present(self, client):
        body = client.get("/").get_data(as_text=True)
        assert "dataset.submitting" in body   # the double-click guard

    def test_dev_secret_flag_set_without_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        app = create_app(sim_db_path=tmp_path / "s.db")
        assert app.config["USING_DEV_SECRET"] is True
        assert app.config["SECRET_KEY"]       # still has a usable fallback

    def test_env_secret_is_used_when_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "a-real-secret-value")
        app = create_app(sim_db_path=tmp_path / "s.db")
        assert app.config["USING_DEV_SECRET"] is False
        assert app.config["SECRET_KEY"] == "a-real-secret-value"

    def test_run_ui_banner_is_rebranded(self):
        import pathlib
        src = pathlib.Path("run_ui.py").read_text(encoding="utf-8")
        assert "ETS" in src and "Portfolio Simulator" not in src


class TestStocksPage:
    def test_lists_every_tradable_stock_from_the_database(self, client):
        body = client.get("/stocks").get_data(as_text=True)
        for ticker in ["RELIANCE.NS", "HDFCBANK.NS", "MARUTI.NS", "AAPL",
                       "TSLA", "NVDA", "AMZN", "JPM", "ASML", "MC.PA"]:
            assert ticker in body, f"{ticker} missing from stocks page"

    def test_shows_background_fields(self, client):
        body = client.get("/stocks").get_data(as_text=True)
        assert "Semiconductors" in body        # sector
        assert "France" in body                # region
        assert "Trades in EUR" in body         # native currency
        assert "Louis" in body                 # description text

    def test_does_not_offer_indices(self, client):
        body = client.get("/stocks").get_data(as_text=True)
        assert "^SP500TR" not in body and "^GSPC" not in body

    def test_price_matches_the_dashboard_price(self, client):
        """The stocks page must use the same as-of-today price, not another."""
        for _ in range(5):
            step(client)
        dash = client.get("/").get_data(as_text=True)
        stocks = client.get("/stocks").get_data(as_text=True)
        # AAPL's price on the dashboard dropdown must appear on the stocks page.
        price = re.search(r"AAPL — Apple \((\$[\d,]+\.\d\d)\)", dash).group(1)
        assert price in stocks

    def test_stock_list_is_data_driven_not_hardcoded(self, client):
        """The dropdown must come from the DB, and must include Maruti."""
        body = client.get("/").get_data(as_text=True)
        assert 'value="MARUTI.NS"' in body
        # The replaced stock must be gone everywhere.
        assert "TMPV" not in body and "TATAMOTORS" not in body

    def test_indices_are_not_offered_for_trading(self, client):
        body = client.get("/").get_data(as_text=True)
        assert 'value="^SP500TR"' not in body
        assert 'value="^GSPC"' not in body


class TestNoLookaheadInTheUI:
    """The single most important guarantee, asserted against rendered HTML."""

    def test_no_page_contains_a_future_date(self, client):
        client.post("/trade", data={"action": "buy", "ticker": "AAPL",
                                    "mode": "percent", "amount": "40"})
        for _ in range(20):
            step(client)
        today = current_date(client)

        # /stocks included: the new company page must not leak future data.
        for path in ["/", "/stocks", "/performance", "/history", "/report/2021"]:
            html = client.get(path).get_data(as_text=True)
            future = [d for d in set(DATE_RE.findall(html)) if d > today]
            assert not future, f"{path} leaked future dates: {future}"

    def test_stock_page_shows_no_performance_data(self, client):
        """
        The company page may show the CURRENT price only. Any percentage,
        return, or history would reveal how a stock has done -- and if it
        covered the future, would break the simulation.
        """
        for _ in range(14):
            step(client)
        html = client.get("/stocks").get_data(as_text=True)
        # Scan only the visible <main> content -- not the trailing footer or the
        # global single-submit <script> (which legitimately contains JS keywords
        # like "return" that are not page content).
        body = html.split("<main")[1].split("</main>")[0]
        assert "%" not in body, "stock page shows a percentage figure"
        assert "<polyline" not in body, "stock page contains a chart"
        for word in ["return", "performance", "gain", "loss", "1-year", "YTD"]:
            assert word not in body.lower().replace("no past performance", ""), \
                f"stock page mentions '{word}'"

    def test_chart_stops_at_the_current_interval(self, client):
        for _ in range(7):
            step(client)
        today = current_date(client)
        html = client.get("/performance").get_data(as_text=True)
        assert max(DATE_RE.findall(html)) <= today

    def test_advancing_reveals_strictly_later_dates(self, client):
        before = current_date(client)
        client.post("/advance")
        assert current_date(client) > before


class TestTrading:
    def test_buy_by_dollars(self, client):
        r = client.post("/trade", data={"action": "buy", "ticker": "AAPL",
                                        "mode": "dollars", "amount": "20000"},
                        follow_redirects=True)
        assert "BUY" in flashes(r)[0]
        assert "AAPL" in r.get_data(as_text=True)

    def test_buy_by_shares(self, client):
        r = client.post("/trade", data={"action": "buy", "ticker": "NVDA",
                                        "mode": "shares", "amount": "50"},
                        follow_redirects=True)
        assert "50.0000 NVDA" in flashes(r)[0]

    def test_sell_all_via_percent_uses_fraction_and_clears_position(self, client):
        """
        'Sell 100%' must go through fraction= so no displayed number is echoed
        back, and must leave no dust position behind.
        """
        client.post("/trade", data={"action": "buy", "ticker": "AAPL",
                                    "mode": "dollars", "amount": "12345.67"},
                    follow_redirects=True)
        r = client.post("/trade", data={"action": "sell", "ticker": "AAPL",
                                        "mode": "percent", "amount": "100"},
                        follow_redirects=True)
        assert "SELL" in flashes(r)[0]
        body = r.get_data(as_text=True)
        # Bound to the holdings PANEL. Splitting on "</table>" is wrong here:
        # with no holdings the template renders a message and no table at all,
        # so the slice would run on into the trade dropdown (which lists AAPL).
        panel = body.split("Your holdings")[1].split("</section>")[0]
        assert "AAPL" not in panel
        assert "don&#39;t hold any stocks" in panel or "don't hold any stocks" in panel

    def test_sell_half_via_percent(self, client):
        client.post("/trade", data={"action": "buy", "ticker": "NVDA",
                                    "mode": "dollars", "amount": "10000"},
                    follow_redirects=True)
        r = client.post("/trade", data={"action": "sell", "ticker": "NVDA",
                                        "mode": "percent", "amount": "50"},
                        follow_redirects=True)
        assert "SELL" in flashes(r)[0]

    @pytest.mark.parametrize("data,expected", [
        ({"action": "buy", "ticker": "AAPL", "mode": "dollars",
          "amount": "5000000"}, "Insufficient cash"),
        ({"action": "sell", "ticker": "AMZN", "mode": "shares",
          "amount": "1"}, "No holdings"),
        ({"action": "buy", "ticker": "AAPL", "mode": "dollars",
          "amount": "abc"}, "must be a number"),
        ({"action": "buy", "ticker": "AAPL", "mode": "dollars",
          "amount": ""}, "Enter an amount"),
        ({"action": "buy", "ticker": "^SP500TR", "mode": "dollars",
          "amount": "100"}, "non-tradable"),
        ({"action": "buy", "ticker": "AAPL", "mode": "percent",
          "amount": "150"}, "fraction"),
        ({"action": "buy", "ticker": "AAPL", "mode": "percent",
          "amount": "0"}, "fraction"),
    ])
    def test_invalid_trades_flash_an_error_not_a_500(self, client, data, expected):
        r = client.post("/trade", data=data, follow_redirects=True)
        assert r.status_code == 200
        assert any(expected.lower() in f.lower() for f in flashes(r)), flashes(r)

    def test_rejected_trade_leaves_portfolio_untouched(self, client):
        client.post("/trade", data={"action": "buy", "ticker": "AAPL",
                                    "mode": "dollars", "amount": "5000000"},
                    follow_redirects=True)
        assert client.get("/history").get_data(as_text=True).count("<tr>") <= 1


class TestAdvanceAndReport:
    def test_advance_moves_one_month(self, client):
        body = client.get("/").get_data(as_text=True)
        assert "Month 1 of 60" in body
        client.post("/advance")
        assert "Month 2 of 60" in client.get("/").get_data(as_text=True)

    def test_december_advance_is_gated_to_the_checkpoint(self, client):
        """December must NOT roll silently into January."""
        for _ in range(11):
            client.post("/advance")             # now parked on 2021-12-31
        assert current_date(client) == "2021-12-31"

        r = client.post("/advance")
        assert r.status_code == 302
        assert "/checkpoint" in r.headers["Location"]
        # ...and time did not move.
        assert current_date(client) == "2021-12-31"

    def test_checkpoint_shows_the_report_and_continue_button(self, client):
        for _ in range(11):
            client.post("/advance")
        body = client.get("/checkpoint").get_data(as_text=True)
        assert "Year 2021 complete" in body
        assert "Continue to 2022" in body
        assert "Annual Performance Report" in body

    def test_only_continue_year_moves_into_january(self, client):
        for _ in range(11):
            client.post("/advance")
        client.get("/checkpoint")
        assert current_date(client) == "2021-12-31"

        client.post("/continue-year")
        assert current_date(client).startswith("2022-01")

    def test_continue_year_is_idempotent(self, client):
        """A double-clicked Continue must not skip an extra month."""
        for _ in range(11):
            client.post("/advance")
        client.post("/continue-year")
        after_first = current_date(client)
        client.post("/continue-year")            # second click
        assert current_date(client) == after_first

    def test_year_end_hook_fires_exactly_once_per_year(self, client):
        """Guards against reintroducing the year-end double-fire bug."""
        for _ in range(26):
            step(client)
        body = client.get("/").get_data(as_text=True)
        years = re.findall(r"/report/(\d{4})", body)
        assert sorted(years) == sorted(set(years)), f"duplicate reports: {years}"

    def test_dashboard_prompts_to_complete_the_year(self, client):
        for _ in range(11):
            client.post("/advance")
        body = client.get("/").get_data(as_text=True)
        assert "Complete 2021" in body
        assert "Advance to next month" not in body

    def test_report_contains_the_required_sections(self, client):
        client.post("/trade", data={"action": "buy", "ticker": "AAPL",
                                    "mode": "percent", "amount": "60"})
        for _ in range(12):
            step(client)
        body = client.get("/report/2021").get_data(as_text=True)
        for needed in ["Value at year start", "Value at year end",
                       "How you compared", "Best performer held",
                       "Decisions made in 2021"]:
            assert needed in body, f"report missing: {needed}"

    def test_report_handles_a_year_with_no_trades(self, client):
        for _ in range(12):
            step(client)
        r = client.get("/report/2021")
        assert r.status_code == 200
        assert "no trades" in r.get_data(as_text=True).lower()

    def test_report_for_an_incomplete_year_redirects(self, client):
        r = client.get("/report/2025", follow_redirects=True)
        assert "No report available" in " ".join(flashes(r))

    def test_checkpoint_redirects_when_not_due(self, client):
        r = client.get("/checkpoint", follow_redirects=False)
        assert r.status_code == 302 and "/checkpoint" not in r.headers["Location"]

    def test_simulation_can_run_to_the_very_end(self, client):
        client.post("/trade", data={"action": "buy", "ticker": "NVDA",
                                    "mode": "percent", "amount": "100"})
        for _ in range(70):
            step(client)
        body = client.get("/").get_data(as_text=True)
        assert "Month 60 of 60" in body
        assert "disabled" in body
        for year in (2021, 2022, 2023, 2024, 2025):
            assert client.get(f"/report/{year}").status_code == 200


class TestMonthTransition:
    def test_advance_signals_the_month_change(self, client):
        r = client.post("/advance")
        assert "moved=1" in r.headers["Location"]
        body = client.get("/?moved=1").get_data(as_text=True)
        assert "monthflash" in body
        assert "Time advanced" in body

    def test_month_is_shown_in_words(self, client):
        assert "January 2021" in client.get("/").get_data(as_text=True)
        client.post("/advance")
        assert "February 2021" in client.get("/").get_data(as_text=True)

    def test_transition_not_shown_on_a_plain_page_load(self, client):
        assert "monthflash" not in client.get("/").get_data(as_text=True)


class TestEdgeCases:
    def test_performance_renders_with_a_single_data_point(self, client):
        assert client.get("/performance").status_code == 200

    def test_unknown_sim_id_in_session_recovers(self, client):
        client.get("/")
        with client.session_transaction() as s:
            s["sim_id"] = "does-not-exist"
        assert client.get("/").status_code == 200

    def test_reset_starts_a_fresh_simulation(self, client):
        client.post("/trade", data={"action": "buy", "ticker": "AAPL",
                                    "mode": "percent", "amount": "100"})
        client.post("/advance")
        r = client.post("/reset", follow_redirects=True)
        body = r.get_data(as_text=True)
        assert "Month 1 of 60" in body and "$100,000.00" in body

    def test_state_persists_across_requests(self, client):
        client.post("/trade", data={"action": "buy", "ticker": "AAPL",
                                    "mode": "dollars", "amount": "25000"})
        client.post("/advance")
        body = client.get("/").get_data(as_text=True)
        assert "AAPL" in body
        assert "Month 2 of 60" in body


class TestConfiguredMarketDatabase:
    def test_reloaded_sim_honours_configured_market_db(self, tmp_path):
        """
        REGRESSION: TradingEngine.load() rebuilds SimConfig from the saved row,
        which does not persist db_path (it is environment-specific). A configured
        market DB was therefore silently dropped on RELOAD, so the first request
        and every subsequent request could read different databases.

        Made observable by removing one stock from the alternate database: if the
        reload path ignores our configured DB, that stock reappears.
        """
        import sqlite3

        alt = tmp_path / "alt_market.db"
        shutil.copy(data_config.DB_PATH, alt)
        conn = sqlite3.connect(alt)
        conn.execute("DELETE FROM prices WHERE ticker = 'JPM'")
        conn.commit()
        conn.close()

        app = create_app(sim_db_path=tmp_path / "sims.db", market_db_path=alt)
        app.config["TESTING"] = True
        with app.test_client() as c:
            first = c.get("/").get_data(as_text=True)          # new-sim path
            assert 'value="JPM"' not in first, "custom DB ignored on creation"

            c.post("/advance")                                  # -> reload path
            later = c.get("/").get_data(as_text=True)
            assert 'value="JPM"' not in later, (
                "reloaded simulation fell back to the default market.db")
            # The rest of the universe is still intact.
            assert 'value="MARUTI.NS"' in later
