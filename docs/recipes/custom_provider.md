# Recipe: Custom Payment Provider Implementation

This recipe shows how to implement a custom payment provider for PSP.

## When to Use This

- You're integrating with a bank/processor not built into PSP
- You have a proprietary payment system
- You're building a mock provider for testing
- You're wrapping an existing internal service

## The Provider Protocol

PSP defines a `PaymentProvider` protocol. Your provider must implement it.

```python
"""
Custom Payment Provider Template

Copy this file and implement the methods for your payment processor.
"""

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Optional, Protocol
from uuid import UUID

from payroll_engine.psp.providers import (
    PaymentProvider,
    SubmitResult,
    PaymentStatus,
    SettlementInfo,
)


@dataclass
class MyProviderConfig:
    """Configuration for your provider."""
    api_key: str
    api_secret: str
    base_url: str
    timeout_seconds: int = 30
    retry_attempts: int = 3


class MyPaymentProvider(PaymentProvider):
    """
    Custom payment provider implementation.

    Replace 'MyPaymentProvider' with your provider name, e.g.:
    - StripeACHProvider
    - ModernTreasuryProvider
    - InternalLedgerProvider
    """

    # =========================================================================
    # REQUIRED: Provider identity
    # =========================================================================

    @property
    def name(self) -> str:
        """Unique identifier for this provider."""
        return "my_provider"

    @property
    def supported_rails(self) -> list[str]:
        """Which payment rails this provider supports."""
        return ["ach", "wire"]  # Add your supported rails

    # =========================================================================
    # REQUIRED: Submit payment
    # =========================================================================

    def submit_payment(
        self,
        instruction_id: UUID,
        amount: Decimal,
        payee_account: str,
        payee_routing: str,
        payee_name: str,
        rail: str,
        idempotency_key: str,
        metadata: Optional[dict] = None,
    ) -> SubmitResult:
        """
        Submit a payment to the external provider.

        This is called when PSP's pay gate approves a payment.

        Args:
            instruction_id: PSP's internal reference
            amount: Payment amount (always positive)
            payee_account: Destination account number
            payee_routing: Destination routing number
            payee_name: Name on destination account
            rail: Payment rail (ach, wire, etc.)
            idempotency_key: Use this for provider-side idempotency
            metadata: Additional data from payment instruction

        Returns:
            SubmitResult with success/failure and provider reference
        """
        try:
            # ----------------------------------------------------------------
            # YOUR IMPLEMENTATION HERE
            # ----------------------------------------------------------------
            # Example: Call your payment API
            response = self._client.create_payment(
                amount=str(amount),
                account_number=payee_account,
                routing_number=payee_routing,
                name=payee_name,
                idempotency_key=idempotency_key,
            )

            if response.success:
                return SubmitResult(
                    success=True,
                    provider_ref=response.payment_id,  # Their reference
                    submitted_at=datetime.utcnow(),
                    expected_settlement_date=response.estimated_arrival,
                )
            else:
                return SubmitResult(
                    success=False,
                    error_code=response.error_code,
                    error_message=response.error_message,
                    retryable=response.error_code in self.RETRYABLE_ERRORS,
                )

        except ConnectionError as e:
            # Network error - likely retryable
            return SubmitResult(
                success=False,
                error_code="CONNECTION_ERROR",
                error_message=str(e),
                retryable=True,
            )
        except Exception as e:
            # Unknown error - don't retry without investigation
            return SubmitResult(
                success=False,
                error_code="UNKNOWN_ERROR",
                error_message=str(e),
                retryable=False,
            )

    # =========================================================================
    # REQUIRED: Check payment status
    # =========================================================================

    def get_payment_status(
        self,
        provider_ref: str,
    ) -> PaymentStatus:
        """
        Check the status of a submitted payment.

        Called for status polling or webhook verification.

        Args:
            provider_ref: Reference returned from submit_payment

        Returns:
            Current payment status from provider
        """
        # ----------------------------------------------------------------
        # YOUR IMPLEMENTATION HERE
        # ----------------------------------------------------------------
        response = self._client.get_payment(provider_ref)

        # Map provider status to PSP status
        status_map = {
            "pending": PaymentStatus.PENDING,
            "processing": PaymentStatus.PROCESSING,
            "completed": PaymentStatus.SETTLED,
            "failed": PaymentStatus.FAILED,
            "returned": PaymentStatus.RETURNED,
        }

        return status_map.get(response.status, PaymentStatus.UNKNOWN)

    # =========================================================================
    # REQUIRED: Parse webhook
    # =========================================================================

    def parse_webhook(
        self,
        payload: bytes,
        headers: dict[str, str],
    ) -> Optional[SettlementInfo]:
        """
        Parse an incoming webhook from the provider.

        PSP calls this when your webhook endpoint receives a POST.

        Args:
            payload: Raw request body
            headers: HTTP headers (for signature verification)

        Returns:
            SettlementInfo if this is a settlement/return notification,
            None if not relevant (e.g., test webhook, other event type)

        Raises:
            WebhookVerificationError: If signature is invalid
        """
        # ----------------------------------------------------------------
        # YOUR IMPLEMENTATION HERE
        # ----------------------------------------------------------------

        # 1. Verify webhook signature
        if not self._verify_signature(payload, headers):
            raise WebhookVerificationError("Invalid webhook signature")

        # 2. Parse the payload
        event = json.loads(payload)

        # 3. Handle relevant event types
        if event["type"] == "payment.completed":
            return SettlementInfo(
                provider_ref=event["payment_id"],
                status=PaymentStatus.SETTLED,
                settled_at=datetime.fromisoformat(event["completed_at"]),
                bank_ref=event.get("trace_id"),
            )

        elif event["type"] == "payment.returned":
            return SettlementInfo(
                provider_ref=event["payment_id"],
                status=PaymentStatus.RETURNED,
                return_code=event["return_code"],
                return_reason=event["return_reason"],
                returned_at=datetime.fromisoformat(event["returned_at"]),
            )

        # Not a relevant event type
        return None

    # =========================================================================
    # OPTIONAL: Cancel payment
    # =========================================================================

    def cancel_payment(
        self,
        provider_ref: str,
    ) -> bool:
        """
        Attempt to cancel a pending payment.

        Not all providers support this. Return False if unsupported.

        Args:
            provider_ref: Reference returned from submit_payment

        Returns:
            True if cancelled, False if cancellation failed or unsupported
        """
        try:
            response = self._client.cancel_payment(provider_ref)
            return response.success
        except Exception:
            return False

    # =========================================================================
    # INTERNAL: Helper methods
    # =========================================================================

    def __init__(self, config: MyProviderConfig):
        """Initialize with provider configuration."""
        self._config = config
        self._client = MyProviderClient(
            api_key=config.api_key,
            api_secret=config.api_secret,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    RETRYABLE_ERRORS = {
        "RATE_LIMITED",
        "SERVICE_UNAVAILABLE",
        "TIMEOUT",
    }

    def _verify_signature(
        self,
        payload: bytes,
        headers: dict[str, str],
    ) -> bool:
        """Verify webhook signature using provider's method."""
        signature = headers.get("X-Webhook-Signature", "")
        expected = hmac.new(
            self._config.api_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)


class WebhookVerificationError(Exception):
    """Raised when webhook signature verification fails."""
    pass


# =============================================================================
# REGISTERING YOUR PROVIDER
# =============================================================================

# In your PSP configuration:
from payroll_engine.psp import PSPConfig
from payroll_engine.psp.config import ProviderConfig

config = PSPConfig(
    # ... other config ...
    providers=[
        ProviderConfig(
            name="my_provider",
            rail="ach",
            provider_class=MyPaymentProvider,  # Your class
            credentials={
                "api_key": "...",
                "api_secret": "...",
                "base_url": "https://api.myprovider.com",
            },
        ),
    ],
)
```

## Key Points

### Idempotency

Your provider MUST handle idempotency:
- PSP passes the same `idempotency_key` on retries
- Your provider should detect duplicates
- Return the original result for duplicates, not an error

```python
def submit_payment(self, ..., idempotency_key: str, ...):
    # Most APIs support idempotency keys
    response = self._client.create_payment(
        ...,
        idempotency_key=idempotency_key,  # Pass through
    )
```

### Error Classification

PSP needs to know if errors are retryable:

```python
return SubmitResult(
    success=False,
    error_code="RATE_LIMITED",
    error_message="Too many requests",
    retryable=True,  # PSP can retry
)

return SubmitResult(
    success=False,
    error_code="INVALID_ACCOUNT",
    error_message="Account number invalid",
    retryable=False,  # Don't retry - will always fail
)
```

### Webhook Security

Never trust unverified webhooks:
1. Verify the signature using your provider's method
2. Check the source IP if possible
3. Validate the event structure
4. Handle replay attacks (check timestamps)

```python
def parse_webhook(self, payload: bytes, headers: dict) -> ...:
    # ALWAYS verify first
    if not self._verify_signature(payload, headers):
        raise WebhookVerificationError(...)

    # Then parse
    ...
```

### Status Mapping

Map provider-specific statuses to PSP statuses:

| Provider Status | PSP Status | Meaning |
|-----------------|------------|---------|
| pending | PENDING | Submitted, not processed |
| processing | PROCESSING | Bank is working on it |
| completed | SETTLED | Funds arrived |
| failed | FAILED | Submission failed |
| returned | RETURNED | Bank returned the payment |

## Testing Your Provider

```python
"""Test your provider with PSP's test harness."""

import pytest
from decimal import Decimal
from uuid import uuid4

from my_provider import MyPaymentProvider, MyProviderConfig


@pytest.fixture
def provider():
    """Create provider with test credentials."""
    return MyPaymentProvider(
        MyProviderConfig(
            api_key="test_key",
            api_secret="test_secret",
            base_url="https://sandbox.myprovider.com",
        )
    )


def test_submit_payment_success(provider):
    """Test successful payment submission."""
    result = provider.submit_payment(
        instruction_id=uuid4(),
        amount=Decimal("100.00"),
        payee_account="123456789",
        payee_routing="021000021",
        payee_name="Test User",
        rail="ach",
        idempotency_key=f"test-{uuid4()}",
    )

    assert result.success
    assert result.provider_ref is not None


def test_submit_payment_idempotency(provider):
    """Test that duplicate submissions return same result."""
    key = f"test-{uuid4()}"

    result1 = provider.submit_payment(
        ...,
        idempotency_key=key,
    )
    result2 = provider.submit_payment(
        ...,
        idempotency_key=key,  # Same key
    )

    assert result1.provider_ref == result2.provider_ref


def test_webhook_verification(provider):
    """Test webhook signature verification."""
    payload = b'{"type": "payment.completed", ...}'

    # Valid signature
    valid_headers = {"X-Webhook-Signature": compute_valid_signature(payload)}
    result = provider.parse_webhook(payload, valid_headers)
    assert result is not None

    # Invalid signature
    invalid_headers = {"X-Webhook-Signature": "bad_signature"}
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(payload, invalid_headers)
```

## Common Provider Patterns

### Retry with Exponential Backoff
```python
def submit_payment(self, ...):
    for attempt in range(self._config.retry_attempts):
        try:
            return self._do_submit(...)
        except RetryableError:
            if attempt == self._config.retry_attempts - 1:
                raise
            time.sleep(2 ** attempt)  # 1s, 2s, 4s, ...
```

### Circuit Breaker
```python
def submit_payment(self, ...):
    if self._circuit_open:
        return SubmitResult(
            success=False,
            error_code="CIRCUIT_OPEN",
            error_message="Provider temporarily unavailable",
            retryable=True,
        )
    # ... normal submission
```

### Mock Provider for Testing
```python
class MockProvider(PaymentProvider):
    """In-memory provider for testing."""

    def __init__(self):
        self.payments = {}
        self.fail_next = False

    def submit_payment(self, instruction_id, ...):
        if self.fail_next:
            self.fail_next = False
            return SubmitResult(success=False, ...)

        ref = f"mock-{uuid4()}"
        self.payments[ref] = {
            "instruction_id": instruction_id,
            "status": "pending",
        }
        return SubmitResult(success=True, provider_ref=ref)

    def settle(self, provider_ref):
        """Test helper to simulate settlement."""
        self.payments[provider_ref]["status"] = "settled"
```
