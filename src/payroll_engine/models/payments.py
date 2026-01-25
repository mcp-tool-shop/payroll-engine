"""Payment and disbursement models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from payroll_engine.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from payroll_engine.models.employee import Employee
    from payroll_engine.models.payroll import PayRun, PayStatement


class EmployeePaymentAccount(Base, TimestampMixin):
    """Employee bank account or paycard for direct deposit."""

    __tablename__ = "employee_payment_account"

    employee_payment_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    employee_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employee_id", ondelete="CASCADE"),
        nullable=False,
    )
    payment_type: Mapped[str] = mapped_column(String, nullable=False)
    tokenized_account_ref: Mapped[str] = mapped_column(String, nullable=False)
    split_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    split_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    effective_start: Mapped[date] = mapped_column(Date, nullable=False)
    effective_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "payment_type IN ('ach', 'paycard')",
            name="employee_payment_account_type_check",
        ),
        CheckConstraint(
            "effective_end IS NULL OR effective_end >= effective_start",
            name="employee_payment_account_dates_check",
        ),
        CheckConstraint(
            "split_percent IS NULL OR split_amount IS NULL",
            name="employee_payment_account_split_check",
        ),
    )

    # Relationships
    employee: Mapped[Employee] = relationship()

    def is_active_on(self, as_of_date: date) -> bool:
        """Check if account is active on a given date."""
        if self.effective_start > as_of_date:
            return False
        if self.effective_end is not None and self.effective_end < as_of_date:
            return False
        return True


class PaymentBatch(Base, TimestampMixin):
    """Payment batch for a pay run."""

    __tablename__ = "payment_batch"

    payment_batch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    pay_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_run.pay_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    processor: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="created")
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), nullable=False, default=Decimal("0")
    )

    __table_args__ = (
        UniqueConstraint("pay_run_id", "processor", name="payment_batch_one_per_run"),
        CheckConstraint(
            "status IN ('created', 'submitted', 'settled', 'failed')",
            name="payment_batch_status_check",
        ),
    )

    # Relationships
    pay_run: Mapped[PayRun] = relationship()
    items: Mapped[list[PaymentBatchItem]] = relationship(back_populates="batch")


class PaymentBatchItem(Base, TimestampMixin):
    """Individual payment within a batch."""

    __tablename__ = "payment_batch_item"

    payment_batch_item_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    payment_batch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("payment_batch.payment_batch_id", ondelete="CASCADE"),
        nullable=False,
    )
    pay_statement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_statement.pay_statement_id", ondelete="CASCADE"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")

    __table_args__ = (
        UniqueConstraint(
            "payment_batch_id", "pay_statement_id", name="payment_batch_item_unique"
        ),
        CheckConstraint(
            "status IN ('queued', 'sent', 'failed', 'settled')",
            name="payment_batch_item_status_check",
        ),
    )

    # Relationships
    batch: Mapped[PaymentBatch] = relationship(back_populates="items")
    statement: Mapped[PayStatement] = relationship()


# =============================================================================
# PSP Payment Models (from psp_build_pack_v2)
# =============================================================================

from sqlalchemy import CHAR, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB


class PaymentInstruction(Base):
    """Payment intent - what we intend to do.

    Idempotent by (tenant_id, idempotency_key).
    """

    __tablename__ = "payment_instruction"

    payment_instruction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    payee_type: Mapped[str] = mapped_column(Text, nullable=False)
    payee_ref_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requested_settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="created")
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('employee_net', 'tax_remit', 'third_party', 'refund', 'fee', 'funding_debit')",
            name="payment_instruction_purpose_ck",
        ),
        CheckConstraint("direction IN ('outbound', 'inbound')", name="payment_instruction_direction_ck"),
        CheckConstraint("amount > 0", name="payment_instruction_amount_ck"),
        CheckConstraint(
            "payee_type IN ('employee', 'agency', 'provider', 'client')",
            name="payment_instruction_payee_type_ck",
        ),
        CheckConstraint(
            "status IN ('created', 'queued', 'submitted', 'accepted', 'settled', 'failed', 'reversed', 'canceled')",
            name="payment_instruction_status_ck",
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="payment_instruction_idem_uq"),
        Index("payment_instruction_status", "tenant_id", "legal_entity_id", "status", "requested_settlement_date"),
    )

    # Relationships
    attempts: Mapped[list["PaymentAttempt"]] = relationship("PaymentAttempt", back_populates="instruction")


class PaymentAttempt(Base):
    """Provider-specific payment attempt.

    Records each submission to a payment rail provider.
    """

    __tablename__ = "payment_attempt"

    payment_attempt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    payment_instruction_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("payment_instruction.payment_instruction_id"),
        nullable=False,
    )
    rail: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_request_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    request_payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("rail IN ('ach', 'wire', 'rtp', 'fednow', 'check')", name="payment_attempt_rail_ck"),
        CheckConstraint("status IN ('submitted', 'accepted', 'failed')", name="payment_attempt_status_ck"),
        UniqueConstraint("provider", "provider_request_id", name="payment_attempt_provider_uq"),
    )

    # Relationships
    instruction: Mapped["PaymentInstruction"] = relationship("PaymentInstruction", back_populates="attempts")


class FundingRequest(Base):
    """Funding request (client -> PSP pull).

    Idempotent by (tenant_id, idempotency_key).
    """

    __tablename__ = "funding_request"

    funding_request_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    funding_model: Mapped[str] = mapped_column(Text, nullable=False)
    rail: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False, server_default="inbound")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    requested_settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="created")
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "funding_model IN ('prefund_all', 'net_only', 'net_and_third_party', 'split_schedule')",
            name="funding_request_model_ck",
        ),
        CheckConstraint("rail IN ('ach', 'wire', 'rtp', 'fednow')", name="funding_request_rail_ck"),
        CheckConstraint("direction IN ('inbound')", name="funding_request_direction_ck"),
        CheckConstraint("amount > 0", name="funding_request_amount_ck"),
        CheckConstraint(
            "status IN ('created', 'submitted', 'accepted', 'settled', 'failed', 'returned', 'canceled')",
            name="funding_request_status_ck",
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="funding_request_idem_uq"),
        Index("funding_request_status", "tenant_id", "legal_entity_id", "status", "requested_settlement_date"),
    )

    # Relationships
    events: Mapped[list["FundingEvent"]] = relationship("FundingEvent", back_populates="funding_request")


class FundingEvent(Base):
    """Funding event (settlement/return updates)."""

    __tablename__ = "funding_event"

    funding_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    funding_request_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("funding_request.funding_request_id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    external_trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    raw_payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('submitted', 'accepted', 'settled', 'failed', 'returned')",
            name="funding_event_status_ck",
        ),
        CheckConstraint("amount > 0", name="funding_event_amount_ck"),
        UniqueConstraint("funding_request_id", "external_trace_id", name="funding_event_trace_uq"),
    )

    # Relationships
    funding_request: Mapped["FundingRequest"] = relationship("FundingRequest", back_populates="events")


class FundingGateEvaluation(Base):
    """Funding gate evaluation record.

    Records the outcome of commit/pay gate checks.
    Idempotent by (tenant_id, idempotency_key).
    """

    __tablename__ = "funding_gate_evaluation"

    funding_gate_evaluation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    pay_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    gate_type: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    required_amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    available_amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    reasons_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("gate_type IN ('commit_gate', 'pay_gate')", name="funding_gate_evaluation_type_ck"),
        CheckConstraint("outcome IN ('pass', 'soft_fail', 'hard_fail')", name="funding_gate_evaluation_outcome_ck"),
        UniqueConstraint("tenant_id", "idempotency_key", name="funding_gate_evaluation_idem_uq"),
        Index("funding_gate_eval_by_run", "tenant_id", "pay_run_id", "gate_type", "evaluated_at"),
    )
