import os


class Config:
    """Centralized configuration, overridable via environment variables so
    the same image/codebase can run unmodified across dev/staging/prod."""

    DATABASE_URL = os.environ.get(
        "DATABASE_URL", "postgresql://canals:canals_dev_password@localhost:5432/canals"
    )

    # Connection pool sizing. Each app process (each gunicorn worker) gets
    # its own pool of this size -- total connections to Postgres across a
    # deployment is roughly (worker count) x (DB_POOL_MAX), so size Postgres's
    # max_connections with that in mind as you scale workers/instances.
    DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", "2"))
    DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "10"))

    # Server-side cap on how long a single statement (including time spent
    # waiting for a row lock, e.g. in reserve_inventory) may run before
    # Postgres aborts it. Without this, a stuck lock or a runaway query
    # blocks its connection -- and anything waiting on the same row --
    # indefinitely.
    DB_STATEMENT_TIMEOUT_MS = int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "30000"))

    JSON_SORT_KEYS = False
