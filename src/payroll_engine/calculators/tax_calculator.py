"""Tax calculation using rule-based JSON configs."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.calculators.types import (
    EmployeeCalculationContext,
    LineCandidate,
    LineType,
    TaxBracket,
    TaxRule,
    TaxableWages,
)
from payroll_engine.models import (
    EmployeeTaxProfile,
    Jurisdiction,
    PayrollRule,
    PayrollRuleVersion,
    TaxAgency,
)


class TaxRuleNotFoundError(Exception):
    """Raised when required tax rule is not found."""

    def __init__(self, rule_name: str, as_of_date: date):
        self.rule_name = rule_name
        self.as_of_date = as_of_date
        super().__init__(f"Tax rule '{rule_name}' not found effective {as_of_date}")


class TaxCalculator:
    """Calculates taxes using rule-based JSON configurations.

    Tax rules are stored in payroll_rule_version.payload_json with structure:
    {
        "tax_type": "federal_income" | "state_income" | "social_security" | etc.,
        "jurisdiction_code": "FED" | "CA" | etc.,
        "brackets": [
            {"min": 0, "max": 10000, "rate": 0.10, "flat": 0},
            {"min": 10000, "max": 40000, "rate": 0.12, "flat": 1000},
            ...
        ],
        "wage_base_limit": 160200,  // optional
        "is_employer_tax": false,
        "filing_status_adjustments": {...}  // optional
    }
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self._rule_cache: dict[str, TaxRule] = {}
        self._jurisdiction_cache: dict[str, Jurisdiction] = {}
        self._agency_cache: dict[str, TaxAgency] = {}

    async def calculate_employee_taxes(
        self,
        ctx: EmployeeCalculationContext,
        taxable_wages: TaxableWages,
        ytd_wages: TaxableWages | None = None,
    ) -> list[LineCandidate]:
        """Calculate all applicable taxes for an employee.

        Returns list of tax line candidates (both employee and employer taxes).
        """
        lines: list[LineCandidate] = []

        # Get employee tax profiles
        profiles = await self._get_tax_profiles(ctx.employee_id, ctx.as_of_date)

        # Federal taxes
        federal_lines = await self._calculate_federal_taxes(ctx, taxable_wages, ytd_wages, profiles)
        lines.extend(federal_lines)

        # State taxes (for each state with taxable wages)
        for state_code, state_wages in taxable_wages.state.items():
            if state_wages > 0:
                state_lines = await self._calculate_state_taxes(
                    ctx, state_code, state_wages, ytd_wages, profiles
                )
                lines.extend(state_lines)

        # Local taxes
        for local_code, local_wages in taxable_wages.local.items():
            if local_wages > 0:
                local_lines = await self._calculate_local_taxes(
                    ctx, local_code, local_wages, profiles
                )
                lines.extend(local_lines)

        return lines

    async def _calculate_federal_taxes(
        self,
        ctx: EmployeeCalculationContext,
        taxable_wages: TaxableWages,
        ytd_wages: TaxableWages | None,
        profiles: list[EmployeeTaxProfile],
    ) -> list[LineCandidate]:
        """Calculate federal taxes (income, SS, Medicare)."""
        lines: list[LineCandidate] = []

        # Get federal jurisdiction
        fed_jurisdiction = await self._get_jurisdiction("FED", "FED")

        # Federal income tax
        fed_profile = next(
            (p for p in profiles if p.jurisdiction_id == fed_jurisdiction.jurisdiction_id),
            None,
        )

        try:
            fed_income_rule = await self._get_tax_rule("federal_income_tax", ctx.as_of_date)
            fed_income = self._calculate_progressive_tax(
                taxable_wages.federal,
                fed_income_rule,
                filing_status=fed_profile.filing_status if fed_profile else None,
                additional_withholding=fed_profile.additional_withholding if fed_profile else None,
            )
            if fed_income > 0:
                lines.append(
                    LineCandidate(
                        line_type=LineType.TAX,
                        amount=-fed_income,  # Employee tax is negative
                        jurisdiction_id=fed_jurisdiction.jurisdiction_id,
                        rule_id=fed_income_rule.rule_id,
                        rule_version_id=fed_income_rule.rule_version_id,
                        explanation="Federal Income Tax",
                    )
                )
        except TaxRuleNotFoundError:
            ctx.errors.append("Federal income tax rule not found")

        # Social Security
        try:
            ss_rule = await self._get_tax_rule("social_security_employee", ctx.as_of_date)
            ytd_ss = ytd_wages.social_security if ytd_wages else Decimal("0")
            ss_tax = self._calculate_wage_base_tax(
                taxable_wages.social_security, ss_rule, ytd_ss
            )
            if ss_tax > 0:
                lines.append(
                    LineCandidate(
                        line_type=LineType.TAX,
                        amount=-ss_tax,
                        jurisdiction_id=fed_jurisdiction.jurisdiction_id,
                        rule_id=ss_rule.rule_id,
                        rule_version_id=ss_rule.rule_version_id,
                        explanation="Social Security Tax (Employee)",
                    )
                )

            # Employer Social Security
            ss_er_rule = await self._get_tax_rule("social_security_employer", ctx.as_of_date)
            ss_er_tax = self._calculate_wage_base_tax(
                taxable_wages.social_security, ss_er_rule, ytd_ss
            )
            if ss_er_tax > 0:
                lines.append(
                    LineCandidate(
                        line_type=LineType.EMPLOYER_TAX,
                        amount=ss_er_tax,  # Employer tax is positive
                        jurisdiction_id=fed_jurisdiction.jurisdiction_id,
                        rule_id=ss_er_rule.rule_id,
                        rule_version_id=ss_er_rule.rule_version_id,
                        explanation="Social Security Tax (Employer)",
                    )
                )
        except TaxRuleNotFoundError:
            ctx.errors.append("Social Security tax rule not found")

        # Medicare
        try:
            med_rule = await self._get_tax_rule("medicare_employee", ctx.as_of_date)
            med_tax = self._calculate_flat_tax(taxable_wages.medicare, med_rule)
            if med_tax > 0:
                lines.append(
                    LineCandidate(
                        line_type=LineType.TAX,
                        amount=-med_tax,
                        jurisdiction_id=fed_jurisdiction.jurisdiction_id,
                        rule_id=med_rule.rule_id,
                        rule_version_id=med_rule.rule_version_id,
                        explanation="Medicare Tax (Employee)",
                    )
                )

            # Employer Medicare
            med_er_rule = await self._get_tax_rule("medicare_employer", ctx.as_of_date)
            med_er_tax = self._calculate_flat_tax(taxable_wages.medicare, med_er_rule)
            if med_er_tax > 0:
                lines.append(
                    LineCandidate(
                        line_type=LineType.EMPLOYER_TAX,
                        amount=med_er_tax,
                        jurisdiction_id=fed_jurisdiction.jurisdiction_id,
                        rule_id=med_er_rule.rule_id,
                        rule_version_id=med_er_rule.rule_version_id,
                        explanation="Medicare Tax (Employer)",
                    )
                )
        except TaxRuleNotFoundError:
            ctx.errors.append("Medicare tax rule not found")

        # FUTA (employer only)
        try:
            futa_rule = await self._get_tax_rule("futa", ctx.as_of_date)
            ytd_futa = ytd_wages.federal if ytd_wages else Decimal("0")
            futa_tax = self._calculate_wage_base_tax(taxable_wages.federal, futa_rule, ytd_futa)
            if futa_tax > 0:
                lines.append(
                    LineCandidate(
                        line_type=LineType.EMPLOYER_TAX,
                        amount=futa_tax,
                        jurisdiction_id=fed_jurisdiction.jurisdiction_id,
                        rule_id=futa_rule.rule_id,
                        rule_version_id=futa_rule.rule_version_id,
                        explanation="FUTA (Federal Unemployment)",
                    )
                )
        except TaxRuleNotFoundError:
            pass  # FUTA is optional in some contexts

        return lines

    async def _calculate_state_taxes(
        self,
        ctx: EmployeeCalculationContext,
        state_code: str,
        wages: Decimal,
        ytd_wages: TaxableWages | None,
        profiles: list[EmployeeTaxProfile],
    ) -> list[LineCandidate]:
        """Calculate state-level taxes."""
        lines: list[LineCandidate] = []

        # Get state jurisdiction
        state_jurisdiction = await self._get_jurisdiction("STATE", state_code)
        if state_jurisdiction is None:
            return lines

        # State income tax
        state_profile = next(
            (p for p in profiles if p.jurisdiction_id == state_jurisdiction.jurisdiction_id),
            None,
        )

        try:
            state_income_rule = await self._get_tax_rule(
                f"state_income_tax_{state_code.lower()}", ctx.as_of_date
            )
            state_income = self._calculate_progressive_tax(
                wages,
                state_income_rule,
                filing_status=state_profile.filing_status if state_profile else None,
                additional_withholding=state_profile.additional_withholding if state_profile else None,
            )
            if state_income > 0:
                lines.append(
                    LineCandidate(
                        line_type=LineType.TAX,
                        amount=-state_income,
                        jurisdiction_id=state_jurisdiction.jurisdiction_id,
                        rule_id=state_income_rule.rule_id,
                        rule_version_id=state_income_rule.rule_version_id,
                        explanation=f"{state_code} State Income Tax",
                    )
                )
        except TaxRuleNotFoundError:
            pass  # Not all states have income tax

        # SUTA (state unemployment - employer)
        try:
            suta_rule = await self._get_tax_rule(f"suta_{state_code.lower()}", ctx.as_of_date)
            ytd_state = ytd_wages.state.get(state_code, Decimal("0")) if ytd_wages else Decimal("0")
            suta_tax = self._calculate_wage_base_tax(wages, suta_rule, ytd_state)
            if suta_tax > 0:
                lines.append(
                    LineCandidate(
                        line_type=LineType.EMPLOYER_TAX,
                        amount=suta_tax,
                        jurisdiction_id=state_jurisdiction.jurisdiction_id,
                        rule_id=suta_rule.rule_id,
                        rule_version_id=suta_rule.rule_version_id,
                        explanation=f"{state_code} SUTA (State Unemployment)",
                    )
                )
        except TaxRuleNotFoundError:
            pass

        return lines

    async def _calculate_local_taxes(
        self,
        ctx: EmployeeCalculationContext,
        local_code: str,
        wages: Decimal,
        profiles: list[EmployeeTaxProfile],
    ) -> list[LineCandidate]:
        """Calculate local taxes (city, county, etc.)."""
        lines: list[LineCandidate] = []

        # Get local jurisdiction
        local_jurisdiction = await self._get_jurisdiction("LOCAL", local_code)
        if local_jurisdiction is None:
            return lines

        try:
            local_rule = await self._get_tax_rule(f"local_tax_{local_code.lower()}", ctx.as_of_date)
            local_tax = self._calculate_flat_tax(wages, local_rule)
            if local_tax > 0:
                lines.append(
                    LineCandidate(
                        line_type=LineType.TAX,
                        amount=-local_tax,
                        jurisdiction_id=local_jurisdiction.jurisdiction_id,
                        rule_id=local_rule.rule_id,
                        rule_version_id=local_rule.rule_version_id,
                        explanation=f"{local_code} Local Tax",
                    )
                )
        except TaxRuleNotFoundError:
            pass

        return lines

    def _calculate_progressive_tax(
        self,
        wages: Decimal,
        rule: TaxRule,
        filing_status: str | None = None,
        additional_withholding: Decimal | None = None,
    ) -> Decimal:
        """Calculate tax using progressive brackets."""
        if wages <= 0:
            return Decimal("0")

        total_tax = Decimal("0")
        remaining = wages

        for bracket in sorted(rule.brackets, key=lambda b: b.min_amount):
            if remaining <= 0:
                break

            bracket_min = bracket.min_amount
            bracket_max = bracket.max_amount if bracket.max_amount else wages + 1

            if wages < bracket_min:
                continue

            taxable_in_bracket = min(remaining, bracket_max - bracket_min)
            if taxable_in_bracket > 0:
                total_tax += bracket.flat_amount + (taxable_in_bracket * bracket.rate)
                remaining -= taxable_in_bracket

        # Add any additional withholding
        if additional_withholding and additional_withholding > 0:
            total_tax += additional_withholding

        return total_tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _calculate_wage_base_tax(
        self,
        wages: Decimal,
        rule: TaxRule,
        ytd_wages: Decimal = Decimal("0"),
    ) -> Decimal:
        """Calculate tax with a wage base limit (e.g., SS, FUTA)."""
        if wages <= 0:
            return Decimal("0")

        wage_base = rule.wage_base_limit
        if wage_base is None:
            # No limit, tax all wages
            taxable = wages
        else:
            # Only tax up to the wage base
            if ytd_wages >= wage_base:
                return Decimal("0")  # Already hit limit
            remaining_base = wage_base - ytd_wages
            taxable = min(wages, remaining_base)

        # Use first bracket rate (wage base taxes are typically flat rate)
        if rule.brackets:
            rate = rule.brackets[0].rate
        else:
            return Decimal("0")

        return (taxable * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _calculate_flat_tax(self, wages: Decimal, rule: TaxRule) -> Decimal:
        """Calculate flat-rate tax."""
        if wages <= 0 or not rule.brackets:
            return Decimal("0")

        rate = rule.brackets[0].rate
        return (wages * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    async def _get_tax_rule(self, rule_name: str, as_of_date: date) -> TaxRule:
        """Get tax rule by name, effective on date."""
        cache_key = f"{rule_name}:{as_of_date}"
        if cache_key in self._rule_cache:
            return self._rule_cache[cache_key]

        # Query rule and version
        result = await self.session.execute(
            select(PayrollRule, PayrollRuleVersion)
            .join(PayrollRuleVersion, PayrollRule.rule_id == PayrollRuleVersion.rule_id)
            .where(
                PayrollRule.rule_name == rule_name,
                PayrollRuleVersion.effective_start <= as_of_date,
                (
                    PayrollRuleVersion.effective_end.is_(None)
                    | (PayrollRuleVersion.effective_end >= as_of_date)
                ),
            )
        )
        row = result.first()

        if row is None:
            raise TaxRuleNotFoundError(rule_name, as_of_date)

        rule, version = row
        payload = version.payload_json

        # Parse brackets
        brackets = []
        for b in payload.get("brackets", []):
            brackets.append(
                TaxBracket(
                    min_amount=Decimal(str(b["min"])),
                    max_amount=Decimal(str(b["max"])) if b.get("max") else None,
                    rate=Decimal(str(b["rate"])),
                    flat_amount=Decimal(str(b.get("flat", 0))),
                )
            )

        # Get jurisdiction
        jurisdiction_code = payload.get("jurisdiction_code", "FED")
        jurisdiction_type = payload.get("jurisdiction_type", "FED")
        jurisdiction = await self._get_jurisdiction(jurisdiction_type, jurisdiction_code)

        tax_rule = TaxRule(
            rule_id=rule.rule_id,
            rule_version_id=version.rule_version_id,
            jurisdiction_id=jurisdiction.jurisdiction_id if jurisdiction else None,
            tax_agency_id=None,  # Could look up from payload
            tax_type=payload.get("tax_type", ""),
            brackets=brackets,
            wage_base_limit=(
                Decimal(str(payload["wage_base_limit"]))
                if payload.get("wage_base_limit")
                else None
            ),
            is_employer_tax=payload.get("is_employer_tax", False),
        )

        self._rule_cache[cache_key] = tax_rule
        return tax_rule

    async def _get_jurisdiction(
        self, jurisdiction_type: str, code: str
    ) -> Jurisdiction | None:
        """Get jurisdiction by type and code."""
        cache_key = f"{jurisdiction_type}:{code}"
        if cache_key in self._jurisdiction_cache:
            return self._jurisdiction_cache[cache_key]

        result = await self.session.execute(
            select(Jurisdiction).where(
                Jurisdiction.jurisdiction_type == jurisdiction_type,
                Jurisdiction.code == code,
            )
        )
        jurisdiction = result.scalar_one_or_none()

        if jurisdiction:
            self._jurisdiction_cache[cache_key] = jurisdiction

        return jurisdiction

    async def _get_tax_profiles(
        self, employee_id: UUID, as_of_date: date
    ) -> list[EmployeeTaxProfile]:
        """Get all tax profiles for an employee effective on a date."""
        result = await self.session.execute(
            select(EmployeeTaxProfile).where(
                EmployeeTaxProfile.employee_id == employee_id,
                EmployeeTaxProfile.effective_start <= as_of_date,
                (
                    EmployeeTaxProfile.effective_end.is_(None)
                    | (EmployeeTaxProfile.effective_end >= as_of_date)
                ),
            )
        )
        return list(result.scalars().all())
