-- 201_psp_ledger_tables.sql (FULL)
-- PSP financial spine: bank accounts, ledger accounts, append-only ledger entries, reservations, settlement events, links,
-- tax liabilities, third-party obligations.
-- Requirements:
-- - append-only: disallow UPDATE/DELETE on psp_ledger_entry after insert (use reversal entries)
-- - idempotency: UNIQUE(tenant_id, idempotency_key) for externally-triggered postings
-- - balance safety: reservations validated transactionally by service layer (DB supports with indexes + locks)

BEGIN;

-- Extensions (if not already present)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1) PSP bank settlement accounts (PSP-owned accounts, tokenized refs)
CREATE TABLE IF NOT EXISTS psp_bank_account (
  psp_bank_account_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL,
  bank_name TEXT NOT NULL,
  bank_account_ref_token TEXT NOT NULL,
  rail_support_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS psp_bank_account_token_uq
  ON psp_bank_account(tenant_id, bank_account_ref_token);

-- 2) PSP ledger accounts (logical buckets per client legal entity)
CREATE TABLE IF NOT EXISTS psp_ledger_account (
  psp_ledger_account_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL,
  legal_entity_id UUID NOT NULL,
  account_type TEXT NOT NULL CHECK (account_type IN (
    'client_funding_clearing',
    'client_net_pay_payable',
    'client_tax_impound_payable',
    'client_third_party_payable',
    'psp_fees_revenue',
    'psp_settlement_clearing'
  )),
  currency CHAR(3) NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, legal_entity_id, account_type, currency)
);

CREATE INDEX IF NOT EXISTS psp_ledger_account_by_tenant
  ON psp_ledger_account(tenant_id, legal_entity_id);

-- 3) PSP ledger entries (append-only double-entry postings)
CREATE TABLE IF NOT EXISTS psp_ledger_entry (
  psp_ledger_entry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL,
  legal_entity_id UUID NOT NULL,
  posted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  entry_type TEXT NOT NULL CHECK (entry_type IN (
    'funding_received','funding_returned',
    'reserve_created','reserve_released',
    'employee_payment_initiated','employee_payment_settled','employee_payment_failed',
    'tax_payment_initiated','tax_payment_settled',
    'third_party_payment_initiated','third_party_payment_settled',
    'fee_assessed','reversal'
  )),
  debit_account_id UUID NOT NULL REFERENCES psp_ledger_account(psp_ledger_account_id),
  credit_account_id UUID NOT NULL REFERENCES psp_ledger_account(psp_ledger_account_id),
  amount NUMERIC(14,4) NOT NULL CHECK (amount > 0),
  source_type TEXT NOT NULL,
  source_id UUID NOT NULL,
  correlation_id UUID,
  idempotency_key TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_by_user_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS psp_ledger_entry_by_source
  ON psp_ledger_entry(tenant_id, source_type, source_id);

CREATE INDEX IF NOT EXISTS psp_ledger_entry_by_accounts
  ON psp_ledger_entry(debit_account_id, credit_account_id, posted_at);

-- Append-only enforcement triggers for ledger entries
CREATE OR REPLACE FUNCTION psp_ledger_entry_append_only()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'psp_ledger_entry is append-only; use reversal entries';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_psp_ledger_entry_no_update ON psp_ledger_entry;
CREATE TRIGGER trg_psp_ledger_entry_no_update
BEFORE UPDATE ON psp_ledger_entry
FOR EACH ROW EXECUTE FUNCTION psp_ledger_entry_append_only();

DROP TRIGGER IF EXISTS trg_psp_ledger_entry_no_delete ON psp_ledger_entry;
CREATE TRIGGER trg_psp_ledger_entry_no_delete
BEFORE DELETE ON psp_ledger_entry
FOR EACH ROW EXECUTE FUNCTION psp_ledger_entry_append_only();

-- 4) Reservations (funds held for obligations)
CREATE TABLE IF NOT EXISTS psp_reservation (
  psp_reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL,
  legal_entity_id UUID NOT NULL,
  reserve_type TEXT NOT NULL CHECK (reserve_type IN ('net_pay','tax','third_party','fees')),
  amount NUMERIC(14,4) NOT NULL CHECK (amount > 0),
  currency CHAR(3) NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','released','consumed')),
  source_type TEXT NOT NULL,
  source_id UUID NOT NULL,
  correlation_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  released_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS psp_reservation_open
  ON psp_reservation(tenant_id, legal_entity_id, reserve_type, status);

-- 5) Settlement events (bank/proc truth)
CREATE TABLE IF NOT EXISTS psp_settlement_event (
  psp_settlement_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  psp_bank_account_id UUID NOT NULL REFERENCES psp_bank_account(psp_bank_account_id),
  rail TEXT NOT NULL CHECK (rail IN ('ach','wire','rtp','fednow','check','internal')),
  direction TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
  amount NUMERIC(14,4) NOT NULL CHECK (amount > 0),
  currency CHAR(3) NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL CHECK (status IN ('created','submitted','accepted','settled','failed','reversed')),
  external_trace_id TEXT NOT NULL,
  effective_date DATE,
  raw_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- external_trace_id should be unique per bank account (avoid duplicates)
CREATE UNIQUE INDEX IF NOT EXISTS psp_settlement_event_trace_uq
  ON psp_settlement_event(psp_bank_account_id, external_trace_id);

CREATE INDEX IF NOT EXISTS psp_settlement_event_status
  ON psp_settlement_event(status, effective_date);

-- 6) Link settlement events to ledger entries
CREATE TABLE IF NOT EXISTS psp_settlement_link (
  psp_settlement_link_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  psp_settlement_event_id UUID NOT NULL REFERENCES psp_settlement_event(psp_settlement_event_id),
  psp_ledger_entry_id UUID NOT NULL REFERENCES psp_ledger_entry(psp_ledger_entry_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (psp_settlement_event_id, psp_ledger_entry_id)
);

-- 7) Tax liabilities (derived)
CREATE TABLE IF NOT EXISTS tax_liability (
  tax_liability_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL,
  legal_entity_id UUID NOT NULL,
  jurisdiction_id UUID NOT NULL,
  tax_agency_id UUID NOT NULL,
  tax_type TEXT NOT NULL,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  due_date DATE NOT NULL,
  amount NUMERIC(14,4) NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','reserved','paid','amended','voided')),
  source_pay_run_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tax_liability_due
  ON tax_liability(legal_entity_id, due_date);

CREATE INDEX IF NOT EXISTS tax_liability_period
  ON tax_liability(tax_agency_id, period_end);

-- 8) Third party obligations
CREATE TABLE IF NOT EXISTS third_party_obligation (
  third_party_obligation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL,
  legal_entity_id UUID NOT NULL,
  obligation_type TEXT NOT NULL,
  payee_profile_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  amount NUMERIC(14,4) NOT NULL CHECK (amount >= 0),
  due_date DATE,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','reserved','paid','failed','voided')),
  source_pay_run_id UUID,
  source_pay_statement_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS third_party_obligation_due
  ON third_party_obligation(tenant_id, legal_entity_id, status, due_date);

COMMIT;
