"""The analytics shadow: export the ledger to a disposable DuckDB file.

``$VAULT/reports/analytics.duckdb`` IS A CACHE, NEVER AN ARCHIVE. The
beancount ledger stays the single source of truth; this file is a derived,
regenerable materialization — rebuilt WHOLE on every run (tmp + atomic
rename), safe to delete at any time, never migrated across DuckDB
versions, never backed up, never committed (the vault .gitignore excludes
it). Anything that must survive belongs in the ledger; the Parquet twins
under ``reports/exports/`` are the frozen-format interchange copies.

The build refuses to emit unless three in-DB cross-checks agree:

1. balance invariant — per-currency posting weights sum to zero (the
   double-entry guarantee, recomputed inside the DB);
2. liquid net worth — the DB figure matches the independent bean-query
   aggregate (the same root query crosscheck.py trusts) to the cent;
3. row counts — DB postings/transactions match the loaded ledger exactly.

Consumers (notebooks, SQL, MCP): always open ``read_only=True`` — DuckDB
is single-writer, and a stray read-write handle blocks the next rebuild.
Regenerate with ``tools/run reports.py`` (or ``sara-analytics``).

Conventions: money is DECIMAL(38,9); ``*_home`` columns are converted to
the vault's home currency — the ledger's first ``operating_currency``,
USD when unset — at the latest price on or before the transaction date
(see sara/ledger/load.py, which owns the flattening).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import astuple, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import duckdb

from sara.ledger import queries
from sara.ledger.load import LoadedLedger, load_ledger
from sara.typed import as_dict, as_list, as_str
from sara.vault import VAULT, rules

REPORTS = VAULT / "reports"
DB_NAME = "analytics.duckdb"
EXPORTS_NAME = "exports"
TOLERANCE = Decimal("0.01")  # same cent gate as tools/crosscheck.py
TABLES: tuple[str, ...] = ("postings", "transactions", "accounts", "commodities",
                           "prices", "balances_daily", "balance_checks", "build_info")
VIEWS: tuple[str, ...] = ("net_worth_daily", "monthly_flows", "register")

_SCHEMA = """
CREATE TABLE postings (
    posting_id      BIGINT PRIMARY KEY,
    txn_id          VARCHAR NOT NULL,
    posting_index   SMALLINT NOT NULL,
    date            DATE NOT NULL,
    flag            VARCHAR,
    payee           VARCHAR,
    narration       VARCHAR,
    tags            VARCHAR[],
    links           VARCHAR[],
    account         VARCHAR NOT NULL,
    other_account   VARCHAR,
    amount          DECIMAL(38,9),
    currency        VARCHAR,
    cost_number     DECIMAL(38,9),
    cost_currency   VARCHAR,
    cost_date       DATE,
    cost_label      VARCHAR,
    price_number    DECIMAL(38,9),
    price_currency  VARCHAR,
    weight_number   DECIMAL(38,9),
    weight_currency VARCHAR,
    amount_home     DECIMAL(18,4),
    external_id     VARCHAR,
    source_file     VARCHAR,
    source_line     INTEGER,
    meta            JSON
);
CREATE TABLE transactions (
    txn_id      VARCHAR PRIMARY KEY,
    date        DATE NOT NULL,
    flag        VARCHAR,
    payee       VARCHAR,
    narration   VARCHAR,
    tags        VARCHAR[],
    links       VARCHAR[],
    n_postings  SMALLINT NOT NULL,
    accounts    VARCHAR[],
    source_file VARCHAR,
    source_line INTEGER,
    meta        JSON
);
CREATE TABLE accounts (
    account      VARCHAR PRIMARY KEY,
    parent       VARCHAR,
    leaf         VARCHAR NOT NULL,
    root         VARCHAR NOT NULL,
    depth        SMALLINT NOT NULL,
    is_open      BOOLEAN NOT NULL,
    open_date    DATE,
    close_date   DATE,
    currencies   VARCHAR[],
    owner        VARCHAR,
    institution  VARCHAR,
    kind         VARCHAR,
    external_ids JSON,
    meta         JSON
);
CREATE TABLE commodities (
    currency    VARCHAR PRIMARY KEY,
    name        VARCHAR,
    "precision" INTEGER,
    kind        VARCHAR,
    meta        JSON
);
CREATE TABLE prices (
    commodity      VARCHAR NOT NULL,
    quote_currency VARCHAR NOT NULL,
    date           DATE NOT NULL,
    price          DECIMAL(38,9) NOT NULL,
    source         VARCHAR NOT NULL,
    PRIMARY KEY (commodity, quote_currency, date)
);
CREATE TABLE balances_daily (
    date            DATE NOT NULL,
    account         VARCHAR NOT NULL,
    currency        VARCHAR NOT NULL,
    units           DECIMAL(38,9) NOT NULL,
    price_home      DECIMAL(38,9),
    value_home      DECIMAL(18,4),
    cost_basis_home DECIMAL(18,4),
    PRIMARY KEY (date, account, currency)
);
CREATE TABLE balance_checks (
    date     DATE NOT NULL,
    account  VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    expected DECIMAL(38,9) NOT NULL,
    actual   DECIMAL(38,9) NOT NULL,
    diff     DECIMAL(38,9) NOT NULL
);
CREATE TABLE build_info (
    built_at          TIMESTAMPTZ NOT NULL,
    ledger_git_sha    VARCHAR,
    beancount_version VARCHAR NOT NULL,
    duckdb_version    VARCHAR NOT NULL,
    home_currency     VARCHAR NOT NULL,
    txn_count         BIGINT NOT NULL,
    posting_count     BIGINT NOT NULL,
    min_date          DATE,
    max_date          DATE
);
"""

# Kimball periodic snapshot: one row per (day, account, currency) with any
# history, from the first posting date through today. Positions are running
# sums of posting units; each day is valued at the latest home-currency
# price on or before that day (implicit acquisition prices included, so
# holdings value from day one); cost basis is the running sum of
# units x cost, exact under beancount booking. Inverse quotes (home listed
# as the base) are derived at double precision — best effort, direct quotes
# always win.
_BALANCES_DAILY = """
INSERT INTO balances_daily
WITH bounds AS (
    SELECT min(date) AS d0, greatest(max(date), current_date) AS d1 FROM postings
),
cal AS (
    SELECT unnest(generate_series(d0, d1, INTERVAL 1 DAY))::DATE AS date
    FROM bounds WHERE d0 IS NOT NULL
),
flows AS (
    SELECT account, currency, date,
           sum(amount) AS units_flow,
           sum(CASE WHEN cost_currency = $home THEN amount * cost_number END) AS basis_flow
    FROM postings
    WHERE currency IS NOT NULL AND amount IS NOT NULL
    GROUP BY ALL
),
cum AS (
    SELECT account, currency, date,
           sum(units_flow) OVER w AS units,
           sum(basis_flow) OVER w AS basis
    FROM flows
    WINDOW w AS (PARTITION BY account, currency ORDER BY date)
),
grid AS (
    SELECT cal.date, pair.account, pair.currency
    FROM cal CROSS JOIN (SELECT DISTINCT account, currency FROM flows) pair
),
pos AS (
    SELECT g.date, g.account, g.currency, c.units, c.basis
    FROM grid g
    ASOF JOIN cum c
      ON c.account = g.account AND c.currency = g.currency AND c.date <= g.date
),
rates AS (
    SELECT currency, date, price FROM (
        SELECT currency, date, price,
               row_number() OVER (PARTITION BY currency, date ORDER BY src) AS rn
        FROM (
            SELECT commodity AS currency, date, price, 0 AS src
            FROM prices WHERE quote_currency = $home
            UNION ALL
            SELECT quote_currency, date, CAST(1.0 / price AS DECIMAL(38,9)), 1
            FROM prices WHERE commodity = $home AND price <> 0
        )
    ) WHERE rn = 1
)
SELECT p.date, p.account, p.currency,
       CAST(p.units AS DECIMAL(38,9)) AS units,
       CASE WHEN p.currency = $home THEN CAST(1 AS DECIMAL(38,9)) ELSE r.price END
           AS price_home,
       CAST(CASE WHEN p.currency = $home THEN p.units ELSE p.units * r.price END
            AS DECIMAL(18,4)) AS value_home,
       CAST(p.basis AS DECIMAL(18,4)) AS cost_basis_home
FROM pos p
ASOF LEFT JOIN rates r ON r.currency = p.currency AND r.date <= p.date
"""


@dataclass(frozen=True, slots=True)
class BuildResult:
    db_path: Path
    exports_dir: Path
    txn_count: int
    posting_count: int
    home_currency: str
    liquid_home: Decimal


# --------------------------------------------------------------- utilities
def _sq(text: str) -> str:
    """Escape a value for embedding in a single-quoted SQL literal."""
    return text.replace("'", "''")


def _illiquid_regex() -> str | None:
    """Regex matching illiquid commodity symbols (rules.toml), or None."""
    household = as_dict(rules().get("household"))
    prefixes = [p.replace("'", "")
                for x in as_list(household.get("illiquid_commodity_prefixes"))
                if (p := as_str(x))]
    if not prefixes:
        return None
    return "^(" + "|".join(re.escape(p) for p in prefixes) + ")"


def _ledger_git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(VAULT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except OSError:
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else None


def _insert_rows(con: duckdb.DuckDBPyConnection, table: str,
                 rows: Sequence[Any]) -> None:
    if not rows:
        return
    params = [astuple(row) for row in rows]
    placeholders = ", ".join("?" * len(params[0]))
    con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", params)


def _one(con: duckdb.DuckDBPyConnection, sql: str,
         params: dict[str, object] | None = None) -> object:
    row = cast("tuple[object, ...] | None", con.execute(sql, params).fetchone())
    return row[0] if row else None


# ------------------------------------------------------------------- build
def _create_views(con: duckdb.DuckDBPyConnection, home: str,
                  illiquid: str | None) -> None:
    liquid_filter = "root IN ('Assets', 'Liabilities')"
    if illiquid:
        liquid_filter += f" AND NOT regexp_matches(currency, '{_sq(illiquid)}')"
    con.execute(f"""
        CREATE VIEW net_worth_daily AS
        WITH per_day AS (
            SELECT date, split_part(account, ':', 1) AS root, currency, value_home
            FROM balances_daily
        )
        SELECT date,
               coalesce(sum(value_home) FILTER (WHERE root = 'Assets'), 0)
                   AS assets_home,
               coalesce(sum(value_home) FILTER (WHERE root = 'Liabilities'), 0)
                   AS liabilities_home,
               coalesce(sum(value_home) FILTER (WHERE root IN ('Assets', 'Liabilities')), 0)
                   AS net_worth_home,
               coalesce(sum(value_home) FILTER (WHERE {liquid_filter}), 0)
                   AS liquid_home
        FROM per_day
        GROUP BY date
        ORDER BY date
    """)
    con.execute("""
        CREATE VIEW monthly_flows AS
        SELECT split_part(account, ':', 1) AS root,
               account,
               date_trunc('month', date)::DATE AS month,
               sum(amount_home) AS total_home
        FROM postings
        GROUP BY ALL
        ORDER BY month, account
    """)
    con.execute("""
        CREATE VIEW register AS
        SELECT posting_id, txn_id, date, account, currency, payee, narration,
               amount,
               sum(amount) OVER (PARTITION BY account, currency
                                 ORDER BY date, posting_id) AS balance,
               amount_home, other_account
        FROM postings
        ORDER BY account, currency, date, posting_id
    """)


def _write_build_info(con: duckdb.DuckDBPyConnection, loaded: LoadedLedger) -> None:
    con.execute(
        "INSERT INTO build_info VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.now(UTC), _ledger_git_sha(), loaded.beancount_version,
         duckdb.__version__, loaded.home_currency, len(loaded.transactions),
         len(loaded.postings), loaded.min_date, loaded.max_date))


def _export_parquet(con: duckdb.DuckDBPyConnection, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        target = out_dir / f"{table}.parquet"
        con.execute(f"COPY {table} TO '{_sq(str(target))}' (FORMAT parquet)")


# ------------------------------------------------------------ cross-checks
def _check_balance_invariant(con: duckdb.DuckDBPyConnection) -> str | None:
    """Double-entry invariant: per-currency posting weights sum to zero."""
    offenders = cast("list[tuple[object, ...]]", con.execute(
        "SELECT weight_currency, sum(weight_number) FROM postings "
        "WHERE weight_currency IS NOT NULL GROUP BY 1 "
        "HAVING abs(sum(weight_number)) >= 0.005").fetchall())
    if not offenders:
        return None
    detail = ", ".join(f"{cur}: {total}" for cur, total in offenders)
    return f"balance invariant: per-currency weight sums are not zero ({detail})"


def _db_liquid(con: duckdb.DuckDBPyConnection, home: str,
               illiquid: str | None) -> Decimal:
    """Liquid net worth from the DB alone: per-account positions valued at
    the latest home price (mirroring bean-query convert), unpriced holdings
    excluded, illiquid commodities excluded."""
    excl = "AND NOT regexp_matches(currency, $illiquid)" if illiquid else ""
    sql = f"""
    WITH pos AS (
        SELECT account, currency, sum(amount) AS units
        FROM postings
        WHERE split_part(account, ':', 1) IN ('Assets', 'Liabilities')
          AND currency IS NOT NULL {excl}
        GROUP BY ALL
    ),
    latest AS (
        SELECT currency, price FROM (
            SELECT currency, price,
                   row_number() OVER (PARTITION BY currency ORDER BY date DESC, src) AS rn
            FROM (
                SELECT commodity AS currency, date, price, 0 AS src
                FROM prices WHERE quote_currency = $home
                UNION ALL
                SELECT quote_currency, date, CAST(1.0 / price AS DECIMAL(38,9)), 1
                FROM prices WHERE commodity = $home AND price <> 0
            )
        ) WHERE rn = 1
    )
    SELECT coalesce(sum(CASE WHEN pos.currency = $home THEN pos.units
                             ELSE pos.units * latest.price END), 0)
    FROM pos
    LEFT JOIN latest ON latest.currency = pos.currency
    WHERE pos.currency = $home OR latest.price IS NOT NULL
    """
    params: dict[str, object] = {"home": home}
    if illiquid:
        params["illiquid"] = illiquid
    value = _one(con, sql, params)
    return value if isinstance(value, Decimal) else Decimal(0)


def _bean_liquid(home: str, illiquid: str | None) -> Decimal:
    """The independent path: the same root-level bean-query aggregate that
    tools/crosscheck.py holds the reports to (shared plumbing is only the
    subprocess runner and the cell parser — no aggregation code)."""
    where = "account ~ '^(Assets|Liabilities)'"
    if illiquid:
        where += f" AND NOT currency ~ '{illiquid}'"
    rows = queries.query(f"SELECT root(account, 1) AS r, "
                         f"sum(convert(position, '{home}')) AS v "
                         f"WHERE {where} GROUP BY r")
    total = Decimal(0)
    for row in rows:
        total += queries.amount(row.get("v"), home)
    return total


def _run_cross_checks(con: duckdb.DuckDBPyConnection, loaded: LoadedLedger,
                      illiquid: str | None) -> tuple[list[str], Decimal]:
    failures: list[str] = []
    if (bad := _check_balance_invariant(con)) is not None:
        failures.append(bad)

    db_liquid = _db_liquid(con, loaded.home_currency, illiquid)
    bean_liquid = _bean_liquid(loaded.home_currency, illiquid)
    if abs(db_liquid - bean_liquid) > TOLERANCE:
        failures.append(
            f"liquid net worth: DB says {db_liquid:.2f} {loaded.home_currency}, "
            f"bean-query says {bean_liquid:.2f} "
            f"(gap {abs(db_liquid - bean_liquid):.2f}, tolerance {TOLERANCE})")

    db_postings = _one(con, "SELECT count(*) FROM postings")
    db_txns = _one(con, "SELECT count(*) FROM transactions")
    if (db_postings, db_txns) != (len(loaded.postings), len(loaded.transactions)):
        failures.append(
            f"row counts: DB {db_txns} txns / {db_postings} postings, "
            f"ledger {len(loaded.transactions)} / {len(loaded.postings)}")
    return failures, db_liquid


# -------------------------------------------------------------- lifecycle
def _sweep_stale_tmp() -> None:
    """Remove leftovers from crashed builds (best effort)."""
    for stale in REPORTS.glob(".analytics.tmp-*"):
        stale.unlink(missing_ok=True)
    for stale in REPORTS.glob(".exports.tmp-*"):
        shutil.rmtree(stale, ignore_errors=True)


def _discard(tmp_db: Path, tmp_exports: Path) -> None:
    tmp_db.unlink(missing_ok=True)
    Path(f"{tmp_db}.wal").unlink(missing_ok=True)
    shutil.rmtree(tmp_exports, ignore_errors=True)


def build() -> BuildResult:
    """Rebuild reports/analytics.duckdb + reports/exports/*.parquet, whole.

    Refuses (exit 2, tmp files deleted) unless all cross-checks agree; the
    previous good build is never replaced by a bad one.
    """
    loaded = load_ledger()
    illiquid = _illiquid_regex()
    REPORTS.mkdir(parents=True, exist_ok=True)
    _sweep_stale_tmp()
    tmp_db = REPORTS / f".analytics.tmp-{os.getpid()}.duckdb"
    tmp_exports = REPORTS / f".exports.tmp-{os.getpid()}"
    db_path = REPORTS / DB_NAME
    exports_dir = REPORTS / EXPORTS_NAME

    con = duckdb.connect(str(tmp_db))
    try:
        con.execute(_SCHEMA)
        _insert_rows(con, "postings", loaded.postings)
        _insert_rows(con, "transactions", loaded.transactions)
        _insert_rows(con, "accounts", loaded.accounts)
        _insert_rows(con, "commodities", loaded.commodities)
        _insert_rows(con, "prices", loaded.prices)
        _insert_rows(con, "balance_checks", loaded.balance_checks)
        con.execute(_BALANCES_DAILY, {"home": loaded.home_currency})
        _create_views(con, loaded.home_currency, illiquid)
        _write_build_info(con, loaded)

        failures, liquid = _run_cross_checks(con, loaded, illiquid)
        if failures:
            print("ANALYTICS CROSS-CHECK MISMATCH — refusing to emit.\n  "
                  + "\n  ".join(failures)
                  + "\nA derived figure could not be independently reproduced — "
                    "fix the ledger/tools, then rebuild. (Most common cause: "
                    "holdings with no price directives — append the lines "
                    "importers/holdings_ofx.py prints, or tag commodities for "
                    "scripts/update_prices.sh.)", file=sys.stderr)
            raise SystemExit(2)
        _export_parquet(con, tmp_exports)
    except BaseException:
        con.close()
        _discard(tmp_db, tmp_exports)
        raise
    con.close()

    os.replace(tmp_db, db_path)
    if exports_dir.exists():
        shutil.rmtree(exports_dir)
    os.replace(tmp_exports, exports_dir)

    print("analytics cross-checks: 3/3 agree")
    print(f"analytics: {len(loaded.transactions):,} txns / {len(loaded.postings):,} postings "
          f"-> {db_path.relative_to(VAULT)} + {len(TABLES)} parquet exports "
          f"(liquid {liquid:,.2f} {loaded.home_currency})")
    return BuildResult(
        db_path=db_path, exports_dir=exports_dir,
        txn_count=len(loaded.transactions), posting_count=len(loaded.postings),
        home_currency=loaded.home_currency, liquid_home=liquid)


def main() -> None:
    build()


if __name__ == "__main__":
    main()
