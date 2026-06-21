import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool
from flask import current_app, g

# Registers Json/dict adaptation for JSONB columns -- lets repositories.py
# pass plain Python dicts straight into INSERT params instead of manually
# json.dumps()-ing, and Postgres hands back already-parsed dicts on read.
from psycopg2.extras import Json  # noqa: F401  (re-exported for repositories.py)


def init_db_pool(app, retries: int = 10, delay_seconds: float = 1.5) -> None:
    """Create the connection pool used by every request, retrying briefly
    on boot in case Postgres isn't accepting connections the instant this
    process starts (e.g. a cold start without docker-compose's
    `depends_on: condition: service_healthy` to wait for it first).

    Schema/data setup is NOT done here, unlike the SQLite version of this
    app. With Postgres, having every app process try to create tables on
    every boot is the wrong default -- it doesn't scale past one instance
    without a real migration tool, and it conflates "is the schema
    correct" with "did this particular process happen to start first".
    Schema and seed data are plain SQL in db/, applied once, outside the
    app's request-serving lifecycle (see README).
    """
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            app.db_pool = psycopg2.pool.ThreadedConnectionPool(
                app.config["DB_POOL_MIN"],
                app.config["DB_POOL_MAX"],
                dsn=app.config["DATABASE_URL"],
                options=f"-c statement_timeout={app.config['DB_STATEMENT_TIMEOUT_MS']}",
            )
            app.logger.info(
                "Connected to Postgres (pool size %s-%s)",
                app.config["DB_POOL_MIN"],
                app.config["DB_POOL_MAX"],
            )
            return
        except psycopg2.OperationalError as e:
            last_error = e
            app.logger.warning("Postgres not ready yet (attempt %s/%s): %s", attempt, retries, e)
            time.sleep(delay_seconds)
    raise RuntimeError(f"Could not connect to Postgres after {retries} attempts") from last_error


def get_db():
    """Return a request-scoped connection borrowed from the pool, opening
    one if needed. Returned to the pool in close_db() at teardown.

    Connections are normalized to autocommit=True as soon as they're
    borrowed -- both because a connection coming back from a previous
    request via the pool could in principle still have autocommit toggled
    off if something went wrong, and because plain reads (GET /orders/:id)
    shouldn't leave an idle transaction open. transaction() below switches
    autocommit off only for the duration of an actual write.
    """
    if "db" not in g:
        conn = current_app.db_pool.getconn()
        conn.rollback()  # defensively clear any leftover transaction state
        conn.autocommit = True
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        if not conn.closed:
            conn.rollback()  # never return a connection mid-transaction to the pool
        current_app.db_pool.putconn(conn)


@contextmanager
def transaction(conn):
    """Wrap a block of writes in a single atomic Postgres transaction.

    Two requests racing to reserve the last unit of stock both issue
    `UPDATE inventory SET quantity = quantity - %s WHERE ... AND quantity
    >= %s`. Postgres takes a row lock on the matching inventory row for
    the duration of the UPDATE; the second transaction blocks until the
    first commits, then re-evaluates its own WHERE clause against the
    now-current value and (correctly) affects 0 rows if stock is gone.
    Commits on success, rolls back on any exception (and re-raises it).
    """
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = True
