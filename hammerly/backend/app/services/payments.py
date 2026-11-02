"""Payment provider boundary. Only the mock provider is implemented.

To go live, add a provider that creates a real payment intent and credits the wallet from the
provider's webhook, then select it with PAYMENT_PROVIDER. Nothing else in the app changes.
"""
from dataclasses import dataclass
from typing import Protocol

from ..config import get_settings


@dataclass
class TopupResult:
    status: str          # succeeded | failed
    provider_ref: str


class PaymentProvider(Protocol):
    async def create_topup(self, user_id: int, amount_cents: int) -> TopupResult: ...


class MockProvider:
    """Test mode: every top-up succeeds immediately. No real money moves."""
    async def create_topup(self, user_id: int, amount_cents: int) -> TopupResult:
        if amount_cents <= 0 or amount_cents > 5_000_000:
            return TopupResult("failed", "")
        return TopupResult("succeeded", f"mock_{user_id}_{amount_cents}")


def get_provider() -> PaymentProvider:
    name = get_settings().payment_provider
    if name == "mock":
        return MockProvider()
    raise RuntimeError(f"Payment provider '{name}' is not implemented. Use 'mock' or add an adapter.")
