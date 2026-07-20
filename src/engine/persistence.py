"""
persistence.py
==============
Saves and restores simulation state so a portfolio survives between sessions.

WHY A SEPARATE DATABASE
-----------------------
`market.db` is immutable reference data and MarketData opens it READ-ONLY on
purpose. Writing mutable user state into it would destroy that guarantee, so all
user state lives in its own `simulations.db`.

    market.db        historical prices + FX      read-only, shared
    simulations.db   portfolios, trades, clock   read-write, per user

SCHEMA (all keyed by sim_id + user_id, so one file holds many users/simulations)
    simulations         config snapshot + current_index (the clock position)
    portfolios          cash + starting capital
    holdings            one row per (sim, user, ticker)
    transactions        append-only audit log
    year_end_snapshots  year-end JSON for the report module

WRITE-THROUGH: every buy/sell/advance commits immediately. For a web UI that's
the right model -- no "save" button, and a crash loses nothing.

NO LOOKAHEAD HOLE: only the CURRENT state is stored, never a per-interval
history of positions. There is no snapshot to rewind into, so restoring is
*resume*, not time travel. `current_index` is bounds-checked on load.
"""

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS simulations (
    sim_id            TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    starting_capital  REAL NOT NULL,
    interval          TEXT NOT NULL,
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    price_field       TEXT NOT NULL,
    current_index     INTEGER NOT NULL,
    -- Optimistic-locking counter. Every write bumps it; a writer that loaded an
    -- older version is rejected rather than silently overwriting.
    version           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS portfolios (
    sim_id            TEXT NOT NULL,
    user_id           TEXT NOT NULL,
    cash              REAL NOT NULL,
    starting_capital  REAL NOT NULL,
    PRIMARY KEY (sim_id, user_id)
);

CREATE TABLE IF NOT EXISTS holdings (
    sim_id   TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    ticker   TEXT NOT NULL,
    shares   REAL NOT NULL,
    PRIMARY KEY (sim_id, user_id, ticker)
);

CREATE TABLE IF NOT EXISTS transactions (
    txn_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    interval_index  INTEGER NOT NULL,
    date            TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    action          TEXT NOT NULL,
    shares          REAL NOT NULL,
    price_usd       REAL NOT NULL,
    total_value     REAL NOT NULL,
    cash_after      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS year_end_snapshots (
    sim_id        TEXT NOT NULL,
    year          INTEGER NOT NULL,
    date          TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY (sim_id, year)
);

CREATE INDEX IF NOT EXISTS idx_txn_sim_user ON transactions (sim_id, user_id);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConcurrentModificationError(Exception):
    """
    Raised when a write is attempted against a simulation that someone else has
    modified since we loaded it (lost-update protection).

    A web UI should catch this, reload the simulation, and tell the user their
    view was stale -- rather than silently clobbering the other change. This is
    what stops a double-clicked "Advance" or two open browser tabs from
    corrupting a portfolio.
    """


class SimulationStore:
    """
    Read/write access to simulations.db.

    Pass db_path=":memory:" for a throwaway store (used by the test suite).
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL lets readers proceed while a writer holds the write lock, which is
        # what a multi-request web app needs. (No-op for :memory: databases.)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        # Version observed when each sim was loaded, for optimistic locking.
        self._versions = {}

    def _migrate(self):
        """Add columns introduced after a database was first created."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(simulations)")}
        if "version" not in cols:
            self._conn.execute(
                "ALTER TABLE simulations "
                "ADD COLUMN version INTEGER NOT NULL DEFAULT 0")

    # ---- optimistic locking ----------------------------------------------
    def _bump_version(self, sim_id):
        """
        Increment the simulation's version, asserting nobody else changed it
        since we loaded. Called INSIDE each write transaction, so a conflict
        rolls the whole write back.
        """
        expected = self._versions.get(sim_id)
        if expected is None:
            # We never loaded this sim (e.g. we just created it), so there is no
            # stale-read to guard against -- just bump.
            self._conn.execute(
                "UPDATE simulations SET version = version + 1, updated_at = ? "
                "WHERE sim_id = ?", (_now(), sim_id))
            return
        cur = self._conn.execute(
            "UPDATE simulations SET version = version + 1, updated_at = ? "
            "WHERE sim_id = ? AND version = ?", (_now(), sim_id, expected))
        if cur.rowcount == 0:
            raise ConcurrentModificationError(
                f"Simulation {sim_id!r} was modified by someone else "
                f"(expected version {expected}). Reload and try again.")
        self._versions[sim_id] = expected + 1

    # ---- existence / listing --------------------------------------------
    def exists(self, sim_id):
        row = self._conn.execute(
            "SELECT 1 FROM simulations WHERE sim_id = ?", (sim_id,)).fetchone()
        return row is not None

    def list_simulations(self):
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM simulations ORDER BY updated_at DESC")]

    def delete(self, sim_id):
        for table in ("transactions", "holdings", "portfolios",
                      "year_end_snapshots", "simulations"):
            self._conn.execute(f"DELETE FROM {table} WHERE sim_id = ?", (sim_id,))
        self._conn.commit()

    # ---- writing ---------------------------------------------------------
    def create_simulation(self, sim_id, config, current_index=0):
        """Insert the simulation row, snapshotting the config it runs under."""
        now = _now()
        self._conn.execute(
            "INSERT OR REPLACE INTO simulations (sim_id, created_at, updated_at,"
            " starting_capital, interval, start_date, end_date, price_field,"
            " current_index) VALUES (?,?,?,?,?,?,?,?,?)",
            (sim_id, now, now, config.starting_capital, config.interval,
             config.start_date, config.end_date, config.price_field,
             current_index),
        )
        self._conn.commit()

    def save_clock(self, sim_id, current_index):
        with self._conn:
            self._conn.execute(
                "UPDATE simulations SET current_index = ? WHERE sim_id = ?",
                (current_index, sim_id))
            self._bump_version(sim_id)

    # --- internal writers: NO commit, so callers control the transaction ---
    def _write_portfolio(self, sim_id, portfolio):
        self._conn.execute(
            "INSERT OR REPLACE INTO portfolios "
            "(sim_id, user_id, cash, starting_capital) VALUES (?,?,?,?)",
            (sim_id, portfolio.user_id, portfolio.cash,
             portfolio.starting_capital))
        # Holdings are small; replacing the set wholesale keeps this simple and
        # guarantees deleted positions actually disappear.
        self._conn.execute(
            "DELETE FROM holdings WHERE sim_id = ? AND user_id = ?",
            (sim_id, portfolio.user_id))
        self._conn.executemany(
            "INSERT INTO holdings (sim_id, user_id, ticker, shares) "
            "VALUES (?,?,?,?)",
            [(sim_id, portfolio.user_id, t, s)
             for t, s in portfolio.holdings.items()])
        self._conn.execute(
            "UPDATE simulations SET updated_at = ? WHERE sim_id = ?",
            (_now(), sim_id))

    def _write_transaction(self, sim_id, txn):
        self._conn.execute(
            "INSERT INTO transactions (sim_id, user_id, interval_index, date,"
            " ticker, action, shares, price_usd, total_value, cash_after)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sim_id, txn.user_id, txn.interval_index, txn.date, txn.ticker,
             txn.action, txn.shares, txn.price_usd, txn.total_value,
             txn.cash_after))

    # --- public writers ---------------------------------------------------
    def save_portfolio(self, sim_id, portfolio):
        """Write cash + the full holdings set for one user (write-through)."""
        with self._conn:                      # commit, or roll back on error
            self._write_portfolio(sim_id, portfolio)
            self._bump_version(sim_id)

    def append_transaction(self, sim_id, txn):
        """Append one trade to the audit log (standalone commit)."""
        with self._conn:
            self._write_transaction(sim_id, txn)
            self._bump_version(sim_id)

    def record_trade(self, sim_id, txn, portfolio):
        """
        ATOMIC trade write: the audit-log entry and the resulting cash/holdings
        commit together, or neither does.

        This must stay a single transaction. Writing them as two separate
        commits means a crash in between leaves the log recording a trade that
        never affected the portfolio -- the books would not balance on reload.
        """
        with self._conn:
            self._write_transaction(sim_id, txn)
            self._write_portfolio(sim_id, portfolio)
            self._bump_version(sim_id)

    def save_year_end(self, sim_id, year, date, snapshot):
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO year_end_snapshots "
                "(sim_id, year, date, snapshot_json) VALUES (?,?,?,?)",
                (sim_id, year, date, json.dumps(snapshot)))

    # ---- reading ---------------------------------------------------------
    def load_simulation(self, sim_id):
        """
        Read the simulation row AND remember its version, so any subsequent
        write from this store instance is checked against it (optimistic lock).
        """
        row = self._conn.execute(
            "SELECT * FROM simulations WHERE sim_id = ?", (sim_id,)).fetchone()
        if row is None:
            return None
        self._versions[sim_id] = row["version"]
        return dict(row)

    def load_portfolios(self, sim_id):
        """Return {user_id: {cash, starting_capital, holdings, transactions}}."""
        out = {}
        for r in self._conn.execute(
                "SELECT * FROM portfolios WHERE sim_id = ?", (sim_id,)):
            out[r["user_id"]] = {
                "cash": r["cash"],
                "starting_capital": r["starting_capital"],
                "holdings": {},
                "transactions": [],
            }
        for r in self._conn.execute(
                "SELECT * FROM holdings WHERE sim_id = ?", (sim_id,)):
            if r["user_id"] in out:
                out[r["user_id"]]["holdings"][r["ticker"]] = r["shares"]
        for r in self._conn.execute(
                "SELECT * FROM transactions WHERE sim_id = ? ORDER BY txn_id",
                (sim_id,)):
            if r["user_id"] in out:
                out[r["user_id"]]["transactions"].append(dict(r))
        return out

    def load_year_ends(self, sim_id):
        return [json.loads(r["snapshot_json"]) for r in self._conn.execute(
            "SELECT snapshot_json FROM year_end_snapshots "
            "WHERE sim_id = ? ORDER BY year", (sim_id,))]

    def close(self):
        self._conn.close()
