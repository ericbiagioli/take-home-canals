from app.errors import ValidationError

_REQUIRED_ADDRESS_FIELDS = ("line1", "city", "postal_code", "country")


def _fail(errors):
    raise ValidationError("The request body is invalid.", details=errors)


def parse_create_order_request(body) -> dict:
    """Validate and normalize the POST /orders payload.

    Collects every validation problem found (rather than stopping at the
    first one) so a client fixing its request doesn't have to round-trip
    once per mistake.
    """
    errors = []

    if not isinstance(body, dict):
        _fail([{"field": "body", "message": "Request body must be a JSON object."}])

    customer_id = body.get("customer_id")
    if not isinstance(customer_id, int) or isinstance(customer_id, bool) or customer_id <= 0:
        errors.append({"field": "customer_id", "message": "customer_id is required and must be a positive integer."})

    shipping_address = _validate_address(body.get("shipping_address"), errors)
    items = _validate_items(body.get("items"), errors)
    payment = _validate_payment(body.get("payment"), errors)

    if errors:
        _fail(errors)

    return {
        "customer_id": customer_id,
        "shipping_address": shipping_address,
        "items": items,
        "payment": payment,
    }


def _validate_address(address, errors) -> dict:
    if not isinstance(address, dict):
        errors.append({"field": "shipping_address", "message": "shipping_address is required and must be an object."})
        return {}

    for field in _REQUIRED_ADDRESS_FIELDS:
        value = address.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append({"field": f"shipping_address.{field}", "message": f"{field} is required."})

    normalized = {
        "line1": (address.get("line1") or "").strip(),
        "line2": (address.get("line2") or "").strip() or None,
        "city": (address.get("city") or "").strip(),
        "state": (address.get("state") or "").strip() or None,
        "postal_code": (address.get("postal_code") or "").strip(),
        "country": (address.get("country") or "").strip(),
    }
    return normalized


def _validate_items(items, errors) -> list:
    if not isinstance(items, list) or len(items) == 0:
        errors.append({"field": "items", "message": "items is required and must be a non-empty array."})
        return []

    parsed = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append({"field": f"items[{idx}]", "message": "Each item must be an object."})
            continue
        product_id = item.get("product_id")
        quantity = item.get("quantity")
        if not isinstance(product_id, int) or isinstance(product_id, bool) or product_id <= 0:
            errors.append({"field": f"items[{idx}].product_id", "message": "product_id must be a positive integer."})
            continue
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            errors.append({"field": f"items[{idx}].quantity", "message": "quantity must be a positive integer."})
            continue
        parsed.append({"product_id": product_id, "quantity": quantity})

    if errors:
        return parsed

    # Merge duplicate product_id entries (e.g. the same SKU added twice by
    # the UI) instead of silently dropping or rejecting them.
    merged: dict[int, int] = {}
    for item in parsed:
        merged[item["product_id"]] = merged.get(item["product_id"], 0) + item["quantity"]
    return [{"product_id": pid, "quantity": qty} for pid, qty in merged.items()]


def _validate_payment(payment, errors) -> dict:
    if not isinstance(payment, dict):
        errors.append({"field": "payment", "message": "payment is required and must be an object."})
        return {}

    card_number = payment.get("card_number")
    if not isinstance(card_number, str) or not card_number.strip():
        errors.append({"field": "payment.card_number", "message": "payment.card_number is required."})

    # Expiry/CVV are part of a realistic charge request; we accept them so
    # the mock gateway has something to (not) look at, but intentionally
    # never persist them anywhere.
    for field in ("expiry_month", "expiry_year"):
        value = payment.get(field)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append({"field": f"payment.{field}", "message": f"payment.{field} must be an integer if provided."})

    return {
        "card_number": (card_number or "").strip(),
        "expiry_month": payment.get("expiry_month"),
        "expiry_year": payment.get("expiry_year"),
        "cvv": payment.get("cvv"),
    }


def format_address(address: dict) -> str:
    line2 = f", {address['line2']}" if address.get("line2") else ""
    state = f", {address['state']}" if address.get("state") else ""
    return (
        f"{address['line1']}{line2}, {address['city']}{state} "
        f"{address['postal_code']}, {address['country']}"
    )
