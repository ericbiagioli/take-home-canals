-- Canals order management schema (PostgreSQL).
--
-- Applied automatically on first boot of the `db` container by the official
-- postgres image's /docker-entrypoint-initdb.d mechanism (see
-- docker-compose.yml), which runs every *.sql file in this directory, in
-- filename order, the first time the data volume is empty.
--
-- For a non-Docker Postgres instance, apply by hand:
--   createdb canals
--   psql "$DATABASE_URL" -f db/01_schema.sql -f db/02_seed.sql
--
-- Wrapped in a transaction so it's all-or-nothing if applied manually.

BEGIN;

CREATE TABLE IF NOT EXISTS customers (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS warehouses (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    address     TEXT NOT NULL,
    latitude    DOUBLE PRECISION NOT NULL,
    longitude   DOUBLE PRECISION NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
    id           BIGSERIAL PRIMARY KEY,
    sku          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    price_cents  INTEGER NOT NULL CHECK (price_cents >= 0),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Stock on hand for a product at a warehouse. The composite PK means a
-- single `UPDATE ... WHERE quantity >= ?` is enough to atomically reserve
-- stock: Postgres takes a row-level lock on exactly that (warehouse_id,
-- product_id) row for the duration of the UPDATE, so two transactions
-- racing for the same row serialize automatically (the loser's WHERE
-- clause re-evaluates against the post-commit value and affects 0 rows),
-- while reservations against *different* rows proceed fully in parallel --
-- finer-grained than the single-writer file lock SQLite used.
CREATE TABLE IF NOT EXISTS inventory (
    warehouse_id  BIGINT NOT NULL REFERENCES warehouses(id),
    product_id    BIGINT NOT NULL REFERENCES products(id),
    quantity      INTEGER NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (warehouse_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_product ON inventory(product_id);

CREATE TABLE IF NOT EXISTS orders (
    id                      BIGSERIAL PRIMARY KEY,
    customer_id             BIGINT NOT NULL REFERENCES customers(id),
    warehouse_id            BIGINT NOT NULL REFERENCES warehouses(id),
    status                  TEXT NOT NULL CHECK (
                                status IN ('pending_payment', 'paid', 'payment_failed', 'cancelled')
                             ),
    shipping_address        TEXT NOT NULL,         -- formatted, human-readable copy
    shipping_address_json   JSONB NOT NULL,         -- structured copy, as submitted
    shipping_latitude       DOUBLE PRECISION NOT NULL,
    shipping_longitude      DOUBLE PRECISION NOT NULL,
    subtotal_cents          INTEGER NOT NULL CHECK (subtotal_cents >= 0),
    total_cents             INTEGER NOT NULL CHECK (total_cents >= 0),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_warehouse ON orders(warehouse_id);

CREATE TABLE IF NOT EXISTS order_items (
    id                  BIGSERIAL PRIMARY KEY,
    order_id            BIGINT NOT NULL REFERENCES orders(id),
    product_id          BIGINT NOT NULL REFERENCES products(id),
    product_name        TEXT NOT NULL,        -- snapshot at time of order
    quantity             INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents     INTEGER NOT NULL CHECK (unit_price_cents >= 0)
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);

CREATE TABLE IF NOT EXISTS payments (
    id                          BIGSERIAL PRIMARY KEY,
    order_id                    BIGINT NOT NULL REFERENCES orders(id),
    amount_cents                INTEGER NOT NULL,
    status                      TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
    provider_transaction_id     TEXT,
    card_last4                  TEXT,
    failure_code                TEXT,
    failure_message             TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);

-- Supports the optional `Idempotency-Key` header on POST /orders so retried
-- requests (a UI double-submit, a client retry after a timeout) are not
-- double-charged / don't double-reserve stock.
--
-- response_status/response_body are nullable: a row is inserted as a claim
-- (NULL response) *before* the request is processed, so two requests racing
-- with the same key genuinely serialize on the INSERT instead of both
-- slipping past a check-then-act read. They're filled in once the request
-- finishes -- see claim_idempotency_key()/save_idempotent_response() in
-- app/repositories.py.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key                  TEXT PRIMARY KEY,
    request_fingerprint  TEXT NOT NULL,
    response_status      INTEGER,
    response_body        JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
