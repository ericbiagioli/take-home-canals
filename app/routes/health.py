from flask import Blueprint, jsonify

from app.db import get_db

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    """Liveness + a real DB connectivity check, not just "the process is
    up". A Flask process can be running fine while Postgres is unreachable
    (network blip, pool exhausted, credentials rotated) -- a load balancer
    or orchestrator relying on this endpoint should see that as unhealthy.
    """
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception:
        return jsonify({"status": "error", "detail": "database unreachable"}), 503
    return jsonify({"status": "ok"})
