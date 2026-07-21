"""
intelligence.py
===============
ETS Intelligence -- the decision-insight + behavioural-bias analysis layer.
This is what turns ETS from a scorekeeper into a teacher.

DESIGN: standalone and reusable. The detectors take PLAIN DATA (lists of trades,
realized round-trips, holdings, and a price accessor) -- no Flask, no engine
import. So a future "challenge mode" could feed the same functions synthetic
scenario data and reuse every detector to score behaviour, with zero changes
here. analytics.py does the engine->data wiring; this module does the thinking.

=========================  NO-LOOKAHEAD (most important)  =====================
Every price lookup goes through PriceView, which REFUSES any date after
`current_date` by raising LookaheadError. A detector therefore cannot read a
future price even by accident -- the accessor will not answer. All the dates a
detector uses come from the user's own past trades or from `current_date`
itself, so nothing here can reveal what happens next in the simulation.
==============================================================================
"""

import math

# ---------------------------------------------------------------------------
# Thresholds -- documented and tunable in one place.
# ---------------------------------------------------------------------------
SELL_WINNER_PCT = 10.0      # realized gain >= +10% counts as "sold a winner"
HOLD_LOSER_PCT = -10.0      # unrealized position <= -10% counts as "held a loser"
PANIC_DROP_PCT = -15.0      # a >=15% fall in the ~2 months before a loss-making
                            #   sell signals a fear-driven exit
FOMO_RISE_PCT = 25.0        # buying after a >=25% run-up over the prior ~2 months
                            #   signals chasing momentum
LOOKBACK_MONTHS = 2         # window for the panic / FOMO "recent move" checks
CONCENTRATION_PCT = 40.0    # >=40% of the portfolio in one stock = over-concentrated
CONCENTRATION_SEVERE = 60.0
OVERTRADING_TRADES = 20     # >20 trades in a year = overtrading (vs ~12 monthly steps)
CUT_WINNER_PCT = 20.0       # a sold stock up >=20% SINCE the sale (up to now) =
                            #   a notable "cut a winner early"


class LookaheadError(Exception):
    """Raised if any analysis tries to read a price after the current date."""


class PriceView:
    """
    Date-gated price accessor. The one and only way this module reads prices,
    so no-lookahead is guaranteed structurally rather than by discipline.
    """

    def __init__(self, price_asof, current_date):
        self._price_asof = price_asof
        self._now = current_date

    def at(self, ticker, iso_date):
        if iso_date > self._now:
            raise LookaheadError(
                f"refused price for {ticker} at {iso_date}: "
                f"after current simulated date {self._now}")
        return self._price_asof(ticker, iso_date)

    def change_pct(self, ticker, from_date, to_date):
        """Percent change between two dates (both must be <= current_date)."""
        a = self.at(ticker, from_date)
        b = self.at(ticker, to_date)
        if not a or not b:
            return None
        return (b / a - 1.0) * 100.0


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _months_before(iso_date, n):
    """ISO date `n` calendar months earlier (day clamped to 28; price_asof uses
    `<=` so the exact day doesn't matter)."""
    y, m, _ = (int(x) for x in iso_date.split("-"))
    total = (y * 12 + (m - 1)) - n
    y2, m2 = divmod(total, 12)
    return f"{y2:04d}-{m2 + 1:02d}-28"


def _money(x):
    """Floor to cents for display -- never round a dollar figure up."""
    return math.floor(x * 100) / 100


def _bias(name, definition, evidence, severity):
    return {"name": name, "definition": definition,
            "evidence": evidence, "severity": severity}


def _month_name(iso_date):
    months = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
    y, m, _ = iso_date.split("-")
    return f"{months[int(m) - 1]} {y}"


# ===========================================================================
# PART B -- behavioural bias detectors
# Each returns a bias dict, or None if the behaviour wasn't exhibited.
# ===========================================================================
def detect_disposition(realized, holdings):
    """
    DISPOSITION EFFECT: selling winners while clinging to losers.

    realized: closed trades this year (need pnl_pct).
    holdings: current positions (need ticker, unreal_pct).
    """
    winners_sold = [r for r in realized if r["pnl_pct"] >= SELL_WINNER_PCT]
    losers_held = [h for h in holdings
                   if h.get("unreal_pct") is not None
                   and h["unreal_pct"] <= HOLD_LOSER_PCT]
    if len(winners_sold) >= 2 and len(losers_held) >= 1:
        w = max(winners_sold, key=lambda r: r["pnl_pct"])
        l = min(losers_held, key=lambda h: h["unreal_pct"])
        evidence = (
            f"You locked in gains on {len(winners_sold)} winning trades this "
            f"year (e.g. {w['ticker']} at +{w['pnl_pct']:.0f}%), yet you're "
            f"still holding {len(losers_held)} position(s) at a loss "
            f"(e.g. {l['ticker']} at {l['unreal_pct']:.0f}%).")
        severity = 3 if len(winners_sold) >= 3 else 2
        return _bias(
            "Disposition effect",
            "The tendency to sell winners too early while holding losers, "
            "hoping they bounce back.",
            evidence, severity)
    return None


def detect_panic_selling(realized, prices):
    """
    PANIC SELLING / LOSS AVERSION: selling at a loss right after a sharp drop.
    """
    panics = []
    for r in realized:
        if r["pnl_pct"] >= 0:
            continue  # only loss-making exits qualify as panic
        prior = prices.change_pct(
            r["ticker"], _months_before(r["sell_date"], LOOKBACK_MONTHS),
            r["sell_date"])
        if prior is not None and prior <= PANIC_DROP_PCT:
            panics.append((r, prior))
    if panics:
        r, drop = min(panics, key=lambda x: x[1])
        evidence = (
            f"You sold {r['ticker']} in {_month_name(r['sell_date'])} after it "
            f"had fallen {drop:.0f}% over the prior ~2 months, realizing a "
            f"{r['pnl_pct']:.0f}% loss.")
        return _bias(
            "Loss aversion (panic selling)",
            "Selling into a sharp decline to stop the pain -- which locks in "
            "the loss instead of riding out the volatility.",
            evidence, 2 if len(panics) == 1 else 3)
    return None


def detect_fomo(buys, prices):
    """
    FOMO / CHASING: buying a stock after it has already run up sharply.
    buys: this year's BUY trades (need ticker, date, price_usd... date used).
    """
    chases = []
    for t in buys:
        if not t.get("date"):
            continue
        prior = prices.change_pct(
            t["ticker"], _months_before(t["date"], LOOKBACK_MONTHS), t["date"])
        if prior is not None and prior >= FOMO_RISE_PCT:
            chases.append((t, prior))
    if chases:
        t, rise = max(chases, key=lambda x: x[1])
        evidence = (
            f"You bought {t['ticker']} in {_month_name(t['date'])} after it had "
            f"already risen {rise:.0f}% over the prior ~2 months.")
        return _bias(
            "FOMO / chasing momentum",
            "Buying after a big run-up for fear of missing out -- which often "
            "means paying near a short-term peak.",
            evidence, 2 if len(chases) >= 2 else 1)
    return None


def detect_overconcentration(holdings, total_value):
    """OVER-CONCENTRATION: a large share of the portfolio in one stock."""
    if total_value <= 0 or not holdings:
        return None
    top = max(holdings, key=lambda h: h["value_usd"])
    weight = top["value_usd"] / total_value * 100.0
    if weight >= CONCENTRATION_PCT:
        evidence = (
            f"{weight:.0f}% of your portfolio is in a single stock "
            f"({top['ticker']}). Concentration magnifies both gains and losses.")
        return _bias(
            "Over-concentration",
            "Holding too much of the portfolio in one stock, so its swings "
            "dominate your results.",
            evidence, 3 if weight >= CONCENTRATION_SEVERE else 2)
    return None


def detect_overtrading(trades):
    """OVERTRADING: an unusually high number of trades in the year."""
    n = len(trades)
    if n > OVERTRADING_TRADES:
        evidence = (
            f"You made {n} trades this year -- well above a buy-and-hold "
            f"cadence. Frequent trading tends to hurt returns through mistimed "
            f"entries and exits.")
        return _bias(
            "Overtrading",
            "Trading far more often than needed, which usually erodes returns "
            "via poor timing (and, in the real world, costs).",
            evidence, 2 if n > OVERTRADING_TRADES * 1.5 else 1)
    return None


def detect_biases(year_data):
    """Run every detector and keep the ones that actually fired."""
    buys = [t for t in year_data["trades"] if t["action"] == "BUY"]
    found = [
        detect_disposition(year_data["realized"], year_data["holdings"]),
        detect_panic_selling(year_data["realized"], year_data["prices"]),
        detect_fomo(buys, year_data["prices"]),
        detect_overconcentration(year_data["holdings"], year_data["total_value"]),
        detect_overtrading(year_data["trades"]),
    ]
    biases = [b for b in found if b]
    biases.sort(key=lambda b: b["severity"], reverse=True)
    return biases


# ===========================================================================
# PART A -- plain-English decision insights (ranked, capped, positive mix)
# ===========================================================================
def _insight(kind, sentiment, headline, detail, weight):
    return {"kind": kind, "sentiment": sentiment, "headline": headline,
            "detail": detail, "weight": weight}


def build_insights(year_data, biases):
    out = []
    best = year_data.get("best")
    worst = year_data.get("worst")
    prices = year_data["prices"]

    # Best realized decision (positive reinforcement).
    if best and best["pnl_usd"] > 0:
        out.append(_insight(
            "best_trade", "good",
            f"Best decision: {best['ticker']} for "
            f"+${_money(best['pnl_usd']):,.2f} ({best['pnl_pct']:+.0f}%)",
            f"You bought at ${_money(best['buy_price']):,.2f} and sold at "
            f"${_money(best['sell_price']):,.2f} -- a well-timed round-trip.",
            abs(best["pnl_usd"])))

    # Worst realized decision (constructive).
    if worst and worst["pnl_usd"] < 0:
        out.append(_insight(
            "worst_trade", "bad",
            f"Toughest decision: {worst['ticker']} for "
            f"${_money(worst['pnl_usd']):,.2f} ({worst['pnl_pct']:+.0f}%)",
            f"Bought at ${_money(worst['buy_price']):,.2f}, sold at "
            f"${_money(worst['sell_price']):,.2f}. Losing trades are part of "
            f"investing -- the lesson is in the sizing and timing.",
            abs(worst["pnl_usd"])))

    # Cut a winner early: a stock you SOLD that has since risen (up to NOW only).
    cut = None
    for r in year_data["realized"]:
        rise = prices.change_pct(r["ticker"], r["sell_date"],
                                 year_data["current_date"])
        if rise is not None and rise >= CUT_WINNER_PCT:
            if cut is None or rise > cut[1]:
                cut = (r, rise)
    if cut:
        r, rise = cut
        out.append(_insight(
            "cut_winner", "warn",
            f"You sold {r['ticker']} before a {rise:.0f}% rise",
            f"You sold {r['ticker']} in {_month_name(r['sell_date'])}; it's up "
            f"{rise:.0f}% since then (as of {year_data['current_date']}). "
            f"Exiting too early can cap your upside.",
            rise * 5))

    # Benchmark comparison for the year.
    for b in year_data.get("beat", []):
        if b["diff"] >= 0:
            out.append(_insight(
                "beat_bench", "good",
                f"You beat {b['name']} by {b['diff']:+.1f} points this year",
                "Your active decisions added value over just holding the "
                "benchmark this year.",
                40 + b["diff"]))
        else:
            out.append(_insight(
                "trail_bench", "info",
                f"You trailed {b['name']} by {b['diff']:.1f} points this year",
                "Beating a passive benchmark is hard -- this is the bar active "
                "trading has to clear.",
                30 + abs(b["diff"])))

    # Positive: held through a volatile year without panicking.
    risk = year_data.get("risk") or {}
    dd = risk.get("max_drawdown")
    panicked = any(bz["name"].startswith("Loss aversion") for bz in biases)
    if dd is not None and dd >= 0.15 and not panicked and year_data["holdings"]:
        out.append(_insight(
            "held_through", "good",
            f"You held through a {dd * 100:.0f}% drawdown without panicking",
            "Staying invested through volatility, rather than selling in fear, "
            "is one of the hardest and most valuable investing habits.",
            35))

    # Clean year (no biases at all) -- explicit positive.
    if not biases and year_data["trades"]:
        out.append(_insight(
            "disciplined", "good",
            "Disciplined trading -- no major behavioural biases this year",
            "No over-concentration, overtrading, panic selling, or chasing "
            "detected. That discipline is exactly what compounds over time.",
            25))

    out.sort(key=lambda i: i["weight"], reverse=True)
    return out[:5]


# ===========================================================================
# public entry point
# ===========================================================================
def analyze(year_data):
    """
    Full analysis for one year. `year_data` is a dict of plain data (built by
    analytics.build_intelligence_input) with keys:
        year, current_date, trades, realized, holdings, total_value,
        prices (PriceView), best, worst, beat, risk
    Returns {"insights": [...], "biases": [...], "clean": bool, "summary": str}.
    """
    biases = detect_biases(year_data)
    insights = build_insights(year_data, biases)
    clean = len(biases) == 0
    if insights:
        summary = insights[0]["headline"]
    elif clean:
        summary = "No major behavioural biases -- disciplined trading."
    else:
        summary = biases[0]["name"] + " detected."
    return {"insights": insights, "biases": biases,
            "clean": clean, "summary": summary}
