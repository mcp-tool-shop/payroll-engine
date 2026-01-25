"""General Ledger export models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from payroll_engine.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from payroll_engine.models.company import Department, Job, LegalEntity, Project, Worksite
    from payroll_engine.models.payroll import (
        DeductionCode,
        EarningCode,
        PayLineItem,
        PayRun,
    )


class GLConfig(Base, TimestampMixin):
    """GL export configuration for a legal entity."""

    __tablename__ = "gl_config"

    gl_config_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("legal_entity.legal_entity_id", ondelete="CASCADE"),
        nullable=False,
    )
    format: Mapped[str] = mapped_column(String, nullable=False)
    segmentation_rules_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    __table_args__ = (
        CheckConstraint("format IN ('csv', 'iif', 'api')", name="gl_config_format_check"),
    )

    # Relationships
    legal_entity: Mapped[LegalEntity] = relationship()
    mapping_rules: Mapped[list[GLMappingRule]] = relationship(back_populates="config")


class GLMappingRule(Base, TimestampMixin):
    """Mapping rule for GL account determination."""

    __tablename__ = "gl_mapping_rule"

    gl_mapping_rule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    gl_config_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("gl_config.gl_config_id", ondelete="CASCADE"),
        nullable=False,
    )
    line_type: Mapped[str] = mapped_column(String, nullable=False)
    earning_code_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("earning_code.earning_code_id"),
        nullable=True,
    )
    deduction_code_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("deduction_code.deduction_code_id"),
        nullable=True,
    )
    debit_account: Mapped[str] = mapped_column(String, nullable=False)
    credit_account: Mapped[str] = mapped_column(String, nullable=False)
    dimension_overrides_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    __table_args__ = (
        CheckConstraint(
            "line_type IN ('EARNING', 'DEDUCTION', 'TAX', 'EMPLOYER_TAX', 'REIMBURSEMENT')",
            name="gl_mapping_rule_type_check",
        ),
    )

    # Relationships
    config: Mapped[GLConfig] = relationship(back_populates="mapping_rules")
    earning_code: Mapped[EarningCode | None] = relationship()
    deduction_code: Mapped[DeductionCode | None] = relationship()


class GLJournalBatch(Base):
    """GL journal batch for a pay run."""

    __tablename__ = "gl_journal_batch"

    gl_journal_batch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    pay_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_run.pay_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="generated")
    generated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('generated', 'exported', 'posted', 'failed')",
            name="gl_journal_batch_status_check",
        ),
    )

    # Relationships
    pay_run: Mapped[PayRun] = relationship()
    lines: Mapped[list[GLJournalLine]] = relationship(back_populates="batch")


class GLJournalLine(Base, TimestampMixin):
    """Individual GL journal entry line."""

    __tablename__ = "gl_journal_line"

    gl_journal_line_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    gl_journal_batch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("gl_journal_batch.gl_journal_batch_id", ondelete="CASCADE"),
        nullable=False,
    )
    account_string: Mapped[str] = mapped_column(String, nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, default=Decimal("0"))
    department_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("department.department_id"),
        nullable=True,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("job.job_id"),
        nullable=True,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("project.project_id"),
        nullable=True,
    )
    worksite_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("worksite.worksite_id"),
        nullable=True,
    )
    source_pay_line_item_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pay_line_item.pay_line_item_id"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "(debit = 0 AND credit <> 0) OR (credit = 0 AND debit <> 0)",
            name="gl_journal_line_debit_credit_check",
        ),
    )

    # Relationships
    batch: Mapped[GLJournalBatch] = relationship(back_populates="lines")
    department: Mapped[Department | None] = relationship()
    job: Mapped[Job | None] = relationship()
    project: Mapped[Project | None] = relationship()
    worksite: Mapped[Worksite | None] = relationship()
    source_line_item: Mapped[PayLineItem | None] = relationship()
