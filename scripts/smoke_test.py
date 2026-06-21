"""End-to-end smoke test against a running instance of the API.

Unlike the old SQLite version's test data (3 customers, 4 warehouses, 6
fixed-id products), the seed dump is now ~500 customers / 16 warehouses /
~420 products with randomized inventory -- hardcoding "product_id=5" would
be fragile and wouldn't survive regenerating the dump. Instead, every test
here discovers its own fixtures by querying the database directly, and the
"does the API pick the right warehouse" assertion is verified by calling
the real app.services.warehouse_selector.find_best_warehouse function
directly as an oracle (against the same DB, with the same inputs) and
checking the live HTTP response agrees with it -- rather than assuming a
hand-picked answer.

Requires:
  - the API running and reachable at BASE_URL (default http://localhost:8000)
  - DATABASE_URL pointing at the same Postgres instance the API is using,
    for direct read/setup queries
  - db/01_schema.sql + db/02_seed.sql already applied

Usage:
    python scripts/smoke_test.py
"""
import os
import sys
import threading
import urllib.error
import urllib.request
import json as json_module
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import psycopg2.extras

from app.services.geocoding import MockGeocodingProvider, KNOWN_CITIES
from app.services.warehouse_selector import find_best_warehouse, NoWarehouseAvailable

BASE_URL = os.environ.get("SMOKE_TEST_BASE_URL", "http://localhost:8000")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://canals:canals_dev_password@localhost:5432/canals"
)

passed = 0
failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok  - {label}")
    else:
        failed += 1
        print(f"FAIL  - {label} {detail}")


def http(method, path, body=None, headers=None):
    url = f"{BASE_URL}{path}"
    data = json_module.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json_module.loads(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, json_module.loads(e.read()), dict(e.headers)


def db_connect():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


def known_city_address(city_key: str) -> dict:
    return {
        "line1": "1 Test St",
        "city": city_key.title(),
        "state": "NA",
        "postal_code": "00000",
        "country": "US",
    }


def pick_any_customer_id(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM customers ORDER BY id LIMIT 1")
        return cur.fetchone()["id"]


def pick_stocked_product(conn, min_qty: int = 5) -> dict:
    """A product some warehouse holds at least min_qty units of."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT product_id, warehouse_id, quantity FROM inventory "
            "WHERE quantity >= %s ORDER BY product_id LIMIT 1",
            (min_qty,),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"No inventory row with quantity >= {min_qty}; is the DB seeded?")
        return row


def oracle_predict_warehouse(conn, items, lat, lon):
    try:
        return find_best_warehouse(conn, items, lat, lon)
    except NoWarehouseAvailable:
        return None


def test_successful_order(conn, customer_id):
    print("\n[1] successful order picks the warehouse the real selection logic predicts")
    stocked = pick_stocked_product(conn, min_qty=2)
    items = [{"product_id": stocked["product_id"], "quantity": 2}]
    city_key = list(KNOWN_CITIES.keys())[0]
    address = known_city_address(city_key)
    lat, lon = MockGeocodingProvider().geocode(address)

    expected_warehouse = oracle_predict_warehouse(conn, items, lat, lon)
    check("oracle found an eligible warehouse", expected_warehouse is not None)

    status, body, _ = http(
        "POST", "/orders",
        {
            "customer_id": customer_id,
            "items": items,
            "shipping_address": address,
            "payment": {"card_number": "4242424242424242", "expiry_month": 12, "expiry_year": 2030, "cvv": "123"},
        },
    )
    check("status is 201", status == 201, f"got {status}: {body}")
    if expected_warehouse is not None and status == 201:
        check(
            "warehouse matches oracle prediction",
            body["warehouse"]["id"] == expected_warehouse["id"],
            f"api picked {body['warehouse']}, oracle predicted {dict(expected_warehouse)}",
        )
    check("payment succeeded", status == 201 and body.get("payment", {}).get("status") == "succeeded")
    return body


def test_payment_decline_rolls_back_stock(conn, customer_id):
    print("\n[2] declined payment rolls back the stock reservation")
    stocked = pick_stocked_product(conn, min_qty=3)
    items = [{"product_id": stocked["product_id"], "quantity": 1}]
    city_key = list(KNOWN_CITIES.keys())[1]
    address = known_city_address(city_key)
    lat, lon = MockGeocodingProvider().geocode(address)
    expected_warehouse = oracle_predict_warehouse(conn, items, lat, lon)
    if expected_warehouse is None:
        print("  skip - oracle found no eligible warehouse for this fixture")
        return

    with conn.cursor() as cur:
        cur.execute(
            "SELECT quantity FROM inventory WHERE warehouse_id=%s AND product_id=%s",
            (expected_warehouse["id"], items[0]["product_id"]),
        )
        before = cur.fetchone()["quantity"]

    status, body, _ = http(
        "POST", "/orders",
        {
            "customer_id": customer_id,
            "items": items,
            "shipping_address": address,
            # Stripe-style guaranteed-decline test card.
            "payment": {"card_number": "4000000000000002", "expiry_month": 12, "expiry_year": 2030, "cvv": "123"},
        },
    )
    check("status is 402", status == 402, f"got {status}: {body}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT quantity FROM inventory WHERE warehouse_id=%s AND product_id=%s",
            (expected_warehouse["id"], items[0]["product_id"]),
        )
        after = cur.fetchone()["quantity"]
    check("stock restored to original level", before == after, f"before={before} after={after}")


def test_no_warehouse_available(conn, customer_id):
    print("\n[3] requesting more than any single warehouse holds returns 409")
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM products ORDER BY id LIMIT 1")
        product_id = cur.fetchone()["id"]
        cur.execute(
            "SELECT COALESCE(MAX(quantity), 0) AS maxq FROM inventory WHERE product_id=%s",
            (product_id,),
        )
        max_qty = cur.fetchone()["maxq"]

    city_key = list(KNOWN_CITIES.keys())[2]
    status, body, _ = http(
        "POST", "/orders",
        {
            "customer_id": customer_id,
            "items": [{"product_id": product_id, "quantity": max_qty + 1}],
            "shipping_address": known_city_address(city_key),
            "payment": {"card_number": "4242424242424242", "expiry_month": 12, "expiry_year": 2030, "cvv": "123"},
        },
    )
    check("status is 409", status == 409, f"got {status}: {body}")


def test_validation_errors(customer_id):
    print("\n[4] validation errors are collected and returned together")
    status, body, _ = http(
        "POST", "/orders",
        {
            "customer_id": customer_id,
            "items": [],
            "shipping_address": {"line1": "", "city": "", "state": "", "postal_code": "", "country": ""},
            "payment": {"card_number": "123", "expiry_month": 99, "expiry_year": 2000, "cvv": "1"},
        },
    )
    check("status is 400", status == 400, f"got {status}: {body}")
    check(
        "multiple field errors reported",
        status == 400 and len(body.get("error", {}).get("details", [])) > 1,
        body,
    )


def test_not_found(conn, customer_id):
    print("\n[5] 404s for missing customer/product")
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) + 1000000 AS missing FROM customers")
        missing_customer = cur.fetchone()["missing"]
        cur.execute("SELECT COALESCE(MAX(id), 0) + 1000000 AS missing FROM products")
        missing_product = cur.fetchone()["missing"]

    status, body, _ = http(
        "POST", "/orders",
        {
            "customer_id": missing_customer,
            "items": [{"product_id": 1, "quantity": 1}],
            "shipping_address": known_city_address(list(KNOWN_CITIES.keys())[3]),
            "payment": {"card_number": "4242424242424242", "expiry_month": 12, "expiry_year": 2030, "cvv": "123"},
        },
    )
    check("missing customer -> 404", status == 404, f"got {status}: {body}")

    status, body, _ = http(
        "POST", "/orders",
        {
            "customer_id": customer_id,
            "items": [{"product_id": missing_product, "quantity": 1}],
            "shipping_address": known_city_address(list(KNOWN_CITIES.keys())[3]),
            "payment": {"card_number": "4242424242424242", "expiry_month": 12, "expiry_year": 2030, "cvv": "123"},
        },
    )
    check("missing product -> 404", status == 404, f"got {status}: {body}")

    status, body, _ = http("GET", "/orders/999999999")
    check("missing order -> 404", status == 404, f"got {status}: {body}")


def test_idempotency(conn, customer_id):
    print("\n[6] idempotency key replay and conflict detection")
    stocked = pick_stocked_product(conn, min_qty=2)
    payload = {
        "customer_id": customer_id,
        "items": [{"product_id": stocked["product_id"], "quantity": 1}],
        "shipping_address": known_city_address(list(KNOWN_CITIES.keys())[4]),
        "payment": {"card_number": "4242424242424242", "expiry_month": 12, "expiry_year": 2030, "cvv": "123"},
    }
    key = "smoke-test-idem-key-001"

    status1, body1, headers1 = http("POST", "/orders", payload, headers={"Idempotency-Key": key})
    check("first request succeeds", status1 == 201, f"got {status1}: {body1}")

    status2, body2, headers2 = http("POST", "/orders", payload, headers={"Idempotency-Key": key})
    check("replay returns identical order id", body2.get("id") == body1.get("id"), f"{body1.get('id')} vs {body2.get('id')}")
    check("replay is flagged", headers2.get("Idempotency-Replayed") == "true")

    payload_different = dict(payload, items=[{"product_id": stocked["product_id"], "quantity": 2}])
    status3, body3, _ = http("POST", "/orders", payload_different, headers={"Idempotency-Key": key})
    check("reusing key with different body is rejected", status3 == 400, f"got {status3}: {body3}")


def test_concurrent_reservation(conn, customer_id):
    print("\n[7] concurrent requests for the last unit of stock: exactly one wins")
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM products ORDER BY id LIMIT 1 OFFSET 50")
        product_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM warehouses ORDER BY id LIMIT 1")
        warehouse_id = cur.fetchone()["id"]

        # Force a known state: exactly one unit of this product, system-wide.
        cur.execute("DELETE FROM inventory WHERE product_id=%s", (product_id,))
        cur.execute(
            "INSERT INTO inventory (warehouse_id, product_id, quantity) VALUES (%s, %s, 1)",
            (warehouse_id, product_id),
        )

    with conn.cursor() as cur:
        cur.execute("SELECT name FROM warehouses WHERE id=%s", (warehouse_id,))
        warehouse_name = cur.fetchone()["name"]
    city_key = warehouse_name.replace(" Distribution Center", "").lower()
    address = known_city_address(city_key)

    payload = {
        "customer_id": customer_id,
        "items": [{"product_id": product_id, "quantity": 1}],
        "shipping_address": address,
        "payment": {"card_number": "4242424242424242", "expiry_month": 12, "expiry_year": 2030, "cvv": "123"},
    }

    results = []
    lock = threading.Lock()

    def fire():
        status, body, _ = http("POST", "/orders", payload)
        with lock:
            results.append(status)

    threads = [threading.Thread(target=fire) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = results.count(201)
    conflicts = sum(1 for s in results if s in (409, 402))
    check("exactly one request succeeded", successes == 1, f"results={results}")
    check("the rest were rejected, none oversold", successes + conflicts == len(results), f"results={results}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT quantity FROM inventory WHERE warehouse_id=%s AND product_id=%s",
            (warehouse_id, product_id),
        )
        final_qty = cur.fetchone()["quantity"]
    check("final stock is exactly 0", final_qty == 0, f"got {final_qty}")


def main():
    conn = db_connect()
    customer_id = pick_any_customer_id(conn)

    test_successful_order(conn, customer_id)
    test_payment_decline_rolls_back_stock(conn, customer_id)
    test_no_warehouse_available(conn, customer_id)
    test_validation_errors(customer_id)
    test_not_found(conn, customer_id)
    test_idempotency(conn, customer_id)
    test_concurrent_reservation(conn, customer_id)

    conn.close()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
