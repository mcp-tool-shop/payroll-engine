"""PSP Funding Gate Service - Safe-to-commit checks.

Implements the funding gate that evaluates whether a pay run can be committed
based on available funds. Supports two policies:
- Strict: Block commit without funds (hard_fail)
- Hybrid: Allow commit, block pay (soft_fail)

Gate evaluations are persisted idempotently for audit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class GateResult:
    """Result of a funding gate evaluation."""

    outcome: str  # pass, soft_fail, hard_fail
    required_amount: Decimal
    available_amount: Decimal
    reasons: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Whether the gate passed."""
        return self.outcome == "pass"

    @property
    def shortfall(self) -> Decimal:
        """Amount of shortfall (0 if no shortfall)."""
        diff = self.required_amount - self.available_amount
        return diff if diff > 0 else Decimal("0")


@dataclass
class FundingRequirement:
    """Computed funding requirements from payroll outputs."""

    net_pay: Decimal = Decimal("0")
    taxes: Decimal = Decimal("0")
    third_party: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        """Total funding required."""
        return self.net_pay + self.taxes + self.third_party + self.fees


class FundingGateService:
    """Funding gate evaluation service.

    Evaluates whether sufficient funds are available to commit/pay a payroll.
    """

    def __init__(self, db: Session):
        self.db = db

    def evaluate_commit_gate(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        pay_run_id: str | UUID,
        funding_model: str,
        idempotency_key: str,
        strict: bool = True,
    ) -> GateResult:
        """Evaluate the commit gate for a pay run.

        Args:
            tenant_id: Tenant identifier
            legal_entity_id: Legal entity for the pay run
            pay_run_id: The pay run to evaluate
            funding_model: Funding model (prefund_all, net_only, etc.)
            idempotency_key: Unique key for deduplication
            strict: If True, use hard_fail; otherwise soft_fail

        Returns:
            GateResult with outcome and details
        """
        # Check for existing evaluation
        existing = self._get_existing_evaluation(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        if existing:
            return existing

        # Compute requirements from pay run
        requirement = self._compute_funding_requirement(
            pay_run_id=pay_run_id,
            funding_model=funding_model,
        )

        # Get available balance
        available = self._get_available_balance(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
        )

        # Evaluate gate
        reasons: list[dict[str, Any]] = []
        required = requirement.total

        if available < required:
            reasons.append({
                "code": "INSUFFICIENT_FUNDS",
                "message": f"Funding not received. Required {required} USD, available {available} USD.",
                "shortfall": str(required - available),
            })

        # Check for high-risk flags
        high_risk_flags = self._check_high_risk_flags(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            pay_run_id=pay_run_id,
            requirement=requirement,
        )
        reasons.extend(high_risk_flags)

        # Determine outcome
        if not reasons:
            outcome = "pass"
        elif strict:
            outcome = "hard_fail"
        else:
            outcome = "soft_fail"

        # Persist evaluation
        self._persist_evaluation(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            pay_run_id=pay_run_id,
            gate_type="commit_gate",
            outcome=outcome,
            required=required,
            available=available,
            reasons=reasons,
            idempotency_key=idempotency_key,
        )

        return GateResult(
            outcome=outcome,
            required_amount=required,
            available_amount=available,
            reasons=reasons,
        )

    def evaluate_pay_gate(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        pay_run_id: str | UUID,
        idempotency_key: str,
    ) -> GateResult:
        """Evaluate the pay gate (always strict).

        The pay gate is evaluated before disbursement and always enforces
        fund availability.

        Args:
            tenant_id: Tenant identifier
            legal_entity_id: Legal entity for the pay run
            pay_run_id: The pay run to evaluate
            idempotency_key: Unique key for deduplication

        Returns:
            GateResult with outcome and details
        """
        # Check for existing evaluation
        existing = self._get_existing_evaluation(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        if existing:
            return existing

        # Compute requirements from pay run
        requirement = self._compute_funding_requirement(
            pay_run_id=pay_run_id,
            funding_model="prefund_all",  # Pay gate always requires all funds
        )

        # Get available balance (net of active reservations)
        available = self._get_available_balance(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            include_reservations=True,
        )

        # Evaluate gate
        reasons: list[dict[str, Any]] = []
        required = requirement.total

        if available < required:
            reasons.append({
                "code": "INSUFFICIENT_FUNDS_FOR_PAY",
                "message": f"Cannot disburse. Required {required} USD, available {available} USD.",
                "shortfall": str(required - available),
            })

        outcome = "pass" if not reasons else "hard_fail"

        # Persist evaluation
        self._persist_evaluation(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            pay_run_id=pay_run_id,
            gate_type="pay_gate",
            outcome=outcome,
            required=required,
            available=available,
            reasons=reasons,
            idempotency_key=idempotency_key,
        )

        return GateResult(
            outcome=outcome,
            required_amount=required,
            available_amount=available,
            reasons=reasons,
        )

    def _get_existing_evaluation(
        self,
        *,
        tenant_id: str | UUID,
        idempotency_key: str,
    ) -> GateResult | None:
        """Check for existing idempotent evaluation."""
        row = self.db.execute(
            text("""
                SELECT outcome, required_amount, available_amount, reasons_json
                FROM funding_gate_evaluation
                WHERE tenant_id = :tenant_id AND idempotency_key = :idk
            """),
            {"tenant_id": str(tenant_id), "idk": idempotency_key},
        ).fetchone()

        if row:
            return GateResult(
                outcome=row[0],
                required_amount=Decimal(str(row[1])),
                available_amount=Decimal(str(row[2])),
                reasons=row[3] if isinstance(row[3], list) else [],
            )
        return None

    def _compute_funding_requirement(
        self,
        *,
        pay_run_id: str | UUID,
        funding_model: str,
    ) -> FundingRequirement:
        """Compute funding requirements from pay run outputs."""
        # Get net pay total from pay statements
        net_result = self.db.execute(
            text("""
                SELECT COALESCE(SUM(ps.net_pay), 0)
                FROM pay_statement ps
                JOIN pay_run_employee pre ON pre.pay_run_employee_id = ps.pay_run_employee_id
                WHERE pre.pay_run_id = :pay_run_id
            """),
            {"pay_run_id": str(pay_run_id)},
        ).scalar()
        net_pay = Decimal(str(net_result or 0))

        # Get employer tax totals from pay line items
        tax_result = self.db.execute(
            text("""
                SELECT COALESCE(SUM(pli.amount), 0)
                FROM pay_line_item pli
                JOIN pay_statement ps ON ps.pay_statement_id = pli.pay_statement_id
                JOIN pay_run_employee pre ON pre.pay_run_employee_id = ps.pay_run_employee_id
                WHERE pre.pay_run_id = :pay_run_id
                  AND pli.category = 'employer_tax'
            """),
            {"pay_run_id": str(pay_run_id)},
        ).scalar()
        taxes = Decimal(str(tax_result or 0))

        # Get third-party deduction totals
        third_party_result = self.db.execute(
            text("""
                SELECT COALESCE(SUM(pli.amount), 0)
                FROM pay_line_item pli
                JOIN pay_statement ps ON ps.pay_statement_id = pli.pay_statement_id
                JOIN pay_run_employee pre ON pre.pay_run_employee_id = ps.pay_run_employee_id
                WHERE pre.pay_run_id = :pay_run_id
                  AND pli.category = 'deduction'
                  AND pli.is_third_party_remit = true
            """),
            {"pay_run_id": str(pay_run_id)},
        ).scalar()
        third_party = Decimal(str(third_party_result or 0))

        # Apply funding model
        req = FundingRequirement(net_pay=net_pay, taxes=taxes, third_party=third_party)

        if funding_model == "net_only":
            req.taxes = Decimal("0")
            req.third_party = Decimal("0")
        elif funding_model == "net_and_third_party":
            req.taxes = Decimal("0")
        # prefund_all and split_schedule require all

        return req

    def _get_available_balance(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        include_reservations: bool = False,
    ) -> Decimal:
        """Get available balance from client funding clearing account."""
        result = self.db.execute(
            text("""
                WITH acct AS (
                    SELECT psp_ledger_account_id
                    FROM psp_ledger_account
                    WHERE tenant_id = :tenant_id
                      AND legal_entity_id = :le
                      AND account_type = 'client_funding_clearing'
                    LIMIT 1
                ),
                credits AS (
                    SELECT COALESCE(SUM(e.amount), 0) AS c
                    FROM psp_ledger_entry e, acct
                    WHERE e.tenant_id = :tenant_id
                      AND e.credit_account_id = acct.psp_ledger_account_id
                ),
                debits AS (
                    SELECT COALESCE(SUM(e.amount), 0) AS d
                    FROM psp_ledger_entry e, acct
                    WHERE e.tenant_id = :tenant_id
                      AND e.debit_account_id = acct.psp_ledger_account_id
                )
                SELECT (credits.c - debits.d)
                FROM credits, debits
            """),
            {"tenant_id": str(tenant_id), "le": str(legal_entity_id)},
        ).scalar()

        available = Decimal(str(result or 0))

        if include_reservations:
            # Subtract active reservations
            reserved = self.db.execute(
                text("""
                    SELECT COALESCE(SUM(amount), 0)
                    FROM psp_reservation
                    WHERE tenant_id = :tenant_id
                      AND legal_entity_id = :le
                      AND status = 'active'
                """),
                {"tenant_id": str(tenant_id), "le": str(legal_entity_id)},
            ).scalar()
            available -= Decimal(str(reserved or 0))

        return available

    def _check_high_risk_flags(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        pay_run_id: str | UUID,
        requirement: FundingRequirement,
    ) -> list[dict[str, Any]]:
        """Check for high-risk conditions.

        Includes:
        - Bank account changes in cooldown period
        - Spike detection (unusual payroll amounts)
        """
        flags: list[dict[str, Any]] = []

        # Check for recent bank account changes (7-day cooldown)
        # This would query a bank_account_audit table in production
        # For now, skip this check

        # Spike detection: compare to recent payroll averages
        avg_result = self.db.execute(
            text("""
                SELECT AVG(total_amount)
                FROM (
                    SELECT SUM(ps.net_pay) AS total_amount
                    FROM pay_statement ps
                    JOIN pay_run_employee pre ON pre.pay_run_employee_id = ps.pay_run_employee_id
                    JOIN pay_run pr ON pr.pay_run_id = pre.pay_run_id
                    WHERE pr.tenant_id = :tenant_id
                      AND pr.legal_entity_id = :le
                      AND pr.status = 'paid'
                      AND pr.pay_run_id != :pay_run_id
                    GROUP BY pr.pay_run_id
                    ORDER BY pr.check_date DESC
                    LIMIT 6
                ) recent
            """),
            {
                "tenant_id": str(tenant_id),
                "le": str(legal_entity_id),
                "pay_run_id": str(pay_run_id),
            },
        ).scalar()

        if avg_result:
            avg = Decimal(str(avg_result))
            if avg > 0 and requirement.net_pay > avg * Decimal("1.5"):
                flags.append({
                    "code": "SPIKE_DETECTED",
                    "message": f"Payroll amount {requirement.net_pay} is 50%+ above recent average {avg}.",
                    "severity": "warning",
                })

        return flags

    def _persist_evaluation(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        pay_run_id: str | UUID,
        gate_type: str,
        outcome: str,
        required: Decimal,
        available: Decimal,
        reasons: list[dict[str, Any]],
        idempotency_key: str,
    ) -> None:
        """Persist the gate evaluation (idempotent)."""
        self.db.execute(
            text("""
                INSERT INTO funding_gate_evaluation(
                    tenant_id, legal_entity_id, pay_run_id, gate_type,
                    outcome, required_amount, available_amount, reasons_json, idempotency_key
                )
                VALUES (
                    :tenant_id, :le, :pay_run_id, :gate_type,
                    :outcome, :required, :available, :reasons::jsonb, :idk
                )
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            """),
            {
                "tenant_id": str(tenant_id),
                "le": str(legal_entity_id),
                "pay_run_id": str(pay_run_id),
                "gate_type": gate_type,
                "outcome": outcome,
                "required": str(required),
                "available": str(available),
                "reasons": json.dumps(reasons),
                "idk": idempotency_key,
            },
        )


class AsyncFundingGateService:
    """Async version of FundingGateService for use with AsyncSession."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def evaluate_commit_gate(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        pay_run_id: str | UUID,
        funding_model: str,
        idempotency_key: str,
        strict: bool = True,
    ) -> GateResult:
        """Async version of evaluate_commit_gate."""
        # Check for existing evaluation
        existing = await self._get_existing_evaluation(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        if existing:
            return existing

        # Compute requirements
        requirement = await self._compute_funding_requirement(
            pay_run_id=pay_run_id,
            funding_model=funding_model,
        )

        # Get available balance
        available = await self._get_available_balance(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
        )

        # Evaluate
        reasons: list[dict[str, Any]] = []
        required = requirement.total

        if available < required:
            reasons.append({
                "code": "INSUFFICIENT_FUNDS",
                "message": f"Funding not received. Required {required} USD, available {available} USD.",
                "shortfall": str(required - available),
            })

        # Check high-risk flags
        high_risk_flags = await self._check_high_risk_flags(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            pay_run_id=pay_run_id,
            requirement=requirement,
        )
        reasons.extend(high_risk_flags)

        outcome = "pass" if not reasons else ("hard_fail" if strict else "soft_fail")

        await self._persist_evaluation(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            pay_run_id=pay_run_id,
            gate_type="commit_gate",
            outcome=outcome,
            required=required,
            available=available,
            reasons=reasons,
            idempotency_key=idempotency_key,
        )

        return GateResult(
            outcome=outcome,
            required_amount=required,
            available_amount=available,
            reasons=reasons,
        )

    async def evaluate_pay_gate(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        pay_run_id: str | UUID,
        idempotency_key: str,
    ) -> GateResult:
        """Async version of evaluate_pay_gate."""
        existing = await self._get_existing_evaluation(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        if existing:
            return existing

        requirement = await self._compute_funding_requirement(
            pay_run_id=pay_run_id,
            funding_model="prefund_all",
        )

        available = await self._get_available_balance(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            include_reservations=True,
        )

        reasons: list[dict[str, Any]] = []
        required = requirement.total

        if available < required:
            reasons.append({
                "code": "INSUFFICIENT_FUNDS_FOR_PAY",
                "message": f"Cannot disburse. Required {required} USD, available {available} USD.",
                "shortfall": str(required - available),
            })

        outcome = "pass" if not reasons else "hard_fail"

        await self._persist_evaluation(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            pay_run_id=pay_run_id,
            gate_type="pay_gate",
            outcome=outcome,
            required=required,
            available=available,
            reasons=reasons,
            idempotency_key=idempotency_key,
        )

        return GateResult(
            outcome=outcome,
            required_amount=required,
            available_amount=available,
            reasons=reasons,
        )

    async def _get_existing_evaluation(
        self,
        *,
        tenant_id: str | UUID,
        idempotency_key: str,
    ) -> GateResult | None:
        """Async check for existing evaluation."""
        result = await self.db.execute(
            text("""
                SELECT outcome, required_amount, available_amount, reasons_json
                FROM funding_gate_evaluation
                WHERE tenant_id = :tenant_id AND idempotency_key = :idk
            """),
            {"tenant_id": str(tenant_id), "idk": idempotency_key},
        )
        row = result.fetchone()

        if row:
            return GateResult(
                outcome=row[0],
                required_amount=Decimal(str(row[1])),
                available_amount=Decimal(str(row[2])),
                reasons=row[3] if isinstance(row[3], list) else [],
            )
        return None

    async def _compute_funding_requirement(
        self,
        *,
        pay_run_id: str | UUID,
        funding_model: str,
    ) -> FundingRequirement:
        """Async compute funding requirements."""
        net_result = await self.db.execute(
            text("""
                SELECT COALESCE(SUM(ps.net_pay), 0)
                FROM pay_statement ps
                JOIN pay_run_employee pre ON pre.pay_run_employee_id = ps.pay_run_employee_id
                WHERE pre.pay_run_id = :pay_run_id
            """),
            {"pay_run_id": str(pay_run_id)},
        )
        net_pay = Decimal(str(net_result.scalar() or 0))

        tax_result = await self.db.execute(
            text("""
                SELECT COALESCE(SUM(pli.amount), 0)
                FROM pay_line_item pli
                JOIN pay_statement ps ON ps.pay_statement_id = pli.pay_statement_id
                JOIN pay_run_employee pre ON pre.pay_run_employee_id = ps.pay_run_employee_id
                WHERE pre.pay_run_id = :pay_run_id
                  AND pli.category = 'employer_tax'
            """),
            {"pay_run_id": str(pay_run_id)},
        )
        taxes = Decimal(str(tax_result.scalar() or 0))

        third_party_result = await self.db.execute(
            text("""
                SELECT COALESCE(SUM(pli.amount), 0)
                FROM pay_line_item pli
                JOIN pay_statement ps ON ps.pay_statement_id = pli.pay_statement_id
                JOIN pay_run_employee pre ON pre.pay_run_employee_id = ps.pay_run_employee_id
                WHERE pre.pay_run_id = :pay_run_id
                  AND pli.category = 'deduction'
                  AND pli.is_third_party_remit = true
            """),
            {"pay_run_id": str(pay_run_id)},
        )
        third_party = Decimal(str(third_party_result.scalar() or 0))

        req = FundingRequirement(net_pay=net_pay, taxes=taxes, third_party=third_party)

        if funding_model == "net_only":
            req.taxes = Decimal("0")
            req.third_party = Decimal("0")
        elif funding_model == "net_and_third_party":
            req.taxes = Decimal("0")

        return req

    async def _get_available_balance(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        include_reservations: bool = False,
    ) -> Decimal:
        """Async get available balance."""
        result = await self.db.execute(
            text("""
                WITH acct AS (
                    SELECT psp_ledger_account_id
                    FROM psp_ledger_account
                    WHERE tenant_id = :tenant_id
                      AND legal_entity_id = :le
                      AND account_type = 'client_funding_clearing'
                    LIMIT 1
                ),
                credits AS (
                    SELECT COALESCE(SUM(e.amount), 0) AS c
                    FROM psp_ledger_entry e, acct
                    WHERE e.tenant_id = :tenant_id
                      AND e.credit_account_id = acct.psp_ledger_account_id
                ),
                debits AS (
                    SELECT COALESCE(SUM(e.amount), 0) AS d
                    FROM psp_ledger_entry e, acct
                    WHERE e.tenant_id = :tenant_id
                      AND e.debit_account_id = acct.psp_ledger_account_id
                )
                SELECT (credits.c - debits.d)
                FROM credits, debits
            """),
            {"tenant_id": str(tenant_id), "le": str(legal_entity_id)},
        )
        available = Decimal(str(result.scalar() or 0))

        if include_reservations:
            reserved_result = await self.db.execute(
                text("""
                    SELECT COALESCE(SUM(amount), 0)
                    FROM psp_reservation
                    WHERE tenant_id = :tenant_id
                      AND legal_entity_id = :le
                      AND status = 'active'
                """),
                {"tenant_id": str(tenant_id), "le": str(legal_entity_id)},
            )
            available -= Decimal(str(reserved_result.scalar() or 0))

        return available

    async def _check_high_risk_flags(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        pay_run_id: str | UUID,
        requirement: FundingRequirement,
    ) -> list[dict[str, Any]]:
        """Async check high-risk flags."""
        flags: list[dict[str, Any]] = []

        avg_result = await self.db.execute(
            text("""
                SELECT AVG(total_amount)
                FROM (
                    SELECT SUM(ps.net_pay) AS total_amount
                    FROM pay_statement ps
                    JOIN pay_run_employee pre ON pre.pay_run_employee_id = ps.pay_run_employee_id
                    JOIN pay_run pr ON pr.pay_run_id = pre.pay_run_id
                    WHERE pr.tenant_id = :tenant_id
                      AND pr.legal_entity_id = :le
                      AND pr.status = 'paid'
                      AND pr.pay_run_id != :pay_run_id
                    GROUP BY pr.pay_run_id
                    ORDER BY pr.check_date DESC
                    LIMIT 6
                ) recent
            """),
            {
                "tenant_id": str(tenant_id),
                "le": str(legal_entity_id),
                "pay_run_id": str(pay_run_id),
            },
        )
        avg = avg_result.scalar()

        if avg:
            avg_decimal = Decimal(str(avg))
            if avg_decimal > 0 and requirement.net_pay > avg_decimal * Decimal("1.5"):
                flags.append({
                    "code": "SPIKE_DETECTED",
                    "message": f"Payroll amount {requirement.net_pay} is 50%+ above recent average {avg_decimal}.",
                    "severity": "warning",
                })

        return flags

    async def _persist_evaluation(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        pay_run_id: str | UUID,
        gate_type: str,
        outcome: str,
        required: Decimal,
        available: Decimal,
        reasons: list[dict[str, Any]],
        idempotency_key: str,
    ) -> None:
        """Async persist evaluation."""
        await self.db.execute(
            text("""
                INSERT INTO funding_gate_evaluation(
                    tenant_id, legal_entity_id, pay_run_id, gate_type,
                    outcome, required_amount, available_amount, reasons_json, idempotency_key
                )
                VALUES (
                    :tenant_id, :le, :pay_run_id, :gate_type,
                    :outcome, :required, :available, :reasons::jsonb, :idk
                )
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            """),
            {
                "tenant_id": str(tenant_id),
                "le": str(legal_entity_id),
                "pay_run_id": str(pay_run_id),
                "gate_type": gate_type,
                "outcome": outcome,
                "required": str(required),
                "available": str(available),
                "reasons": json.dumps(reasons),
                "idk": idempotency_key,
            },
        )
