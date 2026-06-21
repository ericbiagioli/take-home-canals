from app import repositories as repo
from app.db import transaction
from app.errors import ConflictError, NotFoundError
from app.services.warehouse_selector import NoWarehouseAvailable, find_best_warehouse
from app.validation import format_address


class OrderService:
    """Coordinates the moving pieces of POST /orders: pricing, warehouse
    selection, stock reservation, and charging the customer.

    Design note on why this isn't all one big DB transaction: step 3 below
    calls an external payment API, which can be slow or hang. Holding a
    Postgres row lock for the duration of a third-party HTTP call would
    serialize every other order touching that same inventory row behind
    it. Instead we do a short "reserve" transaction, make the network call
    with no lock held, then a short "finalize" transaction. The trade-off
    is a brief window where stock is reserved against an order that might
    still fail payment; we handle that by releasing the reservation if the
    charge is declined, and by recording every step (order row + payment
    row) so the sequence is fully auditable even if the process crashes
    between steps.
    """

    def __init__(self, geocoder, payment_gateway):
        self.geocoder = geocoder
        self.payment_gateway = payment_gateway

    def create_order(self, conn, request: dict) -> tuple[int, dict]:
        customer = repo.get_customer(conn, request["customer_id"])
        if customer is None:
            raise NotFoundError(f"Customer {request['customer_id']} was not found.")

        items = request["items"]
        product_ids = [item["product_id"] for item in items]
        products_by_id = repo.get_products_by_ids(conn, product_ids)
        missing = [pid for pid in product_ids if pid not in products_by_id]
        if missing:
            raise NotFoundError(
                "One or more products were not found.",
                details={"missing_product_ids": missing},
            )

        lat, lon = self.geocoder.geocode(request["shipping_address"])
        address_text = format_address(request["shipping_address"])

        priced_items = []
        subtotal_cents = 0
        for item in items:
            product = products_by_id[item["product_id"]]
            line_total = product["price_cents"] * item["quantity"]
            subtotal_cents += line_total
            priced_items.append(
                {
                    "product_id": product["id"],
                    "product_name": product["name"],
                    "quantity": item["quantity"],
                    "unit_price_cents": product["price_cents"],
                }
            )
        total_cents = subtotal_cents  # no tax/shipping in scope; see README

        # --- Phase 1: pick a warehouse and reserve stock -------------------
        with transaction(conn):
            try:
                warehouse = find_best_warehouse(conn, items, lat, lon)
            except NoWarehouseAvailable:
                raise ConflictError(
                    "No warehouse currently has enough stock to fulfill this order from a single location.",
                    details={"items": items},
                )

            try:
                repo.reserve_inventory(conn, warehouse["id"], items)
            except repo.InsufficientStockError:
                # Stock changed between the check above and the reservation
                # (a concurrent order won the race). Safe to surface as a
                # conflict; the client can retry.
                raise ConflictError(
                    "Stock for this order just changed and is no longer available. Please retry."
                )

            order_id = repo.create_order(
                conn,
                customer_id=customer["id"],
                warehouse_id=warehouse["id"],
                status="pending_payment",
                shipping_address_text=address_text,
                shipping_address_json=request["shipping_address"],
                lat=lat,
                lon=lon,
                subtotal_cents=subtotal_cents,
                total_cents=total_cents,
                items=priced_items,
            )

        # --- Phase 2: charge the card (no DB lock held) ---------------------
        payment_result = self.payment_gateway.charge(
            card_number=request["payment"]["card_number"],
            amount_cents=total_cents,
            description=f"Canals order #{order_id} ({len(priced_items)} item(s))",
        )

        # --- Phase 3: finalize -----------------------------------------------
        with transaction(conn):
            repo.record_payment(conn, order_id=order_id, amount_cents=total_cents, result=payment_result)
            if payment_result.success:
                repo.update_order_status(conn, order_id, "paid")
            else:
                repo.update_order_status(conn, order_id, "payment_failed")
                repo.release_inventory(conn, warehouse["id"], items)

        details = repo.get_order_with_details(conn, order_id)
        status_code = 201 if payment_result.success else 402
        return status_code, serialize_order(details)


def serialize_order(details: dict) -> dict:
    order = details["order"]
    warehouse = details["warehouse"]
    payment = details["payment"]

    return {
        "id": order["id"],
        "status": order["status"],
        "customer_id": order["customer_id"],
        "shipping_address": order["shipping_address"],
        "warehouse": {
            "id": warehouse["id"],
            "name": warehouse["name"],
            "address": warehouse["address"],
        },
        "items": [
            {
                "product_id": item["product_id"],
                "name": item["product_name"],
                "quantity": item["quantity"],
                "unit_price_cents": item["unit_price_cents"],
                "subtotal_cents": item["unit_price_cents"] * item["quantity"],
            }
            for item in details["items"]
        ],
        "subtotal_cents": order["subtotal_cents"],
        "total_cents": order["total_cents"],
        "payment": (
            {
                "status": payment["status"],
                "transaction_id": payment["provider_transaction_id"],
                "card_last4": payment["card_last4"],
                "failure_code": payment["failure_code"],
                "failure_message": payment["failure_message"],
            }
            if payment is not None
            else None
        ),
        "created_at": _isoformat(order["created_at"]),
        "updated_at": _isoformat(order["updated_at"]),
    }


def _isoformat(value):
    """Postgres TIMESTAMPTZ columns come back from psycopg2 as timezone-aware
    datetime objects, not strings -- format explicitly so the API's JSON
    output is a predictable ISO 8601 string regardless of what the JSON
    encoder's default datetime handling happens to do.
    """
    return value.isoformat() if hasattr(value, "isoformat") else value
