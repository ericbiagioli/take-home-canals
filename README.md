# Canals — Order Management API

A production-shaped order management service: a customer places an order,
the service finds the closest warehouse that can fill the entire order from
its own stock, charges the customer, and persists the result in Postgres.

A dashboard at the root URL (`/`) shows live system stats (warehouse/customer/
product/order counts), lets you send `POST /orders` requests by hand (with a
few pre-loaded example payloads) and inspect the response, and can trigger
the `scripts/smoke_test.py` suite with a button and show its output.

The sections below cover setup, the API, design decisions, and the project
layout.

## Quick start

### Option 1: Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

That's the whole setup. It starts Postgres, applies `db/01_schema.sql` and
loads `db/02_seed.sql` (500 customers, 16 warehouses, ~420 products, ~4,500
inventory rows) automatically on first boot, waits for Postgres to report
healthy, then starts the API on `http://localhost:8000`.

Health check: `GET /health` (does a real `SELECT 1` against Postgres, not
just "the process is up").

### Option 2: Make

```bash
make up
```

Equivalent to option 1 — see the [Makefile](Makefile) (`make help` lists all
targets).

### Without Docker

```bash
pip install -r requirements.txt
createdb canals
psql canals -f db/01_schema.sql -f db/02_seed.sql
cp .env.example .env   # adjust DATABASE_URL if your Postgres isn't on localhost:5432
python run.py
```

## The API

### `POST /orders`

```bash
curl -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: optional-client-generated-uuid" \
  -d '{
    "customer_id": 1,
    "shipping_address": {
      "line1": "350 5th Ave",
      "city": "New York",
      "state": "NY",
      "postal_code": "10118",
      "country": "US"
    },
    "items": [
      { "product_id": 1, "quantity": 2 },
      { "product_id": 4, "quantity": 1 }
    ],
    "payment": {
      "card_number": "4242424242424242",
      "expiry_month": 12,
      "expiry_year": 2030,
      "cvv": "123"
    }
  }'
```

Response shape is the same whether the charge succeeds or is declined — the
HTTP status code is what signals the outcome (`201` paid, `402` declined) —
so the client only has to write one parser:

```json
{
  "id": 1,
  "status": "paid",
  "customer_id": 1,
  "shipping_address": "350 5th Ave, New York, NY 10118, US",
  "warehouse": { "id": 1, "name": "Newark Distribution Center", "address": "..." },
  "items": [
    { "product_id": 1, "name": "Rechargeable Phone Stand", "quantity": 2, "unit_price_cents": 2189, "subtotal_cents": 4378 }
  ],
  "subtotal_cents": 4378,
  "total_cents": 4378,
  "payment": { "status": "succeeded", "transaction_id": "mock_txn_...", "card_last4": "4242", "failure_code": null, "failure_message": null },
  "created_at": "2026-06-21T00:32:41.123456+00:00",
  "updated_at": "2026-06-21T00:32:41.123456+00:00"
}
```

| Outcome | Status | Notes |
|---|---|---|
| Charged successfully | `201` | `status: "paid"` |
| Card declined | `402` | `status: "payment_failed"`, order + reserved stock are rolled back |
| Validation error | `400` | `{"error": {"code": "VALIDATION_ERROR", "details": [...]}}` — collects *all* problems, not just the first |
| Customer or product doesn't exist | `404` | |
| No single warehouse has all items in stock | `409` | |
| Stock changed mid-request (lost a race) | `409` | safe to retry |

### `GET /orders/:id`

Returns the same shape as above. (Not in the original spec, but trivial
given everything above already exists, and useful for verifying the result
of a `POST` without going straight to the database.)

### `GET /`

A small dashboard (`app/routes/dashboard.py`, `app/templates/dashboard.html`)
for poking at the running service without `curl`:

- Live counts of warehouses, customers, products, and orders processed
  (`GET /api/dashboard/stats`).
- A text box that sends whatever JSON you put in it straight to
  `POST /orders` and shows the response, with a few one-click example
  payloads (valid order, declined card, missing customer, missing product,
  validation error) to pre-fill it.
- A button that runs `scripts/smoke_test.py` against this same server and
  database (`POST /api/dashboard/smoke-test`) and shows its output. This
  creates real orders and changes real stock levels, the same as running
  the script by hand would.

## Database setup: a SQL dump, not a Python script

`db/01_schema.sql` and `db/02_seed.sql` are plain SQL — no application code
runs them. The official `postgres` Docker image executes every `*.sql` file
in `/docker-entrypoint-initdb.d` (mounted from `db/` in
`docker-compose.yml`) in filename order, the first time its data volume is
empty. For a non-Docker Postgres, apply them by hand with `psql` (see Quick
start above). The app itself never creates or migrates schema on boot —
more on why under "Design decisions" below.

`db/02_seed.sql` is **generated**, not hand-written — see the header
comment in the file itself. Hand-authoring a few thousand realistic rows
isn't practical, so `scripts/generate_seed_dump.py` builds it
combinatorially (name/category templates, realistic-ish price and stock
distributions) with a fixed random seed, so re-running it produces
byte-identical output. That script is a one-time dev tool — it's not part
of the running app and the app never imports or calls it; it only ever
*produces* the SQL file that everything else consumes. Regenerate with:

```bash
python scripts/generate_seed_dump.py > db/02_seed.sql
```

Current scale: 500 customers, 16 warehouses (one per major-city coordinate
the mock geocoder also recognizes — see below), ~420 products across 10
categories, and each warehouse stocking a random 55-85% of the catalog, so
coverage is realistically uneven: some products are everywhere, some are
carried by only one or two warehouses, some warehouses are missing a given
product entirely.

## Testing it yourself

`scripts/smoke_test.py` is a reproducible end-to-end test: happy path,
payment decline + stock rollback, "no warehouse available", validation
errors, 404s, idempotency replay, and a real concurrency test (10
simultaneous requests racing for the last unit of stock).

Because the dataset is now large and randomly generated instead of a
handful of fixed ids, this test doesn't hardcode "product_id=5" anywhere —
it discovers valid fixtures by querying Postgres directly (e.g. "find any
product with at least 5 units somewhere"), and for the warehouse-selection
assertion specifically, it imports the real
`app.services.warehouse_selector.find_best_warehouse` function and calls it
directly against the database as an oracle, then checks that the live HTTP
API agrees with that independently-computed answer. That makes the test
robust to regenerating the seed dump with a different size/shuffle, and is
a more convincing check than asserting against a hand-picked expected
answer.

```bash
docker compose up --build -d
pip install -r requirements.txt   # for psycopg2, to run the test script itself
python scripts/smoke_test.py
```

It needs `DATABASE_URL` pointing at the same Postgres the API is using (the
default matches `docker-compose.yml`) and `SMOKE_TEST_BASE_URL` if the API
isn't at the default `http://localhost:8000`.

A note on the concurrency test specifically: it works because Postgres
takes a row-level lock on the exact `(warehouse_id, product_id)` row for the
duration of `UPDATE inventory SET quantity = quantity - %s WHERE ... AND
quantity >= %s`. The second of two racing transactions blocks until the
first commits, then re-evaluates its own `WHERE` clause against the
now-current value and (correctly) affects 0 rows if stock is gone — no
separate locking step needed. `run.py` also passes `threaded=True` to
Flask's dev server so the 10 requests genuinely overlap rather than
queueing sequentially.

## Deploying this

Three options, in order of effort:

**Quick tunnel (lowest effort, good for a live demo).** Run it locally
(`docker compose up`) and expose the `app` service with a tunnel, e.g.
[ngrok](https://ngrok.com) (`ngrok http 8000`) or `cloudflared tunnel --url
http://localhost:8000`. You get a public HTTPS URL in seconds.

**PaaS with a managed Postgres add-on (Render, Railway, Fly.io).** All
three will build the `Dockerfile` directly from a Git push and offer a
managed Postgres instance alongside it. Rough steps: push this repo, point
the platform at it, provision their Postgres add-on, set `DATABASE_URL` to
the connection string it gives you, apply `db/01_schema.sql` +
`db/02_seed.sql` once via `psql "$DATABASE_URL" -f ...` (or the platform's
shell/exec feature), deploy. Persistent storage is entirely the managed
Postgres's problem, not the app's.

**A small VPS (DigitalOcean, Hetzner, EC2).** `docker compose up -d` this
repo directly, put it behind nginx/Caddy for TLS, done.

## Design decisions worth calling out

**Order must ship from one warehouse.** A warehouse is only eligible if
`inventory.quantity >= requested` holds for *every* line item — partial
availability across multiple warehouses doesn't count, per the spec. Among
eligible warehouses, the closest one wins, by great-circle (haversine)
distance to the geocoded shipping address.

**Reserve → charge → finalize, not one big transaction.** A naive
implementation holds a database transaction open across the call to the
payment gateway. That's a real-world footgun: a slow or hanging payment API
call would hold a row lock and serialize every other order touching that
inventory row behind it. Instead:

1. Short transaction: pick warehouse, atomically decrement stock
   (`UPDATE inventory SET quantity = quantity - %s WHERE ... AND quantity
   >= %s`), write the order as `pending_payment`. Lock held for
   milliseconds.
2. Call the payment gateway with *no* lock held.
3. Short transaction: write the payment result; on success mark the order
   `paid`, on decline mark it `payment_failed` and put the stock back.

The trade-off is a brief window where stock is reserved against an order
that might still fail payment. That's the right trade-off here (better than
serializing all writes behind a network call), and it's bounded — failed
orders release their reservation immediately in step 3. Every step writes a
row, so the full sequence is reconstructable from the database even if the
process crashes mid-flight.

**Row-level locking.** `UPDATE inventory ... WHERE quantity >= %s` takes a
Postgres row lock on exactly the `(warehouse_id, product_id)` row being
touched, for the duration of that statement. Two requests racing for the
*same* row genuinely serialize (the loser's `WHERE` re-evaluates against
the post-commit value and correctly affects 0 rows); requests touching
*different* rows proceed fully in parallel — concurrent orders against
different products/warehouses never block each other.

**Connection pooling.** Each app process opens a
`psycopg2.pool.ThreadedConnectionPool` once at boot (`app/db.py`) and every
request borrows/returns a connection from it, rather than opening a new
Postgres connection per request. Pool size is configurable via
`DB_POOL_MIN`/`DB_POOL_MAX`; total connections to Postgres across a
deployment is roughly `(worker count) × DB_POOL_MAX`, worth keeping in mind
against Postgres's own `max_connections` as you scale workers/instances.

**Schema setup lives entirely outside the app's boot path.** Schema and
seed data are a one-time SQL artifact applied once, outside the
request-serving lifecycle (see "Database setup" above) — no app process
ever creates or migrates schema on boot. One consequence worth flagging
explicitly: the Dockerfile does **not** pass gunicorn's `--preload` flag,
and it must stay that way — `--preload` would create the connection pool
once in the master process and fork its live, open TCP connections into
every worker, which is a fork-safety bug (workers would corrupt each
other's protocol state on the same socket). Each worker has to build its
own pool independently after forking.

**JSONB instead of TEXT for structured columns.** `orders.
shipping_address_json` and `idempotency_keys.response_body` are native
`JSONB` now, not `TEXT` holding `json.dumps()` output. Writes wrap the
Python dict in `psycopg2.extras.Json(...)`; reads come back as an
already-parsed dict — no manual `json.loads()` needed on the read side,
which actually simplified `routes/orders.py` slightly.

**Idempotency key on `POST /orders`.** UI double-submits and client retries
after a timeout are the standard way this kind of endpoint accidentally
double-charges someone. Pass `Idempotency-Key: <uuid>` and a retried
request with the same key + body replays the original response instead of
re-running the order (mirrors how Stripe's API behaves). Reusing a key with
a *different* body is rejected as a client error. The key is claimed with
an atomic `INSERT ... ON CONFLICT DO NOTHING` *before* the order is
processed (`app/repositories.py`), not just checked-then-saved afterwards —
two requests that genuinely arrive at the same instant with the same key
can't both slip past the check and create two orders; the loser gets a
`409` telling it to back off. A claim left unfinished (the process crashed
mid-request) is treated as abandoned after 30 seconds and can be retried.

**Money in cents, everywhere.** Avoids floating-point rounding bugs in
totals; division/formatting to dollars is a presentation-layer concern, not
done here since there's no UI in scope.

**Mocked external services live behind interfaces.**
`app/services/geocoding.py` (`GeocodingProvider`) and
`app/services/payment.py` (`PaymentGateway`) are abstract base classes;
`MockGeocodingProvider`/`MockPaymentGateway` are the only implementations
today. Nothing in `order_service.py` or the routes knows or cares that
they're mocks — swapping in a real geocoding/payment API later is a
one-file change plus wiring in `app/__init__.py`. The payment mock validates
card numbers with a real Luhn checksum and uses Stripe's well-known test
card numbers for declines, so failure paths are exercisable on purpose, not
just by chance. Only the last 4 digits of any card number are ever stored —
the raw PAN is used in-memory for the mock charge call and discarded, which
is the shape you want once this is a real PCI-scoped integration. Warehouse
coordinates in the seed data are drawn from the same city table the mock
geocoder uses (`KNOWN_CITIES` in `geocoding.py`), so demo addresses near
those cities resolve close to the real warehouse locations rather than
falling back to the hash-based jitter.

## Future improvements

- Real geocoding/payment providers behind the existing interfaces, with a
  retry/timeout/circuit-breaker policy around the payment call specifically
  (a network error talking to the processor should not look the same to the
  customer as a declined card).
- A real migration tool (Alembic, or even just numbered `db/NNN_*.sql`
  files applied by a small runner) once the schema needs to change after
  it's already deployed somewhere with data in it — `db/01_schema.sql` is
  fine for "set up a fresh database" but isn't a migration story by itself.
- Webhook-based payment confirmation instead of (or in addition to) the
  synchronous response, since real processors are often async under the
  hood (3DS, bank holds, etc).
- A reconciliation job for orders stuck in `pending_payment` (e.g. the
  process crashes between reserving stock and recording the payment
  result) — release the reservation or retry confirmation after a timeout.
- Splitting an order across multiple warehouses when no single one has
  everything, with per-shipment tracking — explicitly out of scope per the
  spec, but the natural next step.
- Structured logging / request tracing (the request-id middleware here is
  step one) and metrics (order success rate, payment decline rate by
  reason, warehouse fulfillment distribution).
- Auth + per-customer rate limiting on `POST /orders`.
- Moving "does any warehouse have all these items" from a Python-side scan
  over fetched inventory rows into a single SQL query (or a dedicated
  inventory-search index), which would matter at a catalog/warehouse count
  well beyond this project's seed data.

## Project layout

```
app/
  __init__.py             app factory, wiring, request logging
  config.py               env-driven configuration (DATABASE_URL, pool size)
  db.py                   connection pool + transaction() context manager
  errors.py               ApiError hierarchy -> JSON error responses
  validation.py           POST /orders request parsing/validation
  repositories.py         SQL access (no business logic)
  services/
    geocoding.py            GeocodingProvider + mock
    payment.py              PaymentGateway + mock
    warehouse_selector.py   "closest warehouse with full stock" logic
    order_service.py        orchestrates the steps above
  routes/
    orders.py               POST /orders, GET /orders/:id, idempotency
    health.py               GET /health (real DB connectivity check)
    dashboard.py            GET /, stats API, smoke-test runner (see below)
  templates/
    dashboard.html            dashboard markup
  static/
    dashboard.css             dashboard styling
    dashboard.js               dashboard stats/console/smoke-test behavior
db/
  01_schema.sql           table definitions, indices (plain SQL, no app code)
  02_seed.sql             generated realistic demo data (see above)
scripts/
  generate_seed_dump.py   dev tool that produces db/02_seed.sql -- not run by the app
  smoke_test.py           reproducible end-to-end test scenarios
docker-compose.yml        postgres + app, dev-only orchestration with auto schema/seed init
run.py                    entrypoint
```
