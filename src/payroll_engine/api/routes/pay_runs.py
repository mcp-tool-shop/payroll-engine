"""Pay run API endpoints."""

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from payroll_engine.api.dependencies import DbSession, TenantId
from payroll_engine.api.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    CommitResponse,
    EmployeePreview,
    ErrorResponse,
    PayLineItemResponse,
    PayRunCreate,
    PayRunEmployeeListResponse,
    PayRunEmployeeResponse,
    PayRunListResponse,
    PayRunResponse,
    PayStatementResponse,
    PreviewLineItem,
    PreviewResponse,
    ReopenResponse,
)
from payroll_engine.models.employee import Employee, Employment
from payroll_engine.models.payroll import PayLineItem, PayRun, PayRunEmployee, PayStatement
from payroll_engine.services.commit_service import CommitService
from payroll_engine.services.locking_service import LockingService
from payroll_engine.services.pay_run_service import PayRunService
from payroll_engine.services.state_machine import PayRunStateMachine, StateTransitionError

router = APIRouter(prefix="/pay-runs", tags=["pay-runs"])


# ============================================================================
# Pay Run CRUD
# ============================================================================


@router.post(
    "",
    response_model=PayRunResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}},
)
async def create_pay_run(
    db: DbSession,
    tenant_id: TenantId,
    payload: PayRunCreate,
) -> PayRunResponse:
    """Create a new pay run in draft status."""
    pay_run = PayRun(
        tenant_id=tenant_id,
        legal_entity_id=payload.legal_entity_id,
        pay_schedule_id=payload.pay_schedule_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        check_date=payload.check_date,
        run_type=payload.run_type,
        status="draft",
    )
    db.add(pay_run)
    await db.commit()
    await db.refresh(pay_run)
    return PayRunResponse.model_validate(pay_run)


@router.get(
    "",
    response_model=PayRunListResponse,
    responses={400: {"model": ErrorResponse}},
)
async def list_pay_runs(
    db: DbSession,
    tenant_id: TenantId,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    legal_entity_id: UUID | None = None,
) -> PayRunListResponse:
    """List pay runs for a tenant with optional filters."""
    query = select(PayRun).where(PayRun.tenant_id == tenant_id)

    if status_filter:
        query = query.where(PayRun.status == status_filter)
    if legal_entity_id:
        query = query.where(PayRun.legal_entity_id == legal_entity_id)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Apply pagination
    query = query.order_by(PayRun.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    pay_runs = result.scalars().all()

    return PayRunListResponse(
        items=[PayRunResponse.model_validate(pr) for pr in pay_runs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{pay_run_id}",
    response_model=PayRunResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_pay_run(
    db: DbSession,
    tenant_id: TenantId,
    pay_run_id: Annotated[UUID, Path()],
) -> PayRunResponse:
    """Get a specific pay run by ID."""
    pay_run = await db.get(PayRun, pay_run_id)
    if not pay_run or pay_run.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay run not found",
        )
    return PayRunResponse.model_validate(pay_run)


@router.get(
    "/{pay_run_id}/employees",
    response_model=PayRunEmployeeListResponse,
    responses={404: {"model": ErrorResponse}},
)
async def list_pay_run_employees(
    db: DbSession,
    tenant_id: TenantId,
    pay_run_id: Annotated[UUID, Path()],
) -> PayRunEmployeeListResponse:
    """List employees in a pay run."""
    pay_run = await db.get(PayRun, pay_run_id)
    if not pay_run or pay_run.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay run not found",
        )

    query = (
        select(PayRunEmployee, Employee.first_name, Employee.last_name)
        .join(Employment, PayRunEmployee.employment_id == Employment.id)
        .join(Employee, Employment.employee_id == Employee.id)
        .where(PayRunEmployee.pay_run_id == pay_run_id)
    )
    result = await db.execute(query)
    rows = result.all()

    items = []
    for pre, first_name, last_name in rows:
        resp = PayRunEmployeeResponse.model_validate(pre)
        resp.employee_name = f"{first_name} {last_name}"
        items.append(resp)

    return PayRunEmployeeListResponse(items=items, total=len(items))


# ============================================================================
# Pay Run State Transitions
# ============================================================================


@router.post(
    "/{pay_run_id}/preview",
    response_model=PreviewResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def preview_pay_run(
    db: DbSession,
    tenant_id: TenantId,
    pay_run_id: Annotated[UUID, Path()],
) -> PreviewResponse:
    """Generate a preview for a pay run. Idempotent and deterministic."""
    pay_run = await db.get(PayRun, pay_run_id)
    if not pay_run or pay_run.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay run not found",
        )

    # Transition to preview if in draft
    state_machine = PayRunStateMachine(pay_run)
    if pay_run.status == "draft":
        try:
            state_machine.transition_to("preview")
            await db.commit()
        except StateTransitionError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    # Run preview calculation
    service = PayRunService(db)
    try:
        preview_result = await service.preview(pay_run_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Preview calculation failed: {e}",
        )

    # Build response
    employees = []
    for emp_result in preview_result.employee_results:
        earnings = [
            PreviewLineItem(
                category=line.category,
                code=line.code,
                description=line.description,
                amount=line.amount,
                hours=line.hours,
                rate=line.rate,
            )
            for line in emp_result.lines
            if line.category == "earning"
        ]
        deductions = [
            PreviewLineItem(
                category=line.category,
                code=line.code,
                description=line.description,
                amount=line.amount,
            )
            for line in emp_result.lines
            if line.category == "deduction"
        ]
        taxes = [
            PreviewLineItem(
                category=line.category,
                code=line.code,
                description=line.description,
                amount=line.amount,
                jurisdiction=line.jurisdiction,
            )
            for line in emp_result.lines
            if line.category == "tax"
        ]
        employer_taxes = [
            PreviewLineItem(
                category=line.category,
                code=line.code,
                description=line.description,
                amount=line.amount,
                jurisdiction=line.jurisdiction,
            )
            for line in emp_result.lines
            if line.category == "employer_tax"
        ]

        employees.append(
            EmployeePreview(
                employee_id=emp_result.employee_id,
                employee_name=emp_result.employee_name,
                gross=emp_result.gross,
                net=emp_result.net,
                earnings=earnings,
                deductions=deductions,
                taxes=taxes,
                employer_taxes=employer_taxes,
            )
        )

    return PreviewResponse(
        pay_run_id=pay_run_id,
        calculation_id=preview_result.calculation_id,
        status="preview",
        employees=employees,
        total_gross=preview_result.total_gross,
        total_net=preview_result.total_net,
        total_employer_taxes=preview_result.total_employer_taxes,
        computed_at=datetime.now(timezone.utc),
    )


@router.post(
    "/{pay_run_id}/approve",
    response_model=ApprovalResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def approve_pay_run(
    db: DbSession,
    tenant_id: TenantId,
    pay_run_id: Annotated[UUID, Path()],
    payload: ApprovalRequest,
) -> ApprovalResponse:
    """Approve a pay run and lock all inputs."""
    pay_run = await db.get(PayRun, pay_run_id)
    if not pay_run or pay_run.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay run not found",
        )

    # Transition to approved
    state_machine = PayRunStateMachine(pay_run)
    try:
        state_machine.transition_to("approved")
    except StateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Lock inputs
    locking_service = LockingService(db)
    locked_count = await locking_service.lock_inputs(pay_run_id)

    # Set approval metadata
    pay_run.approved_at = datetime.now(timezone.utc)
    pay_run.approved_by = payload.approver_id

    await db.commit()

    return ApprovalResponse(
        pay_run_id=pay_run_id,
        status="approved",
        approved_at=pay_run.approved_at,
        approved_by=pay_run.approved_by,
        inputs_locked=locked_count,
    )


@router.post(
    "/{pay_run_id}/commit",
    response_model=CommitResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def commit_pay_run(
    db: DbSession,
    tenant_id: TenantId,
    pay_run_id: Annotated[UUID, Path()],
) -> CommitResponse:
    """Commit a pay run. Creates pay statements and line items. Idempotent."""
    pay_run = await db.get(PayRun, pay_run_id)
    if not pay_run or pay_run.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay run not found",
        )

    # Use commit service which handles idempotency
    commit_service = CommitService(db)
    try:
        result = await commit_service.commit(pay_run_id)
    except StateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Commit failed: {e}",
        )

    return CommitResponse(
        pay_run_id=pay_run_id,
        status="committed",
        statements_created=result.statements_created,
        line_items_created=result.line_items_created,
        committed_at=result.committed_at,
    )


@router.post(
    "/{pay_run_id}/reopen",
    response_model=ReopenResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def reopen_pay_run(
    db: DbSession,
    tenant_id: TenantId,
    pay_run_id: Annotated[UUID, Path()],
) -> ReopenResponse:
    """Reopen an approved pay run back to preview. Unlocks inputs."""
    pay_run = await db.get(PayRun, pay_run_id)
    if not pay_run or pay_run.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay run not found",
        )

    # Can only reopen from approved (not committed)
    if pay_run.status == "committed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reopen a committed pay run. Use void/reissue flow instead.",
        )

    # Transition back to preview
    state_machine = PayRunStateMachine(pay_run)
    try:
        state_machine.transition_to("preview")
    except StateTransitionError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Unlock inputs
    locking_service = LockingService(db)
    unlocked_count = await locking_service.unlock_inputs(pay_run_id)

    # Clear approval metadata
    pay_run.approved_at = None
    pay_run.approved_by = None

    await db.commit()

    return ReopenResponse(
        pay_run_id=pay_run_id,
        status="preview",
        inputs_unlocked=unlocked_count,
        reopened_at=datetime.now(timezone.utc),
    )


# ============================================================================
# Pay Statements
# ============================================================================


@router.get(
    "/{pay_run_id}/statements",
    response_model=list[PayStatementResponse],
    responses={404: {"model": ErrorResponse}},
)
async def list_pay_statements(
    db: DbSession,
    tenant_id: TenantId,
    pay_run_id: Annotated[UUID, Path()],
) -> list[PayStatementResponse]:
    """List all pay statements for a committed pay run."""
    pay_run = await db.get(PayRun, pay_run_id)
    if not pay_run or pay_run.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay run not found",
        )

    if pay_run.status not in ("committed", "paid", "voided"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pay run has not been committed yet",
        )

    query = (
        select(PayStatement)
        .join(PayRunEmployee)
        .where(PayRunEmployee.pay_run_id == pay_run_id)
        .options(selectinload(PayStatement.line_items))
    )
    result = await db.execute(query)
    statements = result.scalars().all()

    return [
        PayStatementResponse(
            id=stmt.id,
            pay_run_employee_id=stmt.pay_run_employee_id,
            calculation_id=stmt.calculation_id,
            gross_pay=stmt.gross_pay,
            net_pay=stmt.net_pay,
            total_taxes=stmt.total_taxes,
            total_deductions=stmt.total_deductions,
            total_employer_taxes=stmt.total_employer_taxes,
            check_number=stmt.check_number,
            line_items=[
                PayLineItemResponse.model_validate(li) for li in stmt.line_items
            ],
        )
        for stmt in statements
    ]


@router.get(
    "/{pay_run_id}/statements/{statement_id}",
    response_model=PayStatementResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_pay_statement(
    db: DbSession,
    tenant_id: TenantId,
    pay_run_id: Annotated[UUID, Path()],
    statement_id: Annotated[UUID, Path()],
) -> PayStatementResponse:
    """Get a specific pay statement with line items."""
    pay_run = await db.get(PayRun, pay_run_id)
    if not pay_run or pay_run.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay run not found",
        )

    query = (
        select(PayStatement)
        .where(PayStatement.id == statement_id)
        .options(selectinload(PayStatement.line_items))
    )
    result = await db.execute(query)
    stmt = result.scalar_one_or_none()

    if not stmt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pay statement not found",
        )

    return PayStatementResponse(
        id=stmt.id,
        pay_run_employee_id=stmt.pay_run_employee_id,
        calculation_id=stmt.calculation_id,
        gross_pay=stmt.gross_pay,
        net_pay=stmt.net_pay,
        total_taxes=stmt.total_taxes,
        total_deductions=stmt.total_deductions,
        total_employer_taxes=stmt.total_employer_taxes,
        check_number=stmt.check_number,
        line_items=[PayLineItemResponse.model_validate(li) for li in stmt.line_items],
    )
