# Runbook: Replay Domain Events

## Overview

Domain events in PSP are the source of truth for what happened. Event replay enables:

- Rebuilding read models after corruption
- Debugging by tracing event sequences
- Audit queries for compliance
- Integration catch-up after downtime

## When to Replay

| Scenario | Replay Type |
|----------|-------------|
| Read model corruption | Full rebuild from events |
| Integration missed events | Catch-up from last processed |
| Debugging an incident | Query event sequence |
| New integration setup | Backfill historical events |
| Compliance audit | Export event range |

## Safety Rules

1. **Replay is READ-ONLY** - Never modify events during replay
2. **Replay is IDEMPOTENT** - Same events + same handlers = same state
3. **Replay PRESERVES ORDER** - Events replayed in timestamp order
4. **Replay RESPECTS TENANT** - Always filter by tenant_id

## Queries for Event Investigation

### 1. Get Events for a Payment

```sql
SELECT
    event_id,
    event_type,
    timestamp,
    payload
FROM psp_domain_event
WHERE payload->>'payment_instruction_id' = :instruction_id
ORDER BY timestamp ASC;
```

### 2. Get Correlated Events

```sql
SELECT
    event_id,
    event_type,
    timestamp,
    payload
FROM psp_domain_event
WHERE correlation_id = :correlation_id
ORDER BY timestamp ASC;
```

### 3. Get Events by Type and Time Range

```sql
SELECT
    event_id,
    event_type,
    timestamp,
    payload
FROM psp_domain_event
WHERE tenant_id = :tenant_id
  AND event_type = :event_type
  AND timestamp BETWEEN :start_time AND :end_time
ORDER BY timestamp ASC
LIMIT 1000;
```

### 4. Get Event Causation Chain

```sql
WITH RECURSIVE event_chain AS (
    -- Start with the final event
    SELECT
        event_id,
        event_type,
        causation_id,
        timestamp,
        payload,
        1 AS depth
    FROM psp_domain_event
    WHERE event_id = :final_event_id

    UNION ALL

    -- Walk back through causation
    SELECT
        e.event_id,
        e.event_type,
        e.causation_id,
        e.timestamp,
        e.payload,
        ec.depth + 1
    FROM psp_domain_event e
    JOIN event_chain ec ON e.event_id = ec.causation_id
    WHERE ec.depth < 20  -- Prevent infinite loops
)
SELECT * FROM event_chain
ORDER BY depth DESC;  -- Oldest first
```

### 5. Event Volume by Type

```sql
SELECT
    event_type,
    COUNT(*) AS event_count,
    MIN(timestamp) AS first_event,
    MAX(timestamp) AS last_event
FROM psp_domain_event
WHERE tenant_id = :tenant_id
  AND timestamp > NOW() - INTERVAL '24 hours'
GROUP BY event_type
ORDER BY event_count DESC;
```

## Replay Methods

### Method 1: CLI Tool (Recommended)

```bash
# Replay all events for a tenant since a timestamp
python -m payroll_engine.psp.cli replay-events \
    --tenant-id abc123 \
    --since "2025-01-20T00:00:00Z" \
    --handler notifications

# Replay specific event types
python -m payroll_engine.psp.cli replay-events \
    --tenant-id abc123 \
    --event-types PaymentSettled,PaymentReturned \
    --handler compliance_alerts

# Dry run (show what would be replayed)
python -m payroll_engine.psp.cli replay-events \
    --tenant-id abc123 \
    --since "2025-01-20T00:00:00Z" \
    --dry-run

# Export events to file
python -m payroll_engine.psp.cli export-events \
    --tenant-id abc123 \
    --since "2025-01-01T00:00:00Z" \
    --until "2025-01-31T23:59:59Z" \
    --output events.jsonl
```

### Method 2: Python API

```python
from payroll_engine.psp.events import EventStore, AsyncEventStore
from datetime import datetime, timedelta

# Sync version
store = EventStore(session)

# Replay to a handler
for event in store.replay(
    tenant_id=tenant_id,
    after=datetime.utcnow() - timedelta(days=7),
    event_types=["PaymentSettled", "PaymentReturned"],
    limit=1000,
):
    process_event(event)

# Async version
async_store = AsyncEventStore(async_session)

events = await async_store.replay(
    tenant_id=tenant_id,
    after=cutoff_time,
    categories=[EventCategory.PAYMENT],
)
for event in events:
    await async_process_event(event)
```

### Method 3: Subscription Catch-up

```python
# Check subscription position
subscription = session.execute(
    text("""
        SELECT last_event_id, last_event_timestamp
        FROM psp_event_subscription
        WHERE subscriber_name = :name
    """),
    {"name": "compliance_alerts"},
).fetchone()

# Get events since last processed
events = store.replay(
    tenant_id=tenant_id,
    after=subscription.last_event_timestamp,
)

for event in events:
    # Process event
    process_event(event)

    # Update position
    session.execute(
        text("""
            UPDATE psp_event_subscription
            SET last_event_id = :event_id,
                last_event_timestamp = :timestamp,
                last_processed_at = NOW()
            WHERE subscriber_name = :name
        """),
        {
            "event_id": str(event.event_id),
            "timestamp": event.timestamp,
            "name": "compliance_alerts",
        },
    )
    session.commit()
```

## Rebuilding Read Models

### Scenario: Notification History Corrupted

```python
# 1. Clear the corrupted read model
session.execute(text("TRUNCATE TABLE notification_history"))

# 2. Replay all notification-relevant events
store = EventStore(session)

for event in store.replay(
    tenant_id=tenant_id,
    event_types=[
        "PaymentSettled",
        "PaymentReturned",
        "FundingBlocked",
        "LiabilityClassified",
    ],
):
    # Rebuild notification record
    notification = build_notification_from_event(event)
    session.add(notification)

session.commit()
```

### Scenario: Balance Cache Stale

```python
# 1. Reset balance cache
session.execute(text("""
    UPDATE psp_ledger_account
    SET cached_balance = NULL,
        cache_updated_at = NULL
    WHERE tenant_id = :tenant_id
"""), {"tenant_id": str(tenant_id)})

# 2. Replay ledger events to rebuild
for event in store.replay(
    tenant_id=tenant_id,
    event_types=["LedgerEntryPosted", "LedgerEntryReversed"],
):
    update_balance_cache_from_event(event)

session.commit()
```

## Debugging with Events

### Trace a Failed Payment

```python
# 1. Find the payment events
events = store.get_by_entity(
    entity_type="payment_instruction",
    entity_id=instruction_id,
    tenant_id=tenant_id,
)

# 2. Print timeline
for event in events:
    print(f"{event.timestamp} | {event.event_type}")
    print(f"  {event.payload}")
    print()

# Expected sequence for successful payment:
# PaymentInstructionCreated
# PaymentSubmitted
# PaymentAccepted (or PaymentFailed)
# PaymentSettled (or PaymentReturned)
```

### Find Missing Events

```python
# Check for gaps in event sequence
events = list(store.replay(
    tenant_id=tenant_id,
    after=start_time,
    before=end_time,
))

for i, event in enumerate(events[1:], 1):
    prev_event = events[i-1]
    gap = (event.timestamp - prev_event.timestamp).total_seconds()

    if gap > 3600:  # More than 1 hour gap
        print(f"Gap detected: {gap}s between {prev_event.event_type} and {event.event_type}")
```

## Compliance Export

```python
# Export events for audit
import json

with open("audit_export.jsonl", "w") as f:
    for event in store.replay(
        tenant_id=tenant_id,
        after=audit_start,
        before=audit_end,
        categories=[EventCategory.PAYMENT, EventCategory.LIABILITY],
    ):
        f.write(json.dumps({
            "event_id": str(event.event_id),
            "event_type": event.event_type,
            "timestamp": event.timestamp.isoformat(),
            "payload": event.payload,
        }) + "\n")
```

## Escalation

Escalate if:

- Events missing from expected sequence
- Event timestamps out of order
- Replay produces different state than live
- Event payload schema changed (version mismatch)

## Prevention

- Always emit events through the facade
- Never modify events directly
- Monitor event emission lag
- Alert on event volume anomalies
- Regular event store integrity checks
