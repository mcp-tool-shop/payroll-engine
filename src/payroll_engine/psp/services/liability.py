"""PSP Liability Attribution Service.

Handles classification of errors, assignment of liability, and tracking of
loss recovery. This is the system that answers "who eats the loss?"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


class ErrorOrigin(str, Enum):
    """Where the error originated."""

    CLIENT = "client"  # Client provided bad data
    PAYROLL_ENGINE = "payroll_engine"  # Our bug
    PROVIDER = "provider"  # Bank/processor error
    BANK = "bank"  # Receiving bank error
    RECIPIENT = "recipient"  # Recipient action


class LiabilityParty(str, Enum):
    """Who bears financial responsibility."""

    EMPLOYER = "employer"
    PSP = "psp"
    PROCESSOR = "processor"
    SHARED = "shared"
    PENDING = "pending"


class RecoveryPath(str, Enum):
    """How loss will be recovered."""

    OFFSET_FUTURE = "offset_future"  # Offset against future payroll
    CLAWBACK = "clawback"  # Recover from recipient
    WRITE_OFF = "write_off"  # Accept as loss
    INSURANCE = "insurance"  # Insurance claim
    DISPUTE = "dispute"  # In dispute resolution
    NONE = "none"  # No recovery needed


class RecoveryStatus(str, Enum):
    """Status of recovery effort."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PARTIAL = "partial"
    COMPLETE = "complete"
    FAILED = "failed"
    WRITTEN_OFF = "written_off"


@dataclass
class LiabilityClassification:
    """Classification of a liability event."""

    error_origin: ErrorOrigin
    liability_party: LiabilityParty
    recovery_path: RecoveryPath | None
    loss_amount: Decimal
    determination_reason: str
    is_recoverable: bool = False
    confidence: str = "high"  # high, medium, low


@dataclass
class LiabilityEvent:
    """A recorded liability event."""

    liability_event_id: UUID
    tenant_id: UUID
    legal_entity_id: UUID
    source_type: str
    source_id: UUID
    error_origin: ErrorOrigin
    liability_party: LiabilityParty
    loss_amount: Decimal
    recovery_path: RecoveryPath | None
    recovery_status: RecoveryStatus
    recovery_amount: Decimal
    determination_reason: str
    created_at: datetime
    resolved_at: datetime | None = None


class LiabilityService:
    """Service for liability attribution and loss tracking.

    Answers the critical question: "Who pays for this failure?"
    """

    def __init__(self, db: Session):
        self.db = db

    def classify_return(
        self,
        *,
        rail: str,
        return_code: str,
        amount: Decimal,
        context: dict[str, Any] | None = None,
    ) -> LiabilityClassification:
        """Classify a payment return based on return code.

        Uses the return_code_reference table for default classifications,
        with context-aware overrides.

        Args:
            rail: Payment rail (ach, fednow, etc.)
            return_code: The return/rejection code
            amount: Amount of the failed payment
            context: Additional context for classification

        Returns:
            LiabilityClassification with recommended attribution
        """
        # Look up default classification
        ref = self.db.execute(
            text("""
                SELECT default_error_origin, default_liability_party, is_recoverable, description
                FROM return_code_reference
                WHERE rail = :rail AND code = :code
            """),
            {"rail": rail, "code": return_code},
        ).fetchone()

        if ref:
            error_origin = ErrorOrigin(ref[0])
            liability_party = LiabilityParty(ref[1])
            is_recoverable = ref[2]
            reason = f"Return code {return_code}: {ref[3]}"
        else:
            # Unknown code - default to pending investigation
            error_origin = ErrorOrigin.RECIPIENT
            liability_party = LiabilityParty.PENDING
            is_recoverable = False
            reason = f"Unknown return code {return_code} - requires investigation"

        # Context-aware overrides
        if context:
            # If this is a repeat failure for same employee, escalate
            if context.get("repeat_failure_count", 0) >= 3:
                liability_party = LiabilityParty.EMPLOYER
                reason += " (repeated failures - employer must update payment info)"

            # If we have evidence of our error, take responsibility
            if context.get("our_data_error"):
                error_origin = ErrorOrigin.PAYROLL_ENGINE
                liability_party = LiabilityParty.PSP
                reason = "PSP data handling error: " + context.get("error_detail", "")

        # Determine recovery path
        if liability_party == LiabilityParty.EMPLOYER and is_recoverable:
            recovery_path = RecoveryPath.OFFSET_FUTURE
        elif liability_party == LiabilityParty.PSP:
            recovery_path = RecoveryPath.WRITE_OFF  # We eat it
        elif liability_party == LiabilityParty.PENDING:
            recovery_path = RecoveryPath.DISPUTE
        else:
            recovery_path = RecoveryPath.NONE

        return LiabilityClassification(
            error_origin=error_origin,
            liability_party=liability_party,
            recovery_path=recovery_path,
            loss_amount=amount,
            determination_reason=reason,
            is_recoverable=is_recoverable,
        )

    def record_liability_event(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        source_type: str,
        source_id: str | UUID,
        classification: LiabilityClassification,
        determined_by_user_id: str | UUID | None = None,
        evidence: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> UUID:
        """Record a liability event for tracking.

        Args:
            tenant_id: Tenant identifier
            legal_entity_id: Legal entity affected
            source_type: Type of source (payment_instruction, psp_settlement_event)
            source_id: ID of source record
            classification: Liability classification
            determined_by_user_id: User who made determination (or None for automated)
            evidence: Supporting documentation
            idempotency_key: Optional deduplication key

        Returns:
            UUID of created liability event
        """
        sql = text("""
            INSERT INTO liability_event(
                tenant_id, legal_entity_id, source_type, source_id,
                error_origin, liability_party, loss_amount,
                recovery_path, recovery_status,
                determined_by_user_id, determination_reason,
                evidence_json, idempotency_key
            )
            VALUES (
                :tenant_id, :legal_entity_id, :source_type, :source_id,
                :error_origin, :liability_party, :loss_amount,
                :recovery_path, 'pending',
                :user_id, :reason,
                :evidence::jsonb, :idk
            )
            ON CONFLICT (tenant_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            DO NOTHING
            RETURNING liability_event_id
        """)

        result = self.db.execute(
            sql,
            {
                "tenant_id": str(tenant_id),
                "legal_entity_id": str(legal_entity_id),
                "source_type": source_type,
                "source_id": str(source_id),
                "error_origin": classification.error_origin.value,
                "liability_party": classification.liability_party.value,
                "loss_amount": str(classification.loss_amount),
                "recovery_path": classification.recovery_path.value if classification.recovery_path else None,
                "user_id": str(determined_by_user_id) if determined_by_user_id else None,
                "reason": classification.determination_reason,
                "evidence": json.dumps(evidence or {}),
                "idk": idempotency_key,
            },
        ).fetchone()

        if result:
            return UUID(str(result[0]))

        # Conflict - fetch existing
        existing = self.db.execute(
            text("""
                SELECT liability_event_id FROM liability_event
                WHERE tenant_id = :tenant_id AND idempotency_key = :idk
            """),
            {"tenant_id": str(tenant_id), "idk": idempotency_key},
        ).fetchone()

        return UUID(str(existing[0])) if existing else UUID(str(result[0]))

    def update_recovery_status(
        self,
        *,
        tenant_id: str | UUID,
        liability_event_id: str | UUID,
        new_status: RecoveryStatus,
        recovery_amount: Decimal | None = None,
        notes: str | None = None,
    ) -> bool:
        """Update the recovery status of a liability event.

        Args:
            tenant_id: Tenant identifier
            liability_event_id: Event to update
            new_status: New recovery status
            recovery_amount: Amount recovered (if applicable)
            notes: Additional notes

        Returns:
            True if updated, False if not found
        """
        update_fields = ["recovery_status = :status"]
        params: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "id": str(liability_event_id),
            "status": new_status.value,
        }

        if recovery_amount is not None:
            update_fields.append("recovery_amount = :amount")
            params["amount"] = str(recovery_amount)

        if new_status in (RecoveryStatus.COMPLETE, RecoveryStatus.WRITTEN_OFF, RecoveryStatus.FAILED):
            update_fields.append("resolved_at = now()")

        sql = text(f"""
            UPDATE liability_event
            SET {', '.join(update_fields)}
            WHERE liability_event_id = :id AND tenant_id = :tenant_id
        """)

        result = self.db.execute(sql, params)
        return result.rowcount > 0

    def update_payment_instruction_liability(
        self,
        *,
        tenant_id: str | UUID,
        payment_instruction_id: str | UUID,
        classification: LiabilityClassification,
    ) -> bool:
        """Update liability fields on a payment instruction.

        Args:
            tenant_id: Tenant identifier
            payment_instruction_id: Instruction to update
            classification: Liability classification

        Returns:
            True if updated
        """
        result = self.db.execute(
            text("""
                UPDATE payment_instruction
                SET error_origin = :error_origin,
                    liability_party = :liability_party,
                    recovery_path = :recovery_path,
                    liability_amount = :amount,
                    liability_notes = :notes
                WHERE payment_instruction_id = :id AND tenant_id = :tenant_id
            """),
            {
                "tenant_id": str(tenant_id),
                "id": str(payment_instruction_id),
                "error_origin": classification.error_origin.value,
                "liability_party": classification.liability_party.value,
                "recovery_path": classification.recovery_path.value if classification.recovery_path else None,
                "amount": str(classification.loss_amount),
                "notes": classification.determination_reason,
            },
        )
        return result.rowcount > 0

    def get_pending_liabilities(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get liability events requiring attention.

        Args:
            tenant_id: Tenant identifier
            legal_entity_id: Optional filter by legal entity
            limit: Maximum results

        Returns:
            List of pending liability events
        """
        params: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "limit": limit,
        }

        sql = """
            SELECT liability_event_id, legal_entity_id, source_type, source_id,
                   error_origin, liability_party, loss_amount, recovery_path,
                   recovery_status, determination_reason, created_at
            FROM liability_event
            WHERE tenant_id = :tenant_id
              AND recovery_status IN ('pending', 'in_progress', 'partial')
        """

        if legal_entity_id:
            sql += " AND legal_entity_id = :legal_entity_id"
            params["legal_entity_id"] = str(legal_entity_id)

        sql += " ORDER BY created_at DESC LIMIT :limit"

        rows = self.db.execute(text(sql), params).fetchall()

        return [
            {
                "liability_event_id": str(row[0]),
                "legal_entity_id": str(row[1]),
                "source_type": row[2],
                "source_id": str(row[3]),
                "error_origin": row[4],
                "liability_party": row[5],
                "loss_amount": str(row[6]),
                "recovery_path": row[7],
                "recovery_status": row[8],
                "determination_reason": row[9],
                "created_at": row[10].isoformat(),
            }
            for row in rows
        ]

    def get_liability_summary(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        """Get summary of liability exposure.

        Args:
            tenant_id: Tenant identifier
            legal_entity_id: Optional filter

        Returns:
            Summary statistics
        """
        params: dict[str, Any] = {"tenant_id": str(tenant_id)}
        le_filter = ""
        if legal_entity_id:
            le_filter = "AND legal_entity_id = :legal_entity_id"
            params["legal_entity_id"] = str(legal_entity_id)

        # Total by liability party
        by_party = self.db.execute(
            text(f"""
                SELECT liability_party, SUM(loss_amount) as total, COUNT(*) as count
                FROM liability_event
                WHERE tenant_id = :tenant_id {le_filter}
                GROUP BY liability_party
            """),
            params,
        ).fetchall()

        # Total by recovery status
        by_status = self.db.execute(
            text(f"""
                SELECT recovery_status,
                       SUM(loss_amount) as total_loss,
                       SUM(recovery_amount) as total_recovered,
                       COUNT(*) as count
                FROM liability_event
                WHERE tenant_id = :tenant_id {le_filter}
                GROUP BY recovery_status
            """),
            params,
        ).fetchall()

        return {
            "by_liability_party": {
                row[0]: {"total": str(row[1]), "count": row[2]} for row in by_party
            },
            "by_recovery_status": {
                row[0]: {
                    "total_loss": str(row[1]),
                    "total_recovered": str(row[2]),
                    "count": row[3],
                }
                for row in by_status
            },
        }


class AsyncLiabilityService:
    """Async version of LiabilityService."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def classify_return(
        self,
        *,
        rail: str,
        return_code: str,
        amount: Decimal,
        context: dict[str, Any] | None = None,
    ) -> LiabilityClassification:
        """Async classify a payment return."""
        result = await self.db.execute(
            text("""
                SELECT default_error_origin, default_liability_party, is_recoverable, description
                FROM return_code_reference
                WHERE rail = :rail AND code = :code
            """),
            {"rail": rail, "code": return_code},
        )
        ref = result.fetchone()

        if ref:
            error_origin = ErrorOrigin(ref[0])
            liability_party = LiabilityParty(ref[1])
            is_recoverable = ref[2]
            reason = f"Return code {return_code}: {ref[3]}"
        else:
            error_origin = ErrorOrigin.RECIPIENT
            liability_party = LiabilityParty.PENDING
            is_recoverable = False
            reason = f"Unknown return code {return_code} - requires investigation"

        if context:
            if context.get("repeat_failure_count", 0) >= 3:
                liability_party = LiabilityParty.EMPLOYER
                reason += " (repeated failures - employer must update payment info)"

            if context.get("our_data_error"):
                error_origin = ErrorOrigin.PAYROLL_ENGINE
                liability_party = LiabilityParty.PSP
                reason = "PSP data handling error: " + context.get("error_detail", "")

        if liability_party == LiabilityParty.EMPLOYER and is_recoverable:
            recovery_path = RecoveryPath.OFFSET_FUTURE
        elif liability_party == LiabilityParty.PSP:
            recovery_path = RecoveryPath.WRITE_OFF
        elif liability_party == LiabilityParty.PENDING:
            recovery_path = RecoveryPath.DISPUTE
        else:
            recovery_path = RecoveryPath.NONE

        return LiabilityClassification(
            error_origin=error_origin,
            liability_party=liability_party,
            recovery_path=recovery_path,
            loss_amount=amount,
            determination_reason=reason,
            is_recoverable=is_recoverable,
        )

    async def record_liability_event(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        source_type: str,
        source_id: str | UUID,
        classification: LiabilityClassification,
        determined_by_user_id: str | UUID | None = None,
        evidence: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> UUID:
        """Async record a liability event."""
        sql = text("""
            INSERT INTO liability_event(
                tenant_id, legal_entity_id, source_type, source_id,
                error_origin, liability_party, loss_amount,
                recovery_path, recovery_status,
                determined_by_user_id, determination_reason,
                evidence_json, idempotency_key
            )
            VALUES (
                :tenant_id, :legal_entity_id, :source_type, :source_id,
                :error_origin, :liability_party, :loss_amount,
                :recovery_path, 'pending',
                :user_id, :reason,
                :evidence::jsonb, :idk
            )
            ON CONFLICT (tenant_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            DO NOTHING
            RETURNING liability_event_id
        """)

        result = await self.db.execute(
            sql,
            {
                "tenant_id": str(tenant_id),
                "legal_entity_id": str(legal_entity_id),
                "source_type": source_type,
                "source_id": str(source_id),
                "error_origin": classification.error_origin.value,
                "liability_party": classification.liability_party.value,
                "loss_amount": str(classification.loss_amount),
                "recovery_path": classification.recovery_path.value if classification.recovery_path else None,
                "user_id": str(determined_by_user_id) if determined_by_user_id else None,
                "reason": classification.determination_reason,
                "evidence": json.dumps(evidence or {}),
                "idk": idempotency_key,
            },
        )
        row = result.fetchone()

        if row:
            return UUID(str(row[0]))

        existing_result = await self.db.execute(
            text("""
                SELECT liability_event_id FROM liability_event
                WHERE tenant_id = :tenant_id AND idempotency_key = :idk
            """),
            {"tenant_id": str(tenant_id), "idk": idempotency_key},
        )
        existing = existing_result.fetchone()
        return UUID(str(existing[0]))
