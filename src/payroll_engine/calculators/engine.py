"""Payroll calculation engine - main orchestrator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from payroll_engine.calculators.line_builder import LineItemBuilder
from payroll_engine.calculators.rate_resolver import RateNotFoundError, RateResolver
from payroll_engine.calculators.tax_calculator import TaxCalculator
from payroll_engine.calculators.types import (
    EmployeeCalculationContext,
    LineCandidate,
    LineType,
    TaxableWages,
)
from payroll_engine.config import get_settings
from payroll_engine.models import (
    DeductionCode,
    EarningCode,
    Employee,
    EmployeeDeduction,
    Employment,
    GarnishmentOrder,
    PayInputAdjustment,
    PayPeriod,
    PayRun,
    PayRunEmployee,
    TimeEntry,
)


@dataclass
class CalculationResult:
    """Result of calculating pay for one employee."""

    employee_id: UUID
    calculation_id: UUID
    gross: Decimal
    net: Decimal
    lines: list[LineCandidate]
    errors: list[str]
    inputs_fingerprint: str
    rules_fingerprint: str

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


@dataclass
class PayRunCalculationResult:
    """Result of calculating an entire pay run."""

    pay_run_id: UUID
    results: dict[UUID, CalculationResult]  # employee_id -> result
    total_gross: Decimal = Decimal("0")
    total_net: Decimal = Decimal("0")
    error_count: int = 0


class PayrollEngine:
    """Main payroll calculation engine.

    Calculation pipeline (stable order per employee):
    1) Build earnings lines from time entries and adjustments
    2) Apply pre-tax deductions
    3) Compute taxable wages
    4) Compute employee taxes (per jurisdiction)
    5) Apply post-tax deductions
    6) Apply garnishments (priority order)
    7) Compute employer taxes (liability only)
    8) Rounding reconciliation
    9) Validate net = sum(lines)
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.rate_resolver = RateResolver(session)
        self.tax_calculator = TaxCalculator(session)
        self.settings = get_settings()

    async def calculate_pay_run(
        self, pay_run_id: UUID
    ) -> PayRunCalculationResult:
        """Calculate pay for all included employees in a pay run."""
        # Load pay run with employees
        pay_run = await self._load_pay_run(pay_run_id)
        if pay_run is None:
            raise ValueError(f"Pay run {pay_run_id} not found")

        # Validate status allows calculation
        if pay_run.status not in ("draft", "preview", "approved"):
            raise ValueError(
                f"Cannot calculate pay run in status '{pay_run.status}'"
            )

        # Get as-of date and period boundaries
        as_of_date = pay_run.get_as_of_date()
        period = pay_run.pay_period
        if period is None:
            raise ValueError("Pay run must have a pay period")

        results: dict[UUID, CalculationResult] = {}
        total_gross = Decimal("0")
        total_net = Decimal("0")
        error_count = 0

        # Calculate each included employee
        for pre in pay_run.employees:
            if pre.status == "excluded":
                continue

            ctx = EmployeeCalculationContext(
                employee_id=pre.employee_id,
                pay_run_id=pay_run_id,
                as_of_date=as_of_date,
                check_date=period.check_date,
                period_start=period.period_start,
                period_end=period.period_end,
                legal_entity_id=pay_run.legal_entity_id,
            )

            try:
                result = await self._calculate_employee(ctx)
                results[pre.employee_id] = result

                if result.success:
                    total_gross += result.gross
                    total_net += result.net
                    # Update pay_run_employee
                    pre.gross = result.gross
                    pre.net = result.net
                    pre.status = "included"
                    pre.error_message = None
                else:
                    pre.status = "error"
                    pre.error_message = "; ".join(result.errors)
                    error_count += 1

            except Exception as e:
                # Catch unexpected errors
                error_result = CalculationResult(
                    employee_id=pre.employee_id,
                    calculation_id=self._generate_calculation_id(
                        pay_run_id, pre.employee_id, as_of_date, "", ""
                    ),
                    gross=Decimal("0"),
                    net=Decimal("0"),
                    lines=[],
                    errors=[f"Unexpected error: {str(e)}"],
                    inputs_fingerprint="",
                    rules_fingerprint="",
                )
                results[pre.employee_id] = error_result
                pre.status = "error"
                pre.error_message = str(e)
                error_count += 1

        return PayRunCalculationResult(
            pay_run_id=pay_run_id,
            results=results,
            total_gross=total_gross,
            total_net=total_net,
            error_count=error_count,
        )

    async def _calculate_employee(
        self, ctx: EmployeeCalculationContext
    ) -> CalculationResult:
        """Calculate pay for a single employee."""
        lines: list[LineCandidate] = []
        rule_ids_used: list[str] = []

        # 1) Load and validate employment
        employment = await self._get_employment(
            ctx.employee_id, ctx.legal_entity_id, ctx.as_of_date
        )
        if employment is None:
            ctx.errors.append(
                f"No active employment for employee {ctx.employee_id} "
                f"in legal entity {ctx.legal_entity_id} on {ctx.as_of_date}"
            )
            return self._build_error_result(ctx)

        # 2) Build earnings lines
        earnings_lines, inputs_data = await self._build_earnings_lines(ctx)
        lines.extend(earnings_lines)

        if not earnings_lines:
            ctx.errors.append("No earnings for this pay period")
            return self._build_error_result(ctx)

        # Calculate gross
        ctx.gross = LineItemBuilder.calculate_gross_from_lines(lines)

        # 3) Load and apply deductions
        deductions = await self._get_employee_deductions(ctx.employee_id, ctx.as_of_date)

        # Separate pre-tax and post-tax
        pretax_deductions = [d for d in deductions if d.deduction_code.is_pretax]
        posttax_deductions = [d for d in deductions if not d.deduction_code.is_pretax]

        # 4) Apply pre-tax deductions (affects taxable wages)
        pretax_total = Decimal("0")
        for ded in pretax_deductions:
            ded_line = await self._calculate_deduction(ctx, ded)
            if ded_line:
                lines.append(ded_line)
                pretax_total += abs(ded_line.amount)

        # 5) Calculate taxable wages
        ctx.taxable_wages = self._calculate_taxable_wages(
            ctx.gross, pretax_total, earnings_lines
        )

        # 6) Calculate employee taxes
        # Load YTD wages for wage base calculations
        ytd_wages = await self._get_ytd_wages(ctx.employee_id, ctx.as_of_date)

        tax_lines = await self.tax_calculator.calculate_employee_taxes(
            ctx, ctx.taxable_wages, ytd_wages
        )
        lines.extend(tax_lines)

        # Track rule IDs
        for tl in tax_lines:
            if tl.rule_id:
                rule_ids_used.append(str(tl.rule_id))

        # 7) Apply post-tax deductions
        for ded in posttax_deductions:
            ded_line = await self._calculate_deduction(ctx, ded)
            if ded_line:
                lines.append(ded_line)

        # 8) Apply garnishments (priority order)
        garnishments = await self._get_garnishments(ctx.employee_id, ctx.as_of_date)
        current_disposable = self._calculate_disposable_income(lines)

        for garnishment in sorted(garnishments, key=lambda g: g.priority_rank):
            garn_line = self._calculate_garnishment(ctx, garnishment, current_disposable)
            if garn_line:
                lines.append(garn_line)
                current_disposable -= abs(garn_line.amount)

        # 9) Calculate net and reconcile rounding
        calculated_net = LineItemBuilder.calculate_net_from_lines(lines)

        # Validate signs
        sign_errors = LineItemBuilder.validate_line_signs(lines)
        ctx.errors.extend(sign_errors)

        # Check for negative net (unless allowed)
        if calculated_net < 0:
            ctx.errors.append(f"Negative net pay: {calculated_net}")

        # Add rounding adjustment if needed
        ctx.net = calculated_net

        # Generate fingerprints
        inputs_fingerprint = self._compute_inputs_fingerprint(inputs_data)
        rules_fingerprint = self._compute_rules_fingerprint(rule_ids_used)

        # Generate calculation ID
        calculation_id = self._generate_calculation_id(
            ctx.pay_run_id,
            ctx.employee_id,
            ctx.as_of_date,
            inputs_fingerprint,
            rules_fingerprint,
        )

        return CalculationResult(
            employee_id=ctx.employee_id,
            calculation_id=calculation_id,
            gross=ctx.gross,
            net=ctx.net,
            lines=lines,
            errors=ctx.errors,
            inputs_fingerprint=inputs_fingerprint,
            rules_fingerprint=rules_fingerprint,
        )

    async def _build_earnings_lines(
        self, ctx: EmployeeCalculationContext
    ) -> tuple[list[LineCandidate], list[dict[str, Any]]]:
        """Build earnings lines from time entries and adjustments."""
        lines: list[LineCandidate] = []
        inputs_data: list[dict[str, Any]] = []

        # Load time entries for the period
        time_entries = await self._get_time_entries(
            ctx.employee_id, ctx.period_start, ctx.period_end
        )

        for entry in time_entries:
            try:
                rate = await self.rate_resolver.resolve_rate_for_time_entry(
                    entry, ctx.as_of_date
                )
            except RateNotFoundError as e:
                ctx.errors.append(str(e))
                continue

            # Calculate amount
            quantity = entry.hours or entry.units or Decimal("0")
            amount = (quantity * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            if amount > 0:
                line = LineItemBuilder.create_earning_line(
                    earning_code_id=entry.earning_code_id,
                    amount=amount,
                    quantity=quantity,
                    rate=rate,
                    source_input_id=entry.time_entry_id,
                    explanation=f"{entry.earning_code.code}: {quantity} @ {rate}",
                    taxability_flags={
                        "federal": entry.earning_code.is_taxable_federal,
                        "state": entry.earning_code.is_taxable_state_default,
                        "local": entry.earning_code.is_taxable_local_default,
                    },
                )
                lines.append(line)
                inputs_data.append({
                    "type": "time_entry",
                    "id": str(entry.time_entry_id),
                    "amount": str(amount),
                })

        # Load earning adjustments
        adjustments = await self._get_earning_adjustments(
            ctx.employee_id, ctx.pay_run_id, ctx.period_start, ctx.period_end
        )

        for adj in adjustments:
            if adj.amount is not None:
                amount = adj.amount
            elif adj.quantity is not None and adj.rate is not None:
                amount = (adj.quantity * adj.rate).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            else:
                ctx.errors.append(
                    f"Adjustment {adj.pay_input_adjustment_id} has no amount"
                )
                continue

            line = LineItemBuilder.create_earning_line(
                earning_code_id=adj.earning_code_id,
                amount=amount,
                quantity=adj.quantity,
                rate=adj.rate,
                source_input_id=adj.pay_input_adjustment_id,
                explanation=f"Adjustment: {adj.memo or 'N/A'}",
            )
            lines.append(line)
            inputs_data.append({
                "type": "adjustment",
                "id": str(adj.pay_input_adjustment_id),
                "amount": str(amount),
            })

        return lines, inputs_data

    async def _calculate_deduction(
        self, ctx: EmployeeCalculationContext, ded: EmployeeDeduction
    ) -> LineCandidate | None:
        """Calculate a single deduction."""
        code = ded.deduction_code

        if code.calc_method == "flat":
            if ded.employee_amount is None:
                return None
            amount = ded.employee_amount

        elif code.calc_method == "percent":
            if ded.employee_percent is None:
                return None
            # Percent of gross
            amount = (ctx.gross * ded.employee_percent / 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        else:
            # tiered or other - not implemented in Phase 1
            ctx.errors.append(f"Unsupported calc_method: {code.calc_method}")
            return None

        if amount <= 0:
            return None

        return LineItemBuilder.create_deduction_line(
            deduction_code_id=code.deduction_code_id,
            amount=amount,
            explanation=f"{code.code}: {code.name}",
        )

    def _calculate_garnishment(
        self,
        ctx: EmployeeCalculationContext,
        garnishment: GarnishmentOrder,
        disposable_income: Decimal,
    ) -> LineCandidate | None:
        """Calculate garnishment amount respecting limits."""
        if disposable_income <= 0:
            return None

        # Calculate maximum based on rules
        max_amount = disposable_income

        if garnishment.max_percent:
            percent_limit = (disposable_income * garnishment.max_percent / 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            max_amount = min(max_amount, percent_limit)

        if garnishment.max_amount:
            max_amount = min(max_amount, garnishment.max_amount)

        if max_amount <= 0:
            return None

        # For Phase 1, take the max_amount as the garnishment
        # Real implementation would check remaining balance, etc.
        return LineCandidate(
            line_type=LineType.DEDUCTION,
            amount=-max_amount,  # Negative for deduction
            explanation=f"Garnishment: {garnishment.order_type} ({garnishment.case_number or 'N/A'})",
        )

    def _calculate_taxable_wages(
        self,
        gross: Decimal,
        pretax_deductions: Decimal,
        earnings_lines: list[LineCandidate],
    ) -> TaxableWages:
        """Calculate taxable wages by jurisdiction type."""
        # Start with gross minus pre-tax deductions
        base_taxable = gross - pretax_deductions

        # For Phase 1, assume all wages are taxable at all levels
        # Real implementation would check taxability flags per earning
        return TaxableWages(
            federal=base_taxable,
            state={"CA": base_taxable},  # Placeholder - would come from employee profile
            local={},
            social_security=base_taxable,
            medicare=base_taxable,
        )

    def _calculate_disposable_income(self, lines: list[LineCandidate]) -> Decimal:
        """Calculate disposable income for garnishment purposes.

        This is a simplified calculation. Real implementation would
        follow CCPA guidelines.
        """
        # Net before garnishments
        return LineItemBuilder.calculate_net_from_lines(
            [l for l in lines if "Garnishment" not in (l.explanation or "")]
        )

    async def _get_ytd_wages(
        self, employee_id: UUID, as_of_date: date
    ) -> TaxableWages | None:
        """Get YTD wages from prior committed statements.

        For Phase 1, returns None (YTD tracking not fully implemented).
        """
        # TODO: Query prior pay_statement/pay_line_item for YTD totals
        return None

    def _build_error_result(self, ctx: EmployeeCalculationContext) -> CalculationResult:
        """Build a result with errors."""
        return CalculationResult(
            employee_id=ctx.employee_id,
            calculation_id=self._generate_calculation_id(
                ctx.pay_run_id, ctx.employee_id, ctx.as_of_date, "", ""
            ),
            gross=Decimal("0"),
            net=Decimal("0"),
            lines=[],
            errors=ctx.errors,
            inputs_fingerprint="",
            rules_fingerprint="",
        )

    def _generate_calculation_id(
        self,
        pay_run_id: UUID,
        employee_id: UUID,
        as_of_date: date,
        inputs_fingerprint: str,
        rules_fingerprint: str,
    ) -> UUID:
        """Generate deterministic calculation ID."""
        data = {
            "pay_run_id": str(pay_run_id),
            "employee_id": str(employee_id),
            "as_of_date": str(as_of_date),
            "engine_version": self.settings.engine_version,
            "inputs_fingerprint": inputs_fingerprint,
            "rules_fingerprint": rules_fingerprint,
        }
        json_str = json.dumps(data, sort_keys=True)
        hash_bytes = hashlib.sha256(json_str.encode()).digest()
        return UUID(bytes=hash_bytes[:16])

    def _compute_inputs_fingerprint(self, inputs_data: list[dict[str, Any]]) -> str:
        """Compute fingerprint of all inputs used in calculation."""
        json_str = json.dumps(inputs_data, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()[:32]

    def _compute_rules_fingerprint(self, rule_ids: list[str]) -> str:
        """Compute fingerprint of all rules used in calculation."""
        json_str = json.dumps(sorted(rule_ids))
        return hashlib.sha256(json_str.encode()).hexdigest()[:32]

    # === Data Loading Methods ===

    async def _load_pay_run(self, pay_run_id: UUID) -> PayRun | None:
        """Load pay run with relationships."""
        result = await self.session.execute(
            select(PayRun)
            .where(PayRun.pay_run_id == pay_run_id)
            .options(
                selectinload(PayRun.employees),
                selectinload(PayRun.pay_period),
                selectinload(PayRun.legal_entity),
            )
        )
        return result.scalar_one_or_none()

    async def _get_employment(
        self, employee_id: UUID, legal_entity_id: UUID, as_of_date: date
    ) -> Employment | None:
        """Get active employment for employee in legal entity."""
        result = await self.session.execute(
            select(Employment).where(
                Employment.employee_id == employee_id,
                Employment.legal_entity_id == legal_entity_id,
                Employment.start_date <= as_of_date,
                (Employment.end_date.is_(None) | (Employment.end_date >= as_of_date)),
            )
        )
        return result.scalar_one_or_none()

    async def _get_time_entries(
        self, employee_id: UUID, period_start: date, period_end: date
    ) -> list[TimeEntry]:
        """Get time entries for employee in pay period."""
        result = await self.session.execute(
            select(TimeEntry)
            .where(
                TimeEntry.employee_id == employee_id,
                TimeEntry.work_date >= period_start,
                TimeEntry.work_date <= period_end,
            )
            .options(selectinload(TimeEntry.earning_code))
        )
        return list(result.scalars().all())

    async def _get_earning_adjustments(
        self,
        employee_id: UUID,
        pay_run_id: UUID,
        period_start: date,
        period_end: date,
    ) -> list[PayInputAdjustment]:
        """Get earning adjustments for employee."""
        result = await self.session.execute(
            select(PayInputAdjustment)
            .where(
                PayInputAdjustment.employee_id == employee_id,
                PayInputAdjustment.adjustment_type == "earning",
                (
                    (PayInputAdjustment.target_pay_run_id == pay_run_id)
                    | (PayInputAdjustment.target_pay_run_id.is_(None))
                ),
            )
            .options(selectinload(PayInputAdjustment.earning_code))
        )
        return list(result.scalars().all())

    async def _get_employee_deductions(
        self, employee_id: UUID, as_of_date: date
    ) -> list[EmployeeDeduction]:
        """Get active deductions for employee."""
        result = await self.session.execute(
            select(EmployeeDeduction)
            .where(
                EmployeeDeduction.employee_id == employee_id,
                EmployeeDeduction.start_date <= as_of_date,
                (
                    EmployeeDeduction.end_date.is_(None)
                    | (EmployeeDeduction.end_date >= as_of_date)
                ),
            )
            .options(selectinload(EmployeeDeduction.deduction_code))
        )
        return list(result.scalars().all())

    async def _get_garnishments(
        self, employee_id: UUID, as_of_date: date
    ) -> list[GarnishmentOrder]:
        """Get active garnishments for employee."""
        result = await self.session.execute(
            select(GarnishmentOrder)
            .where(
                GarnishmentOrder.employee_id == employee_id,
                GarnishmentOrder.start_date <= as_of_date,
                (
                    GarnishmentOrder.end_date.is_(None)
                    | (GarnishmentOrder.end_date >= as_of_date)
                ),
            )
            .order_by(GarnishmentOrder.priority_rank)
        )
        return list(result.scalars().all())
