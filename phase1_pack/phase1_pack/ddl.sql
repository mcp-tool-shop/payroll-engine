-- Phase 1 Payroll Platform — Postgres DDL Skeleton
-- Notes:
-- - UUID PKs everywhere
-- - Immutable payroll results (pay_statement/pay_line_item append-only)
-- - Effective-dated tables use exclusion constraints to prevent overlaps
-- - Keep “type” columns as TEXT + CHECK constraints to avoid enum migrations in early phase

BEGIN;

-- ===== Extensions =====
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- exclusion constraints on uuid + daterange

-- ===== Shared helpers =====
CREATE TABLE IF NOT EXISTS app_meta (
  k TEXT PRIMARY KEY,
  v JSONB NOT NULL
);

-- ===== 1) Multi-tenant & employer structure =====
CREATE TABLE tenant (
  tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE address (
  address_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  line1 TEXT NOT NULL,
  line2 TEXT,
  city TEXT NOT NULL,
  state TEXT NOT NULL,
  postal_code TEXT NOT NULL,
  county TEXT,
  country TEXT NOT NULL DEFAULT 'US' CHECK (country = 'US'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE legal_entity (
  legal_entity_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
  legal_name TEXT NOT NULL,
  dba_name TEXT,
  ein TEXT NOT NULL,
  address_id UUID REFERENCES address(address_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, ein)
);
CREATE INDEX legal_entity_tenant_idx ON legal_entity(tenant_id);

CREATE TABLE worksite (
  worksite_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  address_id UUID REFERENCES address(address_id),
  worksite_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (legal_entity_id, worksite_code)
);
CREATE INDEX worksite_legal_entity_idx ON worksite(legal_entity_id);

CREATE TABLE department (
  department_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  department_code TEXT NOT NULL,
  name TEXT NOT NULL,
  UNIQUE (legal_entity_id, department_code)
);
CREATE INDEX department_legal_entity_idx ON department(legal_entity_id);

CREATE TABLE job (
  job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  job_code TEXT NOT NULL,
  title TEXT NOT NULL,
  is_union_eligible BOOLEAN NOT NULL DEFAULT false,
  UNIQUE (legal_entity_id, job_code)
);
CREATE INDEX job_legal_entity_idx ON job(legal_entity_id);

CREATE TABLE project (
  project_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  project_code TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed','archived')),
  UNIQUE (legal_entity_id, project_code)
);
CREATE INDEX project_legal_entity_idx ON project(legal_entity_id);

-- ===== 2) People & employment =====
CREATE TABLE person (
  person_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  dob DATE,
  ssn_last4 TEXT,
  email TEXT,
  phone TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX person_tenant_idx ON person(tenant_id);

CREATE TABLE employee (
  employee_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
  person_id UUID NOT NULL REFERENCES person(person_id) ON DELETE RESTRICT,
  employee_number TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','terminated','on_leave')),
  primary_legal_entity_id UUID REFERENCES legal_entity(legal_entity_id),
  home_address_id UUID REFERENCES address(address_id),
  hire_date DATE,
  termination_date DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, employee_number)
);
CREATE INDEX employee_tenant_idx ON employee(tenant_id);
CREATE INDEX employee_person_idx ON employee(person_id);

CREATE TABLE employment (
  employment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id UUID NOT NULL REFERENCES employee(employee_id) ON DELETE CASCADE,
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  start_date DATE NOT NULL,
  end_date DATE,
  worker_type TEXT NOT NULL DEFAULT 'w2' CHECK (worker_type IN ('w2')),
  pay_type TEXT NOT NULL CHECK (pay_type IN ('hourly','salary')),
  flsa_status TEXT NOT NULL CHECK (flsa_status IN ('exempt','nonexempt')),
  primary_worksite_id UUID REFERENCES worksite(worksite_id),
  primary_department_id UUID REFERENCES department(department_id),
  primary_job_id UUID REFERENCES job(job_id),
  manager_employee_id UUID REFERENCES employee(employee_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_date IS NULL OR end_date >= start_date)
);
CREATE INDEX employment_employee_idx ON employment(employee_id);
CREATE INDEX employment_legal_entity_idx ON employment(legal_entity_id);

ALTER TABLE employment
  ADD CONSTRAINT employment_no_overlap
  EXCLUDE USING gist (
    employee_id WITH =,
    legal_entity_id WITH =,
    daterange(start_date, COALESCE(end_date, 'infinity'::date), '[]') WITH &&
  );

-- ===== 3) Pay schedules & periods =====
CREATE TABLE pay_schedule (
  pay_schedule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  frequency TEXT NOT NULL CHECK (frequency IN ('weekly','biweekly','semimonthly','monthly')),
  pay_day_rule TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (legal_entity_id, name)
);
CREATE INDEX pay_schedule_legal_entity_idx ON pay_schedule(legal_entity_id);

CREATE TABLE pay_period (
  pay_period_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pay_schedule_id UUID NOT NULL REFERENCES pay_schedule(pay_schedule_id) ON DELETE CASCADE,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  check_date DATE NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','locked','paid','voided')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (period_end >= period_start),
  UNIQUE (pay_schedule_id, period_start, period_end)
);
CREATE INDEX pay_period_schedule_idx ON pay_period(pay_schedule_id);
CREATE INDEX pay_period_check_date_idx ON pay_period(check_date);

CREATE TABLE employee_pay_schedule (
  employee_pay_schedule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id UUID NOT NULL REFERENCES employee(employee_id) ON DELETE CASCADE,
  pay_schedule_id UUID NOT NULL REFERENCES pay_schedule(pay_schedule_id) ON DELETE CASCADE,
  start_date DATE NOT NULL,
  end_date DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_date IS NULL OR end_date >= start_date)
);
CREATE INDEX eps_employee_idx ON employee_pay_schedule(employee_id);
CREATE INDEX eps_schedule_idx ON employee_pay_schedule(pay_schedule_id);

ALTER TABLE employee_pay_schedule
  ADD CONSTRAINT eps_no_overlap
  EXCLUDE USING gist (
    employee_id WITH =,
    daterange(start_date, COALESCE(end_date, 'infinity'::date), '[]') WITH &&
  );

-- ===== 4) Pay rates =====
CREATE TABLE pay_rate (
  pay_rate_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id UUID NOT NULL REFERENCES employee(employee_id) ON DELETE CASCADE,
  start_date DATE NOT NULL,
  end_date DATE,
  rate_type TEXT NOT NULL,
  amount NUMERIC(12,4) NOT NULL,
  currency CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
  job_id UUID REFERENCES job(job_id),
  project_id UUID REFERENCES project(project_id),
  department_id UUID REFERENCES department(department_id),
  worksite_id UUID REFERENCES worksite(worksite_id),
  priority INT NOT NULL DEFAULT 0,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_date IS NULL OR end_date >= start_date)
);
CREATE INDEX pay_rate_employee_idx ON pay_rate(employee_id);
CREATE INDEX pay_rate_dims_idx ON pay_rate(employee_id, job_id, project_id, department_id, worksite_id);
CREATE INDEX pay_rate_effective_idx ON pay_rate(employee_id, start_date, end_date);

-- ===== 5) Earnings & deductions =====
CREATE TABLE earning_code (
  earning_code_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  earning_category TEXT NOT NULL,
  is_taxable_federal BOOLEAN NOT NULL DEFAULT true,
  is_taxable_state_default BOOLEAN NOT NULL DEFAULT true,
  is_taxable_local_default BOOLEAN NOT NULL DEFAULT true,
  gl_account_hint TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (legal_entity_id, code)
);
CREATE INDEX earning_code_legal_entity_idx ON earning_code(legal_entity_id);

CREATE TABLE deduction_code (
  deduction_code_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  deduction_type TEXT NOT NULL CHECK (deduction_type IN ('pretax','posttax','roth','aftertax','loan','other')),
  calc_method TEXT NOT NULL CHECK (calc_method IN ('flat','percent','tiered')),
  limit_type TEXT CHECK (limit_type IN ('per_check','per_period','annual')),
  is_employer_match_eligible BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (legal_entity_id, code)
);
CREATE INDEX deduction_code_legal_entity_idx ON deduction_code(legal_entity_id);

CREATE TABLE employee_deduction (
  employee_deduction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id UUID NOT NULL REFERENCES employee(employee_id) ON DELETE CASCADE,
  deduction_code_id UUID NOT NULL REFERENCES deduction_code(deduction_code_id) ON DELETE CASCADE,
  start_date DATE NOT NULL,
  end_date DATE,
  employee_amount NUMERIC(12,4),
  employee_percent NUMERIC(7,4),
  employer_amount NUMERIC(12,4),
  employer_percent NUMERIC(7,4),
  taxability_overrides_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  provider_reference TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_date IS NULL OR end_date >= start_date),
  CHECK (
    (employee_amount IS NOT NULL)::int + (employee_percent IS NOT NULL)::int <= 1
  ),
  CHECK (
    (employer_amount IS NOT NULL)::int + (employer_percent IS NOT NULL)::int <= 1
  )
);
CREATE INDEX employee_deduction_employee_idx ON employee_deduction(employee_id);
CREATE INDEX employee_deduction_code_idx ON employee_deduction(deduction_code_id);

ALTER TABLE employee_deduction
  ADD CONSTRAINT employee_deduction_no_overlap
  EXCLUDE USING gist (
    employee_id WITH =,
    deduction_code_id WITH =,
    daterange(start_date, COALESCE(end_date, 'infinity'::date), '[]') WITH &&
  );

CREATE TABLE garnishment_order (
  garnishment_order_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id UUID NOT NULL REFERENCES employee(employee_id) ON DELETE CASCADE,
  order_type TEXT NOT NULL,
  priority_rank INT NOT NULL DEFAULT 100,
  start_date DATE NOT NULL,
  end_date DATE,
  max_percent NUMERIC(7,4),
  max_amount NUMERIC(12,4),
  case_number TEXT,
  payee_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  rules_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_date IS NULL OR end_date >= start_date)
);
CREATE INDEX garnishment_employee_idx ON garnishment_order(employee_id);
CREATE INDEX garnishment_priority_idx ON garnishment_order(employee_id, priority_rank);

-- ===== 6) Jurisdictions & tax setup =====
CREATE TABLE jurisdiction (
  jurisdiction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  jurisdiction_type TEXT NOT NULL CHECK (jurisdiction_type IN ('FED','STATE','LOCAL')),
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  parent_jurisdiction_id UUID REFERENCES jurisdiction(jurisdiction_id),
  UNIQUE (jurisdiction_type, code)
);
CREATE INDEX jurisdiction_parent_idx ON jurisdiction(parent_jurisdiction_id);

CREATE TABLE tax_agency (
  tax_agency_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  jurisdiction_id UUID NOT NULL REFERENCES jurisdiction(jurisdiction_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  agency_type TEXT NOT NULL,
  registration_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX tax_agency_jurisdiction_idx ON tax_agency(jurisdiction_id);

CREATE TABLE employer_tax_account (
  employer_tax_account_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  tax_agency_id UUID NOT NULL REFERENCES tax_agency(tax_agency_id) ON DELETE CASCADE,
  account_number_token TEXT,
  deposit_schedule TEXT,
  effective_start DATE NOT NULL,
  effective_end DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (effective_end IS NULL OR effective_end >= effective_start)
);
CREATE INDEX employer_tax_account_le_idx ON employer_tax_account(legal_entity_id);
CREATE INDEX employer_tax_account_agency_idx ON employer_tax_account(tax_agency_id);

ALTER TABLE employer_tax_account
  ADD CONSTRAINT employer_tax_account_no_overlap
  EXCLUDE USING gist (
    legal_entity_id WITH =,
    tax_agency_id WITH =,
    daterange(effective_start, COALESCE(effective_end, 'infinity'::date), '[]') WITH &&
  );

CREATE TABLE employee_tax_profile (
  employee_tax_profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id UUID NOT NULL REFERENCES employee(employee_id) ON DELETE CASCADE,
  jurisdiction_id UUID NOT NULL REFERENCES jurisdiction(jurisdiction_id) ON DELETE CASCADE,
  filing_status TEXT,
  allowances INT,
  additional_withholding NUMERIC(12,4),
  residency_status TEXT CHECK (residency_status IN ('resident','nonresident')),
  work_location_basis TEXT CHECK (work_location_basis IN ('home','work','both')),
  effective_start DATE NOT NULL,
  effective_end DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (effective_end IS NULL OR effective_end >= effective_start)
);
CREATE INDEX employee_tax_profile_employee_idx ON employee_tax_profile(employee_id);
CREATE INDEX employee_tax_profile_jurisdiction_idx ON employee_tax_profile(jurisdiction_id);

ALTER TABLE employee_tax_profile
  ADD CONSTRAINT employee_tax_profile_no_overlap
  EXCLUDE USING gist (
    employee_id WITH =,
    jurisdiction_id WITH =,
    daterange(effective_start, COALESCE(effective_end, 'infinity'::date), '[]') WITH &&
  );

-- ===== 7) Compliance traceability (rules + versions) =====
CREATE TABLE payroll_rule (
  rule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rule_name TEXT NOT NULL,
  rule_type TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payroll_rule_version (
  rule_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rule_id UUID NOT NULL REFERENCES payroll_rule(rule_id) ON DELETE CASCADE,
  effective_start DATE NOT NULL,
  effective_end DATE,
  source_url TEXT NOT NULL,
  source_last_verified_at TIMESTAMPTZ NOT NULL,
  logic_hash TEXT NOT NULL,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (effective_end IS NULL OR effective_end >= effective_start)
);
CREATE INDEX payroll_rule_version_rule_idx ON payroll_rule_version(rule_id);
CREATE INDEX payroll_rule_version_effective_idx ON payroll_rule_version(rule_id, effective_start, effective_end);

ALTER TABLE payroll_rule_version
  ADD CONSTRAINT payroll_rule_version_no_overlap
  EXCLUDE USING gist (
    rule_id WITH =,
    daterange(effective_start, COALESCE(effective_end, 'infinity'::date), '[]') WITH &&
  );

-- ===== 8) Users =====
CREATE TABLE app_user (
  user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  mfa_enabled BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, email)
);
CREATE INDEX app_user_tenant_idx ON app_user(tenant_id);

-- ===== 9) Payroll run & immutable results =====
CREATE TABLE pay_run (
  pay_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  pay_period_id UUID REFERENCES pay_period(pay_period_id),
  run_type TEXT NOT NULL CHECK (run_type IN ('regular','offcycle','bonus','manual')),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','preview','approved','committed','paid','voided')),
  created_by_user_id UUID REFERENCES app_user(user_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_at TIMESTAMPTZ,
  committed_at TIMESTAMPTZ
);
CREATE INDEX pay_run_le_idx ON pay_run(legal_entity_id);
CREATE INDEX pay_run_period_idx ON pay_run(pay_period_id);
CREATE INDEX pay_run_status_idx ON pay_run(status);

CREATE TABLE pay_run_employee (
  pay_run_employee_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pay_run_id UUID NOT NULL REFERENCES pay_run(pay_run_id) ON DELETE CASCADE,
  employee_id UUID NOT NULL REFERENCES employee(employee_id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'included' CHECK (status IN ('included','excluded','error')),
  calculation_version TEXT NOT NULL,
  gross NUMERIC(14,4) NOT NULL DEFAULT 0,
  net NUMERIC(14,4) NOT NULL DEFAULT 0,
  currency CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (pay_run_id, employee_id)
);
CREATE INDEX pre_pay_run_idx ON pay_run_employee(pay_run_id);
CREATE INDEX pre_employee_idx ON pay_run_employee(employee_id);

CREATE TABLE pay_statement (
  pay_statement_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pay_run_employee_id UUID NOT NULL REFERENCES pay_run_employee(pay_run_employee_id) ON DELETE CASCADE,
  check_number TEXT,
  check_date DATE NOT NULL,
  payment_method TEXT NOT NULL CHECK (payment_method IN ('ach','check','paycard','other')),
  statement_status TEXT NOT NULL DEFAULT 'issued' CHECK (statement_status IN ('issued','voided','reissued')),
  net_pay NUMERIC(14,4) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX pay_statement_pre_idx ON pay_statement(pay_run_employee_id);
CREATE INDEX pay_statement_check_date_idx ON pay_statement(check_date);

CREATE TABLE pay_line_item (
  pay_line_item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pay_statement_id UUID NOT NULL REFERENCES pay_statement(pay_statement_id) ON DELETE CASCADE,
  line_type TEXT NOT NULL CHECK (line_type IN ('EARNING','DEDUCTION','TAX','EMPLOYER_TAX','REIMBURSEMENT')),
  earning_code_id UUID REFERENCES earning_code(earning_code_id),
  deduction_code_id UUID REFERENCES deduction_code(deduction_code_id),
  tax_agency_id UUID REFERENCES tax_agency(tax_agency_id),
  jurisdiction_id UUID REFERENCES jurisdiction(jurisdiction_id),
  quantity NUMERIC(14,4),
  rate NUMERIC(14,6),
  amount NUMERIC(14,4) NOT NULL,
  taxability_flags_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_input_id UUID,
  rule_id UUID REFERENCES payroll_rule(rule_id),
  rule_version_id UUID REFERENCES payroll_rule_version(rule_version_id),
  explanation TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    ((earning_code_id IS NOT NULL)::int +
     (deduction_code_id IS NOT NULL)::int +
     (tax_agency_id IS NOT NULL)::int) <= 1
  ),
  CHECK (
    (line_type NOT IN ('TAX','EMPLOYER_TAX')) OR (jurisdiction_id IS NOT NULL)
  )
);
CREATE INDEX pli_statement_idx ON pay_line_item(pay_statement_id);
CREATE INDEX pli_type_idx ON pay_line_item(line_type);
CREATE INDEX pli_rule_idx ON pay_line_item(rule_id, rule_version_id);
CREATE INDEX pli_jurisdiction_idx ON pay_line_item(jurisdiction_id);

-- ===== 10) Inputs =====
CREATE TABLE time_entry (
  time_entry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id UUID NOT NULL REFERENCES employee(employee_id) ON DELETE CASCADE,
  work_date DATE NOT NULL,
  earning_code_id UUID NOT NULL REFERENCES earning_code(earning_code_id) ON DELETE CASCADE,
  hours NUMERIC(12,4),
  units NUMERIC(12,4),
  rate_override NUMERIC(14,6),
  department_id UUID REFERENCES department(department_id),
  job_id UUID REFERENCES job(job_id),
  project_id UUID REFERENCES project(project_id),
  worksite_id UUID REFERENCES worksite(worksite_id),
  source_system TEXT NOT NULL DEFAULT 'manual' CHECK (source_system IN ('manual','import','api')),
  approved_by_user_id UUID REFERENCES app_user(user_id),
  approved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((hours IS NOT NULL)::int + (units IS NOT NULL)::int >= 1)
);
CREATE INDEX time_entry_employee_date_idx ON time_entry(employee_id, work_date);
CREATE INDEX time_entry_earning_code_idx ON time_entry(earning_code_id);

CREATE TABLE pay_input_adjustment (
  pay_input_adjustment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id UUID NOT NULL REFERENCES employee(employee_id) ON DELETE CASCADE,
  target_pay_run_id UUID REFERENCES pay_run(pay_run_id) ON DELETE SET NULL,
  target_pay_period_id UUID REFERENCES pay_period(pay_period_id) ON DELETE SET NULL,
  adjustment_type TEXT NOT NULL CHECK (adjustment_type IN ('earning','deduction')),
  earning_code_id UUID REFERENCES earning_code(earning_code_id),
  deduction_code_id UUID REFERENCES deduction_code(deduction_code_id),
  amount NUMERIC(14,4),
  quantity NUMERIC(14,4),
  rate NUMERIC(14,6),
  department_id UUID REFERENCES department(department_id),
  job_id UUID REFERENCES job(job_id),
  project_id UUID REFERENCES project(project_id),
  worksite_id UUID REFERENCES worksite(worksite_id),
  memo TEXT,
  created_by_user_id UUID REFERENCES app_user(user_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    (adjustment_type = 'earning' AND earning_code_id IS NOT NULL AND deduction_code_id IS NULL) OR
    (adjustment_type = 'deduction' AND deduction_code_id IS NOT NULL AND earning_code_id IS NULL)
  ),
  CHECK (
    amount IS NOT NULL OR (quantity IS NOT NULL AND rate IS NOT NULL)
  )
);
CREATE INDEX pia_employee_idx ON pay_input_adjustment(employee_id);
CREATE INDEX pia_target_run_idx ON pay_input_adjustment(target_pay_run_id);
CREATE INDEX pia_target_period_idx ON pay_input_adjustment(target_pay_period_id);

-- ===== 11) GL exports =====
CREATE TABLE gl_config (
  gl_config_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  legal_entity_id UUID NOT NULL REFERENCES legal_entity(legal_entity_id) ON DELETE CASCADE,
  format TEXT NOT NULL CHECK (format IN ('csv','iif','api')),
  segmentation_rules_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX gl_config_le_idx ON gl_config(legal_entity_id);

CREATE TABLE gl_mapping_rule (
  gl_mapping_rule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  gl_config_id UUID NOT NULL REFERENCES gl_config(gl_config_id) ON DELETE CASCADE,
  line_type TEXT NOT NULL CHECK (line_type IN ('EARNING','DEDUCTION','TAX','EMPLOYER_TAX','REIMBURSEMENT')),
  earning_code_id UUID REFERENCES earning_code(earning_code_id),
  deduction_code_id UUID REFERENCES deduction_code(deduction_code_id),
  debit_account TEXT NOT NULL,
  credit_account TEXT NOT NULL,
  dimension_overrides_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    ((earning_code_id IS NOT NULL)::int + (deduction_code_id IS NOT NULL)::int) <= 1
  )
);
CREATE INDEX gl_mapping_config_idx ON gl_mapping_rule(gl_config_id);

CREATE TABLE gl_journal_batch (
  gl_journal_batch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pay_run_id UUID NOT NULL REFERENCES pay_run(pay_run_id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'generated' CHECK (status IN ('generated','exported','posted','failed')),
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX gl_journal_batch_run_idx ON gl_journal_batch(pay_run_id);

CREATE TABLE gl_journal_line (
  gl_journal_line_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  gl_journal_batch_id UUID NOT NULL REFERENCES gl_journal_batch(gl_journal_batch_id) ON DELETE CASCADE,
  account_string TEXT NOT NULL,
  debit NUMERIC(14,4) NOT NULL DEFAULT 0,
  credit NUMERIC(14,4) NOT NULL DEFAULT 0,
  department_id UUID REFERENCES department(department_id),
  job_id UUID REFERENCES job(job_id),
  project_id UUID REFERENCES project(project_id),
  worksite_id UUID REFERENCES worksite(worksite_id),
  source_pay_line_item_id UUID REFERENCES pay_line_item(pay_line_item_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((debit = 0 AND credit <> 0) OR (credit = 0 AND debit <> 0))
);
CREATE INDEX gl_journal_line_batch_idx ON gl_journal_line(gl_journal_batch_id);
CREATE INDEX gl_journal_line_source_idx ON gl_journal_line(source_pay_line_item_id);

-- ===== 12) Payments =====
CREATE TABLE employee_payment_account (
  employee_payment_account_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  employee_id UUID NOT NULL REFERENCES employee(employee_id) ON DELETE CASCADE,
  payment_type TEXT NOT NULL CHECK (payment_type IN ('ach','paycard')),
  tokenized_account_ref TEXT NOT NULL,
  split_percent NUMERIC(7,4),
  split_amount NUMERIC(14,4),
  effective_start DATE NOT NULL,
  effective_end DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (effective_end IS NULL OR effective_end >= effective_start),
  CHECK ((split_percent IS NULL OR split_amount IS NULL))
);
CREATE INDEX epa_employee_idx ON employee_payment_account(employee_id);

ALTER TABLE employee_payment_account
  ADD CONSTRAINT employee_payment_account_no_overlap
  EXCLUDE USING gist (
    employee_id WITH =,
    payment_type WITH =,
    daterange(effective_start, COALESCE(effective_end, 'infinity'::date), '[]') WITH &&
  );

CREATE TABLE payment_batch (
  payment_batch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pay_run_id UUID NOT NULL REFERENCES pay_run(pay_run_id) ON DELETE CASCADE,
  processor TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created','submitted','settled','failed')),
  total_amount NUMERIC(14,4) NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payment_batch_run_idx ON payment_batch(pay_run_id);

CREATE TABLE payment_batch_item (
  payment_batch_item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_batch_id UUID NOT NULL REFERENCES payment_batch(payment_batch_id) ON DELETE CASCADE,
  pay_statement_id UUID NOT NULL REFERENCES pay_statement(pay_statement_id) ON DELETE CASCADE,
  amount NUMERIC(14,4) NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed','settled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (payment_batch_id, pay_statement_id)
);
CREATE INDEX payment_batch_item_batch_idx ON payment_batch_item(payment_batch_id);

-- ===== 13) Audit trail =====
CREATE TABLE audit_event (
  audit_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
  actor_user_id UUID REFERENCES app_user(user_id),
  entity_type TEXT NOT NULL,
  entity_id UUID NOT NULL,
  action TEXT NOT NULL,
  before_json JSONB,
  after_json JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ip INET,
  user_agent TEXT
);
CREATE INDEX audit_event_tenant_time_idx ON audit_event(tenant_id, created_at DESC);
CREATE INDEX audit_event_entity_idx ON audit_event(entity_type, entity_id);

COMMIT;
