"""
Tests for the ETS Intelligence layer (web/intelligence.py).

Each bias detector is tested with a constructed sequence that SHOULD trigger it
and one that SHOULD NOT, plus the no-lookahead guarantee (the price accessor
must refuse any date after the current date).
"""

import pytest

from web import intelligence as intel
from web.intelligence import LookaheadError, PriceView


# --- a tiny fake price history for the panic/FOMO/cut detectors ---
def make_prices(history, current_date):
    """history: {ticker: {date: price}}. Returns a date-gated PriceView."""
    def price_asof(ticker, iso):
        series = history.get(ticker, {})
        past = {d: p for d, p in series.items() if d <= iso}
        return past[max(past)] if past else None
    return PriceView(price_asof, current_date)


class TestPriceViewNoLookahead:
    def test_refuses_future_date(self):
        pv = make_prices({"X": {"2021-01-31": 100}}, current_date="2021-06-30")
        with pytest.raises(LookaheadError):
            pv.at("X", "2021-07-31")            # after current date

    def test_allows_past_and_present(self):
        pv = make_prices({"X": {"2021-01-31": 100, "2021-06-30": 120}},
                         current_date="2021-06-30")
        assert pv.at("X", "2021-06-30") == 120   # present ok
        assert pv.at("X", "2021-01-31") == 100   # past ok

    def test_change_pct_never_reads_future(self):
        pv = make_prices({"X": {"2021-01-31": 100}}, current_date="2021-03-31")
        with pytest.raises(LookaheadError):
            pv.change_pct("X", "2021-01-31", "2021-12-31")


class TestDisposition:
    def test_triggers_selling_winners_holding_losers(self):
        realized = [{"ticker": "A", "pnl_pct": 30}, {"ticker": "B", "pnl_pct": 15}]
        holdings = [{"ticker": "C", "unreal_pct": -18}]
        b = intel.detect_disposition(realized, holdings)
        assert b and b["name"] == "Disposition effect"
        assert "2 winning trades" in b["evidence"]

    def test_no_trigger_with_only_one_winner_sold(self):
        realized = [{"ticker": "A", "pnl_pct": 30}]
        holdings = [{"ticker": "C", "unreal_pct": -18}]
        assert intel.detect_disposition(realized, holdings) is None

    def test_no_trigger_when_no_losers_held(self):
        realized = [{"ticker": "A", "pnl_pct": 30}, {"ticker": "B", "pnl_pct": 15}]
        holdings = [{"ticker": "C", "unreal_pct": +5}]
        assert intel.detect_disposition(realized, holdings) is None


class TestPanicSelling:
    def test_triggers_loss_sale_after_sharp_drop(self):
        prices = make_prices(
            {"X": {"2021-01-31": 100, "2021-05-31": 70}}, "2021-12-31")
        realized = [{"ticker": "X", "pnl_pct": -25, "sell_date": "2021-05-31"}]
        b = intel.detect_panic_selling(realized, prices)
        assert b and b["name"].startswith("Loss aversion")

    def test_no_trigger_if_sale_was_profitable(self):
        prices = make_prices(
            {"X": {"2021-01-31": 100, "2021-05-31": 70}}, "2021-12-31")
        realized = [{"ticker": "X", "pnl_pct": +12, "sell_date": "2021-05-31"}]
        assert intel.detect_panic_selling(realized, prices) is None

    def test_no_trigger_without_a_sharp_prior_drop(self):
        prices = make_prices(
            {"X": {"2021-01-31": 100, "2021-05-31": 98}}, "2021-12-31")
        realized = [{"ticker": "X", "pnl_pct": -3, "sell_date": "2021-05-31"}]
        assert intel.detect_panic_selling(realized, prices) is None


class TestFomo:
    def test_triggers_buy_after_big_runup(self):
        prices = make_prices(
            {"X": {"2021-01-31": 100, "2021-05-31": 140}}, "2021-12-31")
        buys = [{"ticker": "X", "date": "2021-05-31"}]
        b = intel.detect_fomo(buys, prices)
        assert b and b["name"].startswith("FOMO")
        assert "40%" in b["evidence"]

    def test_no_trigger_after_flat_or_down(self):
        prices = make_prices(
            {"X": {"2021-01-31": 100, "2021-05-31": 105}}, "2021-12-31")
        buys = [{"ticker": "X", "date": "2021-05-31"}]
        assert intel.detect_fomo(buys, prices) is None


class TestOverConcentration:
    def test_triggers_above_threshold(self):
        holdings = [{"ticker": "A", "value_usd": 60000},
                    {"ticker": "B", "value_usd": 20000}]
        b = intel.detect_overconcentration(holdings, total_value=100000)
        assert b and b["name"] == "Over-concentration"
        assert "60%" in b["evidence"]

    def test_severe_flag_above_60(self):
        holdings = [{"ticker": "A", "value_usd": 70000}]
        b = intel.detect_overconcentration(holdings, total_value=100000)
        assert b["severity"] == 3

    def test_no_trigger_when_diversified(self):
        holdings = [{"ticker": "A", "value_usd": 25000},
                    {"ticker": "B", "value_usd": 25000}]
        assert intel.detect_overconcentration(holdings, 100000) is None


class TestOvertrading:
    def test_triggers_above_threshold(self):
        trades = [{"action": "BUY"}] * 25
        b = intel.detect_overtrading(trades)
        assert b and b["name"] == "Overtrading"
        assert "25 trades" in b["evidence"]

    def test_no_trigger_for_modest_activity(self):
        assert intel.detect_overtrading([{"action": "BUY"}] * 8) is None


class TestAnalyzeIntegration:
    def _base(self, current_date="2021-12-31"):
        return {
            "year": 2021, "current_date": current_date,
            "trades": [], "realized": [], "holdings": [],
            "total_value": 100000,
            "prices": make_prices({}, current_date),
            "best": None, "worst": None, "beat": [], "risk": {},
        }

    def test_clean_when_nothing_fires(self):
        data = self._base()
        data["trades"] = [{"action": "BUY", "ticker": "A", "date": "2021-02-28"}]
        result = intel.analyze(data)
        assert result["clean"] is True
        assert result["biases"] == []
        assert any(i["kind"] == "disciplined" for i in result["insights"])

    def test_caps_insights_at_five(self):
        data = self._base()
        data["best"] = {"ticker": "A", "pnl_usd": 5000, "pnl_pct": 40,
                        "buy_price": 100, "sell_price": 140}
        data["worst"] = {"ticker": "B", "pnl_usd": -2000, "pnl_pct": -20,
                         "buy_price": 100, "sell_price": 80}
        data["beat"] = [{"name": "X", "diff": 5}, {"name": "Y", "diff": -3}]
        result = intel.analyze(data)
        assert len(result["insights"]) <= 5

    def test_biases_ranked_by_severity(self):
        data = self._base()
        data["holdings"] = [{"ticker": "A", "value_usd": 80000, "unreal_pct": 5}]
        data["trades"] = [{"action": "BUY", "ticker": "A"}] * 35
        result = intel.analyze(data)
        sevs = [b["severity"] for b in result["biases"]]
        assert sevs == sorted(sevs, reverse=True)
