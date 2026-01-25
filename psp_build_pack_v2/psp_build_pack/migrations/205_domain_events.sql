-- Migration 205: Domain Events
-- Creates the event store table for PSP domain events.
--
-- Events provide:
-- - Audit trail for all operations
-- - Deterministic replay capability
-- - Integration with compliance alerts, support tooling, client notifications
-- - Event sourcing foundation

-- =============================================================================
-- Domain Event Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS psp_domain_event (
    -- Primary key is the event_id itself (idempotent writes)
    event_id UUID PRIMARY KEY,

    -- Event classification
    event_type TEXT NOT NULL,  -- e.g., 'PaymentSettled', 'FundingBlocked'
    category TEXT NOT NULL,    -- e.g., 'payment', 'funding', 'settlement'

    -- Tenant isolation
    tenant_id UUID NOT NULL,

    -- Event correlation (for tracing related events)
    correlation_id UUID NOT NULL,  -- Groups related events
    causation_id UUID,             -- Event that caused this one

    -- Timing
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Full event payload (for replay)
    payload JSONB NOT NULL,

    -- Schema versioning
    version INT NOT NULL DEFAULT 1,

    -- Audit
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Indexes
-- =============================================================================

-- Tenant + time for replay queries
CREATE INDEX IF NOT EXISTS idx_psp_domain_event_tenant_time
    ON psp_domain_event (tenant_id, timestamp);

-- Correlation for related event queries
CREATE INDEX IF NOT EXISTS idx_psp_domain_event_correlation
    ON psp_domain_event (correlation_id);

-- Event type for filtering
CREATE INDEX IF NOT EXISTS idx_psp_domain_event_type
    ON psp_domain_event (event_type);

-- Category for filtering
CREATE INDEX IF NOT EXISTS idx_psp_domain_event_category
    ON psp_domain_event (category);

-- JSONB index for entity lookups (e.g., find events for a payment_instruction)
CREATE INDEX IF NOT EXISTS idx_psp_domain_event_payload_gin
    ON psp_domain_event USING GIN (payload jsonb_path_ops);

-- =============================================================================
-- Append-Only Protection
-- =============================================================================

-- Events are immutable - no updates allowed
CREATE OR REPLACE FUNCTION prevent_event_update()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'psp_domain_event is append-only. Updates are not allowed.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_prevent_event_update
    BEFORE UPDATE ON psp_domain_event
    FOR EACH ROW
    EXECUTE FUNCTION prevent_event_update();

-- Events cannot be deleted in production
-- (This can be disabled for GDPR deletion jobs via session variable)
CREATE OR REPLACE FUNCTION prevent_event_delete()
RETURNS TRIGGER AS $$
BEGIN
    -- Allow deletion if explicitly enabled (for GDPR compliance jobs)
    IF current_setting('psp.allow_event_deletion', true) = 'true' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'psp_domain_event deletion is not allowed. Set psp.allow_event_deletion=true for GDPR jobs.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_prevent_event_delete
    BEFORE DELETE ON psp_domain_event
    FOR EACH ROW
    EXECUTE FUNCTION prevent_event_delete();

-- =============================================================================
-- Event Subscription Table (for tracking consumer offsets)
-- =============================================================================

CREATE TABLE IF NOT EXISTS psp_event_subscription (
    subscription_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Subscriber identity
    subscriber_name TEXT NOT NULL,  -- e.g., 'compliance_alerts', 'client_notifications'

    -- Position tracking
    last_event_id UUID,
    last_event_timestamp TIMESTAMPTZ,
    last_processed_at TIMESTAMPTZ,

    -- Filtering
    event_types TEXT[],    -- NULL = all types
    categories TEXT[],     -- NULL = all categories
    tenant_ids UUID[],     -- NULL = all tenants

    -- Status
    is_active BOOLEAN NOT NULL DEFAULT true,

    -- Audit
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_psp_event_subscription_name UNIQUE (subscriber_name)
);

-- =============================================================================
-- Helper Functions
-- =============================================================================

-- Get events for a subscriber (respecting their last position)
CREATE OR REPLACE FUNCTION get_events_for_subscriber(
    p_subscriber_name TEXT,
    p_limit INT DEFAULT 100
)
RETURNS TABLE (
    event_id UUID,
    event_type TEXT,
    category TEXT,
    tenant_id UUID,
    correlation_id UUID,
    causation_id UUID,
    timestamp TIMESTAMPTZ,
    payload JSONB,
    version INT
) AS $$
DECLARE
    v_subscription psp_event_subscription%ROWTYPE;
BEGIN
    -- Get subscription
    SELECT * INTO v_subscription
    FROM psp_event_subscription
    WHERE subscriber_name = p_subscriber_name
      AND is_active = true;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Subscription % not found or inactive', p_subscriber_name;
    END IF;

    -- Return events after last processed
    RETURN QUERY
    SELECT e.event_id, e.event_type, e.category, e.tenant_id,
           e.correlation_id, e.causation_id, e.timestamp,
           e.payload, e.version
    FROM psp_domain_event e
    WHERE (v_subscription.last_event_timestamp IS NULL
           OR e.timestamp > v_subscription.last_event_timestamp)
      AND (v_subscription.event_types IS NULL
           OR e.event_type = ANY(v_subscription.event_types))
      AND (v_subscription.categories IS NULL
           OR e.category = ANY(v_subscription.categories))
      AND (v_subscription.tenant_ids IS NULL
           OR e.tenant_id = ANY(v_subscription.tenant_ids))
    ORDER BY e.timestamp ASC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- Update subscriber position after processing
CREATE OR REPLACE FUNCTION update_subscriber_position(
    p_subscriber_name TEXT,
    p_event_id UUID,
    p_event_timestamp TIMESTAMPTZ
)
RETURNS VOID AS $$
BEGIN
    UPDATE psp_event_subscription
    SET last_event_id = p_event_id,
        last_event_timestamp = p_event_timestamp,
        last_processed_at = NOW(),
        updated_at = NOW()
    WHERE subscriber_name = p_subscriber_name;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Subscription % not found', p_subscriber_name;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON TABLE psp_domain_event IS
    'Append-only event store for PSP domain events. Enables audit, replay, and integration.';

COMMENT ON COLUMN psp_domain_event.correlation_id IS
    'Links related events (e.g., all events for a single payroll run)';

COMMENT ON COLUMN psp_domain_event.causation_id IS
    'The event that directly caused this event (for causal chain tracing)';

COMMENT ON COLUMN psp_domain_event.payload IS
    'Full event data as JSONB. Includes all fields for deterministic replay.';

COMMENT ON TABLE psp_event_subscription IS
    'Tracks consumer positions for event subscriptions (offset tracking).';
