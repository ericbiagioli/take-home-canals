import re
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class PaymentResult:
    success: bool
    card_last4: str
    transaction_id: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None


class PaymentGateway(ABC):
    """Port for charging a card. In production this wraps a real processor
    (Stripe, Braintree, Adyen...) over HTTPS, with its own idempotency keys,
    retries on network errors, and webhook-based confirmation. The order
    service only depends on this interface, so the mock below can be
    replaced without touching order-creation logic.
    """

    @abstractmethod
    def charge(self, card_number: str, amount_cents: int, description: str) -> PaymentResult:
        raise NotImplementedError


# Stripe-style test card numbers, so failure paths are easy to exercise
# on purpose during review/testing rather than only by chance.
_DECLINE_CODES = {
    "4000000000000002": ("card_declined", "The card was declined."),
    "4000000000009995": ("insufficient_funds", "The card has insufficient funds."),
    "4000000000000069": ("expired_card", "The card has expired."),
    "4000000000000127": ("incorrect_cvc", "The card's security code is incorrect."),
}


class MockPaymentGateway(PaymentGateway):
    """Simulates a payment processor without making any network calls.

    Card numbers are validated with a real Luhn checksum (so obviously
    malformed numbers fail the way a real processor would reject them
    before even attempting authorization) and a short artificial delay
    stands in for the network round trip. We deliberately only ever look
    at/store the last 4 digits -- holding raw PANs is exactly what you
    don't want to do once this talks to a real processor (PCI scope).
    """

    def charge(self, card_number: str, amount_cents: int, description: str) -> PaymentResult:
        digits = re.sub(r"\D", "", card_number or "")
        last4 = digits[-4:] if len(digits) >= 4 else digits

        if not (13 <= len(digits) <= 19):
            return PaymentResult(
                success=False,
                card_last4=last4,
                failure_code="invalid_card_number",
                failure_message="Card number has an invalid length.",
            )
        if not self._luhn_valid(digits):
            return PaymentResult(
                success=False,
                card_last4=last4,
                failure_code="invalid_card_number",
                failure_message="Card number failed validation.",
            )
        if amount_cents <= 0:
            return PaymentResult(
                success=False,
                card_last4=last4,
                failure_code="invalid_amount",
                failure_message="Charge amount must be positive.",
            )

        time.sleep(0.05)  # stand-in for the real network round trip

        if digits in _DECLINE_CODES:
            code, message = _DECLINE_CODES[digits]
            return PaymentResult(success=False, card_last4=last4, failure_code=code, failure_message=message)

        return PaymentResult(
            success=True,
            card_last4=last4,
            transaction_id=f"mock_txn_{uuid.uuid4().hex[:18]}",
        )

    @staticmethod
    def _luhn_valid(digits: str) -> bool:
        total = 0
        for i, ch in enumerate(reversed(digits)):
            d = int(ch)
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return total % 10 == 0
