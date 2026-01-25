-- 204_liability_attribution.sql
-- Adds liability attribution columns to track error origin and loss responsibility.
-- Required for dispute resolution, loss allocation, and compliance reporting.

BEGIN;

-- 1) Add error origin and liability tracking to payment_instruction
ALTER TABLE payment_instruction
    ADD COLUMN IF NOT EXISTS error_origin TEXT CHECK (error_origin IN (
        'client',           -- Client provided bad data (wrong account, etc.)
        'payroll_engine',   -- Our calculation or logic error
        'provider',         -- Bank/processor error
        'bank',             -- Receiving bank error
        'recipient'         -- Recipient action (account closed, refused, etc.)
    )),
    ADD COLUMN IF NOT EXISTS liability_party TEXT CHECK (liability_party IN (
        'employer',         -- Client bears the loss
        'psp',              -- We bear the loss
        'processor',        -- Bank/processor bears the loss
        'shared',           -- Loss split between parties
        'pending'           -- Not yet determined
    )),
    ADD COLUMN IF NOT EXISTS recovery_path TEXT CHECK (recovery_path IN (
        'offset_future',    -- Offset against future payroll
        'clawback',         -- Attempt to recover from recipient
        'write_off',        -- Accept as loss
        'insurance',        -- Insurance claim
        'dispute',          -- In dispute resolution
        'none'              -- No recovery needed (no loss)
    )),
    ADD COLUMN IF NOT EXISTS liability_amount NUMERIC(14,4) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS liability_notes TEXT,
    ADD COLUMN IF NOT EXISTS liability_resolved_at TIMESTAMPTZ;

-- 2) Add liability tracking to psp_settlement_event for returns/failures
ALTER TABLE psp_settlement_event
    ADD COLUMN IF NOT EXISTS error_origin TEXT CHECK (error_origin IN (
        'client', 'payroll_engine', 'provider', 'bank', 'recipient'
    )),
    ADD COLUMN IF NOT EXISTS return_code TEXT,  -- ACH R-codes, FedNow rejection codes
    ADD COLUMN IF NOT EXISTS return_reason TEXT;

-- 3) Create liability_event table for tracking loss allocation decisions
CREATE TABLE IF NOT EXISTS liability_event (
    liability_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    legal_entity_id UUID NOT NULL,

    -- What failed
    source_type TEXT NOT NULL CHECK (source_type IN (
        'payment_instruction',
        'psp_settlement_event',
        'funding_request'
    )),
    source_id UUID NOT NULL,

    -- Classification
    error_origin TEXT NOT NULL CHECK (error_origin IN (
        'client', 'payroll_engine', 'provider', 'bank', 'recipient'
    )),
    liability_party TEXT NOT NULL CHECK (liability_party IN (
        'employer', 'psp', 'processor', 'shared', 'pending'
    )),

    -- Financial impact
    loss_amount NUMERIC(14,4) NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'USD',

    -- Recovery
    recovery_path TEXT CHECK (recovery_path IN (
        'offset_future', 'clawback', 'write_off', 'insurance', 'dispute', 'none'
    )),
    recovery_amount NUMERIC(14,4) DEFAULT 0,
    recovery_status TEXT NOT NULL DEFAULT 'pending' CHECK (recovery_status IN (
        'pending',          -- Not yet attempted
        'in_progress',      -- Recovery underway
        'partial',          -- Partially recovered
        'complete',         -- Fully recovered
        'failed',           -- Recovery failed
        'written_off'       -- Accepted as loss
    )),

    -- Audit
    determined_by_user_id UUID,
    determination_reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,

    -- Supporting documentation
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Idempotency for automated liability assignment
    idempotency_key TEXT,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS liability_event_source
    ON liability_event(tenant_id, source_type, source_id);

CREATE INDEX IF NOT EXISTS liability_event_status
    ON liability_event(tenant_id, liability_party, recovery_status);

CREATE INDEX IF NOT EXISTS liability_event_unresolved
    ON liability_event(tenant_id, recovery_status)
    WHERE recovery_status IN ('pending', 'in_progress', 'partial');

-- 4) Add return code lookup table for common ACH/FedNow codes
CREATE TABLE IF NOT EXISTS return_code_reference (
    return_code_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rail TEXT NOT NULL CHECK (rail IN ('ach', 'wire', 'rtp', 'fednow')),
    code TEXT NOT NULL,
    description TEXT NOT NULL,
    default_error_origin TEXT NOT NULL CHECK (default_error_origin IN (
        'client', 'payroll_engine', 'provider', 'bank', 'recipient'
    )),
    default_liability_party TEXT NOT NULL CHECK (default_liability_party IN (
        'employer', 'psp', 'processor', 'shared', 'pending'
    )),
    is_recoverable BOOLEAN NOT NULL DEFAULT false,
    notes TEXT,
    UNIQUE (rail, code)
);

-- 5) Seed common ACH return codes with liability defaults
INSERT INTO return_code_reference (rail, code, description, default_error_origin, default_liability_party, is_recoverable, notes)
VALUES
    -- Client-caused returns (employer typically liable)
    ('ach', 'R01', 'Insufficient Funds', 'recipient', 'employer', true, 'Retry after funding'),
    ('ach', 'R02', 'Account Closed', 'client', 'employer', false, 'Client provided stale account'),
    ('ach', 'R03', 'No Account/Unable to Locate', 'client', 'employer', false, 'Invalid account number'),
    ('ach', 'R04', 'Invalid Account Number', 'client', 'employer', false, 'Bad account format'),
    ('ach', 'R07', 'Authorization Revoked', 'recipient', 'employer', false, 'Employee revoked ACH auth'),
    ('ach', 'R08', 'Payment Stopped', 'recipient', 'employer', false, 'Stop payment on account'),
    ('ach', 'R10', 'Customer Advises Unauthorized', 'recipient', 'pending', false, 'Dispute - needs investigation'),
    ('ach', 'R29', 'Corporate Customer Advises Not Authorized', 'recipient', 'pending', false, 'Corporate dispute'),

    -- Bank/processor issues (typically processor liable)
    ('ach', 'R05', 'Unauthorized Debit to Consumer Account', 'provider', 'processor', false, 'Bank compliance issue'),
    ('ach', 'R06', 'Returned per ODFI Request', 'provider', 'processor', true, 'ODFI initiated return'),
    ('ach', 'R09', 'Uncollected Funds', 'bank', 'processor', true, 'Bank hold issue'),

    -- FedNow rejection codes
    ('fednow', 'AC01', 'Incorrect Account Number', 'client', 'employer', false, 'Invalid account'),
    ('fednow', 'AC04', 'Closed Account Number', 'client', 'employer', false, 'Account closed'),
    ('fednow', 'AM02', 'Not Allowed Amount', 'payroll_engine', 'psp', false, 'Exceeded limits'),
    ('fednow', 'BE04', 'Missing Creditor Address', 'client', 'employer', false, 'Incomplete data'),
    ('fednow', 'RJCT', 'Rejected by Receiving Bank', 'bank', 'pending', false, 'Needs investigation')
ON CONFLICT (rail, code) DO NOTHING;

COMMIT;
