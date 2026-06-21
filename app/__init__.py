import logging
import time
import uuid

from flask import Flask, g, request

from app.config import Config
from app.db import close_db, init_db_pool
from app.errors import register_error_handlers
from app.services.geocoding import MockGeocodingProvider
from app.services.order_service import OrderService
from app.services.payment import MockPaymentGateway


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    init_db_pool(app)
    app.teardown_appcontext(close_db)
    register_error_handlers(app)

    # Services are constructed once behind their interfaces and attached to
    # the app; routes never instantiate MockGeocodingProvider/MockPaymentGateway
    # directly, so wiring in real implementations later means changing this
    # one spot.
    app.order_service = OrderService(
        geocoder=MockGeocodingProvider(),
        payment_gateway=MockPaymentGateway(),
    )

    from app.routes.health import bp as health_bp
    from app.routes.orders import bp as orders_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(orders_bp)

    @app.before_request
    def _start_request():
        g.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        g.start_time = time.monotonic()

    @app.after_request
    def _log_request(response):
        duration_ms = (time.monotonic() - g.get("start_time", time.monotonic())) * 1000
        response.headers["X-Request-Id"] = g.get("request_id", "")
        app.logger.info(
            "%s %s -> %s (%.1fms) [%s]",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            g.get("request_id", ""),
        )
        return response

    return app
