from typing import Iterable

from app.utils.geo import haversine_km


class NoWarehouseAvailable(Exception):
    """No single warehouse has enough stock of every requested product."""


def find_best_warehouse(conn, items: Iterable[dict], dest_lat: float, dest_lon: float):
    """Return the closest warehouse able to fill the entire order from its
    own stock, or raise NoWarehouseAvailable.

    An order must ship from a single warehouse, so a warehouse only
    qualifies if `quantity >= requested` holds for *every* line item there
    -- partial availability across multiple warehouses doesn't count.
    Among qualifying warehouses we pick the one with the shortest
    great-circle distance to the shipping address.

    This does the "which warehouses qualify" filtering in Python rather
    than as a single SQL query, which is simpler to read and plenty fast
    at this catalog's scale (a handful of line items against a few
    thousand inventory rows). At a much larger scale you'd push this into
    SQL (or a dedicated inventory-search index) instead of pulling every
    matching row back to the app -- see README.
    """
    items = list(items)
    product_ids = [item["product_id"] for item in items]

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT warehouse_id, product_id, quantity
            FROM inventory
            WHERE product_id = ANY(%s)
            """,
            (product_ids,),
        )
        rows = cur.fetchall()

    stock_by_warehouse: dict[int, dict[int, int]] = {}
    for row in rows:
        stock_by_warehouse.setdefault(row["warehouse_id"], {})[row["product_id"]] = row["quantity"]

    eligible_ids = [
        warehouse_id
        for warehouse_id, stock in stock_by_warehouse.items()
        if all(stock.get(item["product_id"], 0) >= item["quantity"] for item in items)
    ]

    if not eligible_ids:
        raise NoWarehouseAvailable()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, address, latitude, longitude FROM warehouses WHERE id = ANY(%s)",
            (eligible_ids,),
        )
        warehouses = cur.fetchall()

    return min(
        warehouses,
        key=lambda w: haversine_km(dest_lat, dest_lon, w["latitude"], w["longitude"]),
    )
