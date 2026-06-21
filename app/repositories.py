from psycopg2.extras import Json


def get_customer(conn, customer_id: int):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM customers WHERE id = %s", (customer_id,))
        return cur.fetchone()


def get_products_by_ids(conn, product_ids: list[int]) -> dict:
    if not product_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM products WHERE id = ANY(%s)", (product_ids,)
        )
        rows = cur.fetchall()
    return {row["id"]: row for row in rows}


class InsufficientStockError(Exception):
    def __init__(self, product_id: int):
        self.product_id = product_id


def reserve_inventory(conn, warehouse_id: int, items: list[dict]) -> None:
    """Atomically decrement stock for each line item. Must be called inside
    an open transaction() block. The WHERE quantity >= %s clause means this
    is safe even if stock changed since it was checked moments earlier:
    Postgres takes a row lock on the matching (warehouse_id, product_id)
    row for the UPDATE's duration, so a concurrent reservation against the
    *same* row genuinely serializes (the loser's WHERE re-evaluates against
    the post-commit value), while reservations against different rows
    proceed in parallel. If the row lock can't be acquired before the
    statement_timeout, Postgres raises rather than hanging forever.

    Items are locked in a fixed (product_id) order so two orders that share
    several products, requested in different orders, can't deadlock by
    each holding one row the other is waiting on.
    """
    with conn.cursor() as cur:
        for item in sorted(items, key=lambda i: i["product_id"]):
            cur.execute(
                """
                UPDATE inventory
                SET quantity = quantity - %s
                WHERE warehouse_id = %s AND product_id = %s AND quantity >= %s
                """,
                (item["quantity"], warehouse_id, item["product_id"], item["quantity"]),
            )
            if cur.rowcount == 0:
                raise InsufficientStockError(item["product_id"])


def release_inventory(conn, warehouse_id: int, items: list[dict]) -> None:
    """Undo reserve_inventory, e.g. after a payment decline."""
    with conn.cursor() as cur:
        for item in sorted(items, key=lambda i: i["product_id"]):
            cur.execute(
                """
                UPDATE inventory
                SET quantity = quantity + %s
                WHERE warehouse_id = %s AND product_id = %s
                """,
                (item["quantity"], warehouse_id, item["product_id"]),
            )


def create_order(conn, *, customer_id, warehouse_id, status,
                  shipping_address_text, shipping_address_json, lat, lon,
                  subtotal_cents, total_cents, items) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO orders (
                customer_id, warehouse_id, status, shipping_address,
                shipping_address_json, shipping_latitude, shipping_longitude,
                subtotal_cents, total_cents
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                customer_id, warehouse_id, status, shipping_address_text,
                Json(shipping_address_json), lat, lon, subtotal_cents, total_cents,
            ),
        )
        order_id = cur.fetchone()["id"]

        for item in items:
            cur.execute(
                """
                INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price_cents)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (order_id, item["product_id"], item["product_name"], item["quantity"], item["unit_price_cents"]),
            )
    return order_id


def update_order_status(conn, order_id: int, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE orders SET status = %s, updated_at = now() WHERE id = %s",
            (status, order_id),
        )


def record_payment(conn, *, order_id, amount_cents, result) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payments (
                order_id, amount_cents, status, provider_transaction_id,
                card_last4, failure_code, failure_message
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_id,
                amount_cents,
                "succeeded" if result.success else "failed",
                result.transaction_id,
                result.card_last4,
                result.failure_code,
                result.failure_message,
            ),
        )


def get_order_with_details(conn, order_id: int):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
        order = cur.fetchone()
        if order is None:
            return None

        cur.execute(
            "SELECT * FROM order_items WHERE order_id = %s ORDER BY id", (order_id,)
        )
        items = cur.fetchall()

        cur.execute(
            "SELECT id, name, address FROM warehouses WHERE id = %s", (order["warehouse_id"],)
        )
        warehouse = cur.fetchone()

        cur.execute(
            "SELECT * FROM payments WHERE order_id = %s ORDER BY id DESC LIMIT 1", (order_id,)
        )
        payment = cur.fetchone()

    return {"order": order, "items": items, "warehouse": warehouse, "payment": payment}


def list_orders_for_customer(conn, customer_id: int, limit: int = 50):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM orders WHERE customer_id = %s ORDER BY id DESC LIMIT %s",
            (customer_id, limit),
        )
        return cur.fetchall()


# --- Idempotency -----------------------------------------------------------
#
# claim_idempotency_key() / release_idempotency_claim() / save_idempotent_response()
# work together as a mutex around a key, not just a response cache:
#
#   1. claim_idempotency_key() tries to INSERT a NULL-response row. Two
#      requests racing with the *same* key on the same INSERT genuinely
#      serialize on the table's primary key -- only one can win "claimed";
#      the other sees "in_progress" and should back off, rather than both
#      slipping past a check-then-act read and double-processing the order.
#   2. The winner processes the request, then either:
#      - save_idempotent_response() fills in the real response, or
#      - release_idempotency_claim() deletes the row if processing raised
#        before producing a cacheable response (so a retry isn't blocked
#        until the staleness window below elapses).
#   3. If a claimant crashes without doing either, its row is "in_progress"
#      forever -- after IDEMPOTENCY_CLAIM_STALE_SECONDS, claim_idempotency_key()
#      treats it as abandoned and lets a later request take it over.

IDEMPOTENCY_CLAIM_STALE_SECONDS = 30


def claim_idempotency_key(conn, key: str, fingerprint: str, stale_after_seconds: int = IDEMPOTENCY_CLAIM_STALE_SECONDS):
    """Try to claim `key` for processing.

    Returns ("claimed", None) if this call won the claim -- the caller owns
    the key and must follow up with save_idempotent_response() or
    release_idempotency_claim(). Returns ("completed", row) if the key
    already has a finished response (caller should check the fingerprint
    and replay it). Returns ("in_progress", row) if another request holds
    an unexpired claim on this key right now (caller should reject; this is
    a genuine concurrent duplicate, not a sequential retry).
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO idempotency_keys (key, request_fingerprint) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            (key, fingerprint),
        )
        if cur.rowcount == 1:
            return "claimed", None

        cur.execute(
            """
            UPDATE idempotency_keys
            SET request_fingerprint = %s, created_at = now()
            WHERE key = %s
              AND response_status IS NULL
              AND created_at < now() - %s * INTERVAL '1 second'
            """,
            (fingerprint, key, stale_after_seconds),
        )
        if cur.rowcount == 1:
            return "claimed", None

        cur.execute("SELECT * FROM idempotency_keys WHERE key = %s", (key,))
        existing = cur.fetchone()

    if existing["response_status"] is not None:
        return "completed", existing
    return "in_progress", existing


def release_idempotency_claim(conn, key: str) -> None:
    """Drop a still-pending claim after the request that owned it failed
    before producing a response to cache, so a retry isn't stuck waiting
    out the staleness window for no reason."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM idempotency_keys WHERE key = %s AND response_status IS NULL",
            (key,),
        )


def save_idempotent_response(conn, key: str, status: int, body: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE idempotency_keys SET response_status = %s, response_body = %s WHERE key = %s",
            (status, Json(body), key),
        )
