import hashlib
import json

from flask import Blueprint, current_app, jsonify, request

from app import repositories as repo
from app.db import get_db
from app.errors import ConflictError, NotFoundError, ValidationError
from app.validation import parse_create_order_request

bp = Blueprint("orders", __name__, url_prefix="/orders")


@bp.post("")
def create_order():
    raw_body = request.get_json(silent=True)
    if raw_body is None:
        raise ValidationError("Request body must be valid JSON.")

    idempotency_key = request.headers.get("Idempotency-Key")
    conn = get_db()

    if idempotency_key:
        replay = _claim_or_replay(conn, idempotency_key, raw_body)
        if replay is not None:
            return replay

    try:
        parsed = parse_create_order_request(raw_body)
        status, body = current_app.order_service.create_order(conn, parsed)
    except Exception:
        # No exception from here down is left unhandled: this re-raises
        # unconditionally, and register_error_handlers() in app/errors.py
        # catches everything that reaches it -- ApiError subclasses
        # (ValidationError/NotFoundError/ConflictError/...) get their real
        # status code, and anything else (including a raw psycopg2/database
        # error) still gets a clean JSON 500 instead of propagating as an
        # unhandled crash. This block exists purely for the
        # idempotency-claim cleanup, not for classifying the error.
        #
        # The cleanup itself is best-effort and wrapped separately: if the
        # original exception was the database connection itself going bad,
        # trying to run another query on it here would raise too, and an
        # unguarded second exception would replace the first one and hide
        # what actually went wrong.
        if idempotency_key:
            try:
                repo.release_idempotency_claim(conn, idempotency_key)
            except Exception:
                current_app.logger.exception(
                    "Failed to release idempotency claim %s after a failed order attempt", idempotency_key
                )
        raise

    if idempotency_key:
        repo.save_idempotent_response(conn, idempotency_key, status, body)

    response = jsonify(body)
    response.status_code = status
    return response


@bp.get("/<int:order_id>")
def get_order(order_id: int):
    conn = get_db()
    details = repo.get_order_with_details(conn, order_id)
    if details is None:
        raise NotFoundError(f"Order {order_id} was not found.")
    from app.services.order_service import serialize_order

    return jsonify(serialize_order(details))


def _fingerprint(body) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _claim_or_replay(conn, key: str, raw_body: dict):
    """Returns a Flask response to short-circuit with if this key already
    has a finished response, or None if this call won the claim and should
    proceed with normal processing (and later call save_idempotent_response
    or release_idempotency_claim).

    Reusing a key with a *different* request body is treated as a client
    bug (most likely accidentally reusing a key across two different
    orders) and rejected, the same way Stripe's idempotency keys behave. A
    key claimed by another request right now (a genuine concurrent
    duplicate, not a sequential retry) is rejected as a conflict rather
    than risking double-processing the order.
    """
    fingerprint = _fingerprint(raw_body)
    claim_status, existing = repo.claim_idempotency_key(conn, key, fingerprint)

    if claim_status == "claimed":
        return None

    if claim_status == "in_progress":
        raise ConflictError(
            "A request with this Idempotency-Key is already being processed. Please retry shortly.",
            details={"idempotency_key": key},
        )

    if existing["request_fingerprint"] != fingerprint:
        raise ValidationError(
            "This Idempotency-Key was already used with a different request body.",
            details={"idempotency_key": key},
        )

    response = jsonify(existing["response_body"])
    response.status_code = existing["response_status"]
    response.headers["Idempotency-Replayed"] = "true"
    return response
