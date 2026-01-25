from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

@dataclass(frozen=True)
class Balance:
    available: Decimal
    reserved: Decimal
    currency: str = "USD"

class LedgerService:
    """Append-only double-entry ledger posting service.

    Notes:
    - psp_ledger_entry is append-only (DB triggers enforce).
    - idempotency_key is unique per (tenant_id).
    """

    def __init__(self, db: Session):
        self.db = db

    def post_entry(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        idempotency_key: str,
        entry_type: str,
        debit_account_id: str,
        credit_account_id: str,
        amount: Decimal,
        source_type: str,
        source_id: str,
        correlation_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        created_by_user_id: Optional[str] = None,
    ) -> str:
        sql = text("""
        INSERT INTO psp_ledger_entry(
          tenant_id, legal_entity_id, entry_type, debit_account_id, credit_account_id, amount,
          source_type, source_id, correlation_id, idempotency_key, metadata_json, created_by_user_id
        )
        VALUES (
          :tenant_id, :legal_entity_id, :entry_type, :debit_account_id, :credit_account_id, :amount,
          :source_type, :source_id, :correlation_id, :idempotency_key, :metadata_json::jsonb, :created_by_user_id
        )
        ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
        RETURNING psp_ledger_entry_id
        """)
        row = self.db.execute(sql, {
            "tenant_id": tenant_id,
            "legal_entity_id": legal_entity_id,
            "entry_type": entry_type,
            "debit_account_id": debit_account_id,
            "credit_account_id": credit_account_id,
            "amount": str(amount),
            "source_type": source_type,
            "source_id": source_id,
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
            "metadata_json": (metadata or {}),
            "created_by_user_id": created_by_user_id,
        }).fetchone()

        # If conflict, fetch existing id
        if row and row[0]:
            return str(row[0])
        existing = self.db.execute(text("""
          SELECT psp_ledger_entry_id FROM psp_ledger_entry
          WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key
        """), {"tenant_id": tenant_id, "idempotency_key": idempotency_key}).fetchone()
        if not existing:
            raise RuntimeError("Ledger post failed unexpectedly.")
        return str(existing[0])

    def reverse_entry(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        original_entry_id: str,
        idempotency_key: str,
        reason: str,
        created_by_user_id: Optional[str] = None,
    ) -> str:
        # reversal swaps debit/credit
        orig = self.db.execute(text("""
          SELECT entry_type, debit_account_id, credit_account_id, amount, source_type, source_id, correlation_id
          FROM psp_ledger_entry
          WHERE psp_ledger_entry_id = :id AND tenant_id = :tenant_id
        """), {"id": original_entry_id, "tenant_id": tenant_id}).fetchone()
        if not orig:
            raise ValueError("Original entry not found")

        return self.post_entry(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            idempotency_key=idempotency_key,
            entry_type="reversal",
            debit_account_id=str(orig[2]),
            credit_account_id=str(orig[1]),
            amount=Decimal(orig[3]),
            source_type="psp_ledger_entry",
            source_id=original_entry_id,
            correlation_id=str(orig[6]) if orig[6] else None,
            metadata={"reason": reason, "reverses": original_entry_id},
            created_by_user_id=created_by_user_id,
        )

    def get_balance(self, *, tenant_id: str, ledger_account_id: str) -> Balance:
        # Basic derived balance: credits - debits for account. Reservations tracked separately.
        # For production, precompute snapshots and use proper locking. This is a starting point.
        credits = self.db.execute(text("""
          SELECT COALESCE(SUM(amount),0) FROM psp_ledger_entry
          WHERE tenant_id = :tenant_id AND credit_account_id = :acct
        """), {"tenant_id": tenant_id, "acct": ledger_account_id}).scalar()
        debits = self.db.execute(text("""
          SELECT COALESCE(SUM(amount),0) FROM psp_ledger_entry
          WHERE tenant_id = :tenant_id AND debit_account_id = :acct
        """), {"tenant_id": tenant_id, "acct": ledger_account_id}).scalar()
        available = Decimal(str(credits)) - Decimal(str(debits))

        reserved = self.db.execute(text("""
          SELECT COALESCE(SUM(amount),0) FROM psp_reservation
          WHERE tenant_id = :tenant_id AND status = 'active' AND legal_entity_id IN (
            SELECT legal_entity_id FROM psp_ledger_account WHERE psp_ledger_account_id = :acct
          )
        """), {"tenant_id": tenant_id, "acct": ledger_account_id}).scalar()
        return Balance(available=available, reserved=Decimal(str(reserved)))

    def create_reservation(
        self,
        *,
        tenant_id: str,
        legal_entity_id: str,
        reserve_type: str,
        amount: Decimal,
        source_type: str,
        source_id: str,
        correlation_id: Optional[str] = None,
    ) -> str:
        sql = text("""
        INSERT INTO psp_reservation(
          tenant_id, legal_entity_id, reserve_type, amount, source_type, source_id, correlation_id
        )
        VALUES (:tenant_id, :legal_entity_id, :reserve_type, :amount, :source_type, :source_id, :correlation_id)
        RETURNING psp_reservation_id
        """)
        rid = self.db.execute(sql, {
            "tenant_id": tenant_id,
            "legal_entity_id": legal_entity_id,
            "reserve_type": reserve_type,
            "amount": str(amount),
            "source_type": source_type,
            "source_id": source_id,
            "correlation_id": correlation_id,
        }).scalar()
        return str(rid)
