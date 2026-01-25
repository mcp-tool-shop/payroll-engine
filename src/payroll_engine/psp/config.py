"""PSP Configuration Objects.

Explicit configuration for PSP. No defaults that move money.

Pattern:
    psp = PSP(
        session=session,
        config=PSPConfig(
            tenant_id=...,
            ledger=LedgerConfig(...),
            funding_gate=FundingGateConfig(...),
            providers=[ACHProvider(...), FedNowProvider(...)],
            event_store=EventStoreConfig(...),
        ),
    )

Rules:
    1. No env vars. Configuration is explicit.
    2. No globals. Each PSP instance has its own config.
    3. No hidden defaults that move money.
    4. Immutable after creation (frozen dataclasses).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class LedgerConfig:
    """
    Ledger behavior configuration.

    Attributes:
        require_balanced_entries: If True, every ledger entry must have
            equal debits and credits. Default True.
        allow_negative_balances: If True, accounts can go negative.
            WARNING: Only enable for liability accounts. Default False.
        enable_reservations: If True, balance checks consider reservations.
            Default True.
    """

    require_balanced_entries: bool = True
    allow_negative_balances: bool = False
    enable_reservations: bool = True


@dataclass(frozen=True)
class FundingGateConfig:
    """
    Funding gate configuration.

    The funding gate has two checkpoints:
    1. Commit Gate - Validates funding before creating payment instructions
    2. Pay Gate - Validates funding before submitting to rails (ALWAYS enforced)

    Attributes:
        commit_gate_enabled: If True, validate funding at commit time.
            Can be disabled for testing. Default True.
        pay_gate_enabled: If True, validate funding before payment execution.
            WARNING: MUST be True in production. There is no bypass.
            Default True.
        reservation_ttl_hours: How long reservations remain valid.
            After expiry, funds are released. Default 48 hours.
        allow_partial_funding: If True, allow partial batch execution
            when full funding is unavailable. Default False.
    """

    commit_gate_enabled: bool = True
    pay_gate_enabled: bool = True  # NEVER disable in production
    reservation_ttl_hours: int = 48
    allow_partial_funding: bool = False

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.reservation_ttl_hours < 1:
            raise ValueError("reservation_ttl_hours must be at least 1")
        if self.reservation_ttl_hours > 168:  # 1 week
            raise ValueError("reservation_ttl_hours cannot exceed 168 (1 week)")


@dataclass(frozen=True)
class ProviderConfig:
    """
    Payment provider configuration.

    Attributes:
        name: Unique identifier for this provider instance.
        provider_type: Type of provider ("ach", "fednow", "wire", "rtp").
        sandbox: If True, use sandbox/test environment. Default True.
        credentials: Provider-specific credentials. Structure varies by provider.
        webhook_secret: Secret for validating incoming webhooks.
        timeout_seconds: Request timeout. Default 30.
        retry_count: Number of retries on failure. Default 3.
    """

    name: str
    provider_type: str
    sandbox: bool = True
    credentials: dict[str, str] = field(default_factory=dict)
    webhook_secret: str | None = None
    timeout_seconds: int = 30
    retry_count: int = 3

    def __post_init__(self) -> None:
        """Validate configuration."""
        valid_types = {"ach", "fednow", "wire", "rtp", "check"}
        if self.provider_type not in valid_types:
            raise ValueError(f"provider_type must be one of {valid_types}")
        if not self.name:
            raise ValueError("name is required")


@dataclass(frozen=True)
class EventStoreConfig:
    """
    Event store configuration.

    Attributes:
        retention_days: Days to retain events. None = forever. Default None.
        enable_replay: If True, allow event replay. Default True.
        enable_subscriptions: If True, allow event subscriptions. Default True.
        batch_size: Number of events to fetch per query. Default 1000.
    """

    retention_days: int | None = None
    enable_replay: bool = True
    enable_subscriptions: bool = True
    batch_size: int = 1000

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.retention_days is not None and self.retention_days < 1:
            raise ValueError("retention_days must be at least 1 or None")
        if self.batch_size < 1 or self.batch_size > 10000:
            raise ValueError("batch_size must be between 1 and 10000")


@dataclass(frozen=True)
class ReconciliationConfig:
    """
    Reconciliation configuration.

    Attributes:
        auto_match: If True, automatically match settlements to payments.
            Default True.
        match_tolerance_cents: Amount tolerance for fuzzy matching.
            Default 0 (exact match required).
        unmatched_alert_threshold: Alert if unmatched count exceeds this.
            Default 10.
        stale_payment_days: Days after which unmatched payments are flagged.
            Default 7.
    """

    auto_match: bool = True
    match_tolerance_cents: int = 0
    unmatched_alert_threshold: int = 10
    stale_payment_days: int = 7


@dataclass(frozen=True)
class LiabilityConfig:
    """
    Liability classification configuration.

    Attributes:
        auto_classify: If True, automatically classify liability on returns.
            Default True.
        default_recovery_path: Default recovery path when classification
            is ambiguous. Default "manual_review".
        employer_return_codes: Return codes that default to employer liability.
        platform_return_codes: Return codes that default to platform liability.
    """

    auto_classify: bool = True
    default_recovery_path: str = "manual_review"
    employer_return_codes: tuple[str, ...] = ("R01", "R02", "R03", "R04", "R07", "R08", "R10")
    platform_return_codes: tuple[str, ...] = ("R05", "R06", "R09")


@dataclass(frozen=True)
class PSPConfig:
    """
    Complete PSP configuration.

    This is the top-level configuration object. All behavior is explicit.
    There are no environment variables, no hidden defaults.

    Example:
        config = PSPConfig(
            tenant_id=UUID("..."),
            legal_entity_id=UUID("..."),
            ledger=LedgerConfig(),
            funding_gate=FundingGateConfig(
                pay_gate_enabled=True,  # NEVER False in production
            ),
            providers=[
                ProviderConfig(
                    name="primary_ach",
                    provider_type="ach",
                    sandbox=False,
                    credentials={"api_key": "..."},
                ),
            ],
            event_store=EventStoreConfig(),
        )

    Attributes:
        tenant_id: The tenant this PSP instance operates for.
        legal_entity_id: The legal entity for accounting purposes.
        ledger: Ledger configuration.
        funding_gate: Funding gate configuration.
        providers: List of payment provider configurations.
        event_store: Event store configuration.
        reconciliation: Reconciliation configuration.
        liability: Liability classification configuration.
    """

    tenant_id: UUID
    legal_entity_id: UUID
    ledger: LedgerConfig
    funding_gate: FundingGateConfig
    providers: list[ProviderConfig]
    event_store: EventStoreConfig
    reconciliation: ReconciliationConfig = field(default_factory=ReconciliationConfig)
    liability: LiabilityConfig = field(default_factory=LiabilityConfig)

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not self.providers:
            raise ValueError("At least one provider is required")

        # Check for duplicate provider names
        names = [p.name for p in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("Provider names must be unique")

    def get_provider(self, name: str) -> ProviderConfig | None:
        """Get provider config by name."""
        for provider in self.providers:
            if provider.name == name:
                return provider
        return None

    def get_providers_by_type(self, provider_type: str) -> list[ProviderConfig]:
        """Get all providers of a given type."""
        return [p for p in self.providers if p.provider_type == provider_type]


# =============================================================================
# Configuration Builders (Optional Convenience)
# =============================================================================


def create_sandbox_config(
    tenant_id: UUID,
    legal_entity_id: UUID,
) -> PSPConfig:
    """
    Create a sandbox configuration for testing.

    This is a CONVENIENCE method. All values are still explicit.
    Use this only for development and testing.

    Args:
        tenant_id: Tenant UUID
        legal_entity_id: Legal entity UUID

    Returns:
        PSPConfig configured for sandbox use
    """
    return PSPConfig(
        tenant_id=tenant_id,
        legal_entity_id=legal_entity_id,
        ledger=LedgerConfig(),
        funding_gate=FundingGateConfig(
            commit_gate_enabled=True,
            pay_gate_enabled=True,
        ),
        providers=[
            ProviderConfig(
                name="ach_sandbox",
                provider_type="ach",
                sandbox=True,
            ),
            ProviderConfig(
                name="fednow_sandbox",
                provider_type="fednow",
                sandbox=True,
            ),
        ],
        event_store=EventStoreConfig(),
    )


def validate_production_config(config: PSPConfig) -> list[str]:
    """
    Validate that a configuration is safe for production.

    Returns a list of warnings/errors. Empty list = safe.

    Args:
        config: The configuration to validate

    Returns:
        List of warning/error messages
    """
    issues: list[str] = []

    # Pay gate MUST be enabled
    if not config.funding_gate.pay_gate_enabled:
        issues.append("CRITICAL: pay_gate_enabled is False. This allows payments without funding.")

    # Check for sandbox providers
    sandbox_providers = [p.name for p in config.providers if p.sandbox]
    if sandbox_providers:
        issues.append(f"WARNING: Sandbox providers enabled: {sandbox_providers}")

    # Check webhook secrets
    for provider in config.providers:
        if not provider.sandbox and not provider.webhook_secret:
            issues.append(f"WARNING: Provider '{provider.name}' has no webhook_secret")

    # Check credentials
    for provider in config.providers:
        if not provider.sandbox and not provider.credentials:
            issues.append(f"WARNING: Provider '{provider.name}' has no credentials")

    return issues
