from flask import jsonify
from werkzeug.exceptions import HTTPException


class ApiError(Exception):
    """Base class for errors that should be turned into a clean JSON HTTP
    response rather than a raw 500. Subclasses set status_code/code."""

    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self):
        body = {"code": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return body


class ValidationError(ApiError):
    status_code = 400
    code = "VALIDATION_ERROR"


class NotFoundError(ApiError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(ApiError):
    status_code = 409
    code = "CONFLICT"


class PaymentFailedError(ApiError):
    status_code = 402
    code = "PAYMENT_FAILED"


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def handle_api_error(err: ApiError):
        response = jsonify({"error": err.to_dict()})
        response.status_code = err.status_code
        return response

    @app.errorhandler(404)
    def handle_404(_err):
        response = jsonify(
            {"error": {"code": "NOT_FOUND", "message": "The requested resource was not found."}}
        )
        response.status_code = 404
        return response

    @app.errorhandler(405)
    def handle_405(_err):
        response = jsonify(
            {"error": {"code": "METHOD_NOT_ALLOWED", "message": "That HTTP method isn't supported on this route."}}
        )
        response.status_code = 405
        return response

    @app.errorhandler(Exception)
    def handle_unexpected_error(err):
        # Registering a handler for the base Exception class also catches
        # HTTPException subclasses Flask/Werkzeug would otherwise turn into
        # the right status code itself (413 payload too large, 415
        # unsupported media type, etc). Without this branch every one of
        # those would get reported as an opaque 500 instead of its real code.
        if isinstance(err, HTTPException):
            response = jsonify(
                {"error": {"code": err.name.upper().replace(" ", "_"), "message": err.description}}
            )
            response.status_code = err.code
            return response

        app.logger.exception("Unhandled exception while processing request")
        response = jsonify(
            {"error": {"code": "INTERNAL_ERROR", "message": "Something went wrong on our end."}}
        )
        response.status_code = 500
        return response
