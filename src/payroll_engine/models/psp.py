"""PSP (Payment Service Provider) models.

Covers the financial sub-ledger for payroll service provider operations:
- Bank accounts (settlement accounts)
- Ledger accounts (logical buckets)
- Ledger entries (append-only double-entry)
- Reservations
- Settlement events
- Tax liabilities
- Third-party obligations
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from payroll_engine.models.base import Base

if TYPE_CHECKING:
    from payroll_engine.models.company import LegalEntity, Tenant
    from payroll_engine.models.payroll import PayRun, PayStatement


class PspBankAccount(Base):
    """PSP-owned settlement accounts at banks/processors.

    Stores tokenized references - never raw account numbers.
    """

    __tablename__ = "psp_bank_account"

    psp_bank_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    bank_name: Mapped[str] = mapped_column(Text, nullable=False)
    bank_account_ref_token: Mapped[str] = mapped_column(Text, nullable=False)
    rail_support_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="psp_bank_account_status_ck"),
        UniqueConstraint("tenant_id", "bank_account_ref_token", name="psp_bank_account_token_uq"),
    )

    # Relationships
    settlement_events: Mapped[list["PspSettlementEvent"]] = relationship(
        "PspSettlementEvent", back_populates="bank_account"
    )


class PspLedgerAccount(Base):
    """Logical ledger accounts (client buckets).

    Each (tenant, legal_entity, account_type, currency) is unique.
    """

    __tablename__ = "psp_ledger_account"

    psp_ledger_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    account_type: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            """account_type IN (
                'client_funding_clearing',
                'client_net_pay_payable',
                'client_tax_impound_payable',
                'client_third_party_payable',
                'psp_fees_revenue',
                'psp_settlement_clearing'
            )""",
            name="psp_ledger_account_type_ck",
        ),
        CheckConstraint("status IN ('active', 'closed')", name="psp_ledger_account_status_ck"),
        UniqueConstraint(
            "tenant_id", "legal_entity_id", "account_type", "currency",
            name="psp_ledger_account_uq"
        ),
        Index("psp_ledger_account_by_tenant", "tenant_id", "legal_entity_id"),
    )

    # Relationships
    debit_entries: Mapped[list["PspLedgerEntry"]] = relationship(
        "PspLedgerEntry",
        foreign_keys="PspLedgerEntry.debit_account_id",
        back_populates="debit_account",
    )
    credit_entries: Mapped[list["PspLedgerEntry"]] = relationship(
        "PspLedgerEntry",
        foreign_keys="PspLedgerEntry.credit_account_id",
        back_populates="credit_account",
    )


class PspLedgerEntry(Base):
    """Append-only double-entry ledger postings.

    CRITICAL: This table is append-only. DB triggers prevent UPDATE/DELETE.
    Use reversal entries to correct errors.
    """

    __tablename__ = "psp_ledger_entry"

    psp_ledger_entry_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    entry_type: Mapped[str] = mapped_column(Text, nullable=False)
    debit_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("psp_ledger_account.psp_ledger_account_id"),
        nullable=False,
    )
    credit_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("psp_ledger_account.psp_ledger_account_id"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    correlation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            """entry_type IN (
                'funding_received', 'funding_returned',
                'reserve_created', 'reserve_released',
                'employee_payment_initiated', 'employee_payment_settled', 'employee_payment_failed',
                'tax_payment_initiated', 'tax_payment_settled',
                'third_party_payment_initiated', 'third_party_payment_settled',
                'fee_assessed', 'reversal'
            )""",
            name="psp_ledger_entry_type_ck",
        ),
        CheckConstraint("amount > 0", name="psp_ledger_entry_amount_ck"),
        UniqueConstraint("tenant_id", "idempotency_key", name="psp_ledger_entry_idem_uq"),
        Index("psp_ledger_entry_by_source", "tenant_id", "source_type", "source_id"),
        Index("psp_ledger_entry_by_accounts", "debit_account_id", "credit_account_id", "posted_at"),
    )

    # Relationships
    debit_account: Mapped["PspLedgerAccount"] = relationship(
        "PspLedgerAccount",
        foreign_keys=[debit_account_id],
        back_populates="debit_entries",
    )
    credit_account: Mapped["PspLedgerAccount"] = relationship(
        "PspLedgerAccount",
        foreign_keys=[credit_account_id],
        back_populates="credit_entries",
    )
    settlement_links: Mapped[list["PspSettlementLink"]] = relationship(
        "PspSettlementLink", back_populates="ledger_entry"
    )


class PspReservation(Base):
    """Funds held for specific obligations.

    Reservations prevent overspend without moving money externally.
    """

    __tablename__ = "psp_reservation"

    psp_reservation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reserve_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    correlation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "reserve_type IN ('net_pay', 'tax', 'third_party', 'fees')",
            name="psp_reservation_type_ck",
        ),
        CheckConstraint("amount > 0", name="psp_reservation_amount_ck"),
        CheckConstraint(
            "status IN ('active', 'released', 'consumed')",
            name="psp_reservation_status_ck",
        ),
        Index("psp_reservation_open", "tenant_id", "legal_entity_id", "reserve_type", "status"),
    )


class PspSettlementEvent(Base):
    """Bank/processor settlement results - the truth of what happened."""

    __tablename__ = "psp_settlement_event"

    psp_settlement_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    psp_bank_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("psp_bank_account.psp_bank_account_id"),
        nullable=False,
    )
    rail: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    external_trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    raw_payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "rail IN ('ach', 'wire', 'rtp', 'fednow', 'check', 'internal')",
            name="psp_settlement_event_rail_ck",
        ),
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="psp_settlement_event_direction_ck",
        ),
        CheckConstraint("amount > 0", name="psp_settlement_event_amount_ck"),
        CheckConstraint(
            "status IN ('created', 'submitted', 'accepted', 'settled', 'failed', 'reversed')",
            name="psp_settlement_event_status_ck",
        ),
        UniqueConstraint(
            "psp_bank_account_id", "external_trace_id",
            name="psp_settlement_event_trace_uq"
        ),
        Index("psp_settlement_event_status", "status", "effective_date"),
    )

    # Relationships
    bank_account: Mapped["PspBankAccount"] = relationship(
        "PspBankAccount", back_populates="settlement_events"
    )
    links: Mapped[list["PspSettlementLink"]] = relationship(
        "PspSettlementLink", back_populates="settlement_event"
    )


class PspSettlementLink(Base):
    """Links settlement events to ledger entries (many-to-many)."""

    __tablename__ = "psp_settlement_link"

    psp_settlement_link_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    psp_settlement_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("psp_settlement_event.psp_settlement_event_id"),
        nullable=False,
    )
    psp_ledger_entry_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("psp_ledger_entry.psp_ledger_entry_id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        UniqueConstraint(
            "psp_settlement_event_id", "psp_ledger_entry_id",
            name="psp_settlement_link_uq"
        ),
    )

    # Relationships
    settlement_event: Mapped["PspSettlementEvent"] = relationship(
        "PspSettlementEvent", back_populates="links"
    )
    ledger_entry: Mapped["PspLedgerEntry"] = relationship(
        "PspLedgerEntry", back_populates="settlement_links"
    )


class TaxLiability(Base):
    """Tax liabilities derived from committed payroll.

    Created per agency/jurisdiction from committed pay_line_items.
    """

    __tablename__ = "tax_liability"

    tax_liability_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    jurisdiction_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tax_agency_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tax_type: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    source_pay_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'reserved', 'paid', 'amended', 'voided')",
            name="tax_liability_status_ck",
        ),
        Index("tax_liability_due", "legal_entity_id", "due_date"),
        Index("tax_liability_period", "tax_agency_id", "period_end"),
    )


class ThirdPartyObligation(Base):
    """Third-party obligations (401k, HSA, garnishments, union dues, etc.)."""

    __tablename__ = "third_party_obligation"

    third_party_obligation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    obligation_type: Mapped[str] = mapped_column(Text, nullable=False)
    payee_profile_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    source_pay_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    source_pay_statement_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    __table_args__ = (
        CheckConstraint("amount >= 0", name="third_party_obligation_amount_ck"),
        CheckConstraint(
            "status IN ('open', 'reserved', 'paid', 'failed', 'voided')",
            name="third_party_obligation_status_ck",
        ),
        Index("third_party_obligation_due", "tenant_id", "legal_entity_id", "status", "due_date"),
    )
