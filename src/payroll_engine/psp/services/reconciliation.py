"""PSP Reconciliation Job - Settlement reconciliation.

Handles daily reconciliation of settlement events from bank/processor
feeds against ledger entries and payment instructions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from payroll_engine.psp.providers.base import PaymentRailProvider, SettlementRecord
from payroll_engine.psp.services.ledger_service import LedgerService, AsyncLedgerService


@dataclass
class ReconciliationResult:
    """Result of a reconciliation run."""

    reconciliation_date: date
    records_processed: int = 0
    records_matched: int = 0
    records_created: int = 0
    records_failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Whether reconciliation completed without errors."""
        return self.records_failed == 0 and len(self.errors) == 0


class ReconciliationService:
    """Settlement reconciliation service.

    Pulls settlement results from provider adapters and:
    1. Creates psp_settlement_event records (idempotent)
    2. Links settlement events to ledger entries
    3. Posts corresponding ledger entries for state changes
    4. Updates payment instruction statuses
    """

    def __init__(
        self,
        db: Session,
        ledger: LedgerService,
        provider: PaymentRailProvider,
        bank_account_id: str | UUID,
    ):
        self.db = db
        self.ledger = ledger
        self.provider = provider
        self.bank_account_id = str(bank_account_id)

    def run_reconciliation(
        self,
        *,
        reconciliation_date: date,
        tenant_id: str | UUID | None = None,
    ) -> ReconciliationResult:
        """Run reconciliation for a given date.

        Args:
            reconciliation_date: Date to reconcile
            tenant_id: Optional tenant filter

        Returns:
            ReconciliationResult with statistics
        """
        result = ReconciliationResult(reconciliation_date=reconciliation_date)

        # Fetch settlement records from provider
        try:
            records = self.provider.reconcile(reconciliation_date)
        except Exception as e:
            result.errors.append({
                "code": "PROVIDER_ERROR",
                "message": f"Failed to fetch records from provider: {e}",
            })
            return result

        result.records_processed = len(records)

        for record in records:
            try:
                matched = self._process_settlement_record(record, tenant_id)
                if matched:
                    result.records_matched += 1
                else:
                    result.records_created += 1
            except Exception as e:
                result.records_failed += 1
                result.errors.append({
                    "code": "RECORD_ERROR",
                    "trace_id": record.external_trace_id,
                    "message": str(e),
                })

        return result

    def _process_settlement_record(
        self,
        record: SettlementRecord,
        tenant_id: str | UUID | None,
    ) -> bool:
        """Process a single settlement record.

        Returns True if record already existed, False if newly created.
        """
        # Check for existing settlement event (idempotent)
        existing = self.db.execute(
            text("""
                SELECT psp_settlement_event_id, status
                FROM psp_settlement_event
                WHERE psp_bank_account_id = :bank_account_id
                  AND external_trace_id = :trace_id
            """),
            {
                "bank_account_id": self.bank_account_id,
                "trace_id": record.external_trace_id,
            },
        ).fetchone()

        if existing:
            # Update status if changed
            if existing[1] != record.status:
                self.db.execute(
                    text("""
                        UPDATE psp_settlement_event
                        SET status = :status, effective_date = :eff_date
                        WHERE psp_settlement_event_id = :id
                    """),
                    {
                        "status": record.status,
                        "eff_date": record.effective_date,
                        "id": str(existing[0]),
                    },
                )
                self._handle_status_change(
                    settlement_event_id=str(existing[0]),
                    old_status=existing[1],
                    new_status=record.status,
                    amount=Decimal(record.amount),
                    tenant_id=tenant_id,
                )
            return True

        # Determine direction from trace_id pattern or provider
        # This is a simplification - real implementation would parse provider-specific formats
        direction = "outbound"  # Default, would be determined from record metadata

        # Create settlement event
        settlement_id = self.db.execute(
            text("""
                INSERT INTO psp_settlement_event(
                    psp_bank_account_id, rail, direction, amount, currency,
                    status, external_trace_id, effective_date, raw_payload_json
                )
                VALUES (
                    :bank_account_id, :rail, :direction, :amount, :currency,
                    :status, :trace_id, :eff_date, :payload::jsonb
                )
                ON CONFLICT (psp_bank_account_id, external_trace_id) DO NOTHING
                RETURNING psp_settlement_event_id
            """),
            {
                "bank_account_id": self.bank_account_id,
                "rail": self._determine_rail(),
                "direction": direction,
                "amount": record.amount,
                "currency": record.currency,
                "status": record.status,
                "trace_id": record.external_trace_id,
                "eff_date": record.effective_date,
                "payload": json.dumps(record.raw_payload or {}),
            },
        ).scalar()

        if settlement_id:
            # Try to match to payment instruction
            self._match_and_link(
                settlement_event_id=str(settlement_id),
                trace_id=record.external_trace_id,
                status=record.status,
                amount=Decimal(record.amount),
                tenant_id=tenant_id,
            )

        return False

    def _determine_rail(self) -> str:
        """Determine rail from provider capabilities."""
        caps = self.provider.capabilities()
        if caps.fednow:
            return "fednow"
        if caps.rtp:
            return "rtp"
        if caps.ach_credit or caps.ach_debit:
            return "ach"
        if caps.wire:
            return "wire"
        return "internal"

    def _match_and_link(
        self,
        *,
        settlement_event_id: str,
        trace_id: str,
        status: str,
        amount: Decimal,
        tenant_id: str | UUID | None,
    ) -> None:
        """Match settlement event to payment instruction and create links."""
        # Try to find payment attempt by provider_request_id
        params: dict[str, Any] = {"trace_id": trace_id}
        sql = """
            SELECT pa.payment_attempt_id, pa.payment_instruction_id, pi.tenant_id, pi.legal_entity_id
            FROM payment_attempt pa
            JOIN payment_instruction pi ON pi.payment_instruction_id = pa.payment_instruction_id
            WHERE pa.provider_request_id = :trace_id
        """
        if tenant_id:
            sql += " AND pi.tenant_id = :tenant_id"
            params["tenant_id"] = str(tenant_id)

        attempt = self.db.execute(text(sql), params).fetchone()

        if attempt:
            instruction_id = str(attempt[1])
            tenant = str(attempt[2])
            legal_entity = str(attempt[3])

            # Update instruction status based on settlement status
            new_instruction_status = self._map_settlement_to_instruction_status(status)
            if new_instruction_status:
                self.db.execute(
                    text("""
                        UPDATE payment_instruction
                        SET status = :status
                        WHERE payment_instruction_id = :id
                    """),
                    {"status": new_instruction_status, "id": instruction_id},
                )

            # Post ledger entry if settled
            if status == "settled":
                self._post_settlement_ledger_entry(
                    tenant_id=tenant,
                    legal_entity_id=legal_entity,
                    settlement_event_id=settlement_event_id,
                    instruction_id=instruction_id,
                    amount=amount,
                )

    def _map_settlement_to_instruction_status(self, settlement_status: str) -> str | None:
        """Map settlement status to instruction status."""
        mapping = {
            "accepted": "accepted",
            "settled": "settled",
            "failed": "failed",
            "returned": "reversed",
            "reversed": "reversed",
        }
        return mapping.get(settlement_status)

    def _post_settlement_ledger_entry(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        settlement_event_id: str,
        instruction_id: str,
        amount: Decimal,
    ) -> None:
        """Post ledger entry for settlement and create link."""
        # Get accounts
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

        # Post entry
        entry_result = self.ledger.post_entry(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            idempotency_key=f"settlement_{settlement_event_id}",
            entry_type="employee_payment_settled",
            debit_account_id=settlement_account,
            credit_account_id=funding_account,
            amount=amount,
            source_type="psp_settlement_event",
            source_id=settlement_event_id,
        )

        # Create link
        if not entry_result.was_duplicate:
            self.db.execute(
                text("""
                    INSERT INTO psp_settlement_link(psp_settlement_event_id, psp_ledger_entry_id)
                    VALUES (:settlement_id, :entry_id)
                    ON CONFLICT (psp_settlement_event_id, psp_ledger_entry_id) DO NOTHING
                """),
                {
                    "settlement_id": settlement_event_id,
                    "entry_id": str(entry_result.entry_id),
                },
            )

    def _handle_status_change(
        self,
        *,
        settlement_event_id: str,
        old_status: str,
        new_status: str,
        amount: Decimal,
        tenant_id: str | UUID | None,
    ) -> None:
        """Handle settlement status change (e.g., settled -> returned)."""
        if old_status == "settled" and new_status in ("returned", "reversed"):
            # Need to reverse the ledger entry
            # Find the linked ledger entry
            link = self.db.execute(
                text("""
                    SELECT psp_ledger_entry_id, e.tenant_id, e.legal_entity_id
                    FROM psp_settlement_link sl
                    JOIN psp_ledger_entry e ON e.psp_ledger_entry_id = sl.psp_ledger_entry_id
                    WHERE sl.psp_settlement_event_id = :settlement_id
                """),
                {"settlement_id": settlement_event_id},
            ).fetchone()

            if link:
                self.ledger.reverse_entry(
                    tenant_id=str(link[1]),
                    legal_entity_id=str(link[2]),
                    original_entry_id=str(link[0]),
                    idempotency_key=f"settlement_reversal_{settlement_event_id}",
                    reason=f"Settlement status changed from {old_status} to {new_status}",
                )

    def get_unmatched_settlements(
        self,
        *,
        start_date: date,
        end_date: date,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get settlement events that haven't been matched to instructions."""
        rows = self.db.execute(
            text("""
                SELECT se.psp_settlement_event_id, se.external_trace_id,
                       se.amount, se.status, se.effective_date, se.rail
                FROM psp_settlement_event se
                LEFT JOIN psp_settlement_link sl ON sl.psp_settlement_event_id = se.psp_settlement_event_id
                WHERE sl.psp_settlement_link_id IS NULL
                  AND se.psp_bank_account_id = :bank_account_id
                  AND se.effective_date BETWEEN :start_date AND :end_date
                ORDER BY se.effective_date DESC
                LIMIT :limit
            """),
            {
                "bank_account_id": self.bank_account_id,
                "start_date": start_date,
                "end_date": end_date,
                "limit": limit,
            },
        ).fetchall()

        return [
            {
                "settlement_event_id": str(row[0]),
                "external_trace_id": row[1],
                "amount": str(row[2]),
                "status": row[3],
                "effective_date": row[4].isoformat() if row[4] else None,
                "rail": row[5],
            }
            for row in rows
        ]


class AsyncReconciliationService:
    """Async version of ReconciliationService."""

    def __init__(
        self,
        db: AsyncSession,
        ledger: AsyncLedgerService,
        provider: PaymentRailProvider,
        bank_account_id: str | UUID,
    ):
        self.db = db
        self.ledger = ledger
        self.provider = provider
        self.bank_account_id = str(bank_account_id)

    async def run_reconciliation(
        self,
        *,
        reconciliation_date: date,
        tenant_id: str | UUID | None = None,
    ) -> ReconciliationResult:
        """Async run reconciliation."""
        result = ReconciliationResult(reconciliation_date=reconciliation_date)

        try:
            records = self.provider.reconcile(reconciliation_date)
        except Exception as e:
            result.errors.append({
                "code": "PROVIDER_ERROR",
                "message": f"Failed to fetch records from provider: {e}",
            })
            return result

        result.records_processed = len(records)

        for record in records:
            try:
                matched = await self._process_settlement_record(record, tenant_id)
                if matched:
                    result.records_matched += 1
                else:
                    result.records_created += 1
            except Exception as e:
                result.records_failed += 1
                result.errors.append({
                    "code": "RECORD_ERROR",
                    "trace_id": record.external_trace_id,
                    "message": str(e),
                })

        return result

    async def _process_settlement_record(
        self,
        record: SettlementRecord,
        tenant_id: str | UUID | None,
    ) -> bool:
        """Async process a single settlement record."""
        existing_result = await self.db.execute(
            text("""
                SELECT psp_settlement_event_id, status
                FROM psp_settlement_event
                WHERE psp_bank_account_id = :bank_account_id
                  AND external_trace_id = :trace_id
            """),
            {
                "bank_account_id": self.bank_account_id,
                "trace_id": record.external_trace_id,
            },
        )
        existing = existing_result.fetchone()

        if existing:
            if existing[1] != record.status:
                await self.db.execute(
                    text("""
                        UPDATE psp_settlement_event
                        SET status = :status, effective_date = :eff_date
                        WHERE psp_settlement_event_id = :id
                    """),
                    {
                        "status": record.status,
                        "eff_date": record.effective_date,
                        "id": str(existing[0]),
                    },
                )
                await self._handle_status_change(
                    settlement_event_id=str(existing[0]),
                    old_status=existing[1],
                    new_status=record.status,
                    amount=Decimal(record.amount),
                    tenant_id=tenant_id,
                )
            return True

        direction = "outbound"

        settlement_result = await self.db.execute(
            text("""
                INSERT INTO psp_settlement_event(
                    psp_bank_account_id, rail, direction, amount, currency,
                    status, external_trace_id, effective_date, raw_payload_json
                )
                VALUES (
                    :bank_account_id, :rail, :direction, :amount, :currency,
                    :status, :trace_id, :eff_date, :payload::jsonb
                )
                ON CONFLICT (psp_bank_account_id, external_trace_id) DO NOTHING
                RETURNING psp_settlement_event_id
            """),
            {
                "bank_account_id": self.bank_account_id,
                "rail": self._determine_rail(),
                "direction": direction,
                "amount": record.amount,
                "currency": record.currency,
                "status": record.status,
                "trace_id": record.external_trace_id,
                "eff_date": record.effective_date,
                "payload": json.dumps(record.raw_payload or {}),
            },
        )
        settlement_id = settlement_result.scalar()

        if settlement_id:
            await self._match_and_link(
                settlement_event_id=str(settlement_id),
                trace_id=record.external_trace_id,
                status=record.status,
                amount=Decimal(record.amount),
                tenant_id=tenant_id,
            )

        return False

    def _determine_rail(self) -> str:
        """Determine rail from provider."""
        caps = self.provider.capabilities()
        if caps.fednow:
            return "fednow"
        if caps.rtp:
            return "rtp"
        if caps.ach_credit or caps.ach_debit:
            return "ach"
        if caps.wire:
            return "wire"
        return "internal"

    async def _match_and_link(
        self,
        *,
        settlement_event_id: str,
        trace_id: str,
        status: str,
        amount: Decimal,
        tenant_id: str | UUID | None,
    ) -> None:
        """Async match and link settlement to instruction."""
        params: dict[str, Any] = {"trace_id": trace_id}
        sql = """
            SELECT pa.payment_attempt_id, pa.payment_instruction_id, pi.tenant_id, pi.legal_entity_id
            FROM payment_attempt pa
            JOIN payment_instruction pi ON pi.payment_instruction_id = pa.payment_instruction_id
            WHERE pa.provider_request_id = :trace_id
        """
        if tenant_id:
            sql += " AND pi.tenant_id = :tenant_id"
            params["tenant_id"] = str(tenant_id)

        attempt_result = await self.db.execute(text(sql), params)
        attempt = attempt_result.fetchone()

        if attempt:
            instruction_id = str(attempt[1])
            tenant = str(attempt[2])
            legal_entity = str(attempt[3])

            new_instruction_status = self._map_settlement_to_instruction_status(status)
            if new_instruction_status:
                await self.db.execute(
                    text("""
                        UPDATE payment_instruction
                        SET status = :status
                        WHERE payment_instruction_id = :id
                    """),
                    {"status": new_instruction_status, "id": instruction_id},
                )

            if status == "settled":
                await self._post_settlement_ledger_entry(
                    tenant_id=tenant,
                    legal_entity_id=legal_entity,
                    settlement_event_id=settlement_event_id,
                    instruction_id=instruction_id,
                    amount=amount,
                )

    def _map_settlement_to_instruction_status(self, settlement_status: str) -> str | None:
        """Map settlement status to instruction status."""
        mapping = {
            "accepted": "accepted",
            "settled": "settled",
            "failed": "failed",
            "returned": "reversed",
            "reversed": "reversed",
        }
        return mapping.get(settlement_status)

    async def _post_settlement_ledger_entry(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        settlement_event_id: str,
        instruction_id: str,
        amount: Decimal,
    ) -> None:
        """Async post settlement ledger entry."""
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

        entry_result = await self.ledger.post_entry(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            idempotency_key=f"settlement_{settlement_event_id}",
            entry_type="employee_payment_settled",
            debit_account_id=settlement_account,
            credit_account_id=funding_account,
            amount=amount,
            source_type="psp_settlement_event",
            source_id=settlement_event_id,
        )

        if not entry_result.was_duplicate:
            await self.db.execute(
                text("""
                    INSERT INTO psp_settlement_link(psp_settlement_event_id, psp_ledger_entry_id)
                    VALUES (:settlement_id, :entry_id)
                    ON CONFLICT (psp_settlement_event_id, psp_ledger_entry_id) DO NOTHING
                """),
                {
                    "settlement_id": settlement_event_id,
                    "entry_id": str(entry_result.entry_id),
                },
            )

    async def _handle_status_change(
        self,
        *,
        settlement_event_id: str,
        old_status: str,
        new_status: str,
        amount: Decimal,
        tenant_id: str | UUID | None,
    ) -> None:
        """Async handle settlement status change."""
        if old_status == "settled" and new_status in ("returned", "reversed"):
            link_result = await self.db.execute(
                text("""
                    SELECT psp_ledger_entry_id, e.tenant_id, e.legal_entity_id
                    FROM psp_settlement_link sl
                    JOIN psp_ledger_entry e ON e.psp_ledger_entry_id = sl.psp_ledger_entry_id
                    WHERE sl.psp_settlement_event_id = :settlement_id
                """),
                {"settlement_id": settlement_event_id},
            )
            link = link_result.fetchone()

            if link:
                await self.ledger.reverse_entry(
                    tenant_id=str(link[1]),
                    legal_entity_id=str(link[2]),
                    original_entry_id=str(link[0]),
                    idempotency_key=f"settlement_reversal_{settlement_event_id}",
                    reason=f"Settlement status changed from {old_status} to {new_status}",
                )
