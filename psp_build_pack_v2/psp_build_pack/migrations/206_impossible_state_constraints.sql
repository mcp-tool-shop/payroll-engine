-- Migration 206: Impossible State Constraints
-- This migration adds DB-level constraints that make corruption impossible.
-- These are NOT just code checks - they're database guarantees.
--
-- Philosophy: "Tests pass" < "You can't corrupt it at 2am"

-- =============================================================================
-- LEDGER ENTRY CONSTRAINTS
-- =============================================================================

-- Amount must be positive (no zero, no negative)
ALTER TABLE psp_ledger_entry
    DROP CONSTRAINT IF EXISTS chk_ledger_entry_amount_positive;
ALTER TABLE psp_ledger_entry
    ADD CONSTRAINT chk_ledger_entry_amount_positive
    CHECK (amount > 0);

-- Debit and credit accounts must be different (no self-transfer)
ALTER TABLE psp_ledger_entry
    DROP CONSTRAINT IF EXISTS chk_ledger_entry_different_accounts;
ALTER TABLE psp_ledger_entry
    ADD CONSTRAINT chk_ledger_entry_different_accounts
    CHECK (debit_account_id <> credit_account_id);

-- Entry type must be valid
ALTER TABLE psp_ledger_entry
    DROP CONSTRAINT IF EXISTS chk_ledger_entry_type_valid;
ALTER TABLE psp_ledger_entry
    ADD CONSTRAINT chk_ledger_entry_type_valid
    CHECK (entry_type IN (
        'funding_received',
        'funding_withdrawal',
        'payment_debit',
        'payment_credit',
        'fee_charged',
        'fee_refund',
        'reversal',
        'adjustment',
        'interest_credit',
        'hold_placed',
        'hold_released'
    ));

-- =============================================================================
-- PAYMENT INSTRUCTION CONSTRAINTS
-- =============================================================================

-- Amount must be positive
ALTER TABLE payment_instruction
    DROP CONSTRAINT IF EXISTS chk_payment_instruction_amount_positive;
ALTER TABLE payment_instruction
    ADD CONSTRAINT chk_payment_instruction_amount_positive
    CHECK (amount > 0);

-- Direction must be valid
ALTER TABLE payment_instruction
    DROP CONSTRAINT IF EXISTS chk_payment_instruction_direction_valid;
ALTER TABLE payment_instruction
    ADD CONSTRAINT chk_payment_instruction_direction_valid
    CHECK (direction IN ('inbound', 'outbound'));

-- Status must be valid
ALTER TABLE payment_instruction
    DROP CONSTRAINT IF EXISTS chk_payment_instruction_status_valid;
ALTER TABLE payment_instruction
    ADD CONSTRAINT chk_payment_instruction_status_valid
    CHECK (status IN (
        'pending',
        'submitted',
        'accepted',
        'settled',
        'failed',
        'returned',
        'canceled'
    ));

-- Purpose must be valid
ALTER TABLE payment_instruction
    DROP CONSTRAINT IF EXISTS chk_payment_instruction_purpose_valid;
ALTER TABLE payment_instruction
    ADD CONSTRAINT chk_payment_instruction_purpose_valid
    CHECK (purpose IN (
        'employee_net',
        'employee_expense',
        'vendor_payment',
        'tax_payment',
        'garnishment',
        'child_support',
        'fee_payment',
        'refund',
        'correction'
    ));

-- Payee type must be valid
ALTER TABLE payment_instruction
    DROP CONSTRAINT IF EXISTS chk_payment_instruction_payee_type_valid;
ALTER TABLE payment_instruction
    ADD CONSTRAINT chk_payment_instruction_payee_type_valid
    CHECK (payee_type IN (
        'employee',
        'vendor',
        'tax_authority',
        'garnishment_agency',
        'internal_account'
    ));

-- =============================================================================
-- PAYMENT ATTEMPT UNIQUENESS
-- =============================================================================

-- Provider + provider_request_id must be unique (prevents duplicate submissions)
CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_attempt_provider_request_unique
    ON payment_attempt (provider, provider_request_id)
    WHERE provider_request_id IS NOT NULL;

-- =============================================================================
-- SETTLEMENT EVENT CONSTRAINTS
-- =============================================================================

-- Amount must be positive
ALTER TABLE psp_settlement_event
    DROP CONSTRAINT IF EXISTS chk_settlement_amount_positive;
ALTER TABLE psp_settlement_event
    ADD CONSTRAINT chk_settlement_amount_positive
    CHECK (amount > 0);

-- Direction must be valid
ALTER TABLE psp_settlement_event
    DROP CONSTRAINT IF EXISTS chk_settlement_direction_valid;
ALTER TABLE psp_settlement_event
    ADD CONSTRAINT chk_settlement_direction_valid
    CHECK (direction IN ('inbound', 'outbound'));

-- Status must be valid
ALTER TABLE psp_settlement_event
    DROP CONSTRAINT IF EXISTS chk_settlement_status_valid;
ALTER TABLE psp_settlement_event
    ADD CONSTRAINT chk_settlement_status_valid
    CHECK (status IN (
        'pending',
        'submitted',
        'accepted',
        'settled',
        'returned',
        'rejected',
        'canceled'
    ));

-- External trace ID must be unique per provider (prevents duplicate imports)
CREATE UNIQUE INDEX IF NOT EXISTS idx_settlement_external_trace_unique
    ON psp_settlement_event (rail, external_trace_id)
    WHERE external_trace_id IS NOT NULL;

-- =============================================================================
-- STATUS TRANSITION CONSTRAINTS
-- Using triggers to enforce valid state machine transitions
-- =============================================================================

-- Payment instruction status transition validation
CREATE OR REPLACE FUNCTION validate_payment_instruction_status_transition()
RETURNS TRIGGER AS $$
DECLARE
    valid_transitions JSONB := '{
        "pending": ["submitted", "canceled"],
        "submitted": ["accepted", "failed", "canceled"],
        "accepted": ["settled", "failed", "returned"],
        "settled": ["returned"],
        "failed": [],
        "returned": [],
        "canceled": []
    }'::JSONB;
    allowed_targets JSONB;
BEGIN
    -- Skip if status unchanged
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;

    -- Get allowed transitions from current status
    allowed_targets := valid_transitions->OLD.status;

    -- Check if new status is in allowed list
    IF NOT (allowed_targets ? NEW.status) THEN
        RAISE EXCEPTION 'Invalid status transition: % -> % for payment_instruction %',
            OLD.status, NEW.status, OLD.payment_instruction_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_validate_payment_status_transition ON payment_instruction;
CREATE TRIGGER trg_validate_payment_status_transition
    BEFORE UPDATE ON payment_instruction
    FOR EACH ROW
    EXECUTE FUNCTION validate_payment_instruction_status_transition();

-- Settlement event status transition validation
CREATE OR REPLACE FUNCTION validate_settlement_status_transition()
RETURNS TRIGGER AS $$
DECLARE
    valid_transitions JSONB := '{
        "pending": ["submitted", "accepted", "settled", "rejected", "canceled"],
        "submitted": ["accepted", "settled", "rejected"],
        "accepted": ["settled", "returned", "rejected"],
        "settled": ["returned"],
        "returned": [],
        "rejected": [],
        "canceled": []
    }'::JSONB;
    allowed_targets JSONB;
BEGIN
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;

    allowed_targets := valid_transitions->OLD.status;

    IF NOT (allowed_targets ? NEW.status) THEN
        RAISE EXCEPTION 'Invalid status transition: % -> % for settlement_event %',
            OLD.status, NEW.status, OLD.psp_settlement_event_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_validate_settlement_status_transition ON psp_settlement_event;
CREATE TRIGGER trg_validate_settlement_status_transition
    BEFORE UPDATE ON psp_settlement_event
    FOR EACH ROW
    EXECUTE FUNCTION validate_settlement_status_transition();

-- =============================================================================
-- REVERSAL INTEGRITY CONSTRAINTS
-- =============================================================================

-- Track which entries have been reversed
ALTER TABLE psp_ledger_entry
    ADD COLUMN IF NOT EXISTS reversed_by_entry_id UUID,
    ADD COLUMN IF NOT EXISTS is_reversal BOOLEAN NOT NULL DEFAULT FALSE;

-- Reversal must reference an existing entry
ALTER TABLE psp_ledger_entry
    DROP CONSTRAINT IF EXISTS fk_ledger_entry_reversed_by;
ALTER TABLE psp_ledger_entry
    ADD CONSTRAINT fk_ledger_entry_reversed_by
    FOREIGN KEY (reversed_by_entry_id)
    REFERENCES psp_ledger_entry(psp_ledger_entry_id);

-- Prevent double-reversal: an entry can only be reversed once
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_entry_reversed_once
    ON psp_ledger_entry (reversed_by_entry_id)
    WHERE reversed_by_entry_id IS NOT NULL;

-- Function to mark original entry when reversal is created
CREATE OR REPLACE FUNCTION link_reversal_to_original()
RETURNS TRIGGER AS $$
BEGIN
    -- If this is a reversal entry
    IF NEW.entry_type = 'reversal' AND NEW.source_type = 'psp_ledger_entry' THEN
        -- Check that original entry exists and isn't already reversed
        IF EXISTS (
            SELECT 1 FROM psp_ledger_entry
            WHERE psp_ledger_entry_id = NEW.source_id
              AND reversed_by_entry_id IS NOT NULL
        ) THEN
            RAISE EXCEPTION 'Entry % has already been reversed', NEW.source_id;
        END IF;

        -- Mark original as reversed
        UPDATE psp_ledger_entry
        SET reversed_by_entry_id = NEW.psp_ledger_entry_id
        WHERE psp_ledger_entry_id = NEW.source_id
          AND reversed_by_entry_id IS NULL;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Could not mark entry % as reversed', NEW.source_id;
        END IF;

        -- Mark this entry as a reversal
        NEW.is_reversal := TRUE;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_link_reversal ON psp_ledger_entry;
CREATE TRIGGER trg_link_reversal
    BEFORE INSERT ON psp_ledger_entry
    FOR EACH ROW
    EXECUTE FUNCTION link_reversal_to_original();

-- =============================================================================
-- RESERVATION CONSTRAINTS
-- =============================================================================

-- Amount must be positive
ALTER TABLE psp_balance_reservation
    DROP CONSTRAINT IF EXISTS chk_reservation_amount_positive;
ALTER TABLE psp_balance_reservation
    ADD CONSTRAINT chk_reservation_amount_positive
    CHECK (amount > 0);

-- Status must be valid
ALTER TABLE psp_balance_reservation
    DROP CONSTRAINT IF EXISTS chk_reservation_status_valid;
ALTER TABLE psp_balance_reservation
    ADD CONSTRAINT chk_reservation_status_valid
    CHECK (status IN ('active', 'consumed', 'expired', 'released'));

-- Expires_at must be in the future when created
-- (Can't enforce easily with CHECK, but documented here)

-- =============================================================================
-- FUNDING REQUEST CONSTRAINTS
-- =============================================================================

-- Amount must be positive
ALTER TABLE psp_funding_request
    DROP CONSTRAINT IF EXISTS chk_funding_request_amount_positive;
ALTER TABLE psp_funding_request
    ADD CONSTRAINT chk_funding_request_amount_positive
    CHECK (amount > 0);

-- Status must be valid
ALTER TABLE psp_funding_request
    DROP CONSTRAINT IF EXISTS chk_funding_request_status_valid;
ALTER TABLE psp_funding_request
    ADD CONSTRAINT chk_funding_request_status_valid
    CHECK (status IN (
        'pending',
        'approved',
        'blocked',
        'partially_funded',
        'funded',
        'canceled'
    ));

-- =============================================================================
-- LIABILITY EVENT CONSTRAINTS
-- =============================================================================

-- Amount must be positive
ALTER TABLE liability_event
    DROP CONSTRAINT IF EXISTS chk_liability_amount_positive;
ALTER TABLE liability_event
    ADD CONSTRAINT chk_liability_amount_positive
    CHECK (amount > 0);

-- Recovery status must be valid
ALTER TABLE liability_event
    DROP CONSTRAINT IF EXISTS chk_liability_recovery_status_valid;
ALTER TABLE liability_event
    ADD CONSTRAINT chk_liability_recovery_status_valid
    CHECK (recovery_status IN (
        'pending',
        'in_progress',
        'recovered',
        'partial',
        'written_off',
        'disputed'
    ));

-- =============================================================================
-- COMMENTS
-- =============================================================================

COMMENT ON CONSTRAINT chk_ledger_entry_amount_positive ON psp_ledger_entry IS
    'INVARIANT: All ledger amounts must be positive. Reversals use separate entry, not negative amounts.';

COMMENT ON CONSTRAINT chk_ledger_entry_different_accounts ON psp_ledger_entry IS
    'INVARIANT: No self-transfers. Debit and credit must be different accounts.';

COMMENT ON INDEX idx_ledger_entry_reversed_once IS
    'INVARIANT: An entry can only be reversed once. This prevents double-reversal attacks.';

COMMENT ON TRIGGER trg_validate_payment_status_transition ON payment_instruction IS
    'INVARIANT: Payment status can only progress forward through valid state machine.';

COMMENT ON INDEX idx_payment_attempt_provider_request_unique IS
    'INVARIANT: Provider request IDs must be unique to prevent duplicate submission attacks.';

COMMENT ON INDEX idx_settlement_external_trace_unique IS
    'INVARIANT: External trace IDs must be unique per rail to prevent duplicate settlement imports.';
