"""PSP Payment Orchestrator - Instruction-based payment execution.

Orchestrates payment execution through:
1. Payment instruction creation (idempotent)
2. Provider submission with attempt tracking
3. Status updates from provider callbacks/polling
4. Settlement reconciliation
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from payroll_engine.psp.providers.base import PaymentRailProvider, SubmitResult
from payroll_engine.psp.services.ledger_service import LedgerService, AsyncLedgerService


@dataclass(frozen=True)
class InstructionResult:
    """Result of payment instruction creation."""

    instruction_id: UUID
    was_duplicate: bool
    status: str


@dataclass(frozen=True)
class SubmissionResult:
    """Result of payment submission to provider."""

    instruction_id: UUID
    attempt_id: UUID | None
    provider_request_id: str
    accepted: bool
    message: str


class PaymentOrchestrator:
    """Payment orchestration service.

    Coordinates payment instruction lifecycle:
    - Create instructions from payroll outputs
    - Submit to payment rail providers
    - Track attempts and status changes
    - Record ledger entries for state transitions
    """

    def __init__(
        self,
        db: Session,
        ledger: LedgerService,
        provider: PaymentRailProvider,
    ):
        self.db = db
        self.ledger = ledger
        self.provider = provider

    def create_employee_net_instruction(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        employee_id: str | UUID,
        pay_statement_id: str | UUID,
        amount: Decimal,
        idempotency_key: str,
        requested_settlement_date: date | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InstructionResult:
        """Create a payment instruction for employee net pay.

        Args:
            tenant_id: Tenant identifier
            legal_entity_id: Legal entity making the payment
            employee_id: Employee receiving the payment
            pay_statement_id: Source pay statement
            amount: Payment amount
            idempotency_key: Unique key for deduplication
            requested_settlement_date: Desired settlement date
            metadata: Optional metadata

        Returns:
            InstructionResult with instruction_id and status
        """
        return self._create_instruction(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            purpose="employee_net",
            direction="outbound",
            payee_type="employee",
            payee_ref_id=employee_id,
            source_type="pay_statement",
            source_id=pay_statement_id,
            amount=amount,
            idempotency_key=idempotency_key,
            requested_settlement_date=requested_settlement_date,
            metadata=metadata,
        )

    def create_tax_instruction(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        tax_agency_id: str | UUID,
        tax_liability_id: str | UUID,
        amount: Decimal,
        idempotency_key: str,
        requested_settlement_date: date | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InstructionResult:
        """Create a payment instruction for tax remittance."""
        return self._create_instruction(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            purpose="tax_remit",
            direction="outbound",
            payee_type="agency",
            payee_ref_id=tax_agency_id,
            source_type="tax_liability",
            source_id=tax_liability_id,
            amount=amount,
            idempotency_key=idempotency_key,
            requested_settlement_date=requested_settlement_date,
            metadata=metadata,
        )

    def create_third_party_instruction(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        provider_id: str | UUID,
        obligation_id: str | UUID,
        amount: Decimal,
        idempotency_key: str,
        requested_settlement_date: date | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InstructionResult:
        """Create a payment instruction for third-party obligations."""
        return self._create_instruction(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            purpose="third_party",
            direction="outbound",
            payee_type="provider",
            payee_ref_id=provider_id,
            source_type="third_party_obligation",
            source_id=obligation_id,
            amount=amount,
            idempotency_key=idempotency_key,
            requested_settlement_date=requested_settlement_date,
            metadata=metadata,
        )

    def create_funding_debit_instruction(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        client_id: str | UUID,
        funding_request_id: str | UUID,
        amount: Decimal,
        idempotency_key: str,
        requested_settlement_date: date | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InstructionResult:
        """Create a payment instruction for funding debit (ACH pull)."""
        return self._create_instruction(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            purpose="funding_debit",
            direction="inbound",
            payee_type="client",
            payee_ref_id=client_id,
            source_type="funding_request",
            source_id=funding_request_id,
            amount=amount,
            idempotency_key=idempotency_key,
            requested_settlement_date=requested_settlement_date,
            metadata=metadata,
        )

    def _create_instruction(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        purpose: str,
        direction: str,
        payee_type: str,
        payee_ref_id: str | UUID,
        source_type: str,
        source_id: str | UUID,
        amount: Decimal,
        idempotency_key: str,
        requested_settlement_date: date | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InstructionResult:
        """Create a payment instruction (internal)."""
        sql = text("""
            INSERT INTO payment_instruction(
                tenant_id, legal_entity_id, purpose, direction, amount, currency,
                payee_type, payee_ref_id, requested_settlement_date, status,
                idempotency_key, source_type, source_id, metadata_json
            )
            VALUES (
                :tenant_id, :legal_entity_id, :purpose, :direction, :amount, 'USD',
                :payee_type, :payee_ref_id, :rsd, 'created',
                :idk, :source_type, :source_id, :metadata::jsonb
            )
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING payment_instruction_id, status
        """)

        result = self.db.execute(
            sql,
            {
                "tenant_id": str(tenant_id),
                "legal_entity_id": str(legal_entity_id),
                "purpose": purpose,
                "direction": direction,
                "amount": str(amount),
                "payee_type": payee_type,
                "payee_ref_id": str(payee_ref_id),
                "rsd": requested_settlement_date,
                "idk": idempotency_key,
                "source_type": source_type,
                "source_id": str(source_id),
                "metadata": json.dumps(metadata or {}),
            },
        ).fetchone()

        if result and result[0]:
            return InstructionResult(
                instruction_id=UUID(str(result[0])),
                was_duplicate=False,
                status=result[1],
            )

        # Conflict - fetch existing
        existing = self.db.execute(
            text("""
                SELECT payment_instruction_id, status
                FROM payment_instruction
                WHERE tenant_id = :tenant_id AND idempotency_key = :idk
            """),
            {"tenant_id": str(tenant_id), "idk": idempotency_key},
        ).fetchone()

        if not existing:
            raise RuntimeError("Failed to create payment instruction")

        return InstructionResult(
            instruction_id=UUID(str(existing[0])),
            was_duplicate=True,
            status=existing[1],
        )

    def submit(
        self,
        *,
        tenant_id: str | UUID,
        payment_instruction_id: str | UUID,
    ) -> SubmissionResult:
        """Submit a payment instruction to the provider.

        Args:
            tenant_id: Tenant identifier
            payment_instruction_id: Instruction to submit

        Returns:
            SubmissionResult with provider response details
        """
        # Fetch instruction
        instr = self.db.execute(
            text("""
                SELECT payment_instruction_id, amount, idempotency_key, purpose,
                       payee_type, payee_ref_id, tenant_id, legal_entity_id,
                       direction, status, metadata_json
                FROM payment_instruction
                WHERE payment_instruction_id = :id AND tenant_id = :tenant_id
            """),
            {"id": str(payment_instruction_id), "tenant_id": str(tenant_id)},
        ).fetchone()

        if not instr:
            raise ValueError(f"Payment instruction {payment_instruction_id} not found")

        # Check status - only submit if created or queued
        if instr[9] not in ("created", "queued"):
            raise ValueError(f"Cannot submit instruction in status: {instr[9]}")

        # Build provider payload
        instruction_payload = {
            "payment_instruction_id": str(instr[0]),
            "amount": str(instr[1]),
            "idempotency_key": instr[2],
            "purpose": instr[3],
            "payee_type": instr[4],
            "payee_ref_id": str(instr[5]),
            "direction": instr[8],
            "metadata": instr[10] if instr[10] else {},
        }

        # Submit to provider
        submit_result = self.provider.submit(instruction_payload)

        # Determine rail from provider
        caps = self.provider.capabilities()
        rail = self._determine_rail(caps, instr[8])  # direction

        # Record attempt
        attempt_id = self._record_attempt(
            instruction_id=instr[0],
            rail=rail,
            provider_request_id=submit_result.provider_request_id,
            accepted=submit_result.accepted,
            payload=instruction_payload,
        )

        # Update instruction status
        new_status = "submitted" if submit_result.accepted else "failed"
        self.db.execute(
            text("""
                UPDATE payment_instruction
                SET status = :status
                WHERE payment_instruction_id = :id
            """),
            {"status": new_status, "id": str(instr[0])},
        )

        # Record ledger entry for initiated payment
        if submit_result.accepted and instr[3] == "employee_net":
            self._record_payment_initiated_entry(
                tenant_id=str(instr[6]),
                legal_entity_id=str(instr[7]),
                instruction_id=str(instr[0]),
                amount=Decimal(str(instr[1])),
            )

        return SubmissionResult(
            instruction_id=UUID(str(instr[0])),
            attempt_id=UUID(str(attempt_id)) if attempt_id else None,
            provider_request_id=submit_result.provider_request_id,
            accepted=submit_result.accepted,
            message=submit_result.message,
        )

    def _determine_rail(self, caps: Any, direction: str) -> str:
        """Determine the payment rail from provider capabilities."""
        if caps.fednow:
            return "fednow"
        if caps.rtp:
            return "rtp"
        if direction == "inbound" and caps.ach_debit:
            return "ach"
        if direction == "outbound" and caps.ach_credit:
            return "ach"
        if caps.wire:
            return "wire"
        return "ach"  # Default fallback

    def _record_attempt(
        self,
        *,
        instruction_id: UUID | str,
        rail: str,
        provider_request_id: str,
        accepted: bool,
        payload: dict[str, Any],
    ) -> UUID | None:
        """Record a payment attempt."""
        result = self.db.execute(
            text("""
                INSERT INTO payment_attempt(
                    payment_instruction_id, rail, provider, provider_request_id,
                    status, request_payload_json
                )
                VALUES (
                    :pi, :rail, :provider, :req, :status, :payload::jsonb
                )
                ON CONFLICT (provider, provider_request_id) DO NOTHING
                RETURNING payment_attempt_id
            """),
            {
                "pi": str(instruction_id),
                "rail": rail,
                "provider": self.provider.provider_name,
                "req": provider_request_id,
                "status": "accepted" if accepted else "failed",
                "payload": json.dumps(payload),
            },
        ).fetchone()

        return UUID(str(result[0])) if result else None

    def _record_payment_initiated_entry(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        instruction_id: str,
        amount: Decimal,
    ) -> None:
        """Record ledger entry for payment initiation."""
        # Get accounts
        net_pay_account = self.ledger.get_or_create_account(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            account_type="client_net_pay_payable",
        )
        settlement_account = self.ledger.get_or_create_account(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            account_type="psp_settlement_clearing",
        )

        # Post entry: debit net_pay_payable, credit settlement_clearing
        self.ledger.post_entry(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            idempotency_key=f"payment_init_{instruction_id}",
            entry_type="employee_payment_initiated",
            debit_account_id=net_pay_account,
            credit_account_id=settlement_account,
            amount=amount,
            source_type="payment_instruction",
            source_id=instruction_id,
        )

    def update_status(
        self,
        *,
        tenant_id: str | UUID,
        payment_instruction_id: str | UUID,
        new_status: str,
        provider_request_id: str | None = None,
    ) -> bool:
        """Update payment instruction status.

        Used for status updates from provider callbacks/polling.

        Args:
            tenant_id: Tenant identifier
            payment_instruction_id: Instruction to update
            new_status: New status value
            provider_request_id: Optional provider reference

        Returns:
            True if update was applied
        """
        result = self.db.execute(
            text("""
                UPDATE payment_instruction
                SET status = :status
                WHERE payment_instruction_id = :id AND tenant_id = :tenant_id
            """),
            {
                "status": new_status,
                "id": str(payment_instruction_id),
                "tenant_id": str(tenant_id),
            },
        )

        if result.rowcount > 0 and new_status == "settled":
            # Record settlement ledger entry
            instr = self.db.execute(
                text("""
                    SELECT amount, legal_entity_id, purpose
                    FROM payment_instruction
                    WHERE payment_instruction_id = :id
                """),
                {"id": str(payment_instruction_id)},
            ).fetchone()

            if instr and instr[2] == "employee_net":
                self._record_payment_settled_entry(
                    tenant_id=str(tenant_id),
                    legal_entity_id=str(instr[1]),
                    instruction_id=str(payment_instruction_id),
                    amount=Decimal(str(instr[0])),
                )

        return result.rowcount > 0

    def _record_payment_settled_entry(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        instruction_id: str,
        amount: Decimal,
    ) -> None:
        """Record ledger entry for payment settlement."""
        settlement_account = self.ledger.get_or_create_account(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            account_type="psp_settlement_clearing",
        )
        funding_account = self.ledger.get_or_create_account(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            account_type="client_funding_clearing",
        )

        # Post entry: debit settlement_clearing (reduce), credit funding_clearing
        self.ledger.post_entry(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            idempotency_key=f"payment_settled_{instruction_id}",
            entry_type="employee_payment_settled",
            debit_account_id=settlement_account,
            credit_account_id=funding_account,
            amount=amount,
            source_type="payment_instruction",
            source_id=instruction_id,
        )

    def get_instructions_for_submission(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get payment instructions ready for submission.

        Returns instructions in 'created' or 'queued' status.
        """
        params: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "limit": limit,
        }

        sql = """
            SELECT payment_instruction_id, legal_entity_id, purpose, direction,
                   amount, payee_type, payee_ref_id, idempotency_key, status
            FROM payment_instruction
            WHERE tenant_id = :tenant_id
              AND status IN ('created', 'queued')
        """

        if legal_entity_id:
            sql += " AND legal_entity_id = :legal_entity_id"
            params["legal_entity_id"] = str(legal_entity_id)

        sql += " ORDER BY created_at LIMIT :limit"

        rows = self.db.execute(text(sql), params).fetchall()

        return [
            {
                "payment_instruction_id": str(row[0]),
                "legal_entity_id": str(row[1]),
                "purpose": row[2],
                "direction": row[3],
                "amount": str(row[4]),
                "payee_type": row[5],
                "payee_ref_id": str(row[6]),
                "idempotency_key": row[7],
                "status": row[8],
            }
            for row in rows
        ]


class AsyncPaymentOrchestrator:
    """Async version of PaymentOrchestrator."""

    def __init__(
        self,
        db: AsyncSession,
        ledger: AsyncLedgerService,
        provider: PaymentRailProvider,
    ):
        self.db = db
        self.ledger = ledger
        self.provider = provider

    async def create_employee_net_instruction(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        employee_id: str | UUID,
        pay_statement_id: str | UUID,
        amount: Decimal,
        idempotency_key: str,
        requested_settlement_date: date | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InstructionResult:
        """Async version of create_employee_net_instruction."""
        return await self._create_instruction(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            purpose="employee_net",
            direction="outbound",
            payee_type="employee",
            payee_ref_id=employee_id,
            source_type="pay_statement",
            source_id=pay_statement_id,
            amount=amount,
            idempotency_key=idempotency_key,
            requested_settlement_date=requested_settlement_date,
            metadata=metadata,
        )

    async def _create_instruction(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        purpose: str,
        direction: str,
        payee_type: str,
        payee_ref_id: str | UUID,
        source_type: str,
        source_id: str | UUID,
        amount: Decimal,
        idempotency_key: str,
        requested_settlement_date: date | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InstructionResult:
        """Async create instruction."""
        sql = text("""
            INSERT INTO payment_instruction(
                tenant_id, legal_entity_id, purpose, direction, amount, currency,
                payee_type, payee_ref_id, requested_settlement_date, status,
                idempotency_key, source_type, source_id, metadata_json
            )
            VALUES (
                :tenant_id, :legal_entity_id, :purpose, :direction, :amount, 'USD',
                :payee_type, :payee_ref_id, :rsd, 'created',
                :idk, :source_type, :source_id, :metadata::jsonb
            )
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING payment_instruction_id, status
        """)

        result = await self.db.execute(
            sql,
            {
                "tenant_id": str(tenant_id),
                "legal_entity_id": str(legal_entity_id),
                "purpose": purpose,
                "direction": direction,
                "amount": str(amount),
                "payee_type": payee_type,
                "payee_ref_id": str(payee_ref_id),
                "rsd": requested_settlement_date,
                "idk": idempotency_key,
                "source_type": source_type,
                "source_id": str(source_id),
                "metadata": json.dumps(metadata or {}),
            },
        )
        row = result.fetchone()

        if row and row[0]:
            return InstructionResult(
                instruction_id=UUID(str(row[0])),
                was_duplicate=False,
                status=row[1],
            )

        # Fetch existing
        existing_result = await self.db.execute(
            text("""
                SELECT payment_instruction_id, status
                FROM payment_instruction
                WHERE tenant_id = :tenant_id AND idempotency_key = :idk
            """),
            {"tenant_id": str(tenant_id), "idk": idempotency_key},
        )
        existing = existing_result.fetchone()

        if not existing:
            raise RuntimeError("Failed to create payment instruction")

        return InstructionResult(
            instruction_id=UUID(str(existing[0])),
            was_duplicate=True,
            status=existing[1],
        )

    async def submit(
        self,
        *,
        tenant_id: str | UUID,
        payment_instruction_id: str | UUID,
    ) -> SubmissionResult:
        """Async submit instruction to provider."""
        result = await self.db.execute(
            text("""
                SELECT payment_instruction_id, amount, idempotency_key, purpose,
                       payee_type, payee_ref_id, tenant_id, legal_entity_id,
                       direction, status, metadata_json
                FROM payment_instruction
                WHERE payment_instruction_id = :id AND tenant_id = :tenant_id
            """),
            {"id": str(payment_instruction_id), "tenant_id": str(tenant_id)},
        )
        instr = result.fetchone()

        if not instr:
            raise ValueError(f"Payment instruction {payment_instruction_id} not found")

        if instr[9] not in ("created", "queued"):
            raise ValueError(f"Cannot submit instruction in status: {instr[9]}")

        instruction_payload = {
            "payment_instruction_id": str(instr[0]),
            "amount": str(instr[1]),
            "idempotency_key": instr[2],
            "purpose": instr[3],
            "payee_type": instr[4],
            "payee_ref_id": str(instr[5]),
            "direction": instr[8],
            "metadata": instr[10] if instr[10] else {},
        }

        submit_result = self.provider.submit(instruction_payload)

        caps = self.provider.capabilities()
        rail = self._determine_rail(caps, instr[8])

        attempt_id = await self._record_attempt(
            instruction_id=instr[0],
            rail=rail,
            provider_request_id=submit_result.provider_request_id,
            accepted=submit_result.accepted,
            payload=instruction_payload,
        )

        new_status = "submitted" if submit_result.accepted else "failed"
        await self.db.execute(
            text("""
                UPDATE payment_instruction
                SET status = :status
                WHERE payment_instruction_id = :id
            """),
            {"status": new_status, "id": str(instr[0])},
        )

        if submit_result.accepted and instr[3] == "employee_net":
            await self._record_payment_initiated_entry(
                tenant_id=str(instr[6]),
                legal_entity_id=str(instr[7]),
                instruction_id=str(instr[0]),
                amount=Decimal(str(instr[1])),
            )

        return SubmissionResult(
            instruction_id=UUID(str(instr[0])),
            attempt_id=UUID(str(attempt_id)) if attempt_id else None,
            provider_request_id=submit_result.provider_request_id,
            accepted=submit_result.accepted,
            message=submit_result.message,
        )

    def _determine_rail(self, caps: Any, direction: str) -> str:
        """Determine payment rail."""
        if caps.fednow:
            return "fednow"
        if caps.rtp:
            return "rtp"
        if direction == "inbound" and caps.ach_debit:
            return "ach"
        if direction == "outbound" and caps.ach_credit:
            return "ach"
        if caps.wire:
            return "wire"
        return "ach"

    async def _record_attempt(
        self,
        *,
        instruction_id: UUID | str,
        rail: str,
        provider_request_id: str,
        accepted: bool,
        payload: dict[str, Any],
    ) -> UUID | None:
        """Async record attempt."""
        result = await self.db.execute(
            text("""
                INSERT INTO payment_attempt(
                    payment_instruction_id, rail, provider, provider_request_id,
                    status, request_payload_json
                )
                VALUES (
                    :pi, :rail, :provider, :req, :status, :payload::jsonb
                )
                ON CONFLICT (provider, provider_request_id) DO NOTHING
                RETURNING payment_attempt_id
            """),
            {
                "pi": str(instruction_id),
                "rail": rail,
                "provider": self.provider.provider_name,
                "req": provider_request_id,
                "status": "accepted" if accepted else "failed",
                "payload": json.dumps(payload),
            },
        )
        row = result.fetchone()
        return UUID(str(row[0])) if row else None

    async def _record_payment_initiated_entry(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        instruction_id: str,
        amount: Decimal,
    ) -> None:
        """Async record payment initiated entry."""
        net_pay_account = await self.ledger.get_or_create_account(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            account_type="client_net_pay_payable",
        )
        settlement_account = await self.ledger.get_or_create_account(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            account_type="psp_settlement_clearing",
        )

        await self.ledger.post_entry(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            idempotency_key=f"payment_init_{instruction_id}",
            entry_type="employee_payment_initiated",
            debit_account_id=net_pay_account,
            credit_account_id=settlement_account,
            amount=amount,
            source_type="payment_instruction",
            source_id=instruction_id,
        )

    async def update_status(
        self,
        *,
        tenant_id: str | UUID,
        payment_instruction_id: str | UUID,
        new_status: str,
        provider_request_id: str | None = None,
    ) -> bool:
        """Async update status."""
        result = await self.db.execute(
            text("""
                UPDATE payment_instruction
                SET status = :status
                WHERE payment_instruction_id = :id AND tenant_id = :tenant_id
            """),
            {
                "status": new_status,
                "id": str(payment_instruction_id),
                "tenant_id": str(tenant_id),
            },
        )

        if result.rowcount > 0 and new_status == "settled":
            instr_result = await self.db.execute(
                text("""
                    SELECT amount, legal_entity_id, purpose
                    FROM payment_instruction
                    WHERE payment_instruction_id = :id
                """),
                {"id": str(payment_instruction_id)},
            )
            instr = instr_result.fetchone()

            if instr and instr[2] == "employee_net":
                await self._record_payment_settled_entry(
                    tenant_id=str(tenant_id),
                    legal_entity_id=str(instr[1]),
                    instruction_id=str(payment_instruction_id),
                    amount=Decimal(str(instr[0])),
                )

        return result.rowcount > 0

    async def _record_payment_settled_entry(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        instruction_id: str,
        amount: Decimal,
    ) -> None:
        """Async record payment settled entry."""
        settlement_account = await self.ledger.get_or_create_account(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            account_type="psp_settlement_clearing",
        )
        funding_account = await self.ledger.get_or_create_account(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            account_type="client_funding_clearing",
        )

        await self.ledger.post_entry(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            idempotency_key=f"payment_settled_{instruction_id}",
            entry_type="employee_payment_settled",
            debit_account_id=settlement_account,
            credit_account_id=funding_account,
            amount=amount,
            source_type="payment_instruction",
            source_id=instruction_id,
        )
