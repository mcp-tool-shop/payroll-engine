"""Pydantic schemas for API request/response models."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Base schemas
# ============================================================================


class TenantBase(BaseModel):
    """Base tenant schema."""

    model_config = ConfigDict(from_attributes=True)


class CompanyBase(BaseModel):
    """Base company schema."""

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Pay Run schemas
# ============================================================================


class PayRunCreate(BaseModel):
    """Schema for creating a new pay run."""

    legal_entity_id: UUID
    pay_schedule_id: UUID
    period_start: date
    period_end: date
    check_date: date
    run_type: str = "regular"


class PayRunResponse(BaseModel):
    """Schema for pay run response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    legal_entity_id: UUID
    pay_schedule_id: UUID
    period_start: date
    period_end: date
    check_date: date
    run_type: str
    status: str
    approved_at: datetime | None = None
    approved_by: UUID | None = None
    committed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PayRunListResponse(BaseModel):
    """Schema for listing pay runs."""

    items: list[PayRunResponse]
    total: int
    page: int
    page_size: int


# ============================================================================
# Pay Run Employee schemas
# ============================================================================


class PayRunEmployeeResponse(BaseModel):
    """Schema for pay run employee response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pay_run_id: UUID
    employee_id: UUID
    employment_id: UUID
    gross: Decimal | None = None
    net: Decimal | None = None
    status: str
    employee_name: str | None = None


class PayRunEmployeeListResponse(BaseModel):
    """Schema for listing pay run employees."""

    items: list[PayRunEmployeeResponse]
    total: int


# ============================================================================
# Preview schemas
# ============================================================================


class PreviewLineItem(BaseModel):
    """Schema for a preview line item."""

    category: str
    code: str
    description: str
    amount: Decimal
    hours: Decimal | None = None
    rate: Decimal | None = None
    jurisdiction: str | None = None


class EmployeePreview(BaseModel):
    """Schema for employee preview results."""

    employee_id: UUID
    employee_name: str
    gross: Decimal
    net: Decimal
    earnings: list[PreviewLineItem]
    deductions: list[PreviewLineItem]
    taxes: list[PreviewLineItem]
    employer_taxes: list[PreviewLineItem]


class PreviewResponse(BaseModel):
    """Schema for preview response."""

    pay_run_id: UUID
    calculation_id: UUID
    status: str
    employees: list[EmployeePreview]
    total_gross: Decimal
    total_net: Decimal
    total_employer_taxes: Decimal
    computed_at: datetime


# ============================================================================
# Commit schemas
# ============================================================================


class CommitResponse(BaseModel):
    """Schema for commit response."""

    pay_run_id: UUID
    status: str
    statements_created: int
    line_items_created: int
    committed_at: datetime


# ============================================================================
# Approval schemas
# ============================================================================


class ApprovalRequest(BaseModel):
    """Schema for approval request."""

    approver_id: UUID


class ApprovalResponse(BaseModel):
    """Schema for approval response."""

    pay_run_id: UUID
    status: str
    approved_at: datetime
    approved_by: UUID
    inputs_locked: int


# ============================================================================
# Reopen schemas
# ============================================================================


class ReopenResponse(BaseModel):
    """Schema for reopen response."""

    pay_run_id: UUID
    status: str
    inputs_unlocked: int
    reopened_at: datetime


# ============================================================================
# Pay Statement schemas
# ============================================================================


class PayLineItemResponse(BaseModel):
    """Schema for pay line item response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pay_statement_id: UUID
    category: str
    code: str
    description: str
    amount: Decimal
    hours: Decimal | None = None
    rate: Decimal | None = None
    jurisdiction: str | None = None
    rule_version_id: UUID | None = None


class PayStatementResponse(BaseModel):
    """Schema for pay statement response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pay_run_employee_id: UUID
    calculation_id: UUID
    gross_pay: Decimal
    net_pay: Decimal
    total_taxes: Decimal
    total_deductions: Decimal
    total_employer_taxes: Decimal
    check_number: str | None = None
    line_items: list[PayLineItemResponse] = []


# ============================================================================
# Error schemas
# ============================================================================


class ErrorResponse(BaseModel):
    """Schema for error response."""

    detail: str
    code: str | None = None
    context: dict[str, Any] | None = None


class ValidationErrorResponse(BaseModel):
    """Schema for validation error response."""

    detail: list[dict[str, Any]]
