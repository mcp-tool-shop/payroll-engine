"""Event store for persistence and replay.

The event store provides:
- Persistent storage of domain events
- Idempotent writes (via event_id)
- Replay capability for rebuilding state
- Filtering by entity, type, time range
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Iterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.psp.events.types import DomainEvent, EventCategory


@dataclass
class StoredEvent:
    """A persisted event record."""

    event_id: UUID
    event_type: str
    category: str
    tenant_id: UUID
    correlation_id: UUID
    causation_id: UUID | None
    timestamp: datetime
    payload: dict[str, Any]
    version: int

    @classmethod
    def from_event(cls, event: DomainEvent) -> StoredEvent:
        """Create stored event from domain event."""
        return cls(
            event_id=event.metadata.event_id,
            event_type=event.event_type,
            category=event.category.value,
            tenant_id=event.metadata.tenant_id,
            correlation_id=event.metadata.correlation_id,
            causation_id=event.metadata.causation_id,
            timestamp=event.metadata.timestamp,
            payload=event.to_dict(),
            version=event.metadata.version,
        )


class EventStore:
    """Synchronous event store backed by SQL.

    Persists events to psp_domain_event table for:
    - Audit trail
    - Event sourcing / replay
    - Debugging
    - Compliance

    Usage:
        store = EventStore(session)

        # Store single event
        store.append(event)

        # Store batch (transactional)
        store.append_batch([event1, event2, event3])

        # Query events
        events = store.get_by_entity(
            entity_type="payment_instruction",
            entity_id=instruction_id,
        )

        # Replay events
        for event in store.replay(
            tenant_id=tenant_id,
            after=cutoff_time,
        ):
            process(event)
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: DomainEvent) -> bool:
        """Append event to store.

        Returns True if event was stored, False if duplicate (idempotent).
        """
        stored = StoredEvent.from_event(event)
        payload_json = json.dumps(stored.payload, default=_json_serializer)

        result = self._session.execute(
            text("""
                INSERT INTO psp_domain_event (
                    event_id, event_type, category, tenant_id,
                    correlation_id, causation_id, timestamp,
                    payload, version
                ) VALUES (
                    :event_id, :event_type, :category, :tenant_id,
                    :correlation_id, :causation_id, :timestamp,
                    :payload::jsonb, :version
                )
                ON CONFLICT (event_id) DO NOTHING
            """),
            {
                "event_id": str(stored.event_id),
                "event_type": stored.event_type,
                "category": stored.category,
                "tenant_id": str(stored.tenant_id),
                "correlation_id": str(stored.correlation_id),
                "causation_id": str(stored.causation_id) if stored.causation_id else None,
                "timestamp": stored.timestamp,
                "payload": payload_json,
                "version": stored.version,
            },
        )
        return result.rowcount > 0

    def append_batch(self, events: list[DomainEvent]) -> int:
        """Append batch of events atomically.

        Returns count of newly stored events (excludes duplicates).
        """
        stored_count = 0
        for event in events:
            if self.append(event):
                stored_count += 1
        return stored_count

    def get_by_id(self, event_id: UUID) -> StoredEvent | None:
        """Get event by ID."""
        row = self._session.execute(
            text("""
                SELECT event_id, event_type, category, tenant_id,
                       correlation_id, causation_id, timestamp,
                       payload, version
                FROM psp_domain_event
                WHERE event_id = :event_id
            """),
            {"event_id": str(event_id)},
        ).fetchone()

        if not row:
            return None

        return self._row_to_stored(row)

    def get_by_correlation(
        self,
        correlation_id: UUID,
        tenant_id: UUID | None = None,
    ) -> list[StoredEvent]:
        """Get all events with same correlation ID (related events)."""
        params: dict[str, Any] = {"correlation_id": str(correlation_id)}
        query = """
            SELECT event_id, event_type, category, tenant_id,
                   correlation_id, causation_id, timestamp,
                   payload, version
            FROM psp_domain_event
            WHERE correlation_id = :correlation_id
        """

        if tenant_id:
            query += " AND tenant_id = :tenant_id"
            params["tenant_id"] = str(tenant_id)

        query += " ORDER BY timestamp ASC"

        rows = self._session.execute(text(query), params).fetchall()
        return [self._row_to_stored(row) for row in rows]

    def get_by_entity(
        self,
        entity_type: str,
        entity_id: UUID,
        tenant_id: UUID | None = None,
    ) -> list[StoredEvent]:
        """Get events for a specific entity.

        Searches payload for entity references like:
        - payment_instruction_id
        - settlement_event_id
        - funding_request_id
        etc.
        """
        entity_key = f"{entity_type}_id"
        params: dict[str, Any] = {"entity_id": str(entity_id)}
        query = f"""
            SELECT event_id, event_type, category, tenant_id,
                   correlation_id, causation_id, timestamp,
                   payload, version
            FROM psp_domain_event
            WHERE payload->>'{entity_key}' = :entity_id
        """

        if tenant_id:
            query += " AND tenant_id = :tenant_id"
            params["tenant_id"] = str(tenant_id)

        query += " ORDER BY timestamp ASC"

        rows = self._session.execute(text(query), params).fetchall()
        return [self._row_to_stored(row) for row in rows]

    def replay(
        self,
        tenant_id: UUID,
        after: datetime | None = None,
        before: datetime | None = None,
        event_types: list[str] | None = None,
        categories: list[EventCategory] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> Iterator[StoredEvent]:
        """Replay events matching criteria.

        Yields events in chronological order for rebuilding state
        or processing missed events.
        """
        params: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "limit": limit,
            "offset": offset,
        }

        conditions = ["tenant_id = :tenant_id"]

        if after:
            conditions.append("timestamp > :after")
            params["after"] = after

        if before:
            conditions.append("timestamp < :before")
            params["before"] = before

        if event_types:
            conditions.append("event_type = ANY(:event_types)")
            params["event_types"] = event_types

        if categories:
            conditions.append("category = ANY(:categories)")
            params["categories"] = [c.value for c in categories]

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT event_id, event_type, category, tenant_id,
                   correlation_id, causation_id, timestamp,
                   payload, version
            FROM psp_domain_event
            WHERE {where_clause}
            ORDER BY timestamp ASC
            LIMIT :limit OFFSET :offset
        """

        rows = self._session.execute(text(query), params).fetchall()
        for row in rows:
            yield self._row_to_stored(row)

    def count(
        self,
        tenant_id: UUID,
        after: datetime | None = None,
        before: datetime | None = None,
        event_types: list[str] | None = None,
        categories: list[EventCategory] | None = None,
    ) -> int:
        """Count events matching criteria."""
        params: dict[str, Any] = {"tenant_id": str(tenant_id)}
        conditions = ["tenant_id = :tenant_id"]

        if after:
            conditions.append("timestamp > :after")
            params["after"] = after

        if before:
            conditions.append("timestamp < :before")
            params["before"] = before

        if event_types:
            conditions.append("event_type = ANY(:event_types)")
            params["event_types"] = event_types

        if categories:
            conditions.append("category = ANY(:categories)")
            params["categories"] = [c.value for c in categories]

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT COUNT(*) FROM psp_domain_event
            WHERE {where_clause}
        """

        return self._session.execute(text(query), params).scalar() or 0

    def _row_to_stored(self, row: Any) -> StoredEvent:
        """Convert database row to StoredEvent."""
        return StoredEvent(
            event_id=UUID(str(row.event_id)),
            event_type=row.event_type,
            category=row.category,
            tenant_id=UUID(str(row.tenant_id)),
            correlation_id=UUID(str(row.correlation_id)),
            causation_id=UUID(str(row.causation_id)) if row.causation_id else None,
            timestamp=row.timestamp,
            payload=row.payload if isinstance(row.payload, dict) else json.loads(row.payload),
            version=row.version,
        )


class AsyncEventStore:
    """Asynchronous event store backed by SQL.

    Same capabilities as EventStore but for async contexts.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, event: DomainEvent) -> bool:
        """Append event to store."""
        stored = StoredEvent.from_event(event)
        payload_json = json.dumps(stored.payload, default=_json_serializer)

        result = await self._session.execute(
            text("""
                INSERT INTO psp_domain_event (
                    event_id, event_type, category, tenant_id,
                    correlation_id, causation_id, timestamp,
                    payload, version
                ) VALUES (
                    :event_id, :event_type, :category, :tenant_id,
                    :correlation_id, :causation_id, :timestamp,
                    :payload::jsonb, :version
                )
                ON CONFLICT (event_id) DO NOTHING
            """),
            {
                "event_id": str(stored.event_id),
                "event_type": stored.event_type,
                "category": stored.category,
                "tenant_id": str(stored.tenant_id),
                "correlation_id": str(stored.correlation_id),
                "causation_id": str(stored.causation_id) if stored.causation_id else None,
                "timestamp": stored.timestamp,
                "payload": payload_json,
                "version": stored.version,
            },
        )
        return result.rowcount > 0

    async def append_batch(self, events: list[DomainEvent]) -> int:
        """Append batch of events atomically."""
        stored_count = 0
        for event in events:
            if await self.append(event):
                stored_count += 1
        return stored_count

    async def get_by_id(self, event_id: UUID) -> StoredEvent | None:
        """Get event by ID."""
        result = await self._session.execute(
            text("""
                SELECT event_id, event_type, category, tenant_id,
                       correlation_id, causation_id, timestamp,
                       payload, version
                FROM psp_domain_event
                WHERE event_id = :event_id
            """),
            {"event_id": str(event_id)},
        )
        row = result.fetchone()

        if not row:
            return None

        return self._row_to_stored(row)

    async def get_by_correlation(
        self,
        correlation_id: UUID,
        tenant_id: UUID | None = None,
    ) -> list[StoredEvent]:
        """Get all events with same correlation ID."""
        params: dict[str, Any] = {"correlation_id": str(correlation_id)}
        query = """
            SELECT event_id, event_type, category, tenant_id,
                   correlation_id, causation_id, timestamp,
                   payload, version
            FROM psp_domain_event
            WHERE correlation_id = :correlation_id
        """

        if tenant_id:
            query += " AND tenant_id = :tenant_id"
            params["tenant_id"] = str(tenant_id)

        query += " ORDER BY timestamp ASC"

        result = await self._session.execute(text(query), params)
        rows = result.fetchall()
        return [self._row_to_stored(row) for row in rows]

    async def get_by_entity(
        self,
        entity_type: str,
        entity_id: UUID,
        tenant_id: UUID | None = None,
    ) -> list[StoredEvent]:
        """Get events for a specific entity."""
        entity_key = f"{entity_type}_id"
        params: dict[str, Any] = {"entity_id": str(entity_id)}
        query = f"""
            SELECT event_id, event_type, category, tenant_id,
                   correlation_id, causation_id, timestamp,
                   payload, version
            FROM psp_domain_event
            WHERE payload->>'{entity_key}' = :entity_id
        """

        if tenant_id:
            query += " AND tenant_id = :tenant_id"
            params["tenant_id"] = str(tenant_id)

        query += " ORDER BY timestamp ASC"

        result = await self._session.execute(text(query), params)
        rows = result.fetchall()
        return [self._row_to_stored(row) for row in rows]

    async def replay(
        self,
        tenant_id: UUID,
        after: datetime | None = None,
        before: datetime | None = None,
        event_types: list[str] | None = None,
        categories: list[EventCategory] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StoredEvent]:
        """Replay events matching criteria."""
        params: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "limit": limit,
            "offset": offset,
        }

        conditions = ["tenant_id = :tenant_id"]

        if after:
            conditions.append("timestamp > :after")
            params["after"] = after

        if before:
            conditions.append("timestamp < :before")
            params["before"] = before

        if event_types:
            conditions.append("event_type = ANY(:event_types)")
            params["event_types"] = event_types

        if categories:
            conditions.append("category = ANY(:categories)")
            params["categories"] = [c.value for c in categories]

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT event_id, event_type, category, tenant_id,
                   correlation_id, causation_id, timestamp,
                   payload, version
            FROM psp_domain_event
            WHERE {where_clause}
            ORDER BY timestamp ASC
            LIMIT :limit OFFSET :offset
        """

        result = await self._session.execute(text(query), params)
        rows = result.fetchall()
        return [self._row_to_stored(row) for row in rows]

    async def count(
        self,
        tenant_id: UUID,
        after: datetime | None = None,
        before: datetime | None = None,
        event_types: list[str] | None = None,
        categories: list[EventCategory] | None = None,
    ) -> int:
        """Count events matching criteria."""
        params: dict[str, Any] = {"tenant_id": str(tenant_id)}
        conditions = ["tenant_id = :tenant_id"]

        if after:
            conditions.append("timestamp > :after")
            params["after"] = after

        if before:
            conditions.append("timestamp < :before")
            params["before"] = before

        if event_types:
            conditions.append("event_type = ANY(:event_types)")
            params["event_types"] = event_types

        if categories:
            conditions.append("category = ANY(:categories)")
            params["categories"] = [c.value for c in categories]

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT COUNT(*) FROM psp_domain_event
            WHERE {where_clause}
        """

        result = await self._session.execute(text(query), params)
        return result.scalar() or 0

    def _row_to_stored(self, row: Any) -> StoredEvent:
        """Convert database row to StoredEvent."""
        return StoredEvent(
            event_id=UUID(str(row.event_id)),
            event_type=row.event_type,
            category=row.category,
            tenant_id=UUID(str(row.tenant_id)),
            correlation_id=UUID(str(row.correlation_id)),
            causation_id=UUID(str(row.causation_id)) if row.causation_id else None,
            timestamp=row.timestamp,
            payload=row.payload if isinstance(row.payload, dict) else json.loads(row.payload),
            version=row.version,
        )


def _json_serializer(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
