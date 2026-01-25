-- fixtures/seed_minimal.sql
-- Minimal fixture data for Phase 1 smoke tests.
-- WARNING: Uses deterministic UUIDs for repeatability. Do not use in production.

BEGIN;

-- Tenant
INSERT INTO tenant (tenant_id, name, status) VALUES
  ('adfb6898-026f-fa17-8583-404672c7972a', 'Demo Tenant', 'active')
ON CONFLICT DO NOTHING;

-- Address + Legal entity + org dims
INSERT INTO address (address_id, line1, city, state, postal_code, country) VALUES
  ('b589e663-32d3-fd91-30ed-e80f7b1c748b', '100 Main St', 'San Francisco', 'CA', '94105', 'US')
ON CONFLICT DO NOTHING;

INSERT INTO legal_entity (legal_entity_id, tenant_id, legal_name, dba_name, ein, address_id) VALUES
  ('d9180594-812b-1cf5-0398-0b98f7bc56c6', 'adfb6898-026f-fa17-8583-404672c7972a', 'DemoCo Inc', 'DemoCo', '12-3456789', 'b589e663-32d3-fd91-30ed-e80f7b1c748b')
ON CONFLICT DO NOTHING;

INSERT INTO worksite (worksite_id, legal_entity_id, name, address_id, worksite_code) VALUES
  ('742523da-4bb0-3047-8abf-a02065951cc9', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', 'HQ', 'b589e663-32d3-fd91-30ed-e80f7b1c748b', 'HQ')
ON CONFLICT DO NOTHING;

INSERT INTO department (department_id, legal_entity_id, department_code, name) VALUES
  ('db64e03c-7c22-03e9-f11e-902c0a634c06', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', 'ENG', 'Engineering')
ON CONFLICT DO NOTHING;

INSERT INTO job (job_id, legal_entity_id, job_code, title, is_union_eligible) VALUES
  ('9dddd5ce-88ac-d477-2d6e-3e54b748b6ea', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', 'SWE1', 'Software Engineer I', false)
ON CONFLICT DO NOTHING;

INSERT INTO project (project_id, legal_entity_id, project_code, name, status) VALUES
  ('4dc844ab-62f7-1768-6dc5-bb610eb82e6e', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', 'P100', 'Project 100', 'active')
ON CONFLICT DO NOTHING;

-- Pay schedule + pay period
INSERT INTO pay_schedule (pay_schedule_id, legal_entity_id, name, frequency, pay_day_rule) VALUES
  ('07faba30-761a-e051-ecc8-e13ee1d340f7', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', 'Biweekly', 'biweekly', 'FRI')
ON CONFLICT DO NOTHING;

INSERT INTO pay_period (pay_period_id, pay_schedule_id, period_start, period_end, check_date, status) VALUES
  ('6c3e4b0f-8bba-1876-739f-ee93de39dd68', '07faba30-761a-e051-ecc8-e13ee1d340f7', '2026-01-05', '2026-01-18', '2026-01-23', 'open')
ON CONFLICT DO NOTHING;

-- User
INSERT INTO app_user (user_id, tenant_id, email, status, mfa_enabled) VALUES
  ('ee11cbb1-697a-4e1b-cc55-4ea5ae67c6e1', 'adfb6898-026f-fa17-8583-404672c7972a', 'admin@democo.test', 'active', true)
ON CONFLICT DO NOTHING;

-- Jurisdictions + agencies
INSERT INTO jurisdiction (jurisdiction_id, jurisdiction_type, code, name, parent_jurisdiction_id) VALUES
  ('1285a601-1acd-cb9e-252a-b57ea6fe89e7', 'FED', 'US', 'United States', NULL),
  ('a135f9b2-d323-2d62-e6df-e65b9357fcc3', 'STATE', 'CA', 'California', '1285a601-1acd-cb9e-252a-b57ea6fe89e7')
ON CONFLICT DO NOTHING;

INSERT INTO tax_agency (tax_agency_id, jurisdiction_id, name, agency_type, registration_url) VALUES
  ('79897913-d7d9-e873-2665-35b6e5d06431', '1285a601-1acd-cb9e-252a-b57ea6fe89e7', 'Internal Revenue Service', 'IRS', 'https://www.irs.gov/'),
  ('f16dda73-1b3f-59c9-4fa8-1c90c4419a82', 'a135f9b2-d323-2d62-e6df-e65b9357fcc3', 'California Franchise Tax Board', 'state_DOR', 'https://www.ftb.ca.gov/')
ON CONFLICT DO NOTHING;

-- Rules + versions (placeholder logic_hash/payload_json)
INSERT INTO payroll_rule (rule_id, rule_name, rule_type) VALUES
  ('964a46e8-5037-6e67-31fb-0cfe8bc68a82', 'Federal Income Tax Withholding', 'TAX'),
  ('8f7db38c-98ef-c4d5-7bb2-7078fec70672', 'FICA (SS + Medicare)', 'TAX'),
  ('f8395700-b49f-f5b8-43e6-7ec30bf964a6', 'CA State Withholding', 'TAX')
ON CONFLICT DO NOTHING;

INSERT INTO payroll_rule_version (rule_version_id, rule_id, effective_start, effective_end, source_url, source_last_verified_at, logic_hash, payload_json) VALUES
  ('fd135175-f59d-612d-be6f-41cf3f3601cf', '964a46e8-5037-6e67-31fb-0cfe8bc68a82', '2025-01-01', NULL, 'https://www.irs.gov/', now(), 'hash-fit', '{}'),
  ('96a0e0cc-ebab-cd6a-439a-da77c338d589', '8f7db38c-98ef-c4d5-7bb2-7078fec70672', '2025-01-01', NULL, 'https://www.irs.gov/', now(), 'hash-fica', '{}'),
  ('4a1aca8e-d14f-d7ed-ca24-d34567b986b4', 'f8395700-b49f-f5b8-43e6-7ec30bf964a6', '2025-01-01', NULL, 'https://www.ftb.ca.gov/', now(), 'hash-ca', '{}')
ON CONFLICT DO NOTHING;

-- People + employees
INSERT INTO person (person_id, tenant_id, first_name, last_name, email) VALUES
  ('25bf0679-38ea-676d-9c53-96b42eba9578', 'adfb6898-026f-fa17-8583-404672c7972a', 'Alice', 'Ng', 'alice@democo.test'),
  ('7bc5facc-8a0d-f2ea-96be-3c3957a89e13', 'adfb6898-026f-fa17-8583-404672c7972a', 'Bob', 'Diaz', 'bob@democo.test')
ON CONFLICT DO NOTHING;

INSERT INTO employee (employee_id, tenant_id, person_id, employee_number, status, primary_legal_entity_id, hire_date) VALUES
  ('86ea3363-d3fb-d158-5c22-5fe5808ffe48', 'adfb6898-026f-fa17-8583-404672c7972a', '25bf0679-38ea-676d-9c53-96b42eba9578', 'E1001', 'active', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', '2024-05-01'),
  ('41ab3465-680d-b500-075e-b5eec7cf4840', 'adfb6898-026f-fa17-8583-404672c7972a', '7bc5facc-8a0d-f2ea-96be-3c3957a89e13', 'E1002', 'active', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', '2023-09-15')
ON CONFLICT DO NOTHING;

-- Employment (effective)
INSERT INTO employment (employment_id, employee_id, legal_entity_id, start_date, end_date, worker_type, pay_type, flsa_status, primary_worksite_id, primary_department_id, primary_job_id)
VALUES
  ('fc0917ec-7eeb-413e-13f7-2794b3d27193', '86ea3363-d3fb-d158-5c22-5fe5808ffe48', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', '2024-05-01', NULL, 'w2', 'hourly', 'nonexempt', '742523da-4bb0-3047-8abf-a02065951cc9', 'db64e03c-7c22-03e9-f11e-902c0a634c06', '9dddd5ce-88ac-d477-2d6e-3e54b748b6ea'),
  ('c619ea20-bc69-08a0-e6df-0d8116e95a2e', '41ab3465-680d-b500-075e-b5eec7cf4840', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', '2023-09-15', NULL, 'w2', 'salary', 'exempt', '742523da-4bb0-3047-8abf-a02065951cc9', 'db64e03c-7c22-03e9-f11e-902c0a634c06', '9dddd5ce-88ac-d477-2d6e-3e54b748b6ea')
ON CONFLICT DO NOTHING;

-- Pay schedule assignment
INSERT INTO employee_pay_schedule (employee_pay_schedule_id, employee_id, pay_schedule_id, start_date, end_date)
VALUES
  ('e3571171-c22b-a6ce-dd81-0f888bc51c5a', '86ea3363-d3fb-d158-5c22-5fe5808ffe48', '07faba30-761a-e051-ecc8-e13ee1d340f7', '2024-05-01', NULL),
  ('0c6fe439-5490-00a6-dc5a-111e80cedfe3', '41ab3465-680d-b500-075e-b5eec7cf4840', '07faba30-761a-e051-ecc8-e13ee1d340f7', '2023-09-15', NULL)
ON CONFLICT DO NOTHING;

-- Earning codes
INSERT INTO earning_code (earning_code_id, legal_entity_id, code, name, earning_category,
  is_taxable_federal, is_taxable_state_default, is_taxable_local_default)
VALUES
  ('5a21c858-a73c-862e-4d28-24c62a905828', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', 'REG', 'Regular', 'regular', true, true, true),
  ('3d40b48a-0636-91ec-cb6e-8d07e28410d4', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', 'BON', 'Bonus', 'bonus', true, true, true)
ON CONFLICT DO NOTHING;

-- Deduction codes
INSERT INTO deduction_code (deduction_code_id, legal_entity_id, code, name, deduction_type, calc_method, limit_type, is_employer_match_eligible)
VALUES
  ('3433946b-b6b0-5e1e-4494-93fda098a817', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', '401K', '401(k) Pretax', 'pretax', 'percent', 'per_check', true),
  ('bcf08044-4da6-9da7-881c-03583371c7bd', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', 'PARK', 'Parking (Post-tax)', 'posttax', 'flat', 'per_check', false)
ON CONFLICT DO NOTHING;

-- Employee deductions
INSERT INTO employee_deduction (employee_deduction_id, employee_id, deduction_code_id, start_date, end_date, employee_percent)
VALUES
  ('5718f602-a8bb-8dc7-1647-358c65389d64', '86ea3363-d3fb-d158-5c22-5fe5808ffe48', '3433946b-b6b0-5e1e-4494-93fda098a817', '2025-01-01', NULL, 0.0500),
  ('25285208-1d1f-ffd5-0ed7-86548c82d1ce', '41ab3465-680d-b500-075e-b5eec7cf4840', 'bcf08044-4da6-9da7-881c-03583371c7bd', '2025-01-01', NULL, NULL)
ON CONFLICT DO NOTHING;

-- Employee tax profiles (Federal + CA)
INSERT INTO employee_tax_profile (employee_tax_profile_id, employee_id, jurisdiction_id, filing_status, allowances, additional_withholding, residency_status, work_location_basis, effective_start, effective_end)
VALUES
  ('e647ac7e-d5c1-cdc1-362c-373dd7bdbfc7', '86ea3363-d3fb-d158-5c22-5fe5808ffe48', 'a135f9b2-d323-2d62-e6df-e65b9357fcc3', 'single', 0, 0.00, 'resident', 'work', '2025-01-01', NULL),
  ('dc627c42-acf4-caf4-a72a-7db66930dd4b', '41ab3465-680d-b500-075e-b5eec7cf4840', 'a135f9b2-d323-2d62-e6df-e65b9357fcc3', 'single', 0, 0.00, 'resident', 'work', '2025-01-01', NULL)
ON CONFLICT DO NOTHING;

-- Pay rates
-- Alice hourly: base $30, plus an optional differential rate type example
INSERT INTO pay_rate (pay_rate_id, employee_id, start_date, end_date, rate_type, amount, currency, job_id, project_id, department_id, worksite_id, priority, metadata_json)
VALUES
  ('ea7b9ce0-91d3-c750-cddd-bfb14a7dbd1d', '86ea3363-d3fb-d158-5c22-5fe5808ffe48', '2025-01-01', NULL, 'hourly', 30.00, 'USD', '9dddd5ce-88ac-d477-2d6e-3e54b748b6ea', '4dc844ab-62f7-1768-6dc5-bb610eb82e6e', 'db64e03c-7c22-03e9-f11e-902c0a634c06', '742523da-4bb0-3047-8abf-a02065951cc9', 10, '{}'),
  ('6f0ea740-044b-8649-becb-a32bf03597f6', '86ea3363-d3fb-d158-5c22-5fe5808ffe48', '2025-01-01', NULL, 'shift_diff', 2.00, 'USD', '9dddd5ce-88ac-d477-2d6e-3e54b748b6ea', '4dc844ab-62f7-1768-6dc5-bb610eb82e6e', 'db64e03c-7c22-03e9-f11e-902c0a634c06', '742523da-4bb0-3047-8abf-a02065951cc9', 5, '{"note":"shift diff example"}'),
  ('c26d629a-c1cc-6c54-7eda-b268593c14dd', '41ab3465-680d-b500-075e-b5eec7cf4840', '2025-01-01', NULL, 'salary', 120000.00, 'USD', '9dddd5ce-88ac-d477-2d6e-3e54b748b6ea', NULL, 'db64e03c-7c22-03e9-f11e-902c0a634c06', '742523da-4bb0-3047-8abf-a02065951cc9', 10, '{}')
ON CONFLICT DO NOTHING;

-- Time entries for Alice (80 hours in period across two entries)
INSERT INTO time_entry (time_entry_id, employee_id, work_date, earning_code_id, hours, department_id, job_id, project_id, worksite_id, source_system, approved_by_user_id, approved_at)
VALUES
  ('a7c2a9cd-7f71-a38d-943d-d24daa38ab89', '86ea3363-d3fb-d158-5c22-5fe5808ffe48', '2026-01-06', '5a21c858-a73c-862e-4d28-24c62a905828', 40.0, 'db64e03c-7c22-03e9-f11e-902c0a634c06', '9dddd5ce-88ac-d477-2d6e-3e54b748b6ea', '4dc844ab-62f7-1768-6dc5-bb610eb82e6e', '742523da-4bb0-3047-8abf-a02065951cc9', 'manual', 'ee11cbb1-697a-4e1b-cc55-4ea5ae67c6e1', now()),
  ('eeaab52f-535a-a3fc-a525-1d00d140b16e', '86ea3363-d3fb-d158-5c22-5fe5808ffe48', '2026-01-13', '5a21c858-a73c-862e-4d28-24c62a905828', 40.0, 'db64e03c-7c22-03e9-f11e-902c0a634c06', '9dddd5ce-88ac-d477-2d6e-3e54b748b6ea', '4dc844ab-62f7-1768-6dc5-bb610eb82e6e', '742523da-4bb0-3047-8abf-a02065951cc9', 'manual', 'ee11cbb1-697a-4e1b-cc55-4ea5ae67c6e1', now())
ON CONFLICT DO NOTHING;

-- Adjustment bonus for Alice
INSERT INTO pay_input_adjustment (pay_input_adjustment_id, employee_id, target_pay_period_id, adjustment_type, earning_code_id, amount, memo, created_by_user_id)
VALUES
  ('96ff8250-6aa5-e0b0-3b5e-5859f8641050', '86ea3363-d3fb-d158-5c22-5fe5808ffe48', '6c3e4b0f-8bba-1876-739f-ee93de39dd68', 'earning', '3d40b48a-0636-91ec-cb6e-8d07e28410d4', 500.00, 'Spot bonus', 'ee11cbb1-697a-4e1b-cc55-4ea5ae67c6e1')
ON CONFLICT DO NOTHING;

-- Pay run + included employees
INSERT INTO pay_run (pay_run_id, legal_entity_id, pay_period_id, run_type, status, created_by_user_id)
VALUES
  ('64e1e1cb-3c24-49e1-5730-23e1691cb0a5', 'd9180594-812b-1cf5-0398-0b98f7bc56c6', '6c3e4b0f-8bba-1876-739f-ee93de39dd68', 'regular', 'draft', 'ee11cbb1-697a-4e1b-cc55-4ea5ae67c6e1')
ON CONFLICT DO NOTHING;

INSERT INTO pay_run_employee (pay_run_employee_id, pay_run_id, employee_id, status, calculation_version)
VALUES
  ('5e893223-52b4-b5c0-ad8b-51f7ea40ead0', '64e1e1cb-3c24-49e1-5730-23e1691cb0a5', '86ea3363-d3fb-d158-5c22-5fe5808ffe48', 'included', 'engine-dev'),
  ('16d204d7-10b7-3666-a7ce-9527f62ed9f2', '64e1e1cb-3c24-49e1-5730-23e1691cb0a5', '41ab3465-680d-b500-075e-b5eec7cf4840', 'included', 'engine-dev')
ON CONFLICT DO NOTHING;

COMMIT;
