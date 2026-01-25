"""PSP Ledger Service - Append-only double-entry posting.

Provides idempotent, transactional posting of ledger entries with:
- Double-entry (debit + credit accounts)
- Idempotency via (tenant_id, idempotency_key) uniqueness
- Reversal-based corrections (no updates/deletes)
- Balance computation and reservations
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Balance:
    """Account balance with available and reserved amounts."""

    available: Decimal
    reserved: Decimal
    currency: str = "USD"

    @property
    def unreserved(self) -> Decimal:
        """Amount available minus reservations."""
        return self.available - self.reserved


@dataclass(frozen=True)
class PostResult:
    """Result of a ledger posting operation.

    IMPORTANT: Always check `is_new` to determine if downstream actions needed.
    If `is_new=False`, this was a duplicate request and the existing entry was returned.
    Do NOT assume success means a new entry was created.
    """

    entry_id: UUID
    is_new: bool  # True if newly created, False if existing (idempotent duplicate)
    entry_type: str

    @property
    def was_duplicate(self) -> bool:
        """Alias for backwards compatibility. Prefer checking `is_new`."""
        return not self.is_new


class LedgerService:
    """Append-only double-entry ledger posting service.

    Notes:
    - psp_ledger_entry is append-only (DB triggers enforce).
    - idempotency_key is unique per (tenant_id).
    - All amounts must be positive (reversals swap debit/credit).
    """

    def __init__(self, db: Session):
        self.db = db

    def post_entry(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        idempotency_key: str,
        entry_type: str,
        debit_account_id: str | UUID,
        credit_account_id: str | UUID,
        amount: Decimal,
        source_type: str,
        source_id: str | UUID,
        correlation_id: str | UUID | None = None,
        metadata: dict[str, Any] | None = None,
        created_by_user_id: str | UUID | None = None,
    ) -> PostResult:
        """Post a double-entry ledger entry.

        Args:
            tenant_id: Tenant identifier
            legal_entity_id: Legal entity for the posting
            idempotency_key: Unique key for deduplication
            entry_type: Type of entry (funding_received, reversal, etc.)
            debit_account_id: Account to debit
            credit_account_id: Account to credit
            amount: Positive amount to post
            source_type: Type of source document
            source_id: ID of source document
            correlation_id: Optional correlation for related entries
            metadata: Optional JSON metadata
            created_by_user_id: Optional user who created entry

        Returns:
            PostResult with entry_id and whether it was a duplicate
        """
        if amount <= 0:
            raise ValueError("Amount must be positive")

        sql = text("""
            INSERT INTO psp_ledger_entry(
                tenant_id, legal_entity_id, entry_type, debit_account_id, credit_account_id,
                amount, source_type, source_id, correlation_id, idempotency_key,
                metadata_json, created_by_user_id
            )
            VALUES (
                :tenant_id, :legal_entity_id, :entry_type, :debit_account_id, :credit_account_id,
                :amount, :source_type, :source_id, :correlation_id, :idempotency_key,
                :metadata_json::jsonb, :created_by_user_id
            )
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING psp_ledger_entry_id
        """)

        params = {
            "tenant_id": str(tenant_id),
            "legal_entity_id": str(legal_entity_id),
            "entry_type": entry_type,
            "debit_account_id": str(debit_account_id),
            "credit_account_id": str(credit_account_id),
            "amount": str(amount),
            "source_type": source_type,
            "source_id": str(source_id),
            "correlation_id": str(correlation_id) if correlation_id else None,
            "idempotency_key": idempotency_key,
            "metadata_json": json.dumps(metadata or {}),
            "created_by_user_id": str(created_by_user_id) if created_by_user_id else None,
        }

        row = self.db.execute(sql, params).fetchone()

        if row and row[0]:
            return PostResult(entry_id=UUID(str(row[0])), is_new=True, entry_type=entry_type)

        # Conflict occurred - fetch existing entry
        existing = self.db.execute(
            text("""
                SELECT psp_ledger_entry_id, entry_type
                FROM psp_ledger_entry
                WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key
            """),
            {"tenant_id": str(tenant_id), "idempotency_key": idempotency_key},
        ).fetchone()

        if not existing:
            raise RuntimeError("Ledger post failed unexpectedly - no entry created or found")

        return PostResult(entry_id=UUID(str(existing[0])), is_new=False, entry_type=existing[1])

    def reverse_entry(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        original_entry_id: str | UUID,
        idempotency_key: str,
        reason: str,
        created_by_user_id: str | UUID | None = None,
    ) -> PostResult:
        """Create a reversal entry for an existing entry.

        Swaps debit and credit accounts to reverse the original posting.

        Args:
            tenant_id: Tenant identifier
            legal_entity_id: Legal entity for the reversal
            original_entry_id: ID of entry to reverse
            idempotency_key: Unique key for deduplication
            reason: Reason for the reversal
            created_by_user_id: Optional user who initiated reversal

        Returns:
            PostResult for the reversal entry
        """
        # Fetch original entry
        orig = self.db.execute(
            text("""
                SELECT entry_type, debit_account_id, credit_account_id, amount,
                       source_type, source_id, correlation_id
                FROM psp_ledger_entry
                WHERE psp_ledger_entry_id = :id AND tenant_id = :tenant_id
            """),
            {"id": str(original_entry_id), "tenant_id": str(tenant_id)},
        ).fetchone()

        if not orig:
            raise ValueError(f"Original entry {original_entry_id} not found for tenant {tenant_id}")

        # Post reversal with swapped accounts
        return self.post_entry(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            idempotency_key=idempotency_key,
            entry_type="reversal",
            debit_account_id=orig[2],  # credit becomes debit
            credit_account_id=orig[1],  # debit becomes credit
            amount=Decimal(str(orig[3])),
            source_type="psp_ledger_entry",
            source_id=str(original_entry_id),
            correlation_id=str(orig[6]) if orig[6] else None,
            metadata={"reason": reason, "reverses": str(original_entry_id), "original_type": orig[0]},
            created_by_user_id=created_by_user_id,
        )

    def get_balance(self, *, tenant_id: str | UUID, ledger_account_id: str | UUID) -> Balance:
        """Compute the current balance for a ledger account.

        Balance = sum(credits) - sum(debits) for the account.
        Reserved = sum of active reservations for the legal entity.

        Args:
            tenant_id: Tenant identifier
            ledger_account_id: The account to get balance for

        Returns:
            Balance with available and reserved amounts
        """
        # Get credits (where account is credited)
        credits = self.db.execute(
            text("""
                SELECT COALESCE(SUM(amount), 0)
                FROM psp_ledger_entry
                WHERE tenant_id = :tenant_id AND credit_account_id = :acct
            """),
            {"tenant_id": str(tenant_id), "acct": str(ledger_account_id)},
        ).scalar()

        # Get debits (where account is debited)
        debits = self.db.execute(
            text("""
                SELECT COALESCE(SUM(amount), 0)
                FROM psp_ledger_entry
                WHERE tenant_id = :tenant_id AND debit_account_id = :acct
            """),
            {"tenant_id": str(tenant_id), "acct": str(ledger_account_id)},
        ).scalar()

        available = Decimal(str(credits)) - Decimal(str(debits))

        # Get active reservations for the legal entity of this account
        reserved = self.db.execute(
            text("""
                SELECT COALESCE(SUM(r.amount), 0)
                FROM psp_reservation r
                JOIN psp_ledger_account a ON a.legal_entity_id = r.legal_entity_id
                WHERE r.tenant_id = :tenant_id
                  AND r.status = 'active'
                  AND a.psp_ledger_account_id = :acct
            """),
            {"tenant_id": str(tenant_id), "acct": str(ledger_account_id)},
        ).scalar()

        return Balance(available=available, reserved=Decimal(str(reserved)))

    def create_reservation(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        reserve_type: str,
        amount: Decimal,
        source_type: str,
        source_id: str | UUID,
        correlation_id: str | UUID | None = None,
    ) -> UUID:
        """Create a reservation to hold funds.

        Reservations prevent overspend without external movement.

        Args:
            tenant_id: Tenant identifier
            legal_entity_id: Legal entity for the reservation
            reserve_type: Type (net_pay, tax, third_party, fees)
            amount: Positive amount to reserve
            source_type: Type of source document
            source_id: ID of source document
            correlation_id: Optional correlation ID

        Returns:
            UUID of the created reservation
        """
        if amount <= 0:
            raise ValueError("Reservation amount must be positive")

        if reserve_type not in ("net_pay", "tax", "third_party", "fees"):
            raise ValueError(f"Invalid reserve_type: {reserve_type}")

        sql = text("""
            INSERT INTO psp_reservation(
                tenant_id, legal_entity_id, reserve_type, amount,
                source_type, source_id, correlation_id
            )
            VALUES (
                :tenant_id, :legal_entity_id, :reserve_type, :amount,
                :source_type, :source_id, :correlation_id
            )
            RETURNING psp_reservation_id
        """)

        result = self.db.execute(
            sql,
            {
                "tenant_id": str(tenant_id),
                "legal_entity_id": str(legal_entity_id),
                "reserve_type": reserve_type,
                "amount": str(amount),
                "source_type": source_type,
                "source_id": str(source_id),
                "correlation_id": str(correlation_id) if correlation_id else None,
            },
        ).scalar()

        return UUID(str(result))

    def release_reservation(
        self,
        *,
        tenant_id: str | UUID,
        reservation_id: str | UUID,
        consumed: bool = False,
    ) -> bool:
        """Release or consume a reservation.

        Args:
            tenant_id: Tenant identifier
            reservation_id: ID of reservation to release
            consumed: If True, mark as consumed; otherwise released

        Returns:
            True if reservation was updated, False if not found or already released
        """
        new_status = "consumed" if consumed else "released"

        result = self.db.execute(
            text("""
                UPDATE psp_reservation
                SET status = :new_status, released_at = now()
                WHERE psp_reservation_id = :id
                  AND tenant_id = :tenant_id
                  AND status = 'active'
            """),
            {
                "new_status": new_status,
                "id": str(reservation_id),
                "tenant_id": str(tenant_id),
            },
        )

        return result.rowcount > 0

    def get_or_create_account(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        account_type: str,
        currency: str = "USD",
    ) -> UUID:
        """Get existing or create a new ledger account.

        Args:
            tenant_id: Tenant identifier
            legal_entity_id: Legal entity for the account
            account_type: Account type (client_funding_clearing, etc.)
            currency: Currency code

        Returns:
            UUID of the account (existing or newly created)
        """
        # Try to insert, ignore conflict
        self.db.execute(
            text("""
                INSERT INTO psp_ledger_account(tenant_id, legal_entity_id, account_type, currency)
                VALUES (:tenant_id, :legal_entity_id, :account_type, :currency)
                ON CONFLICT (tenant_id, legal_entity_id, account_type, currency) DO NOTHING
            """),
            {
                "tenant_id": str(tenant_id),
                "legal_entity_id": str(legal_entity_id),
                "account_type": account_type,
                "currency": currency,
            },
        )

        # Fetch the account ID
        result = self.db.execute(
            text("""
                SELECT psp_ledger_account_id
                FROM psp_ledger_account
                WHERE tenant_id = :tenant_id
                  AND legal_entity_id = :legal_entity_id
                  AND account_type = :account_type
                  AND currency = :currency
            """),
            {
                "tenant_id": str(tenant_id),
                "legal_entity_id": str(legal_entity_id),
                "account_type": account_type,
                "currency": currency,
            },
        ).scalar()

        return UUID(str(result))


class AsyncLedgerService:
    """Async version of LedgerService for use with AsyncSession."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def post_entry(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        idempotency_key: str,
        entry_type: str,
        debit_account_id: str | UUID,
        credit_account_id: str | UUID,
        amount: Decimal,
        source_type: str,
        source_id: str | UUID,
        correlation_id: str | UUID | None = None,
        metadata: dict[str, Any] | None = None,
        created_by_user_id: str | UUID | None = None,
    ) -> PostResult:
        """Async version of post_entry."""
        if amount <= 0:
            raise ValueError("Amount must be positive")

        sql = text("""
            INSERT INTO psp_ledger_entry(
                tenant_id, legal_entity_id, entry_type, debit_account_id, credit_account_id,
                amount, source_type, source_id, correlation_id, idempotency_key,
                metadata_json, created_by_user_id
            )
            VALUES (
                :tenant_id, :legal_entity_id, :entry_type, :debit_account_id, :credit_account_id,
                :amount, :source_type, :source_id, :correlation_id, :idempotency_key,
                :metadata_json::jsonb, :created_by_user_id
            )
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING psp_ledger_entry_id
        """)

        params = {
            "tenant_id": str(tenant_id),
            "legal_entity_id": str(legal_entity_id),
            "entry_type": entry_type,
            "debit_account_id": str(debit_account_id),
            "credit_account_id": str(credit_account_id),
            "amount": str(amount),
            "source_type": source_type,
            "source_id": str(source_id),
            "correlation_id": str(correlation_id) if correlation_id else None,
            "idempotency_key": idempotency_key,
            "metadata_json": json.dumps(metadata or {}),
            "created_by_user_id": str(created_by_user_id) if created_by_user_id else None,
        }

        result = await self.db.execute(sql, params)
        row = result.fetchone()

        if row and row[0]:
            return PostResult(entry_id=UUID(str(row[0])), is_new=True, entry_type=entry_type)

        # Conflict - fetch existing
        result = await self.db.execute(
            text("""
                SELECT psp_ledger_entry_id, entry_type
                FROM psp_ledger_entry
                WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key
            """),
            {"tenant_id": str(tenant_id), "idempotency_key": idempotency_key},
        )
        existing = result.fetchone()

        if not existing:
            raise RuntimeError("Ledger post failed unexpectedly")

        return PostResult(entry_id=UUID(str(existing[0])), is_new=False, entry_type=existing[1])

    async def reverse_entry(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        original_entry_id: str | UUID,
        idempotency_key: str,
        reason: str,
        created_by_user_id: str | UUID | None = None,
    ) -> PostResult:
        """Async version of reverse_entry."""
        result = await self.db.execute(
            text("""
                SELECT entry_type, debit_account_id, credit_account_id, amount,
                       source_type, source_id, correlation_id
                FROM psp_ledger_entry
                WHERE psp_ledger_entry_id = :id AND tenant_id = :tenant_id
            """),
            {"id": str(original_entry_id), "tenant_id": str(tenant_id)},
        )
        orig = result.fetchone()

        if not orig:
            raise ValueError(f"Original entry {original_entry_id} not found")

        return await self.post_entry(
            tenant_id=tenant_id,
            legal_entity_id=legal_entity_id,
            idempotency_key=idempotency_key,
            entry_type="reversal",
            debit_account_id=orig[2],
            credit_account_id=orig[1],
            amount=Decimal(str(orig[3])),
            source_type="psp_ledger_entry",
            source_id=str(original_entry_id),
            correlation_id=str(orig[6]) if orig[6] else None,
            metadata={"reason": reason, "reverses": str(original_entry_id), "original_type": orig[0]},
            created_by_user_id=created_by_user_id,
        )

    async def get_balance(self, *, tenant_id: str | UUID, ledger_account_id: str | UUID) -> Balance:
        """Async version of get_balance."""
        credits_result = await self.db.execute(
            text("""
                SELECT COALESCE(SUM(amount), 0)
                FROM psp_ledger_entry
                WHERE tenant_id = :tenant_id AND credit_account_id = :acct
            """),
            {"tenant_id": str(tenant_id), "acct": str(ledger_account_id)},
        )
        credits = credits_result.scalar()

        debits_result = await self.db.execute(
            text("""
                SELECT COALESCE(SUM(amount), 0)
                FROM psp_ledger_entry
                WHERE tenant_id = :tenant_id AND debit_account_id = :acct
            """),
            {"tenant_id": str(tenant_id), "acct": str(ledger_account_id)},
        )
        debits = debits_result.scalar()

        available = Decimal(str(credits)) - Decimal(str(debits))

        reserved_result = await self.db.execute(
            text("""
                SELECT COALESCE(SUM(r.amount), 0)
                FROM psp_reservation r
                JOIN psp_ledger_account a ON a.legal_entity_id = r.legal_entity_id
                WHERE r.tenant_id = :tenant_id
                  AND r.status = 'active'
                  AND a.psp_ledger_account_id = :acct
            """),
            {"tenant_id": str(tenant_id), "acct": str(ledger_account_id)},
        )
        reserved = reserved_result.scalar()

        return Balance(available=available, reserved=Decimal(str(reserved)))

    async def create_reservation(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        reserve_type: str,
        amount: Decimal,
        source_type: str,
        source_id: str | UUID,
        correlation_id: str | UUID | None = None,
    ) -> UUID:
        """Async version of create_reservation."""
        if amount <= 0:
            raise ValueError("Reservation amount must be positive")

        if reserve_type not in ("net_pay", "tax", "third_party", "fees"):
            raise ValueError(f"Invalid reserve_type: {reserve_type}")

        result = await self.db.execute(
            text("""
                INSERT INTO psp_reservation(
                    tenant_id, legal_entity_id, reserve_type, amount,
                    source_type, source_id, correlation_id
                )
                VALUES (
                    :tenant_id, :legal_entity_id, :reserve_type, :amount,
                    :source_type, :source_id, :correlation_id
                )
                RETURNING psp_reservation_id
            """),
            {
                "tenant_id": str(tenant_id),
                "legal_entity_id": str(legal_entity_id),
                "reserve_type": reserve_type,
                "amount": str(amount),
                "source_type": source_type,
                "source_id": str(source_id),
                "correlation_id": str(correlation_id) if correlation_id else None,
            },
        )

        return UUID(str(result.scalar()))

    async def release_reservation(
        self,
        *,
        tenant_id: str | UUID,
        reservation_id: str | UUID,
        consumed: bool = False,
    ) -> bool:
        """Async version of release_reservation."""
        new_status = "consumed" if consumed else "released"

        result = await self.db.execute(
            text("""
                UPDATE psp_reservation
                SET status = :new_status, released_at = now()
                WHERE psp_reservation_id = :id
                  AND tenant_id = :tenant_id
                  AND status = 'active'
            """),
            {
                "new_status": new_status,
                "id": str(reservation_id),
                "tenant_id": str(tenant_id),
            },
        )

        return result.rowcount > 0

    async def get_or_create_account(
        self,
        *,
        tenant_id: str | UUID,
        legal_entity_id: str | UUID,
        account_type: str,
        currency: str = "USD",
    ) -> UUID:
        """Async version of get_or_create_account."""
        await self.db.execute(
            text("""
                INSERT INTO psp_ledger_account(tenant_id, legal_entity_id, account_type, currency)
                VALUES (:tenant_id, :legal_entity_id, :account_type, :currency)
                ON CONFLICT (tenant_id, legal_entity_id, account_type, currency) DO NOTHING
            """),
            {
                "tenant_id": str(tenant_id),
                "legal_entity_id": str(legal_entity_id),
                "account_type": account_type,
                "currency": currency,
            },
        )

        result = await self.db.execute(
            text("""
                SELECT psp_ledger_account_id
                FROM psp_ledger_account
                WHERE tenant_id = :tenant_id
                  AND legal_entity_id = :legal_entity_id
                  AND account_type = :account_type
                  AND currency = :currency
            """),
            {
                "tenant_id": str(tenant_id),
                "legal_entity_id": str(legal_entity_id),
                "account_type": account_type,
                "currency": currency,
            },
        )

        return UUID(str(result.scalar()))
